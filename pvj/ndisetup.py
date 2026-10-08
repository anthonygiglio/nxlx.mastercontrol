# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Setting the NDI helper up on a box, and taking it off again. Root's code, and all of root's code for this.

The NDI input is opt-in (D62, the owner's answer of 2026-10-08). A box that was only installed has nothing of it
beyond program files: no pvj-ndi unit, no pvj-ndi account or group, no avahi added, nothing in the units of the
panel and the player. One command at the box, `sudo pvj-ndi-runtime install <the NDI SDK>` (pvj/ndi.py), puts the
library in place and then calls enable() here, which:

  1. installs avahi-daemon and libavahi-client3 with apt-get where they are missing (the library finds senders
     through them); a failure is said and is not the end;
  2. makes the group and the account pvj-ndi (the account in no other group); one that is already there and is
     not what this would have made (root, a shared number, a login shell, another group) is refused;
  3. writes /etc/systemd/system/pvj-ndi.service from the template beside this file (pvj/systemd/), and two drop-ins
     that give the panel and the player the extra group pvj-ndi; the panel's also says where the helper's folder is,
     which is how the panel knows the box is set up;
  4. reloads systemd, enables and starts the helper, and restarts the panel and the player if they are running
     (an extra group only reaches a process when it starts).

Whatever can refuse the setup (check) is looked at before any of that, and before the library is copied. A step
that fails after the files were written undoes them on a box that was not opted in before. The helper is watched
for a few seconds after its start, and the command says whether it stayed up.

The panel's drop-in is the mark that the box opted in. It is written last and removed first. The installer
(install/install.sh) looks for it: a box that has it gets its unit and drop-ins written again by refresh() here at
every install or update, so there is one writer of these files; a box that does not have it is left alone.

`sudo pvj-ndi-runtime remove` calls disable(): the helper is stopped and disabled, the unit and the drop-ins go,
the panel and the player are restarted. The account, the group and the avahi packages stay (avahi may have been on
the box before, and other things may use it); the command says so and how to remove them.

None of this has run on a device. tests/test_ndi_setup.py drives it with a fake root folder and fake commands;
tests/real_install_test.sh runs it under a real systemd on the CI runner, without NDI's library.
"""

import glob
import grp
import json
import os
import pwd
import re
import subprocess
import sys
import tempfile

UNIT = "pvj-ndi.service"
SYSTEMD = "/etc/systemd/system"
UNIT_PATH = SYSTEMD + "/" + UNIT
WEB_DROPIN = SYSTEMD + "/pvj-web.service.d/50-pvj-ndi.conf"           # also the mark that this box opted in
PLAYER_DROPIN = SYSTEMD + "/pvj-player.service.d/50-pvj-ndi.conf"
ACCOUNT = GROUP = "pvj-ndi"
RESTART = ("pvj-web.service", "pvj-player.service")                   # the units the drop-ins change
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "systemd", UNIT)
INSTALL_JSON = "/etc/pvj/install.json"
DEFAULT_PREFIX = "/opt/pvj"
COMMAND = 'sudo pvj-ndi-runtime install "/path/to/NDI SDK for Linux"'
# A package, and the files of which one shows that it is there.
PACKAGES = (("avahi-daemon", ("/usr/sbin/avahi-daemon",)),
            ("libavahi-client3", ("/usr/lib/*/libavahi-client.so.3", "/usr/lib/libavahi-client.so.3")))

_SAYS = "# Written by pvj-ndi-runtime (pvj/ndisetup.py) when NDI was set up on this box; removed by 'pvj-ndi-runtime remove'.\n"
# In a drop-in these two settings add to what the unit itself has; they do not replace it.
WEB_TEXT = (_SAYS + "# The panel may reach the NDI helper's socket (D62). This file is also the mark that the box opted in.\n"
            "[Service]\nSupplementaryGroups=pvj-ndi\nEnvironment=PVJ_NDI_DIR=/run/pvj-ndi\n")
PLAYER_TEXT = (_SAYS + "# The player may read the picture from the NDI helper's pipe (D62); the helper itself is not in group pvj.\n"
               "[Service]\nSupplementaryGroups=pvj-ndi\n")

_PREFIX = re.compile(r"/[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+")


class SetupError(Exception):
    pass


def opted_in(root=""):
    """True when this box (or the staged tree under `root`) has been set up for NDI: the panel's drop-in is there."""
    path = root + WEB_DROPIN
    return os.path.isfile(path) and not os.path.islink(path)


def check_prefix(prefix):
    if not isinstance(prefix, str) or not _PREFIX.fullmatch(prefix) or ".." in prefix.split("/"):
        raise SetupError("the install folder %r is not one the installer would have written" % (prefix,))
    return prefix


def read_prefix(root=""):
    """Where the installer put the program (it writes this file); /opt/pvj when it does not say."""
    try:
        with open(root + INSTALL_JSON) as f:
            prefix = json.load(f).get("prefix")
    except (OSError, ValueError, AttributeError):
        return DEFAULT_PREFIX
    return check_prefix(prefix) if prefix else DEFAULT_PREFIX


def unit_text(prefix, template=None):
    with open(template or TEMPLATE) as f:
        text = f.read().replace("@PVJ_DIR@", check_prefix(prefix) + "/current")
    if "@PVJ" in text:
        raise SetupError("the unit template has a placeholder that was not filled in")
    return text


def _write(path, text):
    """Whole or not at all, readable by all, never through a link: the folder is root's, and stays that way."""
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o755, exist_ok=True)
    if os.path.islink(folder) or os.path.islink(path) or os.path.isdir(path):
        raise SetupError("%s is not a plain file in a plain folder; nothing was written" % path)
    fd, tmp = tempfile.mkstemp(prefix=".pvj-ndi-", dir=folder)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_units(root="", prefix=DEFAULT_PREFIX, template=None):
    """The helper's unit and the two drop-ins. The mark (the panel's drop-in) is written last."""
    text = unit_text(prefix, template)             # read and checked before anything is written
    _write(root + UNIT_PATH, text)
    _write(root + PLAYER_DROPIN, PLAYER_TEXT)
    _write(root + WEB_DROPIN, WEB_TEXT)


def remove_units(root=""):
    """The mark first, so a removal that is cut short is not taken for a box that opted in. Something that cannot be
    removed (a folder where a file should be) does not stop the rest: returns what was left, in words."""
    left = []
    for path in (WEB_DROPIN, PLAYER_DROPIN, UNIT_PATH):
        try:
            os.unlink(root + path)
        except FileNotFoundError:
            pass
        except OSError as e:
            left.append("%s could not be removed (%s); remove it by hand" % (root + path, e.strerror or "error"))
    for path in (WEB_DROPIN, PLAYER_DROPIN):       # the folders only if nothing else is in them
        try:
            os.rmdir(os.path.dirname(root + path))
        except OSError:
            pass
    return left


def missing_packages(root=""):
    return [name for name, files in PACKAGES if not any(glob.glob(root + pattern) for pattern in files)]


def _run(command):
    """Run one command and return its exit status; what it prints goes to the terminal."""
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    try:
        return subprocess.call(command, env=env)
    except OSError:
        return 127


def _group(name):
    try:
        return grp.getgrnam(name)
    except KeyError:
        return None


def _user(name):
    try:
        return pwd.getpwnam(name)
    except KeyError:
        return None


NO_LOGIN = ("/usr/sbin/nologin", "/sbin/nologin", "/bin/false", "/usr/bin/false")


def check_account(user=_user, group=_group, groups=grp.getgrall, users=pwd.getpwall):
    """Refuses, and changes nothing. An account pvj-ndi that is already there must be what this setup would have made:
    not root, a uid of its own, no login shell, pvj-ndi as its own group and no other group. Anything else is refused,
    not repaired: the helper loads closed-source code that reads the network, and it must not be root, another
    account under a second name, or in group pvj (the player's socket, the PIN, the settings) or any other. An
    account that is not there yet is fine."""
    u = user(ACCOUNT)
    if u is None:
        return
    if u.pw_uid == 0:
        raise SetupError("the account %s is root under another name; nothing was set up" % ACCOUNT)
    shared = sorted(o.pw_name for o in users() if o.pw_uid == u.pw_uid and o.pw_name != ACCOUNT)
    if shared:
        raise SetupError("the account %s shares its number with %s; nothing was set up" % (ACCOUNT, ", ".join(shared)))
    if u.pw_shell not in NO_LOGIN:
        raise SetupError("the account %s has a login shell (%s); nothing was set up" % (ACCOUNT, u.pw_shell))
    g = group(GROUP)
    if g is None or u.pw_gid != g.gr_gid:
        raise SetupError("the account %s is not in the group %s as its own group; nothing was set up" % (ACCOUNT, GROUP))
    others = sorted(x.gr_name for x in groups() if ACCOUNT in x.gr_mem and x.gr_name != GROUP)
    if others:
        raise SetupError("the account %s is also in %s and must be in no other group; nothing was set up" % (ACCOUNT, ", ".join(others)))


def ensure_account(run=_run, say=print, user=_user, group=_group, groups=grp.getgrall, users=pwd.getpwall):
    """The helper's group and account, made if they are missing: a system account without a home or a shell, whose
    only group is pvj-ndi. One that is already there is checked (check_account) before anything is made and again
    after."""
    check_account(user, group, groups, users)
    if group(GROUP) is None:
        say("creating system group %s" % GROUP)
        if run(["groupadd", "--system", GROUP]) != 0 or group(GROUP) is None:
            raise SetupError("could not create the group %s" % GROUP)
    if user(ACCOUNT) is None:
        say("creating system user %s" % ACCOUNT)
        if run(["useradd", "--system", "--no-create-home", "--shell", "/usr/sbin/nologin", "--gid", GROUP, ACCOUNT]) != 0 or user(ACCOUNT) is None:
            raise SetupError("could not create the account %s" % ACCOUNT)
    check_account(user, group, groups, users)


def _systemd_runs():
    return os.path.isdir("/run/systemd/system")


def _overlay():
    """True while the read-only root (pvj-rootfs) is active: what is written now is gone at the next boot."""
    from . import rootfs
    try:
        return rootfs.current_state() == "overlay"
    except OSError:
        return False


OVERLAY_WARNING = ("the read-only root is active on this box (pvj-rootfs): everything this command writes is in memory and is "
                   "gone at the next restart, and with it the NDI setup. To keep it: sudo pvj-rootfs disable, restart, run "
                   "this command again, sudo pvj-rootfs enable, restart.")


def check(root="", account=check_account):
    """Everything that can refuse a setup, looked at before anything on the box is changed (before the library is
    copied, a package installed or an account made): the install folder the installer wrote down and the unit's
    template, and an account pvj-ndi that is already there. Raises SetupError or OSError."""
    unit_text(read_prefix(root))
    account()


def refresh(root="", prefix=None, run=_run, say=print, account=ensure_account):
    """For the installer, at every install and update: write the unit and the drop-ins of a box that opted in again
    (the unit may have changed, the install folder too). A box that did not opt in is left exactly as it is. With a
    staged tree (`root`) no account is touched. Returns whether the box is opted in.

    `root` is trusted, every folder above it too: it is the installer's own root ("" on a box) or its --stage folder,
    never a path somebody else can write."""
    if not opted_in(root):
        return False
    if not root:
        account(run=run, say=say)
    write_units(root, prefix or read_prefix(root))
    return True


STAYS_UP_SECONDS = 3


def _stays_up(run, sleep):
    """The unit is Type=simple: "restart" answers 0 as soon as the process exists, also for one that ends at once.
    So it is looked at for a few seconds; one that is being started again counts as not running."""
    for _ in range(STAYS_UP_SECONDS):
        sleep(1)
        if run(["systemctl", "is-active", "--quiet", UNIT]) != 0:
            return False
    return True


def enable(run=_run, say=print, systemd=_systemd_runs, account=ensure_account, which=None, root="", check=check, sleep=None,
           overlay=_overlay):
    """Opt this box in (see the top of this file). Root, on the box. Safe to run again. Returns True when the helper
    is set up and running (or enabled, where no systemd runs), False when it is set up and did not stay up.

    Whatever can refuse is looked at first (check). A step that fails after the files were written undoes them on a
    box that was not opted in before, so a failed setup does not leave the mark. `root` is for the tests."""
    import shutil
    import time
    which, sleep = which or shutil.which, sleep or time.sleep
    check(root)
    was = opted_in(root)
    wet = overlay()
    if wet:
        say(OVERLAY_WARNING)
    need = missing_packages(root)
    if need:
        if which("apt-get") is None:
            say("not installed, and there is no apt-get here to install them: %s. NDI senders are not found until they are there." % " ".join(need))
        else:
            say("installing %s (the NDI library finds senders through them)" % " ".join(need))
            if run(["apt-get", "install", "-y", "--no-install-recommends"] + need) != 0:
                say("could not install %s (no network?). NDI senders are not found until they are there: sudo apt-get install %s"
                    % (" ".join(need), " ".join(need)))
    account(run=run, say=say)
    write_units(root, read_prefix(root))
    try:
        if not systemd():
            if run(["systemctl", "enable", UNIT]) != 0:
                raise SetupError("could not enable %s" % UNIT)
            say("systemd is not running here: the NDI helper is enabled and starts at the next boot")
            return True
        if run(["systemctl", "daemon-reload"]) != 0:
            raise SetupError("systemctl daemon-reload failed; the NDI helper was not started")
        if run(["systemctl", "enable", UNIT]) != 0:
            raise SetupError("could not enable %s" % UNIT)
        if run(["systemctl", "restart", UNIT]) != 0:
            raise SetupError("could not start %s; see: journalctl -u pvj-ndi -n 30" % UNIT)
    except SetupError:
        if not was:                        # the box is put back to not set up: no mark, no unit, nothing enabled
            run(["systemctl", "disable", "--now", UNIT] if systemd() else ["systemctl", "disable", UNIT])
            for line in remove_units(root):
                say(line)
            if systemd():
                run(["systemctl", "daemon-reload"])
            say("the setup failed and was undone: this box is not set up for NDI. NDI's library, the account and group %s "
                "and any package installed above stay (sudo pvj-ndi-runtime remove takes the library off)." % ACCOUNT)
        raise
    running = _stays_up(run, sleep)
    say("restarting the panel and the player so they may reach the NDI helper (the screen goes dark for a moment)")
    if run(["systemctl", "try-restart"] + list(RESTART)) != 0:
        say("the panel or the player did not restart; restart the box before using NDI")
    if wet:
        say(OVERLAY_WARNING)
    if running:
        say("NDI is set up on this box: the helper pvj-ndi is installed and was running %d seconds after its start. "
            "Switch the input on under System > NDI input." % STAYS_UP_SECONDS)
    else:
        say("NDI is set up on this box, but the helper pvj-ndi is NOT running: it was started and did not stay up. "
            "See: journalctl -u pvj-ndi -n 30. The setup was left in place (sudo pvj-ndi-runtime remove takes it off).")
    return running


def disable(run=_run, say=print, systemd=_systemd_runs, root=""):
    """Opt this box out again: no helper, no unit, nothing of NDI in the panel's and the player's units. What cannot
    be removed does not stop the rest; it is said, and SetupError is raised at the end. `root` is for the tests."""
    was = opted_in(root) or os.path.lexists(root + UNIT_PATH)
    if os.path.lexists(root + UNIT_PATH):
        run(["systemctl", "disable", "--now", UNIT] if systemd() else ["systemctl", "disable", UNIT])
    left = remove_units(root)
    for line in left:
        say(line)
    if systemd():
        run(["systemctl", "daemon-reload"])
        if was:
            say("restarting the panel and the player without the NDI helper's group (the screen goes dark for a moment)")
            if run(["systemctl", "try-restart"] + list(RESTART)) != 0:
                say("the panel or the player did not restart; restart the box")
    say("the NDI helper is removed from this box. Left in place: the account and group %s (sudo deluser %s; sudo delgroup %s) "
        "and the packages avahi-daemon and libavahi-client3, which may have been here before and which other things may use "
        "(a box with avahi-daemon answers to its name on the network; sudo apt-get remove avahi-daemon takes it off)."
        % (ACCOUNT, ACCOUNT, GROUP))
    if left:
        raise SetupError("not everything of the NDI helper could be removed: " + "; ".join(left))


def refresh_main(argv, out=sys.stdout):
    """`pvj-ndi-runtime refresh [--root DIR] [--prefix DIR]`: the installer's call. Not for people. DIR and every
    folder above it are trusted: it is the installer's own --stage folder, never a path somebody else can write."""
    root, prefix, argv = "", None, list(argv)
    while argv:
        if len(argv) >= 2 and argv[0] == "--root":
            root = argv[1].rstrip("/")
        elif len(argv) >= 2 and argv[0] == "--prefix":
            prefix = argv[1]
        else:
            print("usage: pvj-ndi-runtime refresh [--root DIR] [--prefix DIR]\n"
                  "The installer's call. DIR is the installer's own --stage folder and is trusted, with every folder above it.", file=out)
            return 2
        argv = argv[2:]
    if not root and os.geteuid() != 0:
        print("pvj-ndi-runtime: run it with sudo", file=out)
        return 1
    try:
        if refresh(root, prefix, say=lambda m: print("pvj-ndi-runtime: %s" % m, file=out)):
            print("pvj-ndi-runtime: this box has NDI set up; its helper's unit was written again", file=out)
        return 0
    except (SetupError, OSError) as e:
        print("pvj-ndi-runtime: %s" % e, file=out)
        return 1
