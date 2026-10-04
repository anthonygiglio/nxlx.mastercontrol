# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Wired and Wi-Fi network configuration: validation, the exact NetworkManager commands, and a safe-apply timer.

Nothing here runs a command. `plan()` turns a validated request into argument lists (never a shell string);
`pvj/netd.py` executes them as root. Changing the network of the box you are controlling over that network
can lock you out, so every change is *pending* until confirmed and reverts by itself when the timer runs out.

A change never edits the confirmed profile in place. It is built as a separate *candidate* profile
(`pvj-<iface>-try`, not autoconnect) and brought up; the old profile is untouched, so a power cut, a
crash or a timeout always leaves the old network intact. Only Confirm swaps the candidate in.

Modes (wired first, as on the approved Network wireframe):
  dhcp       take an address from a router
  static     a fixed address, optional gateway and DNS
  linklocal  a direct cable between laptop and box: 169.254.x.x, no router needed
  share      the box serves addresses to whatever is plugged in (NetworkManager "shared")

Wi-Fi interfaces have their own modes:
  dhcp       join a Wi-Fi network and take an address from its router
  static     join a Wi-Fi network with a fixed address
  hotspot    the box's own Wi-Fi network (WPA2), serving addresses to whoever joins it
  off        Wi-Fi off (the radio is switched off when the change is confirmed)

A Wi-Fi password never appears in a command line (every user on the box can read those). The helper writes
the candidate as a NetworkManager keyfile that only root can read, has NetworkManager load it, and then uses
nmcli for everything else. `public()` is what may leave the helper: the config without the password.
"""

import ipaddress
import os
import re
import shlex
import unicodedata

MODES = ("dhcp", "static", "linklocal", "share")
WIFI_MODES = ("dhcp", "static", "hotspot", "off")
WIFI_SECURITY = ("wpa-psk", "sae", "open")   # WPA2 (and WPA2/WPA3 mixed), WPA3 only, no password
WIFI_BANDS = ("bg", "a")                     # 2.4 GHz, 5 GHz
DEFAULT_HOTSPOT = ("10.43.0.1", 24)          # not NetworkManager's 10.42.0.1, so a hotspot and a served cable can coexist
KEYFILE_DIR = "/etc/NetworkManager/system-connections"
TOKEN = re.compile(r"[0-9a-f]{8}")
_PSK_HEX = re.compile(r"[0-9a-fA-F]{64}")
IFACE = re.compile(r"[a-z][a-z0-9_.-]{0,14}")
DEFAULT_SHARE = ("10.42.0.1", 24)
MIN_REVERT, MAX_REVERT, DEFAULT_REVERT = 20, 300, 60
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_ZERO_NET = ipaddress.ip_network("0.0.0.0/8")
_RFC1918 = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]


class NetError(Exception):
    pass


def profile_name(iface):
    return "pvj-" + iface


def candidate_name(iface):
    return "pvj-%s-try" % iface


def old_name(iface):
    return "pvj-%s-old" % iface


def list_interfaces(sysfs="/sys/class/net"):
    """Wired interfaces first, then wireless, from /sys. No privileges needed."""
    out = []
    try:
        names = sorted(os.listdir(sysfs))
    except OSError:
        return out
    for name in names:
        if name == "lo" or not IFACE.fullmatch(name):
            continue
        base = os.path.join(sysfs, name)
        if not os.path.exists(os.path.join(base, "address")):
            continue  # not a real link-layer device (bridges without a MAC, tunnels, ...)
        if any(os.path.exists(os.path.join(base, x)) for x in ("bridge", "bonding", "tun_flags")) or \
                name.startswith(("docker", "veth", "br-", "virbr", "tap", "tun")):
            continue
        wireless = os.path.exists(os.path.join(base, "wireless")) or os.path.exists(os.path.join(base, "phy80211"))

        def read(p):
            try:
                with open(os.path.join(base, p)) as f:
                    return f.read().strip()
            except OSError:
                return None
        speed = read("speed")
        out.append({"name": name, "kind": "wifi" if wireless else "wired", "state": read("operstate") or "unknown",
                    "carrier": read("carrier") == "1", "mac": read("address"),
                    "speed_mbps": int(speed) if speed and speed.lstrip("-").isdigit() and int(speed) > 0 else None})
    out.sort(key=lambda i: (i["kind"] != "wired", i["name"]))
    return out


def _ipv4(text, what):
    try:
        addr = ipaddress.IPv4Address(text)
    except (ipaddress.AddressValueError, ValueError, TypeError):
        raise NetError("%s must be an IPv4 address like 192.168.1.50" % what)
    if str(addr) != text:
        raise NetError("%s must be written plainly (no leading zeros)" % what)
    return addr


def _reject_odd_unicast(addr, what):
    if (addr.is_unspecified or addr.is_multicast or addr.is_loopback or addr.is_link_local or addr.is_reserved
            or addr in _ZERO_NET):
        raise NetError("%s %s cannot be used on a network" % (what, addr))


def validate(request, interfaces, others=()):
    """Return a clean config dict or raise NetError.

    `interfaces` is list_interfaces(); `others` is [(iface, ip_network), ...] for the subnets already in use
    on this box, so a new address cannot collide with another port's network."""
    if not isinstance(request, dict):
        raise NetError("request must be an object")
    iface = request.get("iface")
    if not isinstance(iface, str) or not IFACE.fullmatch(iface):
        raise NetError("invalid interface name")
    match = [i for i in interfaces if i["name"] == iface]
    if not match:
        raise NetError("no such interface: %s" % iface)
    kind = match[0]["kind"]
    modes = WIFI_MODES if kind == "wifi" else MODES
    mode = request.get("mode")
    if mode not in modes:
        raise NetError("mode must be one of: %s" % ", ".join(modes))
    revert = request.get("revert_seconds", DEFAULT_REVERT)
    if isinstance(revert, bool) or not isinstance(revert, int) or not MIN_REVERT <= revert <= MAX_REVERT:
        raise NetError("revert_seconds must be %d to %d" % (MIN_REVERT, MAX_REVERT))
    cfg = {"iface": iface, "mode": mode, "revert_seconds": revert}
    if kind == "wifi":
        cfg["kind"] = "wifi"
        if mode != "off":
            _validate_wifi(request, cfg)
    ipmode = ip_mode(cfg)
    if ipmode in ("static", "share"):
        default = (DEFAULT_HOTSPOT if mode == "hotspot" else DEFAULT_SHARE) if ipmode == "share" else (None, None)
        addr = _ipv4(request.get("address", default[0]), "address")
        prefix = request.get("prefix", default[1])
        low = 16 if ipmode == "share" else 8  # a served pool bigger than a /16 would flood a whole network
        if isinstance(prefix, bool) or not isinstance(prefix, int) or not low <= prefix <= 30:
            raise NetError("prefix must be a whole number from %d to 30 (24 means 255.255.255.0)" % low)
        _reject_odd_unicast(addr, "address")
        net = ipaddress.ip_network("%s/%d" % (addr, prefix), strict=False)
        if addr == net.network_address or addr == net.broadcast_address:
            raise NetError("address %s is the network or broadcast address of %s" % (addr, net))
        if ipmode == "share" and not any(addr in n for n in _RFC1918):
            raise NetError("the box may only serve addresses from 10.x, 172.16-31.x or 192.168.x")
        for other_iface, other_net in others:
            if other_iface != iface and net.overlaps(other_net):
                raise NetError("%s overlaps %s, which %s already uses; pick a different range" % (net, other_net, other_iface))
        cfg.update(address=str(addr), prefix=prefix)
        if ipmode == "static":
            gw = request.get("gateway")
            if gw not in (None, ""):
                gwa = _ipv4(gw, "gateway")
                if gwa not in net or gwa == addr or gwa == net.network_address or gwa == net.broadcast_address:
                    raise NetError("gateway %s must be another address inside %s" % (gwa, net))
                cfg["gateway"] = str(gwa)
            dns = request.get("dns", [])
            if not isinstance(dns, list) or len(dns) > 3:
                raise NetError("dns must be a list of up to 3 addresses")
            servers = []
            for d in dns:
                da = _ipv4(d, "DNS server")
                _reject_odd_unicast(da, "DNS server")
                if str(da) in servers or da == addr:
                    raise NetError("DNS server %s is listed twice or is this box's own address" % da)
                servers.append(str(da))
            cfg["dns"] = servers
    return cfg


def is_wifi(cfg):
    return cfg.get("kind") == "wifi"


def ip_mode(cfg):
    """The addressing a config uses: a hotspot serves addresses like a wired "share"."""
    return "share" if cfg["mode"] == "hotspot" else cfg["mode"]


def clean_ssid(value):
    """A network name: 1 to 32 bytes of UTF-8 with no control or invisible format characters, or None."""
    if not isinstance(value, str) or not value or len(value.encode("utf-8", "surrogatepass")) > 32:
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if any(unicodedata.category(ch)[0] == "C" for ch in value):
        return None
    return value


def _validate_wifi(request, cfg):
    ssid = clean_ssid(request.get("ssid"))
    if ssid is None:
        raise NetError("the network name must be 1 to 32 bytes, with no control characters")
    hotspot = cfg["mode"] == "hotspot"
    security = request.get("security", "wpa-psk")
    if security not in WIFI_SECURITY:
        raise NetError("security must be one of: %s" % ", ".join(WIFI_SECURITY))
    if hotspot and security != "wpa-psk":
        raise NetError("the box's hotspot always uses a WPA2 password")
    cfg.update(ssid=ssid, security=security)
    password = request.get("password")
    if security == "open":
        if password not in (None, ""):
            raise NetError("an open network has no password; choose WPA2 or WPA3 to use one")
    else:
        if not isinstance(password, str) or not (
                (8 <= len(password) <= 63 and all(" " <= ch <= "~" for ch in password)) or _PSK_HEX.fullmatch(password)):
            raise NetError("the Wi-Fi password must be 8 to 63 plain characters (letters, digits, punctuation, spaces)")
        if security == "sae" and _PSK_HEX.fullmatch(password):
            raise NetError("WPA3 takes a password, not a 64-digit hex key")
        cfg["password"] = password
    if hotspot:
        band = request.get("band", "bg")
        if band not in WIFI_BANDS:
            raise NetError("band must be bg (2.4 GHz) or a (5 GHz)")
        cfg["band"] = band
    else:
        hidden = request.get("hidden", False)
        if not isinstance(hidden, bool):
            raise NetError("hidden must be true or false")
        cfg["hidden"] = hidden


def public(cfg):
    """The config as it may be shown or returned: never the password."""
    out = {k: v for k, v in cfg.items() if k != "password"}
    if is_wifi(cfg) and cfg["mode"] != "off":
        out["password_set"] = "password" in cfg
    return out


def keyfile_path(iface, token, directory=KEYFILE_DIR):
    if not IFACE.fullmatch(iface) or not TOKEN.fullmatch(token):
        raise NetError("bad keyfile name")
    return os.path.join(directory, "%s-%s.nmconnection" % (candidate_name(iface), token))


def _kf_escape(text):
    """A GKeyFile string value: backslash and space escaped, so nothing is trimmed or misread."""
    return text.replace("\\", "\\\\").replace(" ", "\\s")


def keyfile(cfg, uuid):
    """The candidate Wi-Fi profile as a NetworkManager keyfile (written root-only by pvj-netd). The addressing
    is set afterwards with nmcli, the same way as for a wired port. The SSID is written as a list of bytes,
    which NetworkManager reads exactly and which needs no escaping."""
    if not UUID.fullmatch(uuid):
        raise NetError("bad connection id")
    hotspot = cfg["mode"] == "hotspot"
    lines = ["[connection]", "id=" + candidate_name(cfg["iface"]), "uuid=" + uuid, "type=wifi",
             "interface-name=" + cfg["iface"], "autoconnect=false", "",
             "[wifi]", "mode=" + ("ap" if hotspot else "infrastructure"),
             "ssid=" + "".join("%d;" % b for b in cfg["ssid"].encode("utf-8"))]
    if hotspot:
        lines.append("band=" + cfg["band"])
    elif cfg.get("hidden"):
        lines.append("hidden=true")
    lines.append("")
    if cfg["security"] != "open":
        lines += ["[wifi-security]", "key-mgmt=" + cfg["security"], "psk-flags=0", "psk=" + _kf_escape(cfg["password"])]
        if hotspot:   # pmf=1 (off): with it on, some phones cannot join a Pi's hotspot
            lines += ["proto=rsn", "pairwise=ccmp", "group=ccmp", "pmf=1"]
        lines.append("")
    lines += ["[ipv4]", "method=auto", "", "[ipv6]", "method=auto", ""]
    return "\n".join(lines)


def settings_for(cfg, autoconnect="yes"):
    """NetworkManager property list for a validated config."""
    s = ["connection.autoconnect", autoconnect, "connection.autoconnect-priority", "100"]
    mode = ip_mode(cfg)
    if mode == "dhcp":
        s += ["ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", "", "ipv4.dns", "", "ipv6.method", "auto"]
    elif mode == "static":
        s += ["ipv4.method", "manual", "ipv4.addresses", "%s/%d" % (cfg["address"], cfg["prefix"]),
              "ipv4.gateway", cfg.get("gateway", ""), "ipv4.dns", " ".join(cfg.get("dns", [])), "ipv6.method", "auto"]
    elif mode == "linklocal":
        s += ["ipv4.method", "link-local", "ipv4.addresses", "", "ipv4.gateway", "", "ipv4.dns", "", "ipv6.method", "link-local"]
    else:  # share
        s += ["ipv4.method", "shared", "ipv4.addresses", "%s/%d" % (cfg["address"], cfg["prefix"]),
              "ipv4.gateway", "", "ipv4.dns", "", "ipv6.method", "link-local"]
    return s


def plan(cfg, token=None, directory=KEYFILE_DIR):
    """Commands that build the CANDIDATE profile and bring it up. The confirmed profile is not touched.

    For Wi-Fi the helper first writes keyfile() to keyfile_path(iface, token); "off" only disconnects."""
    name = candidate_name(cfg["iface"])
    if is_wifi(cfg):
        if cfg["mode"] == "off":
            return [["nmcli", "device", "disconnect", cfg["iface"]]]
        return [["nmcli", "connection", "load", keyfile_path(cfg["iface"], token or "00000000", directory)],
                ["nmcli", "connection", "modify", "id", name] + settings_for(cfg, autoconnect="no"),
                ["nmcli", "connection", "up", "id", name]]
    # autoconnect stays OFF until confirmed: a reboot now falls back to the old network.
    return [["nmcli", "connection", "add", "type", "ethernet", "ifname", cfg["iface"], "con-name", name]
            + settings_for(cfg, autoconnect="no"),
            ["nmcli", "connection", "up", "id", name]]


def confirm_plan(iface, old_exists, wifi_off=False):
    """Swap the candidate in. Order matters: it first becomes the preferred autoconnect profile, and only
    then is the old one removed, so at no moment is there no autoconnect profile.

    Confirming "Wi-Fi off" switches the radio off (it stays off across a restart); the saved Wi-Fi profile is
    kept, so joining again later needs no new profile."""
    if wifi_off:
        return [["nmcli", "radio", "wifi", "off"]]
    cand, final, old = candidate_name(iface), profile_name(iface), old_name(iface)
    cmds = [["nmcli", "connection", "modify", "id", cand, "connection.autoconnect", "yes",
             "connection.autoconnect-priority", "101"]]
    # The old profile is only renamed until the candidate has its name, and deleted last: if anything fails
    # before that, the old one still exists and the change can still be undone (pvj-netd renames it back).
    if old_exists:
        cmds.append(["nmcli", "connection", "modify", "id", final, "connection.id", old])
    cmds.append(["nmcli", "connection", "modify", "id", cand, "connection.id", final])
    if old_exists:
        cmds.append(["nmcli", "connection", "delete", "id", old])
    return cmds


def revert_plan(iface, candidate_exists, previous_uuid, radio_was_off=False):
    """Undo an unconfirmed change: drop the candidate and re-activate what was active before (and switch
    the Wi-Fi radio off again if the change had to switch it on)."""
    cmds = []
    if candidate_exists:
        name = candidate_name(iface)
        cmds += [["nmcli", "connection", "down", "id", name], ["nmcli", "connection", "delete", "id", name]]
    if previous_uuid:
        if not UUID.fullmatch(previous_uuid):
            raise NetError("bad connection id in the saved state")
        cmds.append(["nmcli", "connection", "up", "uuid", previous_uuid])
    if radio_was_off:
        cmds.append(["nmcli", "radio", "wifi", "off"])
    return cmds


def preview(cmds):
    """Human-readable commands: shell-quoted so empty and spaced arguments stay visible. A keyfile load is
    preceded by a line saying the file is written first (its password is never shown)."""
    out = []
    for c in cmds:
        if c[:3] == ["nmcli", "connection", "load"]:
            out.append("# write %s (readable by root only; it holds the Wi-Fi settings and password)" % c[3])
        out.append(shlex.join(c))
    return out


def parse_terse(line):
    """Split one line of `nmcli -t` output: fields are separated by ':', and ':' and '\\' inside a field are
    escaped with a backslash."""
    fields, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    fields.append("".join(cur))
    return fields


def unescape_terse(text):
    """One value of `nmcli -t -g`, with its backslash escapes undone."""
    return ":".join(parse_terse(text))


def scan_results(text, limit=40, current=None):
    """Networks from `nmcli -t -f SSID,SIGNAL,SECURITY,CHAN device wifi list`: one entry per name (the strongest),
    strongest first. Names come from the air, so anything odd is left out, and a name could even hold a line
    break that looks like a record of its own: so "in use" is not read from the list but marked from what the
    box is joined to (`current`), and nothing in a record is trusted beyond being shown."""
    best = {}
    for line in text.splitlines():
        f = parse_terse(line)
        if len(f) != 4:
            continue
        ssid, signal, sec, chan = f
        ssid = clean_ssid(ssid)
        if ssid is None or not signal.isdigit() or not chan.isdigit():
            continue
        sec = sec.upper()
        if not sec.strip() or sec.strip() == "--":
            security = "open"
        elif "802.1X" in sec or "WEP" in sec:
            security = "unsupported"
        elif "WPA2" in sec or "WPA1" in sec:
            security = "wpa-psk"
        elif "WPA3" in sec:
            security = "sae"
        else:
            security = "unsupported"
        entry = {"ssid": ssid, "signal": min(int(signal), 100), "security": security,
                 "channel": int(chan), "in_use": current is not None and ssid == current}
        old = best.get(ssid)
        if old is None or entry["signal"] > old["signal"]:
            best[ssid] = entry
    return sorted(best.values(), key=lambda e: (-e["in_use"], -e["signal"], e["ssid"]))[:limit]


class PendingChange:
    """A change waiting for confirmation."""

    def __init__(self, cfg, previous_uuid, started, seconds):
        self.cfg, self.previous_uuid, self.seconds = cfg, previous_uuid, seconds
        self.deadline = started + seconds

    def restart(self, now):
        """The countdown starts when the new network is up, not when the commands began."""
        self.deadline = now + self.seconds

    def seconds_left(self, now):
        return max(0, int(self.deadline - now + 0.999))

    def expired(self, now):
        return now >= self.deadline
