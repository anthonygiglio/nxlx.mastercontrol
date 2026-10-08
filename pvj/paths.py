# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Every runtime path of the box, in one place (D45).

On a box each service has a folder of its own that only it can write, under a parent that belongs to root:

    /run/pvj                root:root       0755   made by systemd and install/pvj-tmpfiles.conf, never a service's own
    /run/pvj/player         <display>:pvj   0750   pvj-player: player.sock, preview.jpg
    /run/pvj/web            pvj-web:pvj     0750   pvj-web: pin, capture.fifo, overlays, shader and mapping files
    /run/pvj/netd           root:pvj        0750   pvj-netd: netd.sock
    /run/pvj-sysd           root:pvj        0750   pvj-sysd: sysd.sock
    /run/pvj-supportd       root:pvj        0750   pvj-supportd: supportd.sock
    /run/pvj-update         root:root       0755   the update units: result.json

A folder is a `RuntimeDirectory=` of exactly one unit, so systemd never hands it to another account. Before this
the player, the panel and the network helper shared `RuntimeDirectory=pvj`, and systemd gave the whole folder,
with the helper's socket and the PIN in it, to whichever of them started last.

Environment:

* `PVJ_RUNTIME_DIR` is the folder THIS process owns and writes (each unit sets it to its own folder). Without it,
  on a desk, it is `$XDG_RUNTIME_DIR/pvj` or `/tmp/pvj-<uid>`.
* `PVJ_PLAYER_DIR` and `PVJ_NETD_DIR` say where the panel finds the folders of its peers. Without them a peer's
  files are looked for in the process's own folder: one private folder for everything, which is what the tests
  and a player started by hand use.
* `PVJ_SYSD_DIR`, `PVJ_SUPPORTD_DIR` and `PVJ_UPDATE_RESULT` are as before.

A process creates and checks only its own folder (`own_dir`). It never creates a peer's folder: a missing one
means that the peer is not running.
"""
import os
import re
import stat
import time

RUN = "/run/pvj"                              # the parent: root's, nobody else may add, rename or remove a name in it
PLAYER_DIR = RUN + "/player"
WEB_DIR = RUN + "/web"
NETD_DIR = RUN + "/netd"
SYSD_DIR = "/run/pvj-sysd"
SUPPORTD_DIR = "/run/pvj-supportd"
UPDATE_DIR = "/run/pvj-update"
UPDATE_RESULT = UPDATE_DIR + "/result.json"

PLAYER_SOCKET = "player.sock"
PLAYER_PID = "player.pid"
PREVIEW = "preview.jpg"                       # written by mpv, so it lives in the player's folder
NETD_SOCKET = "netd.sock"
SYSD_SOCKET = "sysd.sock"
SUPPORTD_SOCKET = "supportd.sock"
PIN = "pin"
CAPTURE_FIFO = "capture.fifo"
OVERLAY = "overlay.bgra"
UNDERVOLTAGE = "undervoltage-seen"

# What the shared /run/pvj of older versions held, directly in it. The installer removes every non-folder there;
# this list is for the tests and for people.
LEGACY_NAMES = (PLAYER_SOCKET, PLAYER_PID, PREVIEW, NETD_SOCKET, PIN, CAPTURE_FIFO, OVERLAY, UNDERVOLTAGE)
LEGACY_PATTERNS = ("overlay-*.bgra", "mapper-*.glsl", "shader-*.glsl")


class UnsafeDirectory(Exception):
    pass


def _env(env):
    return os.environ if env is None else env


def own_path(env=None):
    """The folder this process writes into (not created, not checked)."""
    env = _env(env)
    base = env.get("PVJ_RUNTIME_DIR")
    if not base:
        xdg = env.get("XDG_RUNTIME_DIR")
        base = os.path.join(xdg, "pvj") if xdg else "/tmp/pvj-%d" % os.getuid()
    return base


def own_dir(env=None):
    """The folder this process writes into: created private if it is missing, and refused if anybody outside its
    group can get into it, or if it belongs to somebody else and we are not in its group."""
    base = own_path(env)
    os.makedirs(base, mode=0o700, exist_ok=True)
    st = os.stat(base)
    mode = stat.S_IMODE(st.st_mode)
    if mode & 0o007:
        raise UnsafeDirectory("unsafe runtime directory %s (world accessible)" % base)
    if st.st_uid != os.getuid():
        if not (mode & 0o070 and st.st_gid in os.getgroups()):
            raise UnsafeDirectory("unsafe runtime directory %s (not yours and not in its group)" % base)
    return base


def player_dir(env=None):
    """Where the player keeps its socket. Only the player creates it."""
    return _env(env).get("PVJ_PLAYER_DIR") or own_path(env)


def netd_dir(env=None):
    """Where the network helper keeps its socket. Only the helper creates it."""
    return _env(env).get("PVJ_NETD_DIR") or own_path(env)


def sysd_dir(env=None):
    return _env(env).get("PVJ_SYSD_DIR") or SYSD_DIR


def supportd_dir(env=None):
    return _env(env).get("PVJ_SUPPORTD_DIR") or SUPPORTD_DIR


def player_socket(env=None):
    return os.path.join(player_dir(env), PLAYER_SOCKET)


def preview_file(env=None):
    return os.path.join(player_dir(env), PREVIEW)


def netd_socket(env=None):
    return os.path.join(netd_dir(env), NETD_SOCKET)


def sysd_socket(env=None):
    return os.path.join(sysd_dir(env), SYSD_SOCKET)


def supportd_socket(env=None):
    return os.path.join(supportd_dir(env), SUPPORTD_SOCKET)


def update_result(env=None):
    return _env(env).get("PVJ_UPDATE_RESULT") or UPDATE_RESULT


def pin_file(rundir):
    return os.path.join(rundir, PIN)


def capture_fifo(rundir):
    return os.path.join(rundir, CAPTURE_FIFO)


def overlay_file(rundir):
    return os.path.join(rundir, OVERLAY)


def undervoltage_marker(rundir):
    return os.path.join(rundir, UNDERVOLTAGE)


def remove_leftovers(folder, pattern, older_than=0.0, now=None):
    """Unlink the temp files a power cut or a crash left in `folder`: plain files whose whole name matches
    `pattern` and that were last written more than `older_than` seconds ago (D70). Returns the names removed.

    Only ever a regular file directly in the folder, found and removed through the opened folder: a link is never
    followed and never removed, and neither is a folder, and if `folder` itself is a link nothing is done. Never
    raises: this runs where a service starts, and a leftover is not worth a service that does not start."""
    gone = []
    try:
        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    except (OSError, TypeError, ValueError):
        return gone
    try:
        limit = (time.time() if now is None else now) - older_than
        for name in os.listdir(fd):
            if not re.fullmatch(pattern, name):
                continue
            try:
                st = os.lstat(name, dir_fd=fd)
                if stat.S_ISREG(st.st_mode) and st.st_mtime <= limit:
                    os.unlink(name, dir_fd=fd)
                    gone.append(name)
            except OSError:
                pass
    except OSError:
        pass
    finally:
        os.close(fd)
    return sorted(gone)
