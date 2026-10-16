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


def detect_board(root="/"):
    """Return {'kind', 'model', 'arch'}.

    kind is one of pi3, pi4, pi5, pi-other, x86, arm-other.
    """
    arch = platform.machine() if root == "/" else "unknown"
    model = _read(root, "/proc/device-tree/model") or _read(root, "/sys/firmware/devicetree/base/model")
    kind = None
    if model:
        m = re.search(r"Raspberry Pi (\d+)", model)
        if m:
            n = m.group(1)
            kind = "pi" + n if n in ("3", "4", "5") else "pi-other"
        elif "Raspberry Pi" in model:
            kind = "pi-other"
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
        notes.append("Pi 3 is the weakest supported board; check heavy files with pvj-selftest first.")
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
