# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The node's time zone: checking a name, reading the one in use, and the list to choose from (J-129).

The schedule runs on the node's local time, so a node on the wrong zone fires an 8 pm entry at the wrong hour. A zone is
an IANA name ("America/Regina"). The name that reaches `timedatectl set-timezone` is checked here, strictly, by the root
helper (pvj/sysd.py) and again by the panel before it asks: letters, digits, "_", "+", "-" in at most three parts split
by "/", no "." at all, a regular file under the zoneinfo folder that really is a time zone file (it starts with "TZif").
Nothing but the checked name is ever passed on.
"""

import os
import re

ZONEINFO = "/usr/share/zoneinfo"
NAME = re.compile(r"[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+){0,2}")
NAME_MAX = 64
SKIP_FIRST = ("posix", "right", "SystemV")       # the same names again under another folder; not for a choice
UTC_NAMES = frozenset(("UTC", "Etc/UTC", "Etc/UCT", "UCT", "Etc/Universal", "Universal", "Etc/Zulu", "Zulu"))


def valid_zone(name, root=ZONEINFO):
    """True if `name` is a safe IANA zone name that exists as a time zone file under `root`."""
    if not isinstance(name, str) or not name or len(name) > NAME_MAX or not NAME.fullmatch(name):
        return False
    if name.split("/")[0] in SKIP_FIRST:
        return False
    path = os.path.join(root, name)
    try:
        real, base = os.path.realpath(path), os.path.realpath(root)
        if os.path.commonpath([real, base]) != base or not os.path.isfile(real):
            return False
        with open(real, "rb") as f:
            return f.read(4) == b"TZif"
    except (OSError, ValueError):
        return False


def current_name(localtime="/etc/localtime", timezone_file="/etc/timezone", env=None):
    """The IANA name the system is on, or None. /etc/localtime is a link into the zoneinfo folder on a node set by
    timedatectl; /etc/timezone is the older Debian record."""
    env = os.environ if env is None else env
    try:
        target = os.readlink(localtime)
        if "zoneinfo/" in target:
            name = target.split("zoneinfo/", 1)[1]
            if NAME.fullmatch(name):
                return name
    except OSError:
        pass
    try:
        with open(timezone_file, "r", encoding="utf-8") as f:
            name = f.readline().strip()
        if NAME.fullmatch(name):
            return name
    except OSError:
        pass
    name = (env.get("TZ") or "").lstrip(":")
    return name if NAME.fullmatch(name) else None


def is_utc(name):
    return name in UTC_NAMES


def zones(root=ZONEINFO):
    """Every zone name a person may pick, sorted: canonical names from the zoneinfo folder (no posix/, right/ copies)."""
    out = []
    try:
        for top, dirs, files in os.walk(root):
            rel = os.path.relpath(top, root)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            if depth == 0:
                dirs[:] = [d for d in dirs if d not in SKIP_FIRST]
            if depth >= 2:
                dirs[:] = []
            for f in files:
                name = f if depth == 0 else rel.replace(os.sep, "/") + "/" + f
                if "/" in name or name in UTC_NAMES or name[:1].isupper():
                    out.append(name)
    except OSError:
        return []
    return sorted(n for n in set(out) if valid_zone(n, root))
