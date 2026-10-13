# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""mpv's own word on whether an NDI source plays, and the hand-overs that are judged by it. One module for both
places that ask: tests/ndi_standin/box_check.py (a really installed box under systemd, the panel over HTTP) and
tests/test_ndi_mpv.py (no systemd: the real Api, Service and Player in one process, in a container with the Pi's
mpv). Each gives a Rig that knows how to reach its panel and its player; everything else is here, once.

"It plays" is never taken from the helper's counts alone (2026-10-12, measured on the owner's Pi: the helper said
"still" and wrote sound steadily while mpv sat with the pipe as its path, no track and no clock, for minutes; and a
source ended with the page saying neither that it plays nor why it does not). It is asked of mpv: the pipe is its
path, it has a track list with a picture track of the right size, and its clock moves. Each case is one way a
source is handed over, and ends in one of two good ways within its time: mpv plays, or GET /api/ndi gives a reason
in "ended". The two bad ways are named in what is raised: SILENT LOSS (the page says neither) and STUCK (the page
says it plays and mpv does not)."""
import json
import socket
import sys
import time

WIDTH, HEIGHT, FRAMES_A_SECOND = 320, 180, 30
LOOKED = ("path", "idle-active", "core-idle", "time-pos", "audio-pts", "track-list/count", "track-list", "video-params", "audio-params", "eof-reached")
CHAIN = ("PVJ-STANDIN (big moving with sound)", "PVJ-STANDIN (big at once A with sound)", "PVJ-STANDIN (big at once B with sound)",
         "PVJ-STANDIN (big changes size with sound)", "PVJ-STANDIN (one still then sound)", "PVJ-STANDIN (big moving with sound)")


TRYING = "; trying again"              # how the page's "ended" closes while the panel's watch is still bringing a source back


class Failed(Exception):
    pass


def trying(st):
    """The page says the source is not on the screen now and that the panel is trying again: not an end."""
    e = st.get("ended")
    return not st.get("playing") and isinstance(e, dict) and str(e.get("message", "")).endswith(TRYING)


def sound_rate(looks):
    """(seconds of sound played, seconds of wall clock they took, when that began) over the last stretch of `looks`
    ([(clock, look)]) in which audio-pts was a number and never went back (a source that is opened again begins at
    0 again). (0, 0, None) when the last look has no audio-pts."""
    run = []
    for when, seen in looks:
        pts = seen["audio-pts"]
        if not number(pts) or (run and pts < run[-1][1]):
            run = []
        if number(pts):
            run.append((when, pts))
    if not run:
        return 0.0, 0.0, None
    return run[-1][1] - run[0][1], run[-1][0] - run[0][0], run[0][0]


def size_of(name, changed=False):
    """The stand-in's picture for a source of that name (standin.c: the words in the name decide)."""
    if "big" in name:
        return (1264, 704) if changed else (1280, 720)
    return (256, 144) if changed else (WIDTH, HEIGHT)


def has_sound(name):
    return "with sound" in name or "one still" in name


def number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def wait(what, seconds, every=0.5):
    """The first true thing `what()` returns within `seconds`, else None."""
    end = time.monotonic() + seconds
    while True:
        got = what()
        if got or time.monotonic() >= end:
            return got or None
        time.sleep(every)


def ask(path, *names):
    """{property: value} from the control socket of the mpv at `path`; a property it will not give is "(the error)"."""
    out = dict.fromkeys(names, "(no answer)")
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(4)
        s.connect(path)
    except OSError as e:
        return dict.fromkeys(names, "(the player's socket: %s)" % e)
    try:
        for n, name in enumerate(names):
            s.sendall(json.dumps({"command": ["get_property", name], "request_id": n + 1}).encode() + b"\n")
        buf, left = b"", len(names)
        while left:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue
                rid = msg.get("request_id") if isinstance(msg, dict) else None
                if isinstance(rid, int) and 1 <= rid <= len(names) and "error" in msg:
                    out[names[rid - 1]] = msg.get("data") if msg["error"] == "success" else "(%s)" % msg["error"]
                    left -= 1
    except OSError as e:
        for name in names:
            if out[name] == "(no answer)":
                out[name] = "(%s)" % e
    finally:
        s.close()
    return out


def look(path):
    """What the mpv at `path` says of itself now, in a few plain fields."""
    m = ask(path, *LOOKED)
    tracks = m["track-list"] if isinstance(m["track-list"], list) else []
    # the size of a picture track is what the stream says (demux-w, demux-h); where an mpv does not give those for a
    # track it has, what its decoder puts out (video-params). A track that is not there has neither.
    vp = m["video-params"] if isinstance(m["video-params"], dict) else {}
    video = [(t.get("demux-w") or vp.get("w"), t.get("demux-h") or vp.get("h")) for t in tracks if isinstance(t, dict) and t.get("type") == "video"]
    return {"path": m["path"], "idle-active": m["idle-active"], "core-idle": m["core-idle"], "time-pos": m["time-pos"], "audio-pts": m["audio-pts"],
            "track-list/count": m["track-list/count"], "video-tracks": ["%sx%s" % wh for wh in video],
            "audio-tracks": len([t for t in tracks if isinstance(t, dict) and t.get("type") == "audio"]),
            "audio-params": m["audio-params"] if not isinstance(m["audio-params"], dict)
            else "%s Hz, %s channels" % (m["audio-params"].get("samplerate"), m["audio-params"].get("channel-count")),
            "eof-reached": m["eof-reached"]}


def not_playing(a, b, pipe, size=(WIDTH, HEIGHT), audio=None, moving=True, clock=True):
    """"" when mpv plays by its own word, else why not. `a` and `b` are two looks about a second apart. `audio`:
    True, the sound must be there; False, it must not; None, either. `moving`: its clock must have gone on between
    the looks (for one still picture and then sound: the picture's or the sound's clock). `clock`: it must have one."""
    if b["path"] != pipe:
        return "mpv has not got the pipe loaded (its path is %r, idle-active %r)" % (b["path"], b["idle-active"])
    if not number(b["track-list/count"]) or b["track-list/count"] < 1:
        return "mpv has the pipe as its path and no track (track-list/count %r): it has not opened it" % (b["track-list/count"],)
    if "%sx%s" % size not in b["video-tracks"]:
        return "mpv has no picture track of %d x %d (its picture tracks: %s)" % (size[0], size[1], b["video-tracks"] or "none")
    if audio is True and (b["audio-tracks"] < 1 or not isinstance(b["audio-params"], str) or b["audio-params"].startswith("(")):
        return "mpv has no sound (sound tracks %d, audio-params %r)" % (b["audio-tracks"], b["audio-params"])
    if audio is False and b["audio-tracks"]:
        return "mpv still has a sound track; the stream without sound has not been loaded"
    if clock and not number(b["time-pos"]) and not number(b["audio-pts"]):
        return "mpv has no clock (time-pos %r, audio-pts %r)" % (b["time-pos"], b["audio-pts"])
    if audio is True and moving and not (number(a["audio-pts"]) and number(b["audio-pts"]) and b["audio-pts"] > a["audio-pts"] + 0.2):
        # Where sound must be there, it is the SOUND's clock that must go on, whatever the picture's does. On the
        # Pi (2026-10-12) a still picture with sound had two tracks, sound parameters, a sound device and
        # core-idle false, and not one sample was played: audio-pts was unavailable, or a number that stood.
        return ("mpv's sound does not play: its clock does not move (audio-pts %r -> %r, time-pos %r -> %r, core-idle %r)"
                % (a["audio-pts"], b["audio-pts"], a["time-pos"], b["time-pos"], b["core-idle"]))
    if moving and not any(number(a[k]) and number(b[k]) and b[k] > a[k] + 0.2 for k in ("time-pos", "audio-pts")):
        return ("mpv's clock does not move (time-pos %r -> %r, audio-pts %r -> %r, core-idle %r)"
                % (a["time-pos"], b["time-pos"], a["audio-pts"], b["audio-pts"], b["core-idle"]))
    return ""


# ---- sound against picture, by mpv's own account -----------------------------------------------------------------------
# What mpv believes of its own sync, and nothing more: with the helper stamping picture and sound as they ARRIVE, mpv
# reads 0.000 even if the sender's sound and picture were out of step. So this guards the box's own pipeline (the
# helper's times, the player's options) and nothing upstream. The bounds were measured on mpv 0.40 (2026-10-12): the
# stream as it was before the sound track began at 0 gave total-avsync-change 0.206 where this one gives under 0.02.
SYNC_ASKED = ("path", "time-pos", "audio-pts", "avsync", "total-avsync-change", "frame-drop-count", "decoder-frame-drop-count",
              "estimated-frame-number")
AVSYNC_MAX = 0.040                     # |avsync| in every sample taken while the picture moves
AVSYNC_CHANGE_MAX = 0.02               # how much total-avsync-change may grow over a case: the discriminator
SOUND_PICTURE_MAX = 0.1                # |audio-pts - time-pos|
DROPS_MAX = 0.01                       # dropped frames (the output's and the decoder's) as a share of the frames in the case
BACK_IN_SYNC = 0.5                     # after a picture that stood moves again: from this long after, avsync is inside the bound


def sync_samples(path, seconds, every=0.5):
    """[(clock, {property: value})] from the mpv at `path`, one every `every` seconds for `seconds`."""
    out, began = [], time.monotonic()
    while True:
        out.append((time.monotonic(), ask(path, *SYNC_ASKED)))
        left = began + len(out) * every - time.monotonic()
        if time.monotonic() - began >= seconds:
            return out
        if left > 0:
            time.sleep(left)


def moving_samples(samples):
    """The samples in which the picture's clock went on since the sample before (a picture that stands has no sync to
    speak of), each with the clock of the sample."""
    out = []
    for (_, a), (when, b) in zip(samples, samples[1:]):
        if number(a["time-pos"]) and number(b["time-pos"]) and b["time-pos"] > a["time-pos"]:
            out.append((when, b))
    return out


def sync_numbers(samples):
    """What a case's samples say, as plain numbers for the log: the worst |avsync| and |audio-pts - time-pos| among the
    samples where the picture moved, how much total-avsync-change grew, the frames dropped and the frames there were.
    None where mpv gave no number at all."""
    moved = [m for _, m in moving_samples(samples)]
    sync = [abs(m["avsync"]) for m in moved if number(m["avsync"])]
    apart = [abs(m["audio-pts"] - m["time-pos"]) for m in moved if number(m["audio-pts"])]
    total = [m["total-avsync-change"] for _, m in samples if number(m["total-avsync-change"])]
    drops = 0
    for key in ("frame-drop-count", "decoder-frame-drop-count"):
        got = [m[key] for _, m in samples if number(m[key])]
        drops += max(0, got[-1] - got[0]) if got else 0
    frames = [m["estimated-frame-number"] for _, m in samples if number(m["estimated-frame-number"])]
    return {"samples": len(samples), "moving": len(moved), "no_avsync": len(moved) - len(sync), "no_sound_clock": len(moved) - len(apart),
            "worst_avsync": max(sync) if sync else None, "worst_apart": max(apart) if apart else None,
            "avsync_change": (total[-1] - total[0]) if total else None, "drops": drops,
            "frames": (frames[-1] - frames[0]) if frames else 0}


def sync_breaches(n, least_moving):
    """Why a case's numbers are outside the bounds, as a list of plain sentences; empty when they are inside."""
    out = []
    if n["moving"] < least_moving:
        out.append("the picture moved in only %d of %d samples (at least %d expected)" % (n["moving"], n["samples"], least_moving))
    if n["no_avsync"] or n["worst_avsync"] is None:
        out.append("mpv gave no avsync in %d of the %d samples where the picture moved" % (n["no_avsync"], n["moving"]))
    elif n["worst_avsync"] > AVSYNC_MAX:
        out.append("avsync was as far as %.3f (the bound is %.3f)" % (n["worst_avsync"], AVSYNC_MAX))
    if n["avsync_change"] is None:
        out.append("mpv gave no total-avsync-change")
    elif abs(n["avsync_change"]) >= AVSYNC_CHANGE_MAX:
        out.append("total-avsync-change grew by %.3f (the bound is under %.2f)" % (n["avsync_change"], AVSYNC_CHANGE_MAX))
    if n["no_sound_clock"] or n["worst_apart"] is None:
        out.append("mpv had no audio-pts in %d of the %d samples where the picture moved" % (n["no_sound_clock"], n["moving"]))
    elif n["worst_apart"] > SOUND_PICTURE_MAX:
        out.append("audio-pts and time-pos were as far apart as %.3f (the bound is %.1f)" % (n["worst_apart"], SOUND_PICTURE_MAX))
    if n["frames"] <= 0:
        out.append("mpv counted no frames")
    elif n["drops"] >= DROPS_MAX * n["frames"]:
        out.append("%d of %d frames were dropped (the bound is under 1 in 100)" % (n["drops"], n["frames"]))
    return out


def sync_line(what, n):
    def f(v):
        return "none" if v is None else "%.3f" % v
    return ("SYNC %s: worst |avsync| %s, total-avsync-change grew %s, worst |audio-pts - time-pos| %s, %d of %d frames dropped; "
            "the picture moved in %d of %d samples" % (what, f(n["worst_avsync"]), f(n["avsync_change"]), f(n["worst_apart"]), n["drops"],
                                                      n["frames"], n["moving"], n["samples"]))


class Rig:
    """A panel and a player to ask. A subclass says where they are: `socket` (mpv's control socket), `pipe` (the
    path the player is told to read), and the four questions below. The rest is the same wherever it runs."""
    socket = pipe = None

    def page(self):
        """GET /api/ndi's answer, a dict (never raises: what went wrong is in the dict)."""
        raise NotImplementedError

    def post_play(self, sid):
        """POST /api/play {"ndi": sid}: (status, answer)."""
        raise NotImplementedError

    def post_sound(self, on):
        """POST /api/ndi {"action": "sound", "on": on}: (status, answer)."""
        raise NotImplementedError

    def player_status(self):
        """(status, the "player" part of GET /api/status or None)."""
        raise NotImplementedError

    def say(self, line):
        print(line)
        sys.stdout.flush()

    def look(self):
        return look(self.socket)

    def source(self, name):
        def find():
            return [s["id"] for s in self.page().get("sources", []) if isinstance(s, dict) and s.get("name") == name]
        found = wait(find, 40)
        if not found:
            raise Failed("the source %r is not in the panel's list (GET /api/ndi)" % name)
        return found[0]

    def wanted(self):
        """Whether the owner's switch for sound is on, by the page."""
        return self.page().get("sound") is True

    def sound(self, word):
        want = word == "on"
        code, out = self.post_sound(want)
        if code != 200 or not isinstance(out, dict) or out.get("sound") is not want:
            raise Failed("POST /api/ndi (sound %s) answered HTTP %s: %s" % (word, code, str(out)[:300]))
        self.say("sound is %s" % word)

    def start(self, name, container=None, size=None):
        """Ask the panel to play `name`. Without `container`: what it must be by the switch and the source's name."""
        size = size or size_of(name)
        container = container or ("matroska" if self.wanted() and has_sound(name) else "raw")
        sid = self.source(name)
        code, out = self.post_play(sid)
        if code != 200 or not isinstance(out, dict):
            raise Failed("POST /api/play for %r answered HTTP %s: %s" % (name, code, out))
        self.say("POST /api/play: %s" % json.dumps(out, sort_keys=True))
        if (out.get("playing"), out.get("width"), out.get("height")) != ("ndi", size[0], size[1]):
            raise Failed("POST /api/play for %r did not answer with the stand-in's picture: %s" % (name, out))
        if out.get("sound") is not (container == "matroska"):
            raise Failed("POST /api/play for %r says sound %s; the pipe was to carry %s" % (name, out.get("sound"), container))
        return sid

    def started(self, what, name, within):
        """Wait until mpv's picture clock moves on the pipe (playback has started), at most `within` seconds; then
        one second more, from where sound against picture is sampled."""
        def moves():
            a = ask(self.socket, "path", "time-pos")
            time.sleep(0.25)
            b = ask(self.socket, "path", "time-pos")
            return b["path"] == self.pipe and number(a["time-pos"]) and number(b["time-pos"]) and b["time-pos"] > a["time-pos"]
        if not wait(moves, within, every=0.1):
            raise Failed("STUCK, %s: %r did not start within %s seconds by mpv's own word: %s"
                         % (what, name, within, json.dumps(self.look(), sort_keys=True)))
        time.sleep(1.0)

    def sync(self, what, name, seconds=15, within=10, judged=True):
        """Sound against picture for a moving source with sound: every half second from one second after playback
        started, for `seconds`. The numbers are printed whatever they are; outside the bounds it raises (unless
        `judged` is False: then a breach is printed as such and nothing more). Returns the numbers."""
        self.started(what, name, within)
        n = sync_numbers(sync_samples(self.socket, seconds))
        self.say(sync_line(what, n))
        bad = sync_breaches(n, least_moving=int(0.8 * (n["samples"] - 1)))
        if bad and judged:
            raise Failed("OUT OF SYNC, %s: %s. %s" % (what, "; ".join(bad), sync_line(what, n)))
        if bad:
            self.say("SYNC BREACH, printed and not judged here (the bounds were measured on mpv 0.40): %s: %s" % (what, "; ".join(bad)))
        return n

    def watch(self, what, name, seconds=10, late=None, **want):
        """After a hand-over: look at mpv every second for `seconds`, and judge by the END of that time (a source
        that plays for three seconds and is then lost has not played). Good: mpv plays, or the page gives a final
        reason in "ended" ("...; trying again" is not final: a source the panel is still bringing back when the time
        is up is NOT BACK). Anything else raises with what mpv and the page said.

        Where sound is expected (audio=True) and the clock must move, the SOUND must have played at about real time:
        from no later than `late` seconds into the look (the time a hand-over may take; by default six tenths of
        it) to its end, audio-pts must have gone on by nine tenths of the wall clock. "It moves" is not enough: at
        30 seconds it must be near 30."""
        want.setdefault("size", size_of(name))
        late = 0.6 * seconds if late is None else late
        began, before, first, why, said = time.monotonic(), self.look(), None, "not looked at yet", []
        looks = [(began, before)]
        while time.monotonic() - began < seconds:
            time.sleep(1.0)
            now = self.look()
            why = not_playing(before, now, self.pipe, **want)
            if not why and first is None:
                first = time.monotonic() - began
            said.append("%.0fs %s" % (time.monotonic() - began, why or "plays"))
            before = now
            looks.append((time.monotonic(), now))
        if not why and want.get("audio") is True and want.get("moving", True):
            played, took, since = sound_rate(looks)
            if since is None or since - began > late + 1.0 or played < 0.9 * took - 0.5:
                why = ("mpv's sound did not play at real time: in the last %.1f seconds it had a sound clock for, audio-pts went on by %.1f "
                       "(that stretch began %s seconds into the look; it may begin %.1f in at the latest)"
                       % (took, played, "%.1f" % (since - began) if since is not None else "never", late))
                said.append("at the end: " + why)
        st = self.page()
        p, e = st.get("playing"), st.get("ended")
        if not why:
            self.say("PLAYS %s: %r, by mpv's own word from %.0f seconds on and still after %d: %s"
                     % (what, name, first, seconds, json.dumps(before, sort_keys=True)))
            return "plays"
        page = "the page says playing=%s ended=%s" % (json.dumps(p, sort_keys=True), json.dumps(e, sort_keys=True))
        if trying(st):
            kind = "NOT BACK"
        elif not p and isinstance(e, dict) and e.get("message"):
            self.say("ENDED WITH A REASON %s: %r does not play and the page says why: \"%s\"" % (what, name, e["message"]))
            return "ended"
        else:
            kind = "SILENT LOSS" if not p and not e else "STUCK"
        if len(said) > 14:                 # a long look: the first and the last seconds say it
            said = said[:6] + ["..."] + said[-6:]
        raise Failed("%s, %s: after %d seconds %s. %s. %s. Second by second: %s. mpv's last word: %s"
                     % (kind, what, seconds, why,
                        "It did play, from second %.0f, and then stopped" % first if first is not None else "It never played in that time",
                        page, "; ".join(said), json.dumps(before, sort_keys=True)))


def case_still(rig, name, seconds=10, late=None):
    """One still picture and then only sound, with sound on: what NDI's Test Patterns sends. On the Pi the helper
    said "still" and wrote sound, and mpv had the pipe as its path with no track and no clock, for minutes."""
    rig.sound("on")
    rig.start(name, "matroska")
    rig.watch("one still picture then only sound, sound on", name, seconds=seconds, late=late, audio=True)


def case_twice(rig, name):
    """The same source played twice in a row, sound off (bare frames). The source sends one picture and then
    nothing, so mpv's clock cannot move: it must have the pipe open with the picture's track and a clock."""
    rig.start(name, "raw")
    time.sleep(3.0)
    rig.start(name, "raw")
    rig.watch("the same source played a second time", name, moving=False)


def case_follow(rig, first, second):
    """Source A, and source B at once: both have their first picture the moment they are opened, so B's writer can
    meet the reader that A left. With sound on and sources that have sound, both are Matroska."""
    with_sound = rig.wanted() and has_sound(second)
    rig.start(first)
    time.sleep(2.0)
    sid = rig.start(second)
    if rig.watch("one source straight after another%s" % (", both with sound" if with_sound else ""), second, audio=with_sound) != "plays":
        return
    code, pl = rig.player_status()
    if not isinstance(pl, dict) or pl.get("ndi_id") != sid:
        raise Failed("one source straight after another: mpv plays, and GET /api/status does not name %r: HTTP %s, player=%s"
                     % (second, code, json.dumps(pl)))


def case_sound(rig, name):
    """Sound switched on and off again under a playing source: the helper ends it as "changed" and the panel loads
    it again, first with its sound (Matroska), then without (bare frames)."""
    rig.sound("off")
    rig.start(name, "raw")
    rig.watch("before the switch", name, seconds=4, audio=False)
    rig.sound("on")
    rig.watch("sound switched on under a playing source", name, audio=True)
    rig.sound("off")
    rig.watch("sound switched off again under a playing source", name, audio=False)


def case_soundon(rig, name):
    """The other direction: the source starts with its sound, sound is switched off under it, and on again."""
    rig.sound("on")
    rig.start(name, "matroska")
    rig.watch("before the switch", name, seconds=4, audio=True)
    rig.sound("off")
    rig.watch("sound switched off under a source that played with sound", name, audio=False)
    rig.sound("on")
    rig.watch("sound switched on again under it", name, audio=True)


def case_size(rig, name):
    """A source that changes size while it plays (Scan Converter does at every clip): four seconds at one size,
    then a smaller one. The panel opens it again; mpv must then play the new size. With sound on and a source that
    has sound, both streams are Matroska (the Pi's lost re-open was that)."""
    with_sound = rig.wanted() and has_sound(name)
    rig.start(name)
    rig.watch("a source that changed size%s" % (", with sound" if with_sound else ""), name, seconds=15, size=size_of(name, True), audio=with_sound)


def case_stuck(rig, name):
    """What was done on the Pi: one still picture and then sound with sound on (which leaves mpv stuck opening the
    pipe: hand-over 1), and from THAT state, nothing cleaned: sound off and the same source played again, then
    sound switched on under it."""
    rig.sound("on")
    rig.start(name, "matroska")
    time.sleep(6.0)
    rig.say("STATE before the hand-over (not judged here; hand-over 1 judges it): mpv says %s" % json.dumps(rig.look(), sort_keys=True))
    rig.sound("off")
    rig.start(name, "raw")
    rig.watch("the same source with sound off, straight after its load with sound that was stuck", name, moving=False, audio=False)
    rig.sound("on")
    rig.watch("sound switched on under it after that", name, audio=True)


def case_chain(rig):
    """Several hand-overs back to back with nothing cleaned between them, sound on throughout except where it is
    switched: what one leaves is what the next one meets. Every link is judged; a link that fails does not stop the
    chain, and the REASON at the end names each one that failed."""
    failed = []

    def link(what, name, **want):
        try:
            rig.watch("chain: " + what, name, **want)
        except Failed as e:
            rig.say("CHAIN LINK FAILED: %s" % e)
            failed.append(str(e).split(",", 1)[0] + " (" + what + ")")

    def play(name):
        try:
            rig.start(name)
            return True
        except Failed as e:
            rig.say("CHAIN LINK FAILED: %s" % e)
            failed.append("could not be started (%s)" % name)
            return False
    rig.sound("on")
    moving, a, b, changes, still, again = CHAIN
    if play(moving):
        link("the first source, with sound", moving, audio=True)
    if play(a):
        time.sleep(2.0)
    if play(b):
        link("B at once after A, both with sound", b, audio=True)
    rig.sound("off")
    link("sound switched off under B", b, audio=False)
    rig.sound("on")
    link("sound switched on again under B", b, audio=True)
    if play(changes):
        link("a source with sound that changes size", changes, seconds=15, size=size_of(changes, True), audio=True)
    if play(still):
        link("one still picture then only sound", still, audio=True)
    if play(again):
        link("a moving source with sound, after the still one", again, audio=True)
    if failed:
        raise Failed("the chain: %d of its links failed: %s" % (len(failed), "; ".join(failed)))


def case_restart(rig, name):
    """Asked right after the helper was restarted under a playing source: the panel brings the source back."""
    rig.watch("the helper restarted under a playing source", name)


def case_goes_still(rig, name, seconds=20):
    """A moving source with sound whose picture stops while it plays (a sender that shows a still, a paused clip):
    five seconds of pictures, then only sound. mpv must go on: the picture's track stays and the clock runs on."""
    rig.sound("on")
    rig.start(name, "matroska")
    rig.watch("before the picture stopped", name, seconds=4, audio=True)
    rig.watch("a moving source with sound whose picture stopped", name, seconds=seconds, audio=True)


def case_sync(rig, name, seconds=15, within=10, judged=True):
    """A moving source with its sound: mpv's own account of sound against picture stays inside the bounds."""
    rig.sound("on")
    rig.start(name, "matroska")
    rig.sync("sound against picture, %s" % name, name, seconds=seconds, within=within, judged=judged)
    rig.watch("after its sync was looked at", name, seconds=3, audio=True)


def case_syncinfo(rig, name):
    """The same numbers on record from an mpv the bounds were not measured on: printed, not judged."""
    case_sync(rig, name, seconds=10, judged=False)


def case_sync_resized(rig, name):
    """A source with sound that changes size: the panel opens it again, and the stream after the re-open is in sync."""
    rig.sound("on")
    rig.start(name, "matroska")
    rig.watch("a source with sound that changed size, before its sync is looked at", name, seconds=12, size=size_of(name, True), audio=True)
    rig.sync("sound against picture after a re-open at another size, %s" % name, name, seconds=12, within=5)


def case_sync_paused(rig, name, still=20.0):
    """A clip that is paused and goes on: five seconds moving, `still` seconds with no picture, moving again. The
    sound must have played throughout (audio-pts at real time over the whole case), and from BACK_IN_SYNC seconds
    after the picture is seen to move again, avsync must be inside the bound. "Moves again" is the helper's own
    count of pictures received going on after it stood (asked with every sample, so seen within a quarter of a
    second where the page is asked directly; a panel asked over HTTP keeps the helper's answer for a second)."""
    what = "sound against picture across a pause, %s" % name
    rig.sound("on")
    rig.start(name, "matroska")
    rig.started(what, name, 10)
    samples, received, began = [], [], time.monotonic()
    while time.monotonic() - began < 4.0 + still + 8.0:
        samples.append((time.monotonic(), ask(rig.socket, *SYNC_ASKED)))
        p = rig.page().get("playing")
        received.append(p.get("counts", {}).get("received") if isinstance(p, dict) else None)
        left = began + len(samples) * 0.25 - time.monotonic()
        if left > 0:
            time.sleep(left)
    # the pause, by the helper's count: it stands for at least half the pause, and then goes on
    stood_from = resumed = None
    for i in range(1, len(samples)):
        same = received[i] is not None and received[i] == received[i - 1]
        if same and stood_from is None:
            stood_from = samples[i - 1][0]
        elif not same and stood_from is not None:
            if samples[i - 1][0] - stood_from >= still / 2:
                resumed = i
                break
            stood_from = None
    before = sync_numbers(samples[:12])                      # the three seconds before the pause
    rig.say(sync_line(what + ", before the pause", before))
    looks = [(when, {"audio-pts": m["audio-pts"]}) for when, m in samples]
    played, took, since = sound_rate(looks)
    rig.say("SYNC %s: the sound's clock went on by %.1f seconds in %.1f seconds of the case" % (what, played, took))
    bad = []
    if since is None or since > samples[0][0] + 0.5 or played < 0.9 * took - 0.5:
        bad.append("the sound did not play throughout: audio-pts went on by %.1f in the last %.1f seconds it had a clock for, of %.1f"
                   % (played, took, samples[-1][0] - samples[0][0]))
    if resumed is None:
        bad.append("the picture was not seen to stand for %.0f seconds and then move again (by the helper's count of pictures received: %s)"
                   % (still / 2, received[::8]))
    else:
        at = samples[resumed][0]
        after = [(when, m) for when, m in samples[resumed:] if when >= at + BACK_IN_SYNC]
        n = sync_numbers(after)
        rig.say(sync_line(what + ", from %.1f s after it moved again" % BACK_IN_SYNC, n))
        if n["no_avsync"] or n["worst_avsync"] is None:
            bad.append("after the pause mpv gave no avsync in %d of %d samples where the picture moved" % (n["no_avsync"], n["moving"]))
        elif n["worst_avsync"] > AVSYNC_MAX:
            bad.append("from %.1f s after the picture moved again avsync was as far as %.3f (the bound is %.3f)" % (BACK_IN_SYNC, n["worst_avsync"], AVSYNC_MAX))
        if n["moving"] < 8:
            bad.append("after the pause the picture moved in only %d samples" % n["moving"])
    if before["worst_avsync"] is None or before["worst_avsync"] > AVSYNC_MAX:
        bad.append("before the pause avsync was %s (the bound is %.3f)" % (before["worst_avsync"], AVSYNC_MAX))
    whole = sync_numbers(samples)
    rig.say(sync_line(what + ", the whole case (for the record; a pause is not judged by these)", whole))
    if bad:
        raise Failed("OUT OF SYNC, %s: %s" % (what, "; ".join(bad)))


CASES = {"sync": (case_sync, 1), "syncinfo": (case_syncinfo, 1), "syncresized": (case_sync_resized, 1), "syncpaused": (case_sync_paused, 1),
         "still": (case_still, 1), "twice": (case_twice, 1), "follow": (case_follow, 2), "sound": (case_sound, 1),
         "soundon": (case_soundon, 1), "size": (case_size, 1), "restart": (case_restart, 1), "stuck": (case_stuck, 1), "chain": (case_chain, 0),
         "gostill": (case_goes_still, 1)}
