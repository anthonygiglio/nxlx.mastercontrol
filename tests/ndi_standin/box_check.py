#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The questions tests/real_install_test.sh asks while a source of the stand-in for NDI's library
(tests/ndi_standin/standin.c) plays on a really installed box: asked of the panel over its own API, as a paired
device asks, and of the player over its control socket, as root. Nothing here reaches into the helper.

    PVJ_TOKEN=<a paired device's token> python3 -B box_check.py play NAME raw|matroska
                                                               back NAME SECONDS
                                                               sound on|off
                                                               case still NAME | twice NAME | follow A B | sound NAME
                                                                    | soundon NAME | size NAME | restart NAME
                                                                    | stuck NAME | chain
                                                               dump

Exit 0: everything asked held. Exit 1: something did not; the reason is the line that begins "REASON:", and what
the panel and the player said last is printed under it.

"It plays" is never taken from the helper's counts alone (2026-10-12, measured on the owner's Pi: the helper said
"still" and wrote sound steadily while mpv sat with the pipe as its path, no track and no clock, for minutes; and a
source ended with the page saying neither that it plays nor why it does not). It is asked of mpv: the pipe is its
path, it has a track list with a picture track of the right size, and its clock moves. Each `case` is one way a
source is handed over, and ends in one of two good ways within its time: mpv plays, or GET /api/ndi gives a reason
in "ended". The two bad ways are named in the REASON line: SILENT LOSS (the page says neither) and STUCK (the page
says it plays and mpv does not)."""
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("PVJ_PANEL", "http://127.0.0.1")
PLAYER = os.environ.get("PVJ_PLAYER_SOCKET", "/run/pvj/player/player.sock")
PIPE = os.environ.get("PVJ_NDI_PIPE", "/run/pvj-ndi-pipe/ndi.fifo")
WIDTH, HEIGHT, FRAMES_A_SECOND = 320, 180, 30


def size_of(name, changed=False):
    """The stand-in's picture for a source of that name (standin.c: the words in the name decide)."""
    if "big" in name:
        return (1264, 704) if changed else (1280, 720)
    return (256, 144) if changed else (WIDTH, HEIGHT)


def has_sound(name):
    return "with sound" in name or "one still" in name


ASKED = ("path", "idle-active", "core-idle", "pause", "eof-reached", "playback-time", "time-pos", "audio-pts", "track-list/count",
         "track-list", "video-params", "audio-params", "vo-configured", "current-vo", "current-ao", "video-codec", "audio-codec-name",
         "estimated-frame-number", "frame-drop-count", "demuxer-cache-state/underrun", "paused-for-cache", "seeking", "mpv-version")
LOOKED = ("path", "idle-active", "core-idle", "time-pos", "audio-pts", "track-list/count", "track-list", "video-params", "audio-params", "eof-reached")


class Failed(Exception):
    pass


def http(method, path, body=None, timeout=30):
    """(status, the answer as JSON or as text)."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + os.environ.get("PVJ_TOKEN", ""))
    if data is not None:
        req.add_header("Content-Type", "application/json")
        req.add_header("X-PVJ-Request", "1")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read()
    except (OSError, ValueError) as e:
        return 0, "no answer (%s)" % e
    try:
        return code, json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return code, raw.decode("utf-8", "replace")[:600]


def ndi():
    code, out = http("GET", "/api/ndi", timeout=20)
    return out if code == 200 and isinstance(out, dict) else {"_http": code, "_said": out}


def player(*names):
    """{property: value} from the player's control socket; a property it will not give is "(the error)"."""
    out = dict.fromkeys(names, "(no answer)")
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(4)
        s.connect(PLAYER)
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


def dump():
    print("--- GET /api/ndi")
    print(json.dumps(ndi(), indent=1, sort_keys=True))
    code, st = http("GET", "/api/status", timeout=20)
    print("--- GET /api/status: HTTP %s, its player part" % code)
    print(json.dumps(st.get("player") if isinstance(st, dict) else st, indent=1, sort_keys=True))
    print("--- the player (mpv) over its socket")
    print(json.dumps(player(*ASKED), indent=1, sort_keys=True))
    sys.stdout.flush()


def wait(what, seconds, every=0.5):
    """The first true thing `what()` returns within `seconds`, else None."""
    end = time.monotonic() + seconds
    while True:
        got = what()
        if got or time.monotonic() >= end:
            return got or None
        time.sleep(every)


def source(name):
    def look():
        st = ndi()
        return [s["id"] for s in st.get("sources", []) if isinstance(s, dict) and s.get("name") == name]
    found = wait(look, 40)
    if not found:
        raise Failed("the source %r is not in the panel's list (GET /api/ndi)" % name)
    return found[0]


def ended(st, name):
    """Raise if the panel says the source ended; the words are the helper's."""
    p, e = st.get("playing"), st.get("ended")
    if isinstance(p, dict) and p.get("state") in ("stopped", "refused"):
        raise Failed("%r did not play: it %s with \"%s\"" % (name, p["state"], p.get("message")))
    if not p and isinstance(e, dict):
        raise Failed("%r did not play: it ended with \"%s\"" % (name, e.get("message")))


def shown(st):
    p = st.get("playing")
    return p.get("counts", {}).get("shown", 0) if isinstance(p, dict) else 0


def written(st):
    p = st.get("playing")
    a = p.get("audio") if isinstance(p, dict) else None
    return a.get("counts", {}).get("written", 0) if isinstance(a, dict) else 0


def wanted():
    """Whether the owner's switch for sound is on, by the page."""
    return ndi().get("sound") is True


def start(name, container=None, size=None):
    """Ask the panel to play `name`. Without `container`: what it must be by the switch and the source's name."""
    size = size or size_of(name)
    container = container or ("matroska" if wanted() and has_sound(name) else "raw")
    sid = source(name)
    code, out = http("POST", "/api/play", {"ndi": sid}, timeout=40)
    if code != 200 or not isinstance(out, dict):
        raise Failed("POST /api/play for %r answered HTTP %s: %s" % (name, code, out))
    print("POST /api/play: %s" % json.dumps(out, sort_keys=True))
    if (out.get("playing"), out.get("width"), out.get("height")) != ("ndi", size[0], size[1]):
        raise Failed("POST /api/play for %r did not answer with the stand-in's picture: %s" % (name, out))
    if out.get("sound") is not (container == "matroska"):
        raise Failed("POST /api/play for %r says sound %s; the pipe was to carry %s" % (name, out.get("sound"), container))
    return sid


def look():
    """What mpv says of itself now, in a few plain fields."""
    m = player(*LOOKED)
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


def number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def not_playing(a, b, size=(WIDTH, HEIGHT), audio=None, moving=True, clock=True):
    """"" when mpv plays by its own word, else why not. `a` and `b` are two looks about a second apart. `audio`:
    True, the sound must be there; False, it must not; None, either. `moving`: its clock must have gone on between
    the looks (for one still picture and then sound: the picture's or the sound's clock). `clock`: it must have one."""
    if b["path"] != PIPE:
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
    if moving and not any(number(a[k]) and number(b[k]) and b[k] > a[k] + 0.2 for k in ("time-pos", "audio-pts")):
        return ("mpv's clock does not move (time-pos %r -> %r, audio-pts %r -> %r, core-idle %r)"
                % (a["time-pos"], b["time-pos"], a["audio-pts"], b["audio-pts"], b["core-idle"]))
    return ""


def watch(what, name, seconds=10, **want):
    """After a hand-over: look at mpv every second for `seconds`, and judge by the END of that time (a source that
    plays for three seconds and is then lost has not played). Good: mpv plays, or the page gives a reason in "ended".
    Anything else raises with what mpv and the page said."""
    want.setdefault("size", size_of(name))
    began, before, first, why, said = time.monotonic(), look(), None, "not looked at yet", []
    while time.monotonic() - began < seconds:
        time.sleep(1.0)
        now = look()
        why = not_playing(before, now, **want)
        if not why and first is None:
            first = time.monotonic() - began
        said.append("%.0fs %s" % (time.monotonic() - began, why or "plays"))
        before = now
    st = ndi()
    p, e = st.get("playing"), st.get("ended")
    if not why:
        print("PLAYS %s: %r, by mpv's own word from %.0f seconds on and still after %d: %s"
              % (what, name, first, seconds, json.dumps(before, sort_keys=True)))
        return "plays"
    page = "the page says playing=%s ended=%s" % (json.dumps(p, sort_keys=True), json.dumps(e, sort_keys=True))
    if not p and isinstance(e, dict) and e.get("message"):
        print("ENDED WITH A REASON %s: %r does not play and the page says why: \"%s\"" % (what, name, e["message"]))
        return "ended"
    kind = "SILENT LOSS" if not p and not e else "STUCK"
    history = "; ".join(said)
    raise Failed("%s, %s: after %d seconds %s. %s. %s. Second by second: %s. mpv's last word: %s"
                 % (kind, what, seconds, why,
                    "It did play, from second %.0f, and then stopped" % first if first is not None else "It never played in that time",
                    page, history, json.dumps(before, sort_keys=True)))


def on_the_screen(name, sid, container, least):
    """The helper says "playing" and counts frames written to the pipe; the count grows; the panel's status names
    the source; the player has the pipe loaded, is not idle, decodes the stand-in's size and its clock moves."""
    def playing():
        st = ndi()
        ended(st, name)
        p = st.get("playing")
        return st if isinstance(p, dict) and p.get("id") == sid and p.get("state") == "playing" and shown(st) >= least else None
    first = wait(playing, 25)
    if first is None:
        st = ndi()
        raise Failed("%r did not reach \"playing\" with %d pictures shown in 25 seconds; the panel says playing=%s ended=%s helper=%s"
                     % (name, least, json.dumps(st.get("playing")), json.dumps(st.get("ended")), st.get("helper")))
    before = player("playback-time", "estimated-frame-number")
    time.sleep(2.0)
    then = ndi()
    ended(then, name)
    p = then.get("playing") or {}
    if p.get("state") != "playing" or shown(then) - shown(first) < FRAMES_A_SECOND:
        raise Failed("%r did not go on playing: two seconds later the state is %r and %d more pictures were shown (about %d expected)"
                     % (name, p.get("state"), shown(then) - shown(first), 2 * FRAMES_A_SECOND))
    if (p.get("width"), p.get("height"), p.get("container")) != (WIDTH, HEIGHT, container):
        raise Failed("%r plays as %s x %s in %r; expected %d x %d in %r" % (name, p.get("width"), p.get("height"), p.get("container"),
                                                                           WIDTH, HEIGHT, container))
    if container == "matroska":
        a = p.get("audio") or {}
        if a.get("rate") != 48000 or written(then) - written(first) < FRAMES_A_SECOND or a.get("problem"):
            raise Failed("%r: the sound did not go on into the pipe: %s" % (name, json.dumps(a)))
    code, st = http("GET", "/api/status", timeout=20)
    pl = st.get("player") if isinstance(st, dict) else None
    if code != 200 or not isinstance(pl, dict) or pl.get("ndi") != name or pl.get("ndi_id") != sid:
        raise Failed("GET /api/status does not say that %r is on the screen: HTTP %s, player=%s" % (name, code, json.dumps(pl)))
    m = player("path", "idle-active", "video-params", "playback-time", "estimated-frame-number", "audio-params", "audio-codec-name", "current-vo")
    if m["path"] != PIPE:
        raise Failed("the player has not got the pipe loaded: its path is %r, the pipe is %s" % (m["path"], PIPE))
    if m["idle-active"] is not False:
        raise Failed("the player is idle (idle-active=%r) while the helper says %r plays" % (m["idle-active"], name))
    vp = m["video-params"] if isinstance(m["video-params"], dict) else {}
    if (vp.get("w"), vp.get("h")) != (WIDTH, HEIGHT) or "uyvy" not in str(vp.get("pixelformat")):
        raise Failed("the player does not decode the stand-in's picture: video-params=%s" % json.dumps(m["video-params"]))
    moved = [isinstance(before[k], (int, float)) and isinstance(m[k], (int, float)) and m[k] > before[k]
             for k in ("playback-time", "estimated-frame-number")]
    if not any(moved):
        raise Failed("the player's clock and frame count stood still over two seconds: before %s, after %s"
                     % (json.dumps(before), json.dumps({k: m[k] for k in before})))
    if container == "matroska":
        ap = m["audio-params"] if isinstance(m["audio-params"], dict) else {}
        if ap.get("samplerate") != 48000 or m["audio-codec-name"] != "pcm_f32le":
            raise Failed("the player does not decode the stand-in's sound: audio-params=%s audio-codec-name=%r"
                         % (json.dumps(m["audio-params"]), m["audio-codec-name"]))
    a = look()
    time.sleep(1.0)
    why = not_playing(a, look(), audio=container == "matroska")
    if why:
        raise Failed("%r: the helper says playing and %s" % (name, why))
    print("OK %r: the helper says playing, %d pictures shown and counting (%s), GET /api/status names it, and the player (vo %s) has %s "
          "loaded at %s x %s %s, playback-time %s -> %s" % (name, shown(then), container, m["current-vo"], m["path"], vp.get("w"), vp.get("h"),
                                                             vp.get("pixelformat"), before["playback-time"], m["playback-time"]))


def play(name, container):
    sid = start(name, container)
    on_the_screen(name, sid, container, FRAMES_A_SECOND)


def back(name, seconds):
    """After the helper was restarted under a playing source: the panel brings it back by itself. Nothing is asked
    for here but what the page asks (GET /api/ndi) and the player's own word."""
    began = time.monotonic()
    sid = source(name)

    def again():
        st = ndi()
        ended(st, name)
        p = st.get("playing")
        if not (isinstance(p, dict) and p.get("id") == sid and p.get("state") == "playing" and shown(st) >= 10):
            return None
        return st if player("path")["path"] == PIPE else None
    st = wait(again, seconds, every=0.25)
    took = time.monotonic() - began
    if st is None:
        now = ndi()
        raise Failed("%r did not come back by itself within %s seconds of the helper's restart; the panel says playing=%s ended=%s helper=%s"
                     % (name, seconds, json.dumps(now.get("playing")), json.dumps(now.get("ended")), now.get("helper")))
    print("BACK %r: playing again %.1f seconds after the helper's restart was asked about, without anyone asking for it" % (name, took))
    on_the_screen(name, sid, "raw", FRAMES_A_SECOND)


def case_still(name):
    """One still picture and then only sound, with sound on: what NDI's Test Patterns sends. On the Pi the helper
    said "still" and wrote sound, and mpv had the pipe as its path with no track and no clock, for minutes."""
    start(name, "matroska")
    watch("one still picture then only sound, sound on", name, audio=True)


def case_twice(name):
    """The same source played twice in a row, sound off (bare frames). The source sends one picture and then
    nothing, so mpv's clock cannot move: it must have the pipe open with the picture's track and a clock."""
    start(name, "raw")
    time.sleep(3.0)
    start(name, "raw")
    watch("the same source played a second time", name, moving=False)


def case_follow(first, second):
    """Source A, and source B at once: both have their first picture the moment they are opened, so B's writer can
    meet the reader that A left. With sound on and sources that have sound, both are Matroska."""
    with_sound = wanted() and has_sound(second)
    start(first)
    time.sleep(2.0)
    sid = start(second)
    if watch("one source straight after another%s" % (", both with sound" if with_sound else ""), second, audio=with_sound) != "plays":
        return
    code, st = http("GET", "/api/status", timeout=20)
    pl = st.get("player") if isinstance(st, dict) else None
    if not isinstance(pl, dict) or pl.get("ndi_id") != sid:
        raise Failed("one source straight after another: mpv plays, and GET /api/status does not name %r: HTTP %s, player=%s"
                     % (second, code, json.dumps(pl)))


def case_sound(name):
    """Sound switched on and off again under a playing source: the helper ends it as "changed" and the panel loads
    it again, first with its sound (Matroska), then without (bare frames)."""
    sound("off")
    start(name, "raw")
    watch("before the switch", name, seconds=4, audio=False)
    sound("on")
    watch("sound switched on under a playing source", name, audio=True)
    sound("off")
    watch("sound switched off again under a playing source", name, audio=False)


def case_soundon(name):
    """The other direction: the source starts with its sound, sound is switched off under it, and on again."""
    sound("on")
    start(name, "matroska")
    watch("before the switch", name, seconds=4, audio=True)
    sound("off")
    watch("sound switched off under a source that played with sound", name, audio=False)
    sound("on")
    watch("sound switched on again under it", name, audio=True)


def case_size(name):
    """A source that changes size while it plays (Scan Converter does at every clip): four seconds at one size,
    then a smaller one. The panel opens it again; mpv must then play the new size. With sound on and a source that
    has sound, both streams are Matroska (the Pi's lost re-open was that)."""
    with_sound = wanted() and has_sound(name)
    start(name)
    watch("a source that changed size%s" % (", with sound" if with_sound else ""), name, seconds=15, size=size_of(name, True), audio=with_sound)


def case_stuck(name):
    """What was done on the Pi: one still picture and then sound with sound on (which leaves mpv stuck opening the
    pipe: hand-over 1), and from THAT state, nothing cleaned: sound off and the same source played again, then
    sound switched on under it."""
    sound("on")
    start(name, "matroska")
    time.sleep(6.0)
    print("STATE before the hand-over (not judged here; hand-over 1 judges it): mpv says %s" % json.dumps(look(), sort_keys=True))
    sound("off")
    start(name, "raw")
    watch("the same source with sound off, straight after its load with sound that was stuck", name, moving=False, audio=False)
    sound("on")
    watch("sound switched on under it after that", name, audio=True)


CHAIN = ("PVJ-STANDIN (big moving with sound)", "PVJ-STANDIN (big at once A with sound)", "PVJ-STANDIN (big at once B with sound)",
         "PVJ-STANDIN (big changes size with sound)", "PVJ-STANDIN (one still then sound)", "PVJ-STANDIN (big moving with sound)")


def case_chain():
    """Several hand-overs back to back with nothing cleaned between them, sound on throughout except where it is
    switched: what one leaves is what the next one meets. Every link is judged; a link that fails does not stop the
    chain, and the REASON at the end names each one that failed."""
    failed = []

    def link(what, name, **want):
        try:
            watch("chain: " + what, name, **want)
        except Failed as e:
            print("CHAIN LINK FAILED: %s" % e)
            failed.append(str(e).split(",", 1)[0] + " (" + what + ")")
        sys.stdout.flush()

    def play(name):
        try:
            start(name)
            return True
        except Failed as e:
            print("CHAIN LINK FAILED: %s" % e)
            failed.append("could not be started (%s)" % name)
            return False
    sound("on")
    moving, a, b, changes, still, again = CHAIN
    if play(moving):
        link("the first source, with sound", moving, audio=True)
    if play(a):
        time.sleep(2.0)
    if play(b):
        link("B at once after A, both with sound", b, audio=True)
    sound("off")
    link("sound switched off under B", b, audio=False)
    sound("on")
    link("sound switched on again under B", b, audio=True)
    if play(changes):
        link("a source with sound that changes size", changes, seconds=15, size=size_of(changes, True), audio=True)
    if play(still):
        link("one still picture then only sound", still, audio=True)
    if play(again):
        link("a moving source with sound, after the still one", again, audio=True)
    if failed:
        raise Failed("the chain: %d of its links failed: %s" % (len(failed), "; ".join(failed)))


def case_restart(name):
    """Asked right after the helper was restarted under a playing source: the panel brings the source back."""
    watch("the helper restarted under a playing source", name)


CASES = {"still": (case_still, 1), "twice": (case_twice, 1), "follow": (case_follow, 2), "sound": (case_sound, 1),
         "soundon": (case_soundon, 1), "size": (case_size, 1), "restart": (case_restart, 1), "stuck": (case_stuck, 1), "chain": (case_chain, 0)}


def sound(word):
    want = word == "on"
    code, out = http("POST", "/api/ndi", {"action": "sound", "on": want})
    if code != 200 or not isinstance(out, dict) or out.get("sound") is not want:
        raise Failed("POST /api/ndi (sound %s) answered HTTP %s: %s" % (word, code, str(out)[:300]))
    print("sound is %s" % word)


def main(argv):
    try:
        if argv[:1] == ["play"] and len(argv) == 3 and argv[2] in ("raw", "matroska"):
            play(argv[1], argv[2])
        elif argv[:1] == ["back"] and len(argv) == 3:
            back(argv[1], float(argv[2]))
        elif argv[:1] == ["case"] and len(argv) >= 2 and argv[1] in CASES and len(argv) == 2 + CASES[argv[1]][1]:
            CASES[argv[1]][0](*argv[2:])
        elif argv[:1] == ["sound"] and len(argv) == 2 and argv[1] in ("on", "off"):
            sound(argv[1])
        elif argv == ["dump"]:
            dump()
        else:
            print(__doc__)
            return 2
    except Failed as e:
        print("REASON: %s" % e)
        dump()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
