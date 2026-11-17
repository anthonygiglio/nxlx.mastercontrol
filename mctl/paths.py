# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Every runtime path of the box, in one place (D45).

On a box each service has a folder of its own that only it can write, under a parent that belongs to root:

    /run/nxlx                root:root       0755   made by systemd and install/nxlx-tmpfiles.conf, never a service's own
    /run/nxlx/player         <display>:nxlx-svc   0750   nxlx-player: player.sock, preview.jpg, screen.json
    /run/nxlx/web            nxlx-web:nxlx-svc     0750   nxlx-web: pin, capture.fifo, overlays, shader and mapping files, loops/
    /run/nxlx/netd           root:nxlx-svc        0750   nxlx-netd: netd.sock
    /run/nxlx-sysd           root:nxlx-svc        0750   nxlx-sysd: sysd.sock
    /run/nxlx-supportd       root:nxlx-svc        0750   nxlx-supportd: supportd.sock
    /run/nxlx-ndi            nxlx-ndi:nxlx-ndi 0750   nxlx-ndi: ndi.sock (D62: its own group, never group nxlx-svc). Only on a box
                                                   that was set up for NDI (mctl/ndisetup.py): there the panel and the
                                                   player are each given the extra group nxlx-ndi by a drop-in
    /run/nxlx-ndi-pipe       root:nxlx-ndi    0750   ndi.fifo (0660 root:nxlx-ndi), made ONCE by root (the tmpfiles lines
                                                   nxlx-ndi-runtime writes): the helper writes it by its group and can
                                                   neither unlink nor replace it, so the name the player opens is
                                                   always that pipe (the review of 2026-10-11, M3)
    /run/nxlx-update         root:root       0755   the update units: result.json, update.lock
    /run/nxlx-usb            root:root       0755   nxlx-usb@ units: clip-stick.json (0644, read by the panel), clip.lock, peek/ and mnt/ (0700, the stick itself) (D143)
    /run/nxlx-raster.sock    root:nxlx-raster 0660   nxlx-raster.socket (D129): the picture helper, started per job; only the
                                                   panel is in group nxlx-raster

A folder is a `RuntimeDirectory=` of exactly one unit, so systemd never hands it to another account. Before this
the player, the panel and the network helper shared `RuntimeDirectory=nxlx`, and systemd gave the whole folder,
with the helper's socket and the PIN in it, to whichever of them started last.

Environment:

* `NXLX_RUNTIME_DIR` is the folder THIS process owns and writes (each unit sets it to its own folder). Without it,
  on a desk, it is `$XDG_RUNTIME_DIR/nxlx` or `/tmp/nxlx-<uid>`.
* `NXLX_PLAYER_DIR` and `NXLX_NETD_DIR` say where the panel finds the folders of its peers. Without them a peer's
  files are looked for in the process's own folder: one private folder for everything, which is what the tests
  and a player started by hand use.
* `NXLX_SYSD_DIR`, `NXLX_SUPPORTD_DIR` and `NXLX_UPDATE_RESULT` are as before.
* `NXLX_NDI_DIR` is the NDI helper's folder. It is set for the panel only on a box that was set up for NDI, and the
  panel takes its absence to mean exactly that: no helper here (server.py). `NXLX_NDI_PIPE_DIR` is the pipe's folder
  (tests and a desk only; on a box it is the fixed root-owned folder).

A process creates and checks only its own folder (`own_dir`). It never creates a peer's folder: a missing one
means that the peer is not running.
"""
import os
import re
import stat
import time

RUN = "/run/nxlx"                              # the parent: root's, nobody else may add, rename or remove a name in it
PLAYER_DIR = RUN + "/player"
PLAYER_STATE_DIR = "/var/lib/nxlx-player/late-screen"    # persistent, the player account's own (StateDirectory=nxlx-player/late-screen)
WEB_DIR = RUN + "/web"
NETD_DIR = RUN + "/netd"
SYSD_DIR = "/run/nxlx-sysd"
SUPPORTD_DIR = "/run/nxlx-supportd"
NDI_DIR = "/run/nxlx-ndi"
NDI_PIPE_DIR = "/run/nxlx-ndi-pipe"
UPDATE_DIR = "/run/nxlx-update"
USB_DIR = "/run/nxlx-usb"                        # nxlx-usb (root): the clip stick's status and the private folder to read its marker (D143)
UPDATE_RESULT = UPDATE_DIR + "/result.json"

PLAYER_SOCKET = "player.sock"
PLAYER_PID = "player.pid"
PREVIEW = "preview.jpg"                       # written by mpv, so it lives in the player's folder
SCREEN_REPORT = "screen.json"                 # the screen's modes and the one the player chose (mctl/screenmode.py, D90)
RENDERER_REPORT = "renderer.json"             # which program draws and why (mctl/renderer.py, D95)
STATE_DIR = "/var/lib/nxlx"                    # the panel's settings; the player may pass through and read what is its group's
NETD_SOCKET = "netd.sock"
SYSD_SOCKET = "sysd.sock"
SUPPORTD_SOCKET = "supportd.sock"
NDI_SOCKET = "ndi.sock"
NDI_FIFO = "ndi.fifo"
PIN = "pin"
CAPTURE_FIFO = "capture.fifo"
LOOPS = "loops"                               # the generated loop lists (mctl/looplist.py, D118): in the panel's folder, read through group nxlx-svc
OVERLAY = "overlay.bgra"
UNDERVOLTAGE = "undervoltage-seen"

# What the shared /run/nxlx of older versions held, directly in it. The installer removes every non-folder there;
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
    base = env.get("NXLX_RUNTIME_DIR")
    if not base:
        xdg = env.get("XDG_RUNTIME_DIR")
        base = os.path.join(xdg, "nxlx") if xdg else "/tmp/nxlx-%d" % os.getuid()
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
    return _env(env).get("NXLX_PLAYER_DIR") or own_path(env)


def netd_dir(env=None):
    """Where the network helper keeps its socket. Only the helper creates it."""
    return _env(env).get("NXLX_NETD_DIR") or own_path(env)


def sysd_dir(env=None):
    return _env(env).get("NXLX_SYSD_DIR") or SYSD_DIR


def supportd_dir(env=None):
    return _env(env).get("NXLX_SUPPORTD_DIR") or SUPPORTD_DIR


def state_dir(env=None):
    """The panel's state folder (settings.json, and screen-mode.json, which the player reads: D90)."""
    return _env(env).get("NXLX_STATE_DIR") or STATE_DIR


def player_state_dir(env=None):
    """The player's own state folder (a StateDirectory= of nxlx-player.service, owned by the account that runs it): what the
    player must remember across a restart of its service, keyed by the boot (mctl/latescreen.py)."""
    return _env(env).get("NXLX_PLAYER_STATE_DIR") or PLAYER_STATE_DIR


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


def ndi_dir(env=None):
    """The NDI helper's folder: its socket for the panel and the pipe the player reads. Only the helper creates it."""
    return _env(env).get("NXLX_NDI_DIR") or NDI_DIR


def ndi_socket(env=None):
    return os.path.join(ndi_dir(env), NDI_SOCKET)


def ndi_pipe_dir(env=None):
    """The pipe's folder: root's, made once (see the table above). Not the helper's own folder."""
    return _env(env).get("NXLX_NDI_PIPE_DIR") or NDI_PIPE_DIR


NDISC_DIR = "/run/nxlx-ndisc"              # the NDI discovery server's supervisor: its socket for the panel (mctl/ndiscovery.py, D85)


def ndisc_dir(env=None):
    """The discovery server's supervisor's folder, or None on a box where it is not set up (the panel's drop-in names it)."""
    return _env(env).get("NXLX_NDISC_DIR") or None


NDI_CONFIG_DIR = "/run/nxlx-ndi-config"    # the NDI library's configuration file: the panel writes it, the helper reads it (mctl/ndisetup.py)


def ndi_config_dir(env=None):
    """Where the panel writes the NDI library's configuration file, or None on a box whose NDI set-up does not
    have the folder (the panel's drop-in names it: mctl/ndisetup.py)."""
    return _env(env).get("NXLX_NDI_CONFIG_DIR") or None


SOUND_DIR = "/run/nxlx-sound"              # the sound-analysis service (mctl/sound.py, D96): its socket and the levels file
SOUND_SOCKET = "sound.sock"
SOUND_LEVELS = "levels"


def sound_dir(env=None):
    """The sound service's folder: its socket for the panel and the levels file (group audio). Only the service creates it."""
    return _env(env).get("NXLX_SOUND_DIR") or SOUND_DIR


def sound_socket(env=None):
    return os.path.join(sound_dir(env), SOUND_SOCKET)


def sound_levels(env=None):
    return os.path.join(sound_dir(env), SOUND_LEVELS)


LINK_DIR = "/run/nxlx-link"                # the Ableton Link follower (render/link/, D117): its socket and its state file
LINK_SOCKET = "link.sock"
LINK_STATE = "state"


def link_dir(env=None):
    """The Link helper's folder: its socket for the panel and its state file (group nxlx-link). Only the service creates it."""
    return _env(env).get("NXLX_LINK_DIR") or LINK_DIR


def link_socket(env=None):
    return os.path.join(link_dir(env), LINK_SOCKET)


def link_state(env=None):
    return os.path.join(link_dir(env), LINK_STATE)


RASTER_SOCKET = "/run/nxlx-raster.sock"   # nxlx-raster (render/raster/, D129): systemd listens here and starts a helper per job


def fonts_dir(env=None):
    """The folder of the bundled fonts (mctl/fonts: open-licence fonts only, D129). The picture helper reads them by name
    from here; no browser is ever sent one."""
    return _env(env).get("NXLX_FONTS_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")


def raster_socket(env=None):
    """The picture helper's socket (install/nxlx-raster.socket). Group nxlx-raster may connect: the panel alone is in it."""
    return _env(env).get("NXLX_RASTER_SOCKET") or RASTER_SOCKET


def ndi_fifo(env=None):
    return os.path.join(ndi_pipe_dir(env), NDI_FIFO)


def update_result(env=None):
    return _env(env).get("NXLX_UPDATE_RESULT") or UPDATE_RESULT


def pin_file(rundir):
    return os.path.join(rundir, PIN)


def loops_dir(rundir):
    return os.path.join(rundir, LOOPS)


def capture_fifo(rundir):
    return os.path.join(rundir, CAPTURE_FIFO)


def overlay_file(rundir):
    return os.path.join(rundir, OVERLAY)


def undervoltage_marker(rundir):
    return os.path.join(rundir, UNDERVOLTAGE)


def remove_leftovers(folder, pattern, older_than=0.0, now=None):
    """Unlink the temp files a power cut or a crash left in `folder`: plain files whose whole name matches
    `pattern` and that were last written more than `older_than` seconds ago (D70). Returns the names removed.

    A time more than `older_than` in the future counts as old as well: a Pi has no clock of its own, and started
    without a time source it believes an earlier time than the one its files carry. A file written just now is
    never that far ahead. (Less far ahead it is kept, and goes once the clock has caught up.)

    Only ever a regular file directly in the folder, found and removed through the opened folder: a link is never
    followed and never removed, and neither is a folder, and if `folder` itself is a link nothing is done. Never
    raises: this runs where a service starts, and a leftover is not worth a service that does not start."""
    gone = []
    try:
        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    except (OSError, TypeError, ValueError):
        return gone
    try:
        now = time.time() if now is None else now
        for name in os.listdir(fd):
            if not re.fullmatch(pattern, name):
                continue
            try:
                st = os.lstat(name, dir_fd=fd)
                if stat.S_ISREG(st.st_mode) and abs(now - st.st_mtime) >= older_than:
                    os.unlink(name, dir_fd=fd)
                    gone.append(name)
            except OSError:
                pass
    except OSError:
        pass
    finally:
        os.close(fd)
    return sorted(gone)
