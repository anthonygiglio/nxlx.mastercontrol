# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Hardware and OS detection, read from /proc and /sys only.

Every function takes an optional `root` so tests can point it at a fake tree.
Nothing here calls vcgencmd, tvservice or other Raspberry Pi specific tools.
"""

import glob
import os
import platform
import re


def _read(root, path):
    try:
        with open(os.path.join(root, path.lstrip("/")), "rb") as f:
            return f.read().replace(b"\0", b"").decode("utf-8", "replace").strip()
    except OSError:
        return None


# Raspberry Pi device-tree model names by the silicon under them. The exact model stays in detect_board()["model"].
_PI_NUMBERED = {"2": "pi3", "3": "pi3", "4": "pi4", "400": "pi4", "5": "pi5", "500": "pi5"}   # Pi 2 is Pi 3 class
_PI_COMPUTE = {"3": "pi3", "4": "pi4", "5": "pi5"}       # Compute Module 3, 4 (and 4S), 5


def pi_kind(model):
    """The kind for a Raspberry Pi device-tree model string, by its silicon: Pi 400 and Compute Module 4 are Pi 4,
    Pi 500 and Compute Module 5 are Pi 5, Pi 2, Pi 3 (A+, B+) and Zero 2 W are Pi 3 class. A Pi 1, a Zero (not 2), a
    Compute Module 1 and anything unnamed are below the supported floor: pi-other."""
    if "Raspberry Pi" not in model:
        return None
    if re.search(r"Raspberry Pi Zero 2\b", model):
        return "pi3"
    m = re.search(r"Raspberry Pi Compute Module (\d+)", model)
    if m:
        return _PI_COMPUTE.get(m.group(1), "pi-other")
    m = re.search(r"Raspberry Pi (\d+)", model)
    if m:
        return _PI_NUMBERED.get(m.group(1), "pi-other")
    return "pi-other"


def tier(kind):
    """How much a board is asked to do: "lean" (Pi 3 class), "standard" (Pi 4 class), "full" (Pi 5, x86) or "unknown"
    (a Pi below the floor, an ARM board the node cannot name). Only renderer.gles2_only reads it for a limit, and it
    treats the Pi 1 class (pi-other) as lean for that one purpose: the same OpenGL ES 2 chip."""
    return {"pi3": "lean", "pi4": "standard", "pi5": "full", "x86": "full"}.get(kind, "unknown")


def detect_board(root="/"):
    """Return {'kind', 'model', 'arch'}.

    kind is one of pi3, pi4, pi5, pi-other, x86, arm-other (pi_kind says which Pi model is which).
    """
    arch = platform.machine() if root == "/" else "unknown"
    model = _read(root, "/proc/device-tree/model") or _read(root, "/sys/firmware/devicetree/base/model")
    kind = pi_kind(model) if model else None
    if kind is None:
        if arch in ("x86_64", "i386", "i686", "AMD64"):
            kind = "x86"
        elif any(v in (_read(root, "/proc/cpuinfo") or "") for v in ("GenuineIntel", "AuthenticAMD")):
            kind = "x86"
        else:
            kind = "arm-other"
    if model is None:
        cpu = _read(root, "/proc/cpuinfo") or ""
        m = re.search(r"model name\s*:\s*(.+)", cpu)
        model = m.group(1).strip() if m else "unknown"
    return {"kind": kind, "model": model, "arch": arch}


def os_release(root="/"):
    text = _read(root, "/etc/os-release") or ""
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v.strip().strip('"')
    return {"name": out.get("PRETTY_NAME", "unknown"), "id": out.get("ID", ""),
            "version_codename": out.get("VERSION_CODENAME", "")}


def temperatures(root="/"):
    """Thermal zones in degrees C, from /sys/class/thermal (works on Pi and x86)."""
    result = []
    pattern = os.path.join(root, "sys/class/thermal/thermal_zone*")
    for zone in sorted(glob.glob(pattern)):
        raw = _read("/", os.path.join(zone, "temp"))
        if raw is None:
            continue
        try:
            celsius = int(raw) / 1000.0
        except ValueError:
            continue
        result.append({"zone": os.path.basename(zone),
                       "type": _read("/", os.path.join(zone, "type")) or "",
                       "celsius": round(celsius, 1)})
    return result


def drm_connectors(root="/"):
    """Display connectors from /sys/class/drm with connection state, modes and card: `cardN-NAME` is the output NAME
    of /dev/dri/cardN, so `card` is N (on a Pi 4 the outputs are card1's; card0 is v3d, which has none)."""
    result = []
    pattern = os.path.join(root, "sys/class/drm/card*-*")
    for path in sorted(glob.glob(pattern)):
        head, name = os.path.basename(path).split("-", 1)
        status = _read("/", os.path.join(path, "status")) or "unknown"
        modes = (_read("/", os.path.join(path, "modes")) or "").split()
        m = re.fullmatch(r"card([0-9]{1,3})", head)
        result.append({"connector": name, "status": status, "modes": modes, "card": int(m.group(1)) if m else None})
    return result


def playback_profile(board, has_desktop):
    """Suggested mpv settings for this board. Values are starting points that
    pvj-selftest verifies on the real device."""
    kind = board["kind"]
    args = ["--hwdec=auto-safe"]
    notes = []
    if not has_desktop:
        args += ["--vo=gpu", "--gpu-context=drm"]
    if kind in ("pi3", "pi4"):
        # mpv's own cheap-rendering profile (bilinear scaling, no dither, no peak detection). Measured on a Pi 4 at
        # 2560x1440: playing 1080p H.264 dropped 15 frames a second with the default scalers and none with these.
        args.append("--profile=fast")
        notes.append("Cheap scaling is on (--profile=fast); the default scalers drop frames on a Pi at 1440p.")
    if kind == "pi5":
        notes.append("Pi 5 has no hardware H.264 decode; use HEVC files or expect software decode.")
    if kind == "pi3":
        notes.append("Pi 3 class is the weakest supported board; check heavy files with pvj-selftest first.")
    return {"mpv_args": args, "notes": notes}


def has_desktop(env=None):
    env = os.environ if env is None else env
    return bool(env.get("WAYLAND_DISPLAY") or env.get("DISPLAY"))


def report(root="/"):
    board = detect_board(root)
    return {
        "board": board,
        "os": os_release(root),
        "temperatures": temperatures(root),
        "displays": drm_connectors(root),
        "profile": playback_profile(board, has_desktop()),
    }
