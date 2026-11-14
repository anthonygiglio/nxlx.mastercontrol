# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Hardware and OS detection, read from /proc and /sys only (and amixer, for which HDMI sound PCMs have a screen).

Every function takes an optional `root` so tests can point it at a fake tree.
Nothing here calls vcgencmd, tvservice or other Raspberry Pi specific tools.
"""

import glob
import os
import platform
import re
import shutil
import subprocess


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
# On a PC the ALSA default is the analog jack, so mpv's own "auto" never reaches the screen's HDMI sound. What was
# measured on an Intel M710q (Kaby Lake HD 630, three screens on HDMI-A-1, -2, -3, 2026-11-14), each PCM playing a tone
# into capture cards: PCM 3 reached HDMI-A-1 and PCM 8 reached HDMI-A-2 (PCM 7, presumably HDMI-A-3). The HDA driver
# gives PCMs to pins as screens come, so neither pin order nor port order says which PCM is which screen. The ELD of
# each PCM (its maker, product and monitor name, from the screen's EDID) also contradicted the EDIDs: PCM 8's ELD had
# the product of HDMI-A-3's EDID, yet its tone came out on HDMI-A-2. So only these are trusted: which PCMs have a screen
# (ALSA's "HDMI/DP,pcm=N Jack" controls); one PCM with a screen is the screen's; with several, a match only when every
# screen on both sides is told apart by name, maker and product. Read through `amixer -c N contents` (alsa-utils).
_EDID_HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"
_HDA_HDMI_DEVICES = (3, 7, 8, 9, 10, 11)       # HDA's HDMI PCM numbers, in hdmi:DEV=k order, when pcmNp/info says nothing
_AMIXER_HEAD = re.compile(r"numid=[0-9]+,iface=([A-Z]+),name='([^']*)'(?:,device=([0-9]{1,3}))?")
_JACK = re.compile(r"HDMI/DP,pcm=([0-9]{1,3}) Jack")


def _edid(connector, root="/"):
    want = re.compile(r"card[0-9]{1,3}-" + re.escape(str(connector)))
    for path in sorted(glob.glob(os.path.join(root, "sys/class/drm/card*-*"))):
        if not want.fullmatch(os.path.basename(path)):
            continue
        try:
            with open(os.path.join(path, "edid"), "rb") as f:
                data = f.read(256)
        except OSError:
            continue
        if len(data) >= 128 and data[:8] == _EDID_HEADER:
            return data
    return None


def edid_monitor_name(connector, root="/"):
    """The monitor name in a DRM connector's EDID (its 0xFC descriptor), or None (no screen, no name, unreadable)."""
    data = _edid(connector, root)
    for off in (54, 72, 90, 108):
        d = (data or b"")[off:off + 18]
        if len(d) == 18 and d[:3] == b"\0\0\0" and d[3] == 0xFC:
            return d[5:18].split(b"\n")[0].decode("ascii", "replace").strip() or None
    return None


def edid_identity(connector, root="/"):
    """(monitor name, maker, product) of a DRM connector's screen, maker and product as the ELD carries them
    (little-endian, "0x2157"), or None without an EDID."""
    data = _edid(connector, root)
    if not data:
        return None
    return (edid_monitor_name(connector, root) or "", "0x%04x" % int.from_bytes(data[8:10], "little"),
            "0x%04x" % int.from_bytes(data[10:12], "little"))


def eld_identity(eld):
    """(monitor name, maker, product) from ELD bytes (CEA-861 ELD: name length in byte 4, maker at 16, product at 18,
    the name from 20), or None for an empty ELD (no screen)."""
    if len(eld) < 20 or not any(eld):
        return None
    mnl = eld[4] & 0x1F
    name = eld[20:20 + mnl].split(b"\0")[0].decode("ascii", "replace").strip()
    return (name, "0x%04x" % int.from_bytes(eld[16:18], "little"), "0x%04x" % int.from_bytes(eld[18:20], "little"))


def parse_amixer(text):
    """{pcm device: {"jack": True | False | None, "eld": bytes | None}} from `amixer -c N contents`."""
    out = {}
    current = None
    for line in (text or "").splitlines():
        head = _AMIXER_HEAD.match(line.strip())
        if head:
            iface, name, device = head.groups()
            jack = _JACK.fullmatch(name) if iface == "CARD" else None
            if jack:
                current = (int(jack.group(1)), "jack")
            elif iface == "PCM" and name == "ELD" and device is not None:
                current = (int(device), "eld")
            else:
                current = None
            continue
        value = re.match(r"\s*:\s*values=(.*)$", line)
        if current is None or not value:
            continue
        entry = out.setdefault(current[0], {"jack": None, "eld": None})
        if current[1] == "jack":
            entry["jack"] = value.group(1).strip() == "on"
        else:
            try:
                entry["eld"] = bytes(int(v, 16) for v in value.group(1).split(",") if v.strip())
            except ValueError:
                entry["eld"] = None
        current = None
    return out


def _amixer(card, run=None):
    exe = shutil.which("amixer") if run is None else "amixer"
    if not exe:
        return None
    r = (run or subprocess.run)([exe, "-c", str(card), "contents"], capture_output=True, text=True, timeout=3)
    return r.stdout if r.returncode == 0 else None


def hdmi_sound_screens(root="/", run=None):
    """The HDMI and DisplayPort sound PCMs that have a screen, by ALSA's jack controls: [{"card", "card_id", "device",
    "hdmi_index", "identity"}], `identity` being the ELD's (name, maker, product) or None. [] when amixer is missing or
    says nothing. `run` stands in for subprocess.run (tests)."""
    found = []
    for card_dir in sorted(glob.glob(os.path.join(root, "proc/asound/card[0-9]*"))):
        m = re.fullmatch(r"card([0-9]{1,3})", os.path.basename(card_dir))
        card_id = _read("/", os.path.join(card_dir, "id"))
        if not m or not card_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", card_id):
            continue
        controls = parse_amixer(_amixer(int(m.group(1)), run))
        named = {}
        for info in glob.glob(os.path.join(card_dir, "pcm*p", "info")):
            dm = re.fullmatch(r"pcm([0-9]{1,3})p", os.path.basename(os.path.dirname(info)))
            nm = re.search(r"(?m)^name: HDMI ([0-9]{1,2})\s*$", _read("/", info) or "")
            if dm and nm:
                named[int(dm.group(1))] = int(nm.group(1))
        for device in sorted(controls):
            if controls[device]["jack"] is not True:
                continue
            k = named.get(device, _HDA_HDMI_DEVICES.index(device) if device in _HDA_HDMI_DEVICES else None)
            eld = controls[device]["eld"]
            found.append({"card": int(m.group(1)), "card_id": card_id, "device": device, "hdmi_index": k,
                          "identity": eld_identity(eld) if eld else None})
    return found


def hdmi_sound_for(connector, connectors, root="/", run=None):
    """The sound PCM (an item of hdmi_sound_screens, with "how") of the screen on DRM connector `connector`, or None
    when it cannot be told safely. `connectors` is drm_connectors() (each with "status": "connected" or not).

    1. One PCM with a screen: it is this screen's ("the only screen"), the common case of one projector on a PC.
    2. Several: only when every PCM's ELD and every connected HDMI or DisplayPort screen's EDID has a (name, maker,
       product) that no other on its side has, and the connector's is one PCM's ("monitor name and model"). Two
       screens that look alike on either side give None: on the M710q the ELDs and EDIDs did not agree (D137)."""
    if connector_kind(connector) not in ("hdmi", "dp"):
        return None
    screens = hdmi_sound_screens(root, run)
    if len(screens) == 1:
        return dict(screens[0], how="the only screen")
    if len(screens) < 2:
        return None
    elds = [s["identity"] for s in screens]
    connected = [c["connector"] for c in connectors if c.get("status") == "connected"
                 and connector_kind(c.get("connector")) in ("hdmi", "dp")]
    edids = [edid_identity(c, root) for c in connected]
    if connector not in connected or None in elds or None in edids:
        return None
    if len(set(elds)) != len(elds) or len(set(edids)) != len(edids):
        return None
    mine = edid_identity(connector, root)
    same = [s for s in screens if s["identity"] == mine]
    return dict(same[0], how="monitor name and model") if len(same) == 1 else None


def report(root="/"):
    board = detect_board(root)
    return {
        "board": board,
        "os": os_release(root),
        "temperatures": temperatures(root),
        "displays": drm_connectors(root),
        "profile": playback_profile(board, has_desktop()),
    }
