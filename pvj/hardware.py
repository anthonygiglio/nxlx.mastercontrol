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
    (a Pi below the floor, an ARM board the node cannot name). pvj/profile.py reads it for every value; renderer.gles2_only
    also treats the Pi 1 class (pi-other) as lean for one purpose: the same OpenGL ES 2 chip."""
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


def connector_kind(name):
    """"hdmi", "composite", "dsi", "dp", "vga", "dvi", "lvds" or "other" for a connector's kernel name (HDMI-A-1, Composite-1, DSI-1,
    DP-2, eDP-1 ...)."""
    n = str(name).lower()
    for prefix, kind in (("hdmi", "hdmi"), ("composite", "composite"), ("dsi", "dsi"), ("dp", "dp"), ("edp", "dp"), ("vga", "vga"),
                         ("dvi", "dvi"), ("lvds", "lvds")):
        if n.startswith(prefix):
            return kind
    return "other"


def output_label(name):
    """The name a person reads for a connector: "HDMI 1", "AV (composite)", "DSI 1", "DisplayPort 2"."""
    kind = connector_kind(name)
    num = re.search(r"-([0-9]+)$", str(name))
    n = num.group(1) if num else ""
    return {"hdmi": "HDMI " + n, "composite": "AV (composite)" + (" " + n if n not in ("", "1") else ""), "dsi": "DSI " + n,
            "dp": ("eDP " if str(name).lower().startswith("edp") else "DisplayPort ") + n}.get(kind, str(name)).strip()


def outputs(root="/"):
    """Every display connector the kernel lists for this board, a writeback connector left out (it is not a screen)."""
    return [c for c in drm_connectors(root) if not c["connector"].lower().startswith("writeback")]


def av_capable(kind):
    """True for a Raspberry Pi: the AV (composite) output exists on the board (a Pi 5's is a pad to solder to; a Pi Zero's, a header)."""
    return kind in ("pi3", "pi4", "pi5", "pi-other")


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


# --- the sound device of a screen on a PC (J-155, D137) ----------------------------------------------------------------
# On a PC the ALSA default is the analog jack, so mpv's own "auto" never reaches the screen's HDMI sound (measured on an
# Intel M710q: a tone on auto never reached the HDMI capture card; on plughw:CARD=PCH,DEV=3 it did). The HDA HDMI codec
# lists one ELD file a pin, /proc/asound/cardN/eld#CODEC.PIN, with the monitor's name and whether one is present; its
# PCM devices are named "HDMI 0", "HDMI 1" ... in pin order (devices 3, 7, 8, 9 ... on Intel). The DRM connector's EDID
# carries the same monitor name, which is how a pin is matched to a connector, not by guessing from port letters.
_ELD_FILE = re.compile(r"eld#([0-9]{1,3})\.([0-9]{1,3})(?:\.([0-9]{1,3}))?")
_HDA_HDMI_DEVICES = (3, 7, 8, 9, 10, 11)       # the HDA driver's fixed HDMI PCM numbers, used when pcmNp/info is missing
_EDID_HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"


def edid_monitor_name(connector, root="/"):
    """The monitor name in a DRM connector's EDID (its 0xFC descriptor), or None (no screen, no name, unreadable)."""
    want = re.compile(r"card[0-9]{1,3}-" + re.escape(str(connector)))
    for path in sorted(glob.glob(os.path.join(root, "sys/class/drm/card*-*"))):
        if not want.fullmatch(os.path.basename(path)):
            continue
        try:
            with open(os.path.join(path, "edid"), "rb") as f:
                data = f.read(256)
        except OSError:
            continue
        if len(data) < 128 or data[:8] != _EDID_HEADER:
            continue
        for off in (54, 72, 90, 108):
            d = data[off:off + 18]
            if d[:3] == b"\0\0\0" and d[3] == 0xFC:
                name = d[5:18].split(b"\n")[0].decode("ascii", "replace").strip()
                return name or None
    return None


def _eld_fields(text):
    fields = {}
    for line in (text or "").splitlines():
        parts = line.split(None, 1)
        if parts:
            fields[parts[0]] = parts[1].strip() if len(parts) > 1 else ""
    return fields


def hdmi_sound_pins(root="/"):
    """The HDMI and DisplayPort sound pins of the HDA sound cards, from their ELD files: [{"card", "card_id", "pin",
    "hdmi_index", "device", "present", "name", "type"}]. `device` is the ALSA PCM device of the pin (None when it
    cannot be told) and `hdmi_index` the N of ALSA's hdmi:CARD=X,DEV=N. A DP MST stream (eld#C.P.D with D above 0) is
    left out: which PCM it gets is not fixed."""
    out = []
    for card_dir in sorted(glob.glob(os.path.join(root, "proc/asound/card[0-9]*"))):
        m = re.fullmatch(r"card([0-9]{1,3})", os.path.basename(card_dir))
        card_id = _read("/", os.path.join(card_dir, "id"))
        if not m or not card_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", card_id):
            continue
        named = {}
        for info in glob.glob(os.path.join(card_dir, "pcm*p", "info")):
            dm = re.fullmatch(r"pcm([0-9]{1,3})p", os.path.basename(os.path.dirname(info)))
            nm = re.search(r"(?m)^name: HDMI ([0-9]{1,2})\s*$", _read("/", info) or "")
            if dm and nm:
                named[int(nm.group(1))] = int(dm.group(1))
        elds = []
        for path in glob.glob(os.path.join(card_dir, "eld#*")):
            em = _ELD_FILE.fullmatch(os.path.basename(path))
            if em and int(em.group(3) or 0) == 0:
                elds.append((int(em.group(1)), int(em.group(2)), path))
        for codec, pin, path in sorted(elds):
            f = _eld_fields(_read("/", path))
            if named:
                device = named.get(pin)
            else:
                device = _HDA_HDMI_DEVICES[pin] if pin < len(_HDA_HDMI_DEVICES) else None
            out.append({"card": int(m.group(1)), "card_id": card_id, "pin": pin, "hdmi_index": pin, "device": device,
                        "present": f.get("monitor_present") == "1" and f.get("eld_valid", "1") == "1",
                        "name": f.get("monitor_name") or None, "type": f.get("connection_type") or None})
    return out


def hdmi_sound_for(connector, connectors, root="/"):
    """The sound pin (an item of hdmi_sound_pins, with "how") of the screen on DRM connector `connector`, or None when
    it cannot be told. The monitor name in the connector's EDID is matched with the pins' ELD; without a name, or with
    no single pin of that name, the only connected HDMI or DisplayPort screen is matched with the only pin that has a
    monitor present. Two screens of one model and no other clue give None: a guess could send the sound to the wrong
    screen. `connectors` is drm_connectors()."""
    kind = connector_kind(connector)
    if kind not in ("hdmi", "dp"):
        return None
    want_type = "HDMI" if kind == "hdmi" else "DisplayPort"
    pins = [p for p in hdmi_sound_pins(root) if p["present"] and p["device"] is not None
            and p["type"] in (None, "HDMI", "DisplayPort") and p["type"] in (None, want_type)]
    if not pins:
        return None
    name = edid_monitor_name(connector, root)
    if name:
        same = [p for p in pins if p["name"] == name]
        if len(same) == 1:
            return dict(same[0], how="monitor name")
    screens = [c for c in connectors if c.get("status") == "connected" and connector_kind(c.get("connector")) in ("hdmi", "dp")]
    if len(screens) == 1 and screens[0].get("connector") == connector and len(pins) == 1 \
            and not (name and pins[0]["name"] and pins[0]["name"] != name):      # two names that differ are two screens
        return dict(pins[0], how="the only screen")
    return None


def report(root="/"):
    board = detect_board(root)
    return {
        "board": board,
        "os": os_release(root),
        "temperatures": temperatures(root),
        "displays": drm_connectors(root),
        "profile": playback_profile(board, has_desktop()),
    }
