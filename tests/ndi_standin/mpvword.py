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
    return "with sound" in name or "one still" in name or "sound only" in name


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
    sync_judged = True                     # whether this rig's mpv is the one the bounds for sound against picture were measured on

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

    def post_ndi(self, body):
        """POST /api/ndi with `body` (an action of the Owner's): (status, answer)."""
        raise NotImplementedError

    def receivers(self, name, bandwidth):
        """How many receivers the stand-in made for the source `name` with that bandwidth (10: sound alone; 100: a
        picture's), or None where that cannot be read (the helper is another process: a box)."""
        return None

    def choose_absent(self, name):
        """Make `name` the box's sound source although it is not on the network now, as a box has it that chose it
        on an earlier day; returns its id. Only where the settings can be written (not over HTTP: the panel takes
        a sound source from the list of what is there)."""
        raise Failed("this rig cannot choose a sound source that is not on the network")

    def next_clip(self):
        """Play a clip that is not NDI, by the panel, and wait until mpv has it; returns mpv's path for it."""
        raise Failed("this rig has no clip to play")

    def sound_from(self, name):
        """The Owner chooses `name` as where the sound of every NDI picture comes from; returns its id."""
        sid = self.source(name)
        code, out = self.post_ndi({"action": "sound_from", "id": sid})
        if code != 200 or not isinstance(out, dict) or out.get("sound_from") != sid or out.get("sound") is not True:
            raise Failed("POST /api/ndi (sound_from %r) answered HTTP %s: %s" % (name, code, str(out)[:300]))
        self.say("the sound of every NDI picture comes from %r" % name)
        return sid

    def sound_source(self):
        """What the page says of the box's sound source: a dict, or {} when it says nothing."""
        got = self.page().get("sound_source")
        return got if isinstance(got, dict) else {}

    def sound_source_is(self, what, name, state, arriving=None):
        got = self.sound_source()
        if got.get("name") != name or got.get("state") != state or (arriving is not None and got.get("arriving") is not arriving):
            raise Failed("%s: the page's sound source is %s; expected %r in the state %r%s"
                         % (what, json.dumps(got, sort_keys=True), name, state, "" if arriving is None else " with arriving %s" % arriving))

    def opened(self, what, name, bandwidth, times, or_more=False):
        """The stand-in's own count of the receivers made for `name`: it must be `times` (with `or_more`: at least
        that; the panel's watch may open a picture once more than the case needs). Where it cannot be read (a
        box), that is said and nothing is judged; real_install_test.sh counts the helper's own line there."""
        n = self.receivers(name, bandwidth)
        kind = "for sound alone" if bandwidth == 10 else "for a picture"
        if n is None:
            self.say("NOT READ here (the helper is another process): how many receivers %s the stand-in made for %r" % (kind, name))
        elif n != times and not (or_more and n > times):
            raise Failed("%s: the stand-in made %d receivers %s for %r; expected %d%s" % (what, n, kind, name, times, " or more" if or_more else ""))
        else:
            self.say("OPENED %d time%s %s: %r, by the stand-in's own count" % (n, "" if n == 1 else "s", kind, name))

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


# ---- sound from another sender (D87): the picture of one source, the sound of another -----------------------------------
# What these show and what they cannot is in pvj/NDI.md ("Sound from another sender: what the tests show"). In short:
# the stand-in's two sources share ONE clock, the process's; two real senders share none. And every number here is
# mpv's own (its tracks, its audio-pts, its avsync, its audio-delay): nobody hears anything.
SOUND_AFTER_IT_APPEARS = 12.0          # (c): mpv plays the sound within this long after the sound source is on the network
APPEARS_AFTER = 5.0                    # the stand-in: "appears 5 s after the first picture" (APPEARS_NS in standin.c)
STOPS_AFTER = 12.0                     # the stand-in: "stops after 12 s" (STOPS_NS)
OFFSET_MS = 200                        # the stand-in: "clicks 200 ms late"
DELAY_EXACT = 0.0005                   # how near mpv's audio-delay must be to the number asked for, in seconds


def case_sound_from(rig, picture, voice, seconds=30):
    """(a) A picture source without sound of its own, and the sound of a source that has sound and no picture: mpv has
    two tracks, sound against picture is inside the bounds every moving source with sound is held to, and audio-pts
    goes on at real time for `seconds`."""
    what = "the picture of %r with the sound of %r" % (picture, voice)
    rig.sound_from(voice)
    rig.start(picture, "matroska")
    rig.sync(what, picture, seconds=15, within=10, judged=rig.sync_judged)
    rig.watch(what, picture, seconds=int(seconds), late=2, audio=True)
    rig.sound_source_is(what, voice, "playing", arriving=True)
    rig.opened(what, voice, 10, 1)
    rig.opened(what, picture, 10, 0)


def case_sound_from_resized(rig, picture, voice):
    """(b) The picture changes size while it plays (four seconds, then smaller): the panel opens it again, and the
    sound is back within the time the re-open case without a sound source is given (case_size: judged at the end of
    15 seconds, the sound's clock at real time from no later than 10 seconds in). The sound's connection is not
    opened again for it: one receiver for sound alone in the whole case, two (or more) for the picture."""
    what = "a picture that changes size, %r, with the sound of %r" % (picture, voice)
    rig.sound_from(voice)
    rig.start(picture, "matroska")
    rig.watch(what, picture, seconds=15, size=size_of(picture, True), audio=True)
    rig.sound_source_is(what, voice, "playing", arriving=True)
    rig.opened(what, voice, 10, 1)
    rig.opened(what, picture, 100, 2, or_more=True)


def case_sound_from_late(rig, picture, voice):
    """(c) The sound sender is not on the network at the press and comes APPEARS_AFTER seconds after it. The picture
    plays at once, as bare frames; then the helper finds the sound source (it looks every 2 seconds), its first block
    ends the picture as "changed", the panel opens it again with sound. Stated: mpv plays the sound no later than
    SOUND_AFTER_IT_APPEARS seconds after the source appeared (2 s until the helper looks, 1 s the stand-in's quiet
    first second, 1 s the panel's watch, about 3 s the hand-over and the re-open, 1 s to see the clock move, and
    room)."""
    what = "the sound source %r comes %d seconds after the press of %r" % (voice, APPEARS_AFTER, picture)
    rig.choose_absent(voice)
    rig.sound_source_is(what + ", before the press", voice, "away", arriving=False)
    pressed = time.monotonic()
    rig.start(picture, "raw")
    answered = time.monotonic() - pressed
    if answered > 4.0:
        raise Failed("%s: the press was answered after %.1f seconds; with the sound source away the picture is not kept waiting for sound"
                     % (what, answered))

    def plays(audio):
        a = rig.look()
        time.sleep(1.0)
        b = rig.look()
        return b if not not_playing(a, b, rig.pipe, size=size_of(picture), audio=audio) else None
    if not wait(lambda: plays(False), 6, every=0.1):
        raise Failed("STUCK, %s: the picture did not play without sound within 6 seconds of the press: %s" % (what, json.dumps(rig.look(), sort_keys=True)))
    bare = time.monotonic() - pressed
    rig.say("PLAYS without sound %.1f seconds after the press (answered in %.1f): %r" % (bare, answered, picture))
    if bare > APPEARS_AFTER:
        raise Failed("%s: the picture took %.1f seconds to play, longer than the sound source is away; this case shows nothing" % (what, bare))
    end = pressed + APPEARS_AFTER + SOUND_AFTER_IT_APPEARS
    heard = None
    while heard is None and time.monotonic() < end:
        if plays(True):
            heard = time.monotonic() - pressed
    if heard is None:
        st = rig.page()
        raise Failed("NO SOUND, %s: %.0f seconds after the sound source appeared mpv does not play its sound. mpv: %s. The page: playing=%s "
                     "ended=%s sound_source=%s" % (what, SOUND_AFTER_IT_APPEARS, json.dumps(rig.look(), sort_keys=True), json.dumps(st.get("playing"), sort_keys=True),
                                                   json.dumps(st.get("ended"), sort_keys=True), json.dumps(st.get("sound_source"), sort_keys=True)))
    rig.say("SOUND %.1f seconds after the press, %.1f after the sound source appeared (stated: within %.0f): %s"
            % (heard, heard - APPEARS_AFTER, SOUND_AFTER_IT_APPEARS, what))
    rig.watch(what + ", from when its sound came", picture, seconds=8, late=1, audio=True)
    rig.sound_source_is(what, voice, "playing", arriving=True)
    rig.opened(what, voice, 10, 1)


def case_sound_from_stops(rig, picture, voice, seconds=12):
    """(d) The sound sender stops while the picture plays (the stand-in: STOPS_AFTER seconds after its receiver was
    made, the connection is said to be down and the finder drops the source). The picture goes on: for `seconds`
    after the page first says "away", mpv keeps the pipe, time-pos goes on at real time and never stands for a
    second; and at the end the page still says "away" for the sound source and "playing" for the picture."""
    what = "the sound source %r stops under the picture %r" % (voice, picture)
    rig.sound_from(voice)
    rig.start(picture, "matroska")
    rig.watch(what + ", before it stops", picture, seconds=4, audio=True)
    if not wait(lambda: rig.sound_source().get("state") == "away", STOPS_AFTER + 6):
        raise Failed("%s: %d seconds after it played with sound the page's sound source is %s, not away"
                     % (what, STOPS_AFTER + 6, json.dumps(rig.sound_source(), sort_keys=True)))
    rig.say("the page says the sound source is away: %s" % json.dumps(rig.sound_source(), sort_keys=True))
    looks, began = [], time.monotonic()
    while time.monotonic() - began < seconds:
        looks.append((time.monotonic(), ask(rig.socket, "path", "time-pos", "audio-pts", "core-idle", "paused-for-cache")))
        time.sleep(1.0)
    bad = []
    if any(m["path"] != rig.pipe for _, m in looks):
        bad.append("mpv let go of the pipe (its paths: %s)" % sorted({str(m["path"]) for _, m in looks}))
    pos = [(when, m["time-pos"]) for when, m in looks]
    if not all(number(v) for _, v in pos):
        bad.append("mpv had no time-pos in %d of %d looks" % (len([v for _, v in pos if not number(v)]), len(pos)))
    else:
        took, went = pos[-1][0] - pos[0][0], pos[-1][1] - pos[0][1]
        stood = [round(b[0] - began, 1) for a, b in zip(pos, pos[1:]) if b[1] - a[1] < 0.5 * (b[0] - a[0])]
        if went < 0.9 * took - 0.5:
            bad.append("time-pos went on by %.1f in %.1f seconds" % (went, took))
        if stood:
            bad.append("time-pos stood, or nearly, in the second before %s" % stood)
    sound = [m["audio-pts"] for _, m in looks]
    rig.say("FOR THE RECORD (not judged): while the sound source was away mpv's audio-pts went %r -> %r in %.1f seconds; core-idle %r, paused-for-cache %r"
            % (sound[0], sound[-1], looks[-1][0] - looks[0][0], looks[-1][1]["core-idle"], looks[-1][1]["paused-for-cache"]))
    st = rig.page()
    p = st.get("playing")
    if not isinstance(p, dict) or p.get("state") != "playing" or p.get("name") != picture:
        bad.append("the page does not say the picture plays: playing=%s ended=%s" % (json.dumps(p, sort_keys=True), json.dumps(st.get("ended"), sort_keys=True)))
    if bad:
        raise Failed("STALLED, %s: %s. mpv's looks: %s" % (what, "; ".join(bad), json.dumps([m for _, m in looks][::3], sort_keys=True)))
    rig.sound_source_is(what + ", at the end", voice, "away", arriving=False)
    rig.say("GOES ON %s: time-pos %.1f -> %.1f in %.1f seconds with the sound source away" % (what, pos[0][1], pos[-1][1], pos[-1][0] - pos[0][0]))


def case_sound_offset_unseen(rig, picture, voice):
    """(e), first half, fatal: two senders with a KNOWN offset (the stand-in's picture flashes on each whole second of
    its clock, its sound-only source clicks OFFSET_MS later; tests/test_ndi_standin.py finds those 0.2 s in the
    stream). mpv plays it inside the same bounds as any other: its avsync is its own belief about the times in the
    stream, and the helper gave each block its time as it arrived. So this passing shows that mpv CANNOT see the
    offset, which is why the delay is set by ear."""
    what = "sound %d ms late against the picture, by the stand-in (%r under %r)" % (OFFSET_MS, voice, picture)
    rig.sound_from(voice)
    rig.start(picture, "matroska")
    n = rig.sync(what, picture, seconds=12, within=10)
    rig.say("WHAT AVSYNC CANNOT SHOW: the sound is %d ms late against the picture in this stream, and mpv says worst |avsync| %.3f" % (OFFSET_MS, n["worst_avsync"]))
    rig.watch(what, picture, seconds=5, late=1, audio=True)


def case_sound_delay(rig, picture, voice, ms=-OFFSET_MS):
    """(e), second half: the Owner sets ndi.sound_delay_ms to take the known offset out, and mpv's audio-delay IS that
    number, in seconds, while the NDI picture is loaded; a change while it plays is mpv's at once; and it is 0 again
    once a clip that is not NDI plays. The sign is mpv's and the product's (NDI.md, "The delay": positive is sound
    later), so sound that is OFFSET_MS late is put right with -OFFSET_MS. Only the property is judged: nothing here
    hears whether flash and click meet."""
    what = "ndi.sound_delay_ms %d under %r with the sound of %r" % (ms, picture, voice)
    rig.sound_from(voice)
    code, out = rig.post_ndi({"action": "sound_delay", "ms": ms})
    if code != 200 or not isinstance(out, dict) or out.get("sound_delay_ms") != ms:
        raise Failed("POST /api/ndi (sound_delay %d) answered HTTP %s: %s" % (ms, code, str(out)[:300]))
    rig.start(picture, "matroska")
    rig.started(what, picture, 10)
    during = ask(rig.socket, "audio-delay", "audio-pts", "time-pos", "avsync")
    rig.say("DELAY %s: with the NDI picture loaded mpv says audio-delay %r (audio-pts %r, time-pos %r, avsync %r)"
            % (what, during["audio-delay"], during["audio-pts"], during["time-pos"], during["avsync"]))
    other = 120                            # changed while it plays: the other side of 0
    code, out = rig.post_ndi({"action": "sound_delay", "ms": other})
    if code != 200 or not isinstance(out, dict) or out.get("sound_delay_ms") != other:
        raise Failed("POST /api/ndi (sound_delay %d) under the playing source answered HTTP %s: %s" % (other, code, str(out)[:300]))
    time.sleep(0.5)
    moved = ask(rig.socket, "audio-delay", "path")
    rig.say("DELAY %s: after it was changed to %d while the source played mpv says audio-delay %r (its path %r)" % (what, other, moved["audio-delay"], moved["path"]))
    clip = rig.next_clip()
    after = ask(rig.socket, "audio-delay", "path")
    rig.say("DELAY %s: with the clip %r loaded mpv says audio-delay %r" % (what, clip, after["audio-delay"]))
    bad = []
    if not number(during["audio-delay"]) or abs(during["audio-delay"] - ms / 1000.0) > DELAY_EXACT:
        bad.append("with the NDI picture loaded mpv's audio-delay is %r; ndi.sound_delay_ms %d is %.3f" % (during["audio-delay"], ms, ms / 1000.0))
    if moved["path"] != rig.pipe or not number(moved["audio-delay"]) or abs(moved["audio-delay"] - other / 1000.0) > DELAY_EXACT:
        bad.append("after the delay was changed to %d under the playing source mpv's audio-delay is %r (its path %r)" % (other, moved["audio-delay"], moved["path"]))
    if not number(after["audio-delay"]) or abs(after["audio-delay"]) > DELAY_EXACT:
        bad.append("with the next clip loaded (%r) mpv's audio-delay is %r; it must be 0 again" % (after["path"], after["audio-delay"]))
    if bad:
        raise Failed("THE DELAY IS NOT THE PLAYER'S, %s: %s" % (what, "; ".join(bad)))


SOUND_ONLY_WORDS = "sends sound and no picture"                  # not chosen, and found to carry no picture (the helper's word)
SOUND_SOURCE_WORDS = "is where this box's NDI sound comes from"   # chosen as the box's sound source (the panel's own refusal)
NOT_TOUCHED_WORDS = "what plays was not touched"


def _refused_play(rig, what, sid, voice, must):
    """Press Play on `sid`; it must be refused with 409 in words that name `voice` and hold every text of `must`,
    and nothing of it may be in mpv or said to play. Returns (the words, seconds the answer took)."""
    began = time.monotonic()
    code, out = rig.post_play(sid)
    took = time.monotonic() - began
    words = str(out.get("error") if isinstance(out, dict) and out.get("error") else out)
    rig.say("%s: POST /api/play answered HTTP %s after %.1f seconds: %s" % (what, code, took, words[:300]))
    if code != 409 or voice not in words or [m for m in must if m not in words]:
        raise Failed("%s was not refused in words that name it and say %s: HTTP %s, %s" % (what, " and ".join(repr(m) for m in must), code, words[:300]))
    time.sleep(1.5)
    st = rig.page()
    m, p = rig.look(), st.get("playing")
    if m["path"] == rig.pipe or p:
        raise Failed("STUCK, %s was refused and yet: mpv's path is %r (the pipe is %s), the page says playing=%s"
                     % (what, m["path"], rig.pipe, json.dumps(p, sort_keys=True)))
    rig.say("REFUSED %s: \"%s\"; mpv has not got the pipe" % (what, words[:200]))
    return words, took


def case_sound_only_play(rig, voice):
    """(f) The sound-only source chosen with Play, as a picture is, in each of the two states it can be in. Both are
    refused, each in its own words, and nothing of the source is loaded into mpv.

    NOT CHOSEN as the box's sound source (each picture plays its own sound): the helper is asked, finds sound and no
    picture, and the answer names the source, says that it sends sound and no picture and where to choose it; from
    then on its row in the page's list says "sound_only"; a second press asks it again (the label never locks).

    CHOSEN as the box's sound source: the panel refuses before the player or the helper is touched, in words that
    name it, say that the box takes its sound from it and that what plays was not touched; it stays the sound source."""
    sid = rig.source(voice)
    code, out = rig.post_ndi({"action": "sound_from", "id": ""})
    if code != 200 or not isinstance(out, dict) or out.get("sound_from") != "" or out.get("sound") is not True:
        raise Failed("POST /api/ndi (each picture's own sound) answered HTTP %s: %s" % (code, str(out)[:300]))
    what = "Play on the sound-only source %r, not chosen as the sound source" % voice
    _refused_play(rig, what, sid, voice, (SOUND_ONLY_WORDS, "Where sound comes from"))
    row = [s for s in rig.page().get("sources", []) if isinstance(s, dict) and s.get("id") == sid]
    if not row or row[0].get("sound_only") is not True:
        raise Failed("%s was refused, and its row in the page's list does not say sound_only: %s" % (what, json.dumps(row, sort_keys=True)))
    # The label is advice, never a lock (the review of #132, H1): a second press asks the sender again, and since it
    # still sends no picture it is refused again in the same words.
    _refused_play(rig, what + ", pressed again", sid, voice, (SOUND_ONLY_WORDS,))
    what = "Play on the sound-only source %r, chosen as the box's sound source" % voice
    rig.sound_from(voice)
    _words, took = _refused_play(rig, what, sid, voice, (SOUND_SOURCE_WORDS, NOT_TOUCHED_WORDS, "choose another sound source"))
    if took > 3.0:
        raise Failed("%s was answered after %.1f seconds: it is refused before anything is asked or touched" % (what, took))
    if rig.page().get("sound_from") != sid:
        raise Failed("%s was refused, and it is no longer the box's sound source: the page says %r" % (what, rig.page().get("sound_from")))


def case_sound_only_play_under_a_picture(rig, picture, voice):
    """A picture plays with the sound-only source's sound, and Play is pressed on that sound source. The press is
    refused before the player or the helper is touched (the box's sound source is not shown as a picture), in words
    that say so; the picture and its sound play on through the ten seconds after it, and the page never gives an end.
    (Before 510d9ae the panel had the player let go of the pipe first, and the picture went black.)"""
    what = "Play on the sound-only source %r while %r plays with its sound" % (voice, picture)
    sid = rig.sound_from(voice)
    rig.start(picture, "matroska")
    rig.watch(what + ", before the press", picture, seconds=4, audio=True)
    code, out = rig.post_play(sid)
    words = str(out.get("error") if isinstance(out, dict) and out.get("error") else out)
    rig.say("POST /api/play answered HTTP %s: %s" % (code, words[:300]))
    if code != 409 or voice not in words or SOUND_SOURCE_WORDS not in words or NOT_TOUCHED_WORDS not in words:
        raise Failed("%s was not refused in words that name it, say %r and %r: HTTP %s, %s" % (what, SOUND_SOURCE_WORDS, NOT_TOUCHED_WORDS, code, words[:300]))
    end = rig.watch(what + ", after the refusal", picture, seconds=10, audio=True)
    if end != "plays":
        raise Failed("%s was refused, and the picture that played did not play on: %s" % (what, end))
    rig.say("AFTER THE REFUSED PRESS the picture that played before it plays on")


CASES = {"soundfrom": (case_sound_from, 3), "soundfromsize": (case_sound_from_resized, 2), "soundfromstops": (case_sound_from_stops, 2),
         "soundonlyplay": (case_sound_only_play, 1), "soundonlyunder": (case_sound_only_play_under_a_picture, 2),
         "sync": (case_sync, 1), "syncinfo": (case_syncinfo, 1), "syncresized": (case_sync_resized, 1), "syncpaused": (case_sync_paused, 1),
         "still": (case_still, 1), "twice": (case_twice, 1), "follow": (case_follow, 2), "sound": (case_sound, 1),
         "soundon": (case_soundon, 1), "size": (case_size, 1), "restart": (case_restart, 1), "stuck": (case_stuck, 1), "chain": (case_chain, 0),
         "gostill": (case_goes_still, 1)}
