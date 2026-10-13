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
                                                                    | stuck NAME | chain | gostill NAME
                                                                    | soundfrom PICTURE SOUND SECONDS
                                                                    | soundfromsize PICTURE SOUND
                                                                    | soundfromstops PICTURE SOUND
                                                                    | soundonlyplay SOUND | soundonlyunder PICTURE SOUND
                                                               dump

Exit 0: everything asked held. Exit 1: something did not; the reason is the line that begins "REASON:", and what
the panel and the player said last is printed under it.

How "it plays" is judged, and the hand-overs (`case`), are in mpvword.py beside this file, shared with
tests/test_ndi_mpv.py; this file is the road to a box: the panel over HTTP, the player over its socket."""
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mpvword  # noqa: E402
from mpvword import CASES, FRAMES_A_SECOND, HEIGHT, WIDTH, Failed, wait  # noqa: E402

BASE = os.environ.get("PVJ_PANEL", "http://127.0.0.1")
PLAYER = os.environ.get("PVJ_PLAYER_SOCKET", "/run/pvj/player/player.sock")
PIPE = os.environ.get("PVJ_NDI_PIPE", "/run/pvj-ndi-pipe/ndi.fifo")
ASKED = ("path", "idle-active", "core-idle", "pause", "eof-reached", "playback-time", "time-pos", "audio-pts", "track-list/count",
         "track-list", "video-params", "audio-params", "vo-configured", "current-vo", "current-ao", "video-codec", "audio-codec-name",
         "estimated-frame-number", "frame-drop-count", "demuxer-cache-state/underrun", "paused-for-cache", "seeking", "mpv-version")


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
    return mpvword.ask(PLAYER, *names)


class HttpRig(mpvword.Rig):
    """The installed box: the panel over HTTP as a paired device, the player's socket as root."""
    socket, pipe = PLAYER, PIPE

    def page(self):
        return ndi()

    def post_play(self, sid):
        return http("POST", "/api/play", {"ndi": sid}, timeout=40)

    def post_sound(self, on):
        return http("POST", "/api/ndi", {"action": "sound", "on": on})

    def post_ndi(self, body):
        return http("POST", "/api/ndi", body)

    def player_status(self):
        code, st = http("GET", "/api/status", timeout=20)
        return code, (st.get("player") if isinstance(st, dict) else None)


RIG = HttpRig()
look, start, source, sound = RIG.look, RIG.start, RIG.source, RIG.sound


def not_playing(a, b, **want):
    return mpvword.not_playing(a, b, PIPE, **want)


def dump():
    print("--- GET /api/ndi")
    print(json.dumps(ndi(), indent=1, sort_keys=True))
    code, st = http("GET", "/api/status", timeout=20)
    print("--- GET /api/status: HTTP %s, its player part" % code)
    print(json.dumps(st.get("player") if isinstance(st, dict) else st, indent=1, sort_keys=True))
    print("--- the player (mpv) over its socket")
    print(json.dumps(player(*ASKED), indent=1, sort_keys=True))
    sys.stdout.flush()


def ended(st, name):
    """Raise if the panel says the source ended for good; the words are the helper's. "...; trying again" is not an
    end: the panel's watch is bringing the source back, and whoever asks waits for that up to a time of its own."""
    p, e = st.get("playing"), st.get("ended")
    if mpvword.trying(st):
        return
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


def on_the_screen(name, sid, container, least):
    """The helper says "playing" and counts frames written to the pipe; the count grows; the panel's status names
    the source; the player has the pipe loaded, is not idle, decodes the stand-in's size and its clock moves.
    The counts are the helper's for ONE opening of the source and start again at 0 when the panel opens it again
    (after the helper was restarted, after a "changed"): two counts are compared only within one opening."""
    def playing():
        st = ndi()
        ended(st, name)
        p = st.get("playing")
        return st if isinstance(p, dict) and p.get("id") == sid and p.get("state") == "playing" and shown(st) >= least else None
    for _ in range(4):
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
        if p and shown(then) >= shown(first):
            break
        print("the source was opened again between two looks (its count went from %d to %s); looking again" % (shown(first), shown(then) if p else "none"))
    else:
        raise Failed("%r was opened again and again: four times in a row its count of pictures started again within two seconds" % name)
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
    """After the helper was restarted under a playing source: the panel's watch brings it back by itself, within
    `seconds` (the stated deadline). Nothing is asked for here but what the page asks (GET /api/ndi) and the
    player's own word. Meanwhile the page may say "...; trying again", which is waited through; and the first
    answers may still be the helper's from before its restart (the panel keeps an answer for a second), so "back"
    is only believed after the source was seen NOT playing, or its count of pictures was seen to start again."""
    began = time.monotonic()
    sid = source(name)
    seen = {"gone": False, "count": None, "said": []}

    def again():
        st = ndi()
        ended(st, name)
        p = st.get("playing")
        on = isinstance(p, dict) and p.get("id") == sid and p.get("state") == "playing"
        note = "%.1fs %s" % (time.monotonic() - began, ("playing, %d shown" % shown(st)) if on else
                             ((st.get("ended") or {}).get("message") or (p or {}).get("state") or "nothing playing"))
        if not seen["said"] or seen["said"][-1].split(" ", 1)[1].split(",")[0] != note.split(" ", 1)[1].split(",")[0]:
            seen["said"].append(note)
        if not on or (seen["count"] is not None and shown(st) < seen["count"]):
            seen["gone"] = True
        seen["count"] = shown(st) if on else None
        if not (seen["gone"] and on and shown(st) >= 10):
            return None
        return st if player("path")["path"] == PIPE else None
    st = wait(again, seconds, every=0.25)
    took = time.monotonic() - began
    if st is None:
        now = ndi()
        raise Failed("%r did not come back by itself within %s seconds of the helper's restart (%s); the panel says playing=%s ended=%s helper=%s. What the page said, in order: %s"
                     % (name, seconds, "it was seen gone" if seen["gone"] else "it was never seen gone: the page said playing all along, with a count that never started again",
                        json.dumps(now.get("playing")), json.dumps(now.get("ended")), now.get("helper"), "; ".join(seen["said"])))
    print("BACK %r: playing again, at least ten pictures into a new opening, %.1f seconds after the helper's restart, without anyone asking for it. "
          "What the page said, in order: %s" % (name, took, "; ".join(seen["said"])))
    on_the_screen(name, sid, "raw", FRAMES_A_SECOND)


def main(argv):
    try:
        if argv[:1] == ["play"] and len(argv) == 3 and argv[2] in ("raw", "matroska"):
            play(argv[1], argv[2])
        elif argv[:1] == ["back"] and len(argv) == 3:
            back(argv[1], float(argv[2]))
        elif argv[:1] == ["case"] and len(argv) >= 2 and argv[1] in CASES and len(argv) == 2 + CASES[argv[1]][1]:
            CASES[argv[1]][0](RIG, *argv[2:])
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
