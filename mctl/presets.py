# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Replaces the ~290 near-identical legacy start scripts with one parser.

`startlessonce05`, `startmaster12`, `startseamless03` and friends differ only
in four things: loop or once, audio output, which folder, and which file
(`NN*`). `parse_legacy_name()` recovers those from the script name so one
implementation serves all of them. The table below was checked against every
script in sync/ by tests/test_presets.py.
"""

import os
import re

from .player import PlayerError

DEFAULT_MEDIA_DIR = "/media/internal/video"
DEFAULT_USB_DIR = "/media/usb"
SCAN_LIMIT = 2000

# family -> (loop, audio, uses_usb, sync_master)
FAMILIES = {
    "less": (True, "local", False, False),          # omxplayer --loop -o local
    "seamless": (True, "both", False, False),       # omxplayer --loop -o both
    "lessonce": (False, "local", False, False),     # omxplayer -o local
    "lesseronce": (False, "both", False, False),    # omxplayer -o both
    "master": (True, None, False, True),            # omxplayer-sync -mu
    "masterone": (False, None, False, True),        # omxplayer-sync -m
    "masterusb": (True, None, True, True),          # omxplayer-sync -mu on /media/usb
}

_NAME = re.compile(r"^start(?P<family>masterone|masterusb|master|lesseronce|lessonce|less|seamless)(?P<index>[0-9]{1,3})?$")


def parse_legacy_name(name):
    """Return the preset for a legacy script name, or raise PlayerError."""
    m = _NAME.fullmatch(os.path.basename(name))  # fullmatch: a trailing newline must not pass
    if not m:
        raise PlayerError("unknown preset %r (slave, stream and wifi presets are not ported yet)" % name)
    loop, audio, usb, sync_master = FAMILIES[m.group("family")]
    return {"family": m.group("family"), "loop": loop, "audio": audio, "usb": usb,
            "sync_master": sync_master, "index": m.group("index")}


def resolve_files(preset, media_dir=None, usb_dir=None):
    """Files a preset plays: everything in the folder, or those starting with the index."""
    if preset["usb"]:
        root = usb_dir or os.environ.get("PVJ_USB_DIR", DEFAULT_USB_DIR)
    else:
        root = media_dir or os.environ.get("PVJ_MEDIA_DIR", DEFAULT_MEDIA_DIR)
    try:
        with os.scandir(root) as it:            # bounded: a USB drive can hold any number of entries
            names = sorted(e.name for _, e in zip(range(SCAN_LIMIT), it) if not e.name.startswith("."))
    except OSError:
        raise PlayerError("media folder %s is not readable" % root)
    if preset["index"] is not None:
        names = [n for n in names if n.startswith(preset["index"])]
    files = [os.path.join(root, n) for n in names if os.path.isfile(os.path.join(root, n))]
    if not files:
        raise PlayerError("no files in %s%s" % (root, (" starting with " + preset["index"]) if preset["index"] else ""))
    return files
