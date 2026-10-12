#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The questions tests/real_install_test.sh asks while a source of the stand-in for NDI's library
(tests/ndi_standin/standin.c) plays on a really installed box: asked of the panel over its own API, as a paired
device asks, and of the player over its control socket, as root. Nothing here reaches into the helper.

    PVJ_TOKEN=<a paired device's token> python3 -B box_check.py play NAME raw|matroska
                                                               back NAME SECONDS
                                                               still NAME
                                                               sound on|off
                                                               dump

Exit 0: everything asked held. Exit 1: something did not; the reason is the line that begins "REASON:", and what
the panel and the player said last is printed under it. `still` judges only what must hold and REPORTS the rest
(see its text): what the player does with one still picture and then only sound was an open question."""
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
REPLACED = "the input pipe was replaced"        # what every source ended with on a box until 2026-10-12
ASKED = ("path", "idle-active", "core-idle", "pause", "eof-reached", "playback-time", "video-params", "audio-params",
         "vo-configured", "current-vo", "current-ao", "video-codec", "audio-codec-name", "estimated-frame-number",
         "frame-drop-count", "demuxer-cache-state/underrun", "paused-for-cache", "mpv-version")


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


def start(name, container):
    sid = source(name)
    code, out = http("POST", "/api/play", {"ndi": sid}, timeout=40)
    if code != 200 or not isinstance(out, dict):
        raise Failed("POST /api/play for %r answered HTTP %s: %s" % (name, code, out))
    print("POST /api/play: %s" % json.dumps(out, sort_keys=True))
    if (out.get("playing"), out.get("width"), out.get("height")) != ("ndi", WIDTH, HEIGHT):
        raise Failed("POST /api/play for %r did not answer with the stand-in's picture: %s" % (name, out))
    if out.get("sound") is not (container == "matroska"):
        raise Failed("POST /api/play for %r says sound %s; the pipe was to carry %s" % (name, out.get("sound"), container))
    return sid


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


def still(name):
    """One still picture and then only sound, as NDI's Test Patterns sends. What must hold: the helper writes it as
    Matroska and never says the pipe was replaced. What is REPORTED, not judged, because nobody had seen it: whether
    the player stays in playback with its picture track while only sound arrives. With the null output of this
    machine that is the player's state, not a picture on a screen."""
    start(name, "matroska")
    rows, last = [], {}
    for second in range(1, 11):
        time.sleep(1.0)
        st = ndi()
        p = st.get("playing") if isinstance(st.get("playing"), dict) else {}
        if REPLACED in str(p.get("message")) or REPLACED in str((st.get("ended") or {}).get("message")):
            ended(st, name)
        m = player("path", "idle-active", "core-idle", "eof-reached", "playback-time", "video-params", "vo-configured", "estimated-frame-number")
        vp = m["video-params"] if isinstance(m["video-params"], dict) else None
        last = {"second": second, "helper": p.get("state"), "message": p.get("message"), "shown": shown(st), "sound_written": written(st),
                "ended": st.get("ended"), "mpv_path": m["path"], "idle-active": m["idle-active"], "core-idle": m["core-idle"],
                "eof-reached": m["eof-reached"], "playback-time": m["playback-time"], "vo-configured": m["vo-configured"],
                "frame": m["estimated-frame-number"], "video-params": "%sx%s %s" % (vp.get("w"), vp.get("h"), vp.get("pixelformat")) if vp else m["video-params"]}
        rows.append(last)
        print("STILL %s" % json.dumps(last, sort_keys=True))
        sys.stdout.flush()
    times = [r["playback-time"] for r in rows if isinstance(r["playback-time"], (int, float))]
    kept = (last.get("helper") in ("playing", "still") and last.get("mpv_path") == PIPE and last.get("idle-active") is False
            and str(last.get("video-params")).startswith("%dx%d" % (WIDTH, HEIGHT)) and len(times) >= 2 and times[-1] > times[0] + 5)
    if kept:
        print("STILL-REPORT: KEPT. Ten seconds after one still picture and then only sound, the player is still in playback on the pipe "
              "(not idle), keeps the picture's track (video-params %s) and its clock runs on with the sound (%s -> %s); the helper says %r "
              "with %s picture shown and %s blocks of sound written. This is mpv with the null video output: it does not show that a "
              "screen keeps the picture." % (last.get("video-params"), times[0], times[-1], last.get("helper"), last.get("shown"), last.get("sound_written")))
    else:
        print("STILL-REPORT: FINDING, NOT KEPT (or not as expected). After one still picture and then only sound the last look was: %s. "
              "This step does not fail on it; it is to be read and decided on." % json.dumps(last, sort_keys=True))


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
        elif argv[:1] == ["still"] and len(argv) == 2:
            still(argv[1])
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
