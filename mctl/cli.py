# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Command line front end: `pvj-player <command> [args]`."""

import argparse
import json
import sys

from . import hardware, presets
from .player import Player, PlayerError


def build_parser():
    p = argparse.ArgumentParser(prog="pvj-player", description="Control the nxlx.mastercontrol mpv player")
    p.add_argument("--mpv-arg", action="append", default=[], help="extra argument for mpv (repeatable)")
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("play", help="play files, folders or URLs (switches clips without a black gap)")
    pl.add_argument("paths", nargs="+")
    pl.add_argument("--once", action="store_true", help="do not loop")
    pl.add_argument("--audio-device", help="mpv audio device name, see: mpv --audio-device=help")
    pl.add_argument("--windowed", action="store_true")
    pl.add_argument("--no-spawn", action="store_true", help="never start mpv; require the systemd service")
    st = sub.add_parser("start", help="run a legacy preset by its old script name, e.g. startlessonce05")
    st.add_argument("preset")
    st.add_argument("--media-dir")
    st.add_argument("--usb-dir")
    st.add_argument("--no-spawn", action="store_true", help="never start mpv; require the systemd service")
    sv = sub.add_parser("serve", help="run the player in the foreground for systemd")
    sv.add_argument("--windowed", action="store_true")
    sub.add_parser("stop")
    pa = sub.add_parser("pause")
    pa.add_argument("state", nargs="?", choices=["on", "off"], help="omit to toggle")
    for name, help_ in (("seek", "seconds, negative goes back"), ("speed", "0.1 to 4"),
                        ("volume", "0 to 130"), ("opacity", "0 to 255"), ("size", "percent, 100 = fit")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("value", type=float)
    po = sub.add_parser("position", help="shift picture, thousandths of width/height")
    po.add_argument("x", type=float)
    po.add_argument("y", type=float, nargs="?", default=0.0)
    sub.add_parser("status")
    sub.add_parser("info", help="print hardware report as JSON")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "info":
            print(json.dumps(hardware.report(), indent=2))
            return 0
        player = Player(extra_args=args.mpv_arg)
        c = args.cmd
        if c == "play":
            extra = hardware.playback_profile(hardware.detect_board(), hardware.has_desktop())["mpv_args"]
            player.extra_args = extra + player.extra_args
            player.play(args.paths, loop=not args.once, audio_device=args.audio_device, windowed=args.windowed,
                        spawn=not args.no_spawn)
        elif c == "serve":
            extra = hardware.playback_profile(hardware.detect_board(), hardware.has_desktop())["mpv_args"]
            player.extra_args = extra + player.extra_args
            player.serve(windowed=args.windowed)
        elif c == "start":
            preset = presets.parse_legacy_name(args.preset)
            files = presets.resolve_files(preset, args.media_dir, args.usb_dir)
            if preset["sync_master"]:
                print("pvj-player: network sync is not ported yet; playing locally only", file=sys.stderr)
            extra = hardware.playback_profile(hardware.detect_board(), hardware.has_desktop())["mpv_args"]
            player.extra_args = extra + player.extra_args
            player.play(files, loop=preset["loop"], spawn=not args.no_spawn)
        elif c == "stop":
            player.stop()
        elif c == "pause":
            player.pause({"on": True, "off": False, None: None}[args.state])
        elif c == "position":
            player.position(args.x, args.y)
        elif c == "status":
            print(json.dumps(player.status()))
        else:
            getattr(player, c)(args.value)
    except PlayerError as e:
        print("pvj-player: %s" % e, file=sys.stderr)
        return 1
    return 0
