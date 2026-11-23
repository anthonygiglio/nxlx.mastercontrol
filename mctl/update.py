# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Safe updates and rollback.

A release is a signed tar.gz bundle. `apply` checks it (SHA-256, an OpenSSH
Ed25519 signature made with `ssh-keygen -Y sign`, safe extraction, version,
schema, disk space), backs up settings, installs it with install/install.sh
(new folder under /opt/nxlx/releases, atomic switch of /opt/nxlx/current),
restarts the services and checks that the panel answers. If it does not,
the previous release and the settings backup are put back automatically.

`rollback` does the same by hand. Settings migrations only go forward, so a
rollback restores the settings backup taken before the update; changes made
since then are set aside, not deleted.
"""

import glob
import hashlib
import errno
import json
import os
import pwd
import re
import secrets
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.request

from . import paths, rootfs

PRINCIPAL = "nxlx-release"
NAMESPACE = "nxlx-release"
MAX_BUNDLE_BYTES = 300 * 1024 * 1024
MAX_FILES = 5000
KEEP_BACKUPS = 5
DEFAULT_RESULT = paths.UPDATE_RESULT     # what the panel's Updates card reads (nxlx-update-*@.service)
_VERSION = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)$")


NO_PREVIOUS = "there is no previous release to go back to"
SCRATCH_PREFIX = ".update-"
SCRATCH_NAME = re.compile(r"\.update-[a-z0-9_]{8}")     # what tempfile.mkdtemp(prefix=SCRATCH_PREFIX) makes


class UpdateError(Exception):
    pass


def parse_version(text):
    m = _VERSION.match(text or "")
    if not m:
        raise UpdateError("bad version %r (expected N.N.N)" % (text,))
    return tuple(int(x) for x in m.groups())


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_sha256(path, expected):
    tokens = (expected or "").split()
    expected = tokens[0].lower() if tokens else ""
    if not re.match(r"^[0-9a-f]{64}$", expected):
        raise UpdateError("no valid SHA-256 given")
    if sha256_file(path) != expected:
        raise UpdateError("checksum does not match; the file is damaged or was changed")


def verify_signature(path, sig_path, allowed_signers, run=subprocess.run):
    try:
        with open(allowed_signers) as f:
            trusted = "".join(line for line in f if line.strip() and not line.lstrip().startswith("#"))
    except OSError:
        trusted = ""
    if not trusted:
        raise UpdateError("no trusted signing key installed (%s is missing or empty)" % allowed_signers)
    if not os.path.isfile(sig_path):
        raise UpdateError("signature file %s is missing" % sig_path)
    with open(path, "rb") as data:
        r = run(["ssh-keygen", "-Y", "verify", "-f", allowed_signers, "-I", PRINCIPAL, "-n", NAMESPACE, "-s", sig_path],
                stdin=data, capture_output=True, text=True)
    if r.returncode != 0:
        raise UpdateError("signature check failed: %s" % (r.stderr or r.stdout).strip())


def open_untrusted(name, dir_fd=None, limit=MAX_BUNDLE_BYTES):
    """Open a file someone else controls (an upload, a USB stick): never through a symlink, never a device or a
    FIFO (O_NONBLOCK keeps a FIFO from blocking the open), and refused when larger than `limit`. Returns a binary
    file object, or None when there is no such file."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise UpdateError("cannot open %s: %s" % (os.path.basename(name), e.strerror or e))
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise UpdateError("%s is not a regular file" % os.path.basename(name))
    if st.st_size > limit:
        os.close(fd)
        raise UpdateError("%s is too large" % os.path.basename(name))
    return os.fdopen(fd, "rb")


def copy_bounded(src, dest, limit):
    """Copy at most `limit` bytes from an open file into a new private file; fail if there is more (the file grew
    after it was opened)."""
    got = 0
    with open(dest, "xb") as out:
        while True:
            chunk = src.read(1 << 20)
            if not chunk:
                break
            got += len(chunk)
            if got > limit:
                raise UpdateError("the file grew while it was being copied")
            out.write(chunk)


SIDE_MAX = 64 * 1024     # a .sig or .sha256 file


def safe_extract(bundle, dest):
    """Unpack only plain files and folders, staying inside dest. Returns the file count."""
    count = total = 0
    with tarfile.open(bundle, "r:gz") as tar:
        members = tar.getmembers()
        if len(members) > MAX_FILES:
            raise UpdateError("bundle has too many files")
        tops = {m.name.split("/")[0] for m in members if m.name not in (".", "")}
        strip = None
        if len(tops) == 1:
            only = next(iter(tops))
            # a single top-level folder (nxlx-1.2.3/) is stripped; a lone file is not
            if any(m.name.startswith(only + "/") for m in members):
                strip = only
        for m in members:
            # Judge the name exactly as stored, before any folder is stripped from it.
            raw = m.name
            if os.path.isabs(raw) or ".." in raw.split("/") or "\\" in raw or "\0" in raw:
                raise UpdateError("unsafe path in bundle: %r" % m.name)
            name = raw
            if name in (".", ""):
                continue
            if strip and (name == strip or name.startswith(strip + "/")):
                name = name[len(strip):].lstrip("/")
                if not name:
                    continue
            if not (m.isfile() or m.isdir()):
                raise UpdateError("bundle contains a link or special file: %r" % m.name)
            target = os.path.join(dest, name)
            if m.isdir():
                os.makedirs(target, mode=0o755, exist_ok=True)
                continue
            total += m.size
            count += 1
            if total > MAX_BUNDLE_BYTES:
                raise UpdateError("bundle is too large when unpacked")
            os.makedirs(os.path.dirname(target), mode=0o755, exist_ok=True)
            with tar.extractfile(m) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            os.chmod(target, 0o755 if m.mode & 0o111 else 0o644)  # never setuid or group/world-writable
    return count


def inspect_bundle(directory):
    """Version and settings schema of an unpacked bundle, without importing its code."""
    def read(rel):
        try:
            with open(os.path.join(directory, rel)) as f:
                return f.read()
        except OSError:
            raise UpdateError("bundle is missing %s" % rel)
    # A release from before the rename of the system names (rename PR 7) runs as pvj-* accounts from /opt/pvj: its
    # installer would put a second, old layout beside the converted one. Going back to it is the converter's undo,
    # which brings the old names back too; an update never installs it.
    if not os.path.isfile(os.path.join(directory, "bin", "nxlx-player")) and (
            os.path.isfile(os.path.join(directory, "bin", "pvj-player"))
            or os.path.isfile(os.path.join(directory, "pvj", "__init__.py"))):
        raise UpdateError("this bundle is a release from before the rename of the system names (pvj to nxlx) and "
                          "cannot be installed on this node; to go back to that release: sudo nxlx-convert --undo")
    version = re.search(r'^__version__ = "([^"]+)"', read("mctl/__init__.py"), re.M)
    schema = re.search(r"^SCHEMA = (\d+)", read("mctl/settings.py"), re.M)
    if not version or not schema:
        raise UpdateError("cannot read version or schema from the bundle")
    for rel in ("bin/nxlx-player", "bin/nxlx-web", "install/install.sh"):
        if not os.path.isfile(os.path.join(directory, rel)):
            raise UpdateError("bundle is missing %s" % rel)
    parse_version(version.group(1))
    return {"version": version.group(1), "schema": int(schema.group(1))}


def require_writable_root(state=None):
    """Raise UpdateError while the read-only root (nxlx-rootfs) is on. `state` returns "overlay" or "normal"."""
    if (state or rootfs.current_state)() == "overlay":
        raise UpdateError(rootfs.UPDATE_REFUSAL)


class Updater:
    def __init__(self, root="/", prefix="/opt/nxlx", state_dir="/var/lib/nxlx", etc_dir="/etc/nxlx",
                 root_state_dir=paths.ROOT_STATE_DIR, run=subprocess.run, restart=None, health=None, install=None, now=time.time, rootfs_state=None):
        self.root = root.rstrip("/")
        self.prefix = prefix
        self.state_dir = state_dir
        self.root_state_dir = root_state_dir
        self.etc_dir = etc_dir
        self.run = run
        self._restart = restart or self._systemd_restart
        self._health = health or self._http_health
        self._install = install or self._run_installer
        self.now = now
        self._rootfs_state = rootfs_state or rootfs.current_state

    def require_writable_root(self):
        """Refuse while the read-only root is on: everything an update or a rollback writes (the release, the units,
        `current`, the settings backups) lands in RAM and is gone at the next restart, with no word of it."""
        require_writable_root(self._rootfs_state)

    # --- paths ----------------------------------------------------------
    def real(self, p):
        return self.root + p

    @property
    def current_link(self):
        return self.real(self.prefix + "/current")

    def allowed_signers(self):
        return self.real(self.etc_dir + "/allowed_signers")

    def settings_path(self):
        return self.real(self.state_dir + "/settings.json")

    def backup_dir(self):
        """Root's own folder (J-178), outside the panel's state folder: the settings copies an update keeps, and the clip
        stick's claim. The panel's account cannot enter it, so it cannot hide, swap or put back what is in it."""
        return self.real(self.root_state_dir)

    # --- state ----------------------------------------------------------
    def _link_version(self):
        try:
            return os.path.basename(os.readlink(self.current_link))
        except OSError:
            return None

    def status(self):
        rel = self.real(self.prefix + "/releases")
        releases = sorted((d for d in os.listdir(rel) if _VERSION.match(d)), key=parse_version) if os.path.isdir(rel) else []
        prev = None
        try:
            with open(self.real(self.prefix + "/previous")) as f:
                prev = os.path.basename(f.read().strip())
        except OSError:
            pass
        return {"current": self._link_version(), "previous": prev, "releases": releases}

    # --- the panel's state folder -----------------------------------------------------------------------------------
    # /var/lib/nxlx belongs to the panel's account and has no sticky bit, so that account can rename, delete or swap any
    # name in it, a folder for a link included. Everything below therefore works on descriptors: the state folder and the
    # backups folder are opened with O_NOFOLLOW|O_DIRECTORY, every name is then handled relative to the open folder
    # (dir_fd), files are made with O_EXCL|O_NOFOLLOW, and owners and modes are set on the descriptor, never on a path.
    _DIR = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    BACKUP_NAME = re.compile(r"settings-[0-9A-Za-z.+-]{1,40}-[0-9]{1,12}\.json")     # what backup_settings writes (nxlx-sysd clears the same names)
    SETTINGS_MAX = 4 << 20           # settings.json is JSON text (pictures are kept in files of their own); more than this is not what it seems

    def state_dir_real(self):
        return self.settings_path().rsplit("/", 1)[0] or "/"

    def _open_state(self):
        """The panel's state folder as a descriptor (the caller closes it), or None when there is none. The last name is
        never followed: the folder's parent is root's."""
        try:
            return os.open(self.settings_path().rsplit("/", 1)[0] or "/", self._DIR)
        except OSError:
            return None

    def _open_backups(self, create=False):
        """Root's own folder (J-178) as a descriptor: opened without following a link, and it must be a folder of this
        process's user (root on a box) that nobody else can write. Its parent (/var/lib) is root's, so no one else can
        plant a name there; a link, a file, a foreign owner or a loose mode is therefore not something to move out of the
        way but a fault to report: None is returned and nothing is touched. With `create` a missing folder is made, mode
        0700, and that mode is set on the descriptor of the folder just checked."""
        path = self.backup_dir()
        made = False
        for _attempt in range(2):
            try:
                fd = os.open(path, self._DIR)
            except FileNotFoundError:
                if not create or made:
                    return None
                try:
                    os.mkdir(path, 0o700)
                except FileExistsError:
                    pass
                except OSError:
                    return None
                made = True
                continue
            except OSError:
                return None                          # a link (ELOOP), not a folder (ENOTDIR), no descriptors left
            st = os.fstat(fd)
            if stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid() and not st.st_mode & 0o022:
                if made:
                    try:
                        os.fchmod(fd, 0o700)         # whatever the umask said
                    except OSError:
                        os.close(fd)
                        raise UpdateError("the settings backups folder could not be set up")
                return fd
            os.close(fd)
            return None
        return None

    def _read_plain(self, name, dir_fd):
        """The bytes of a plain file inside an open folder, or None: never through a link, never a pipe or a device,
        bounded."""
        try:
            fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0),
                         dir_fd=dir_fd)
        except OSError:
            return None
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_size > self.SETTINGS_MAX:
                return None
            chunks, left = [], self.SETTINGS_MAX + 1
            while left > 0:
                block = os.read(fd, min(left, 1 << 20))
                if not block:
                    break
                chunks.append(block)
                left -= len(block)
            data = b"".join(chunks)
            return data if len(data) <= self.SETTINGS_MAX else None
        except OSError:
            return None
        finally:
            os.close(fd)

    def _settings_schema(self):
        state_fd = self._open_state()
        if state_fd is None:
            return None
        try:
            data = self._read_plain("settings.json", state_fd)
        finally:
            os.close(state_fd)
        try:
            return int(json.loads(data.decode("utf-8")).get("schema", 0)) if data is not None else None
        except (ValueError, AttributeError, TypeError, OverflowError, RecursionError):
            return None

    # --- backups --------------------------------------------------------
    def backup_settings(self, version, log=print):
        """Copy settings.json into the root-only backups folder. settings.json is the panel's file: it is read without
        following a link (a link, a pipe or a device there means no backup, not a copy of what it points to), and the
        copy is made with O_EXCL inside the folder that was opened and checked, so no planted name can redirect it."""
        state_fd = self._open_state()
        if state_fd is None:
            return None
        try:
            data = self._read_plain("settings.json", state_fd)
            if data is None:
                if os.path.lexists(os.path.join(self.state_dir_real(), "settings.json")):
                    log("settings.json is not a plain file of at most %d MiB (a link, a pipe or too large), so no copy of the "
                        "settings is kept for this update and the settings schema check is skipped" % (self.SETTINGS_MAX >> 20))
                return None
            bfd = self._open_backups(create=True)
            if bfd is None:
                raise UpdateError("the settings backups folder is not usable (it is not a folder of root's that only root can write)")
            try:
                name = "settings-%s-%d.json" % (version, int(self.now()))
                if not self.BACKUP_NAME.fullmatch(name):
                    raise UpdateError("not a version that can be backed up: %r" % version)
                self._write_new(bfd, name, data, 0o600)
                old = []
                for entry in os.listdir(bfd):
                    if self.BACKUP_NAME.fullmatch(entry):
                        try:
                            st = os.lstat(entry, dir_fd=bfd)
                        except OSError:
                            continue
                        if stat.S_ISREG(st.st_mode):
                            old.append((st.st_mtime, entry))
                for _mtime, entry in sorted(old)[:-KEEP_BACKUPS]:
                    try:
                        os.unlink(entry, dir_fd=bfd)
                    except OSError:
                        pass
            finally:
                os.close(bfd)
        finally:
            os.close(state_fd)
        return os.path.join(self.backup_dir(), name)

    @staticmethod
    def _write_new(dir_fd, name, data, mode, owner=None):
        """Make `name` in the open folder with exactly `data`: a new file (O_EXCL, no link followed), owner and mode set
        on the descriptor. A name that is already taken is removed first (the name itself, a link included)."""
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(name, flags, mode, dir_fd=dir_fd)
        except FileExistsError:
            os.unlink(name, dir_fd=dir_fd)
            fd = os.open(name, flags, mode, dir_fd=dir_fd)
        try:
            os.fchmod(fd, mode)
            if owner is not None:
                os.fchown(fd, *owner)
            with os.fdopen(fd, "wb", closefd=False) as f:
                f.write(data)
                f.flush()
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            try:
                os.unlink(name, dir_fd=dir_fd)
            except OSError:
                pass
            raise
        os.close(fd)

    def restore_settings(self, backup):
        """Put a backup back as settings.json with the SAME owner as before: the panel runs as its
        own user and could not read a root-owned 0600 file, leaving the box without a panel.

        The backup is read from the checked backups folder (never through a link); the old settings.json is renamed
        aside by name; the new one is written under a temporary name, given its owner and mode on the descriptor and
        renamed over settings.json, so nothing a planted name points to is ever opened, chowned or chmoded."""
        state_fd = self._open_state()
        if state_fd is None:
            raise UpdateError("the panel's state folder is not usable")
        try:
            bfd = self._open_backups()
            if bfd is None:
                raise UpdateError("the settings backups folder is not usable, so the settings were not put back")
            try:
                data = self._read_plain(os.path.basename(backup), bfd)
            finally:
                os.close(bfd)
            if data is None:
                raise UpdateError("the settings backup %s could not be read" % os.path.basename(backup))
            owner = None
            try:
                st = os.stat("settings.json", dir_fd=state_fd, follow_symlinks=False)
            except OSError:
                st = None
            if st is not None and stat.S_ISREG(st.st_mode) and st.st_uid != 0:
                owner = (st.st_uid, st.st_gid)
            if owner is None:
                try:
                    web = pwd.getpwnam("nxlx-web")
                    owner = (web.pw_uid, web.pw_gid)
                except KeyError:
                    pass
            # The new file is written first; only then is the old one kept (a link to it, else a copy) and the new one
            # renamed over settings.json. A failure before the rename leaves the old settings.json in place.
            tmp = ".settings.json.%s.tmp" % secrets.token_hex(6)
            self._write_new(state_fd, tmp, data, 0o600, owner=owner if os.geteuid() == 0 else None)
            try:
                if st is not None:
                    keep = "settings.json.rolled-back-%d" % int(self.now())
                    try:
                        os.unlink(keep, dir_fd=state_fd)
                    except OSError:
                        pass
                    try:
                        os.link("settings.json", keep, src_dir_fd=state_fd, dst_dir_fd=state_fd, follow_symlinks=False)
                    except OSError:
                        pass                          # no keepsake; the old one is replaced by the rename below
                os.rename(tmp, "settings.json", src_dir_fd=state_fd, dst_dir_fd=state_fd)
            except BaseException:
                try:
                    os.unlink(tmp, dir_fd=state_fd)
                except OSError:
                    pass
                raise
        finally:
            os.close(state_fd)

    def latest_backup(self, version):
        state_fd = self._open_state()
        if state_fd is None:
            return None
        try:
            bfd = self._open_backups()
            if bfd is None:
                return None
            try:
                exact = re.compile(r"settings-%s-[0-9]{1,12}\.json" % re.escape(str(version)))
                found = []
                for entry in os.listdir(bfd):
                    if exact.fullmatch(entry):
                        try:
                            st = os.lstat(entry, dir_fd=bfd)
                        except OSError:
                            continue
                        if stat.S_ISREG(st.st_mode):
                            found.append((st.st_mtime, entry))
            finally:
                os.close(bfd)
        finally:
            os.close(state_fd)
        return os.path.join(self.backup_dir(), sorted(found)[-1][1]) if found else None

    # --- one-time move out of the panel's folder (J-178) -------------------------------------------------------------
    LEGACY_CLAIM = "clip-stick.json"       # was root's claim record in the panel's state folder; now in root's own folder
    CLAIM_MAX = 4096

    def _read_root_file(self, name, dir_fd, limit):
        """The bytes of a file in an open folder and its stat, only when it is a plain file of this process's user (root)
        with one name, that its group and others cannot read or write, of at most `limit` bytes. Never through a link.
        (None, None) otherwise."""
        try:
            fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0),
                         dir_fd=dir_fd)
        except OSError:
            return None, None
        try:
            st = os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077 or st.st_nlink != 1
                    or st.st_size > limit):
                return None, None
            data = os.read(fd, limit + 1)
            return (data, st) if len(data) <= limit else (None, None)
        except OSError:
            return None, None
        finally:
            os.close(fd)

    @staticmethod
    def _rename_aside(dir_fd, name, tag):
        """Move a name in the open folder away by rename (which follows nothing) so that it is no longer there to be used;
        the new name has a random part, so a name planted in advance cannot make this fail every time."""
        for _try in range(5):
            try:
                os.rename(name, "%s.%s-%s" % (name, tag, secrets.token_hex(6)), src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
                return True
            except FileNotFoundError:
                return True
            except OSError:
                continue
        return False

    def migrate_legacy_state(self, log=print):
        """Move root's two things out of the panel's state folder, once (J-178): the settings copies in `backups` and the
        clip stick's claim record `clip-stick.json`. The panel's account can rename anything in its own folder, so what
        stayed there could be hidden before a factory reset and put back after it.

        Everything is done on descriptors (O_NOFOLLOW|O_DIRECTORY, names relative to the open folder). A copy or the record
        is taken only if it is a plain file of root's with one name that nobody else can read or write (what the updater
        wrote); anything else, a link, a file of the panel's, is not followed or kept, and a `backups` folder that is a link,
        not a folder, or not root's is renamed aside, never opened for its contents. Files are copied into root's folder
        first and removed from the old one after. Returns the number of files moved; problems are logged, never raised."""
        state_fd = self._open_state()
        if state_fd is None:
            return 0
        moved = 0
        try:
            old_backups = old_claim = False
            try:
                os.lstat("backups", dir_fd=state_fd)
                old_backups = True
            except OSError:
                pass
            try:
                os.lstat(self.LEGACY_CLAIM, dir_fd=state_fd)
                old_claim = True
            except OSError:
                pass
            nfd = self._open_backups(create=True)
            if nfd is None:
                if old_backups or old_claim:
                    log("root's folder %s is not usable, so what is left in the panel's folder was not moved" % self.root_state_dir)
                return 0
            try:
                # One time only: the first run leaves a marker in root's folder, also when it found nothing. From then on
                # whatever stands under the old names (a folder the panel hid and put back after a factory reset, a claim
                # it kept) is set aside or removed, never imported: the panel cannot replay old files through this.
                try:
                    os.lstat(paths.ROOT_STATE_MARKER, dir_fd=nfd)
                    done = True
                except OSError:
                    done = False
                if done:
                    if old_claim:
                        try:
                            os.unlink(self.LEGACY_CLAIM, dir_fd=state_fd)
                        except OSError:
                            pass
                    if old_backups:
                        try:
                            os.rmdir("backups", dir_fd=state_fd)
                        except OSError:
                            self._rename_aside(state_fd, "backups", "stale")
                    if old_backups or old_claim:
                        log("the move out of the panel's folder was done before: what is left there under the old names was set aside, not imported")
                    return 0
                if old_claim:
                    data, _st = self._read_root_file(self.LEGACY_CLAIM, state_fd, self.CLAIM_MAX)
                    ok = False
                    if data is not None:
                        try:
                            ok = isinstance(json.loads(data.decode("utf-8")), dict)
                        except (UnicodeDecodeError, ValueError, RecursionError):
                            ok = False
                    try:
                        os.lstat(self.LEGACY_CLAIM, dir_fd=nfd)
                        have = True
                    except OSError:
                        have = False
                    if ok and not have:
                        self._write_new(nfd, self.LEGACY_CLAIM, data, 0o600)
                        moved += 1
                        log("the clip stick claim was moved out of the panel's folder (a release older than this one reads it in the panel's folder and will not find it: claim the stick again with sudo nxlx-usb claim after such a rollback)")
                    elif not ok:
                        log("clip-stick.json in the panel's folder is not a file of root's that only root can use, so it was dropped, not moved")
                    try:
                        os.unlink(self.LEGACY_CLAIM, dir_fd=state_fd)
                    except OSError:
                        pass
                if old_backups:
                    moved += self._migrate_backups(state_fd, nfd, log)
                try:
                    mfd = os.open(paths.ROOT_STATE_MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=nfd)
                    os.close(mfd)
                except FileExistsError:
                    pass
            finally:
                os.close(nfd)
        except OSError as e:
            log("moving root's files out of the panel's folder stopped: %s" % (e.strerror or e))
        finally:
            os.close(state_fd)
        return moved

    def _migrate_backups(self, state_fd, nfd, log):
        moved = 0
        try:
            ofd = os.open("backups", self._DIR, dir_fd=state_fd)
        except FileNotFoundError:
            return 0
        except OSError:                                          # a link, or not a folder: never followed
            self._rename_aside(state_fd, "backups", "rejected")
            log("backups in the panel's folder was not a folder; it was set aside, not followed")
            return 0
        trusted = False
        try:
            st = os.fstat(ofd)
            trusted = stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid() and not st.st_mode & 0o022
            if trusted:
                for entry in sorted(os.listdir(ofd)):
                    if not self.BACKUP_NAME.fullmatch(entry):
                        continue
                    data, fst = self._read_root_file(entry, ofd, self.SETTINGS_MAX)
                    if data is None:
                        continue
                    try:
                        os.lstat(entry, dir_fd=nfd)
                    except FileNotFoundError:
                        self._write_new(nfd, entry, data, 0o600)
                        try:
                            os.utime(entry, ns=(fst.st_atime_ns, fst.st_mtime_ns), dir_fd=nfd, follow_symlinks=False)
                        except (OSError, NotImplementedError):
                            pass
                        moved += 1
                    try:
                        os.unlink(entry, dir_fd=ofd)
                    except OSError:
                        pass
        finally:
            os.close(ofd)
        try:
            os.rmdir("backups", dir_fd=state_fd)               # empty: gone
        except OSError:
            self._rename_aside(state_fd, "backups", "old" if trusted else "rejected")
        log("%d settings cop%s moved out of the panel's folder%s" % (moved, "y" if moved == 1 else "ies", "" if trusted else " (the old folder was not root's: its contents were not used)"))
        return moved

    # --- actions --------------------------------------------------------
    def check(self, bundle, sha256=None, allow_unsigned=False, force=False, dir_fd=None):
        """Verify a bundle without installing it. Returns (info, tree, scratch).

        The bundle, its signature and its checksum are opened without following links (regular files only, size
        limited) and copied into a private folder; everything (checksum, signature, unpacking) is done on that
        copy, so a file on a USB stick or in the upload folder cannot be swapped between the check and the use.
        With `dir_fd`, `bundle` is a name inside that (already opened) folder. A signed bundle needs no checksum
        (the signature covers the content); an unsigned one (development only) must have one. The caller removes
        `scratch`."""
        self.require_writable_root()
        src = open_untrusted(bundle, dir_fd)
        if src is None:
            raise UpdateError("%s not found" % os.path.basename(bundle))
        scratch = None
        try:
            size = os.fstat(src.fileno()).st_size
            if sha256 is None:
                side = open_untrusted(bundle + ".sha256", dir_fd, SIDE_MAX)
                if side is not None:
                    with side:
                        sha256 = side.read(4096).decode("ascii", "replace")
            # The scratch folder is inside the install, never in the system temp folder: on a box /tmp is small
            # or in memory, and the free space measured below has to be that of the disk the release goes to
            # (D70). Where nothing is installed yet the folder is made, as the installer would a moment later.
            parent = self.real(self.prefix)
            try:
                os.makedirs(parent, mode=0o755, exist_ok=True)
                scratch = tempfile.mkdtemp(prefix=SCRATCH_PREFIX, dir=parent)  # mode 0700
            except OSError as e:
                raise UpdateError("cannot make a work folder in %s: %s" % (self.prefix, e.strerror or e))
            if shutil.disk_usage(scratch).free < 4 * size:
                raise UpdateError("not enough free disk space")
            mine = os.path.join(scratch, "bundle.tar.gz")
            copy_bounded(src, mine, MAX_BUNDLE_BYTES)
            sig = open_untrusted(bundle + ".sig", dir_fd, SIDE_MAX)
            if sig is not None:
                with sig:
                    copy_bounded(sig, mine + ".sig", SIDE_MAX)
            if sha256 is not None or allow_unsigned:
                verify_sha256(mine, sha256)
            if not allow_unsigned:
                verify_signature(mine, mine + ".sig", self.allowed_signers(), self.run)
            tree = os.path.join(scratch, "tree")
            os.mkdir(tree)
            safe_extract(mine, tree)
            info = inspect_bundle(tree)
            cur = self._link_version()
            if cur and not force:
                if parse_version(info["version"]) <= parse_version(cur):
                    raise UpdateError("version %s is not newer than the installed %s (use --force to reinstall)"
                                      % (info["version"], cur))
            schema = self._settings_schema()
            if schema is not None and info["schema"] < schema and not force:
                raise UpdateError("this bundle understands settings schema %d but yours is %d; refusing to downgrade"
                                  % (info["schema"], schema))
            return info, tree, scratch
        except BaseException:
            if scratch:
                shutil.rmtree(scratch, ignore_errors=True)
            raise
        finally:
            src.close()

    def sweep_scratch(self, owner=0):
        """Remove the work folders that an update cut off by a power cut left in the install (the bundle and its
        unpacked tree, up to hundreds of megabytes each, and nothing else ever removed them). Call it only while
        holding the update lock: then no update is running and every such folder is a leftover. Only a real folder
        (never a link) directly in the install, named as check() names them and owned by `owner` (root on a box)
        is removed. An install that is a link is swept where it really is, because check() makes its folder there;
        wherever it is, it has to be a folder of `owner` that neither its group nor anybody else can write, or
        nothing is removed. Everything is opened without following a link, looked at through what was opened and
        removed through it, so no path is resolved a second time between the look and the removal. Returns the
        names removed; never raises (D70)."""
        gone = []
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(os.path.realpath(self.real(self.prefix)), flags)
        except OSError:
            return gone
        try:
            top = os.fstat(fd)
            if not stat.S_ISDIR(top.st_mode) or top.st_uid != owner or stat.S_IMODE(top.st_mode) & 0o022:
                return gone
            for name in os.listdir(fd):
                if not SCRATCH_NAME.fullmatch(name):
                    continue
                try:
                    sub = os.open(name, flags, dir_fd=fd)
                except OSError:                 # a link, a file, or gone
                    continue
                try:
                    st = os.fstat(sub)
                    if not stat.S_ISDIR(st.st_mode) or st.st_uid != owner:
                        continue
                    # Bottom up, each folder through its own descriptor. A link is a name to unlink, whatever it
                    # points to: fwalk lists a link to a folder among the folders but does not go into it.
                    for _path, dirs, files, at in os.fwalk(".", dir_fd=sub, topdown=False, follow_symlinks=False):
                        for entry in files:
                            os.unlink(entry, dir_fd=at)
                        for entry in dirs:
                            try:
                                os.rmdir(entry, dir_fd=at)
                            except NotADirectoryError:
                                os.unlink(entry, dir_fd=at)
                    os.rmdir(name, dir_fd=fd)   # fails if the name is no longer the (now empty) folder
                    gone.append(name)
                except OSError:
                    pass
                finally:
                    os.close(sub)
        except OSError:
            pass
        finally:
            os.close(fd)
        return sorted(gone)

    def _previous_file(self):
        return self.real(self.prefix + "/previous")

    def _read_previous(self):
        try:
            with open(self._previous_file()) as f:
                return f.read()
        except OSError:
            return None

    def _restore_previous(self, text):
        if text is None:
            try:
                os.unlink(self._previous_file())
            except OSError:
                pass
        else:
            with open(self._previous_file(), "w") as f:
                f.write(text)

    settings_problem = None         # why the last switch back could not put the settings back (None: it did, or none was asked)

    def apply(self, bundle, sha256=None, allow_unsigned=False, force=False, log=print, dir_fd=None):
        self.require_writable_root()
        self.settings_problem = None
        info, tree, scratch = self.check(bundle, sha256, allow_unsigned, force, dir_fd=dir_fd)
        old_version = self._link_version()
        previous_before = self._read_previous()
        self.migrate_legacy_state(log=log)
        try:
            backup = self.backup_settings(old_version or "none", log=log)
            log("installing %s (was %s)" % (info["version"], old_version or "nothing"))
            try:
                self._install(tree, info)
                self._restart()
                healthy = self._health()
            except BaseException:
                if old_version:
                    self._switch_to(old_version, backup, log=log)
                self._restore_previous(previous_before)
                raise
            if not healthy:
                log("the new version did not come up; rolling back")
                self._switch_to(old_version, backup, log=log)
                self._restore_previous(previous_before)  # a failed update must not disturb rollback history
                raise UpdateError("update to %s failed its health check and was rolled back%s" % (info["version"], self._settings_note()))
            log("update complete: %s" % info["version"])
            return info["version"]
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def _settings_note(self):
        return "; the settings could not be put back (%s) and stay as they are" % self.settings_problem if self.settings_problem else ""

    def _rollback_problem(self, st):
        """Why there is nothing to go back to, in plain words, or None. The panel asks the same question before it shows the
        button, and rollback() asks it again as root: one place for the words."""
        target = st["previous"]
        if not target or not _VERSION.match(target) or not os.path.isdir(self.real("%s/releases/%s" % (self.prefix, target))):
            return NO_PREVIOUS
        # The release before the rename of the system names runs only under the old names (pvj-* units, accounts and
        # folders): switching "current" to it would leave every unit without its program. The converter's undo goes
        # back to it, and puts the old names back with it.
        if not os.path.isfile(self.real("%s/releases/%s/bin/nxlx-web" % (self.prefix, target))):
            return ("the previous release %s is from before the rename of the system names (pvj to nxlx) "
                    "and does not run under them; to go back to it: sudo nxlx-convert --undo" % target)
        return None

    def _release_view(self, version):
        if not version:
            return None
        try:
            date = int(os.stat(self.real("%s/releases/%s" % (self.prefix, version))).st_mtime)     # when it was put on the node
        except OSError:
            date = None
        return {"version": version, "date": date}

    def rollback_view(self):
        """What the panel shows before a rollback (read only, no root needed): the release that runs, the one before it
        with the day it was installed, and `refusal`, the reason there is nothing to go back to (None when there is).
        The read-only system disk is not asked here: the panel has that answer already."""
        st = self.status()
        problem = self._rollback_problem(st)
        prev = None if problem == NO_PREVIOUS else self._release_view(st["previous"])
        return {"current": self._release_view(st["current"]), "previous": prev, "refusal": problem}

    def rollback(self, log=print, expect=None):
        """Go back to the previous release. `expect` is the version the person was shown and confirmed (the panel): if the
        previous release is another one by now (an update ran in between), nothing is done."""
        self.require_writable_root()
        self.settings_problem = None
        st = self.status()
        problem = self._rollback_problem(st)
        if problem:
            raise UpdateError(problem)
        target = st["previous"]
        if expect is not None and expect != target:
            raise UpdateError("the release before this one is %s now, not %s as was shown; look at the Updates card again" % (target, expect))
        log("rolling back %s -> %s" % (st["current"], target))
        self.migrate_legacy_state()
        backup = self.latest_backup(target)
        if backup is None:
            # Normal after a factory reset, which removes every saved copy (J-055): the program goes back, the settings stay as they are.
            log("there is no saved copy of the settings for %s (a factory reset removes them), so the settings stay as they are" % target)
        self._switch_to(target, backup, current=st["current"], log=log)
        if not self._health():
            raise UpdateError("rolled back, but the panel did not answer; check the services%s" % self._settings_note())
        return target

    def _switch_to(self, version, backup, current=None, log=print):
        if version is None:
            raise UpdateError("nothing to switch back to")
        tmp = self.current_link + ".tmp"
        if os.path.lexists(tmp):
            os.unlink(tmp)
        os.symlink("%s/releases/%s" % (self.prefix, version), tmp)
        os.replace(tmp, self.current_link)
        if backup:
            try:
                self.restore_settings(backup)
            except (UpdateError, OSError) as e:
                # The program is switched already: the restart and the note of the previous release still have to
                # happen, or unhealthy processes keep running and the rollback history is lost.
                self.settings_problem = str(e.strerror or e) if isinstance(e, OSError) else str(e)
                log("the settings could not be put back (%s); they stay as they are" % self.settings_problem)
        if current:
            with open(self.real(self.prefix + "/previous"), "w") as f:
                f.write("%s/releases/%s\n" % (self.prefix, current))
        self._restart()

    # --- USB ------------------------------------------------------------
    BUNDLE_NAME = re.compile(r"nxlx-(\d+\.\d+\.\d+)\.tar\.gz")

    def open_inbox(self):
        """The upload folder, opened without following a link: nxlx-web owns /var/lib/nxlx and could replace the
        folder with a link to somewhere root must not touch. Returns a directory fd (the caller closes it), or
        None when there is no inbox."""
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.real(self.state_dir + "/update-inbox"), flags)
        except FileNotFoundError:
            return None
        except OSError as e:
            raise UpdateError("the upload folder is not usable: %s" % (e.strerror or e))
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            os.close(fd)
            raise UpdateError("the upload folder is not a folder")
        return fd

    def inbox_bundles(self, dir_fd):
        """Names of bundles uploaded through the panel in the opened inbox, newest first. Untrusted: check() opens
        each without following links and verifies a private copy."""
        found = []
        for name in os.listdir(dir_fd):
            m = self.BUNDLE_NAME.fullmatch(name)
            if m:
                found.append((parse_version(m.group(1)), name))
        return [n for _, n in sorted(found, reverse=True)]

    def clear_inbox(self, dir_fd):
        """Remove what was uploaded, whatever the outcome: a bundle is tried once. Only inside the opened folder."""
        for name in os.listdir(dir_fd):
            if name.startswith(("nxlx-", "pvj-")):      # also what an old name left behind: cleaned, never taken
                try:
                    os.unlink(name, dir_fd=dir_fd)
                except OSError:
                    pass

    def usb_bundles(self, base="/media/nxlx"):
        found = []
        for path in glob.glob(os.path.join(base, "*", "nxlx-update", "*.tar.gz")):
            m = self.BUNDLE_NAME.fullmatch(os.path.basename(path))
            if m:
                found.append((parse_version(m.group(1)), path))
        return [p for _, p in sorted(found, reverse=True)]

    # --- defaults -------------------------------------------------------
    def _run_installer(self, work, info):
        args = [os.path.join(work, "install", "install.sh"), "--offline", "--no-start", "--prefix", self.prefix]
        r = self.run(args, capture_output=True, text=True)
        if r.returncode != 0:
            raise UpdateError("installer failed: %s" % (r.stderr or r.stdout).strip()[-500:])

    def _systemd_restart(self):
        if os.path.isdir("/run/systemd/system") and self.root == "":
            # The network helper too, only if it is running (try-restart starts nothing): its socket's place is
            # part of a release (mctl/paths.py), and the panel must find the helper that matches it. Not nxlx-sysd
            # (it started this update; it is restarted last, below) and not nxlx-supportd (a support session may be the one updating).
            # The NDI helper likewise, which exists only on a box that opted in (mctl/ndisetup.py): after a switch to
            # another release it must run that release's code, like the panel that asks it.
            # Two calls, so that the network helper's restart does not depend on what systemctl makes of a unit
            # that most boxes do not have.
            self.run(["systemctl", "try-restart", "nxlx-netd.service"], capture_output=True)
            self.run(["systemctl", "try-restart", "nxlx-ndi.service"], capture_output=True)
            # The Link helper too: nothing else stops it after a rollback to a release without Link, where the old
            # panel cannot tell it off and it would stay on the network; the unit's condition keeps it down there.
            self.run(["systemctl", "try-restart", "nxlx-link.service"], capture_output=True)
            self.run(["systemctl", "restart", "nxlx-player.service", "nxlx-web.service"], capture_output=True)
            # The root helper last (J-055): a running nxlx-sysd keeps the code it started with, so an action a new release adds
            # (clear_update_backups) would answer "unknown command" until the next start. Safe here: the update runs in its own unit
            # (nxlx-update-*@.service, a separate cgroup), not inside the helper, and the panel has been restarted already. --no-block:
            # nothing waits for it, and try-restart starts nothing where the helper is not running.
            self.run(["systemctl", "try-restart", "--no-block", "nxlx-sysd.service"], capture_output=True)

    def configured_port(self):
        """The panel's port as the service sees it: /etc/nxlx/nxlx.env, else our environment, else 80."""
        try:
            with open(self.real(self.etc_dir + "/nxlx.env")) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("NXLX_PORT="):
                        value = line.split("=", 1)[1].strip().strip("'\"")
                        if value.isdigit():
                            return value
        except OSError:
            pass
        return os.environ.get("NXLX_PORT", "80")

    def _http_health(self, timeout=30):
        port = self.configured_port()
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%s/api/hello" % port, timeout=2) as r:
                    if r.status == 200:
                        return True
            except OSError:
                time.sleep(1)
        return False


def main(argv=None):
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="nxlx-update", description="Update or roll back nxlx.mastercontrol")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    for name in ("check", "apply"):
        p = sub.add_parser(name)
        p.add_argument("bundle")
        p.add_argument("--sha256")
        p.add_argument("--allow-unsigned", action="store_true", help="development only")
        p.add_argument("--force", action="store_true")
    for name, text in (("usb", "apply the newest bundle in <usb>/nxlx-update/"),
                       ("inbox", "apply the newest bundle uploaded through the panel")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--result", help="write the progress and outcome as JSON here (for the panel)")
        p.add_argument("--version", help="only this version (what the person confirmed in the panel)")
    p = sub.add_parser("finish", help="mark an unfinished panel update as failed (the unit's ExecStopPost)")
    p.add_argument("--result", required=True)
    p = sub.add_parser("rollback", help="go back to the previous release")
    p.add_argument("--result", help="write the progress and outcome as JSON here (for the panel)")
    p.add_argument("--version", help="only if the previous release is this one (what the person confirmed in the panel)")
    args = ap.parse_args(argv)
    if args.cmd != "status" and os.geteuid() != 0:
        print("nxlx-update: run as root (sudo)", file=sys.stderr)
        return 1
    u = Updater()
    result = getattr(args, "result", None)
    if result is None and args.cmd in ("apply", "rollback"):
        # Run from a terminal: the panel's Updates card reads this file, and it would go on saying "updated to X"
        # after a rollback. No folder is made for it here. On a box it is in the update units' folder, which is
        # also the lock's, and take_lock() makes that one where no unit has run yet; so there it is always written.
        result = os.environ.get("NXLX_UPDATE_RESULT", DEFAULT_RESULT)

    def report(state, message, version=None):
        if not result:
            return
        data = {"state": state, "message": message, "version": version, "at": int(time.time())}
        if args.cmd == "rollback":
            data["action"] = "rollback"       # the panel says "rollback", not "update", and the event log too
        tmp = result + ".tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o644)
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.replace(tmp, result)
        except OSError:
            pass

    def log(text):
        print(text, flush=True)
        report("running", text)
    if args.cmd == "finish":
        try:
            with open(result) as f:
                if json.load(f).get("state") == "running":
                    report("failed", "the update stopped before it finished")
        except (OSError, ValueError):
            pass
        return 0
    lock = None
    if args.cmd in ("check", "apply", "usb", "inbox", "rollback"):      # check too: it makes a work folder (D70)
        try:
            lock = take_lock()
        except UpdateError as e:          # without the lock nobody knows whether an update is running: do nothing,
            print("nxlx-update: %s" % e, file=sys.stderr)      # and say so where the panel reads it (it has already
            report("failed", str(e))                          # answered "started")
            return 1
        if lock is None:
            print("nxlx-update: another update or check is running", file=sys.stderr)
            try:                          # a result that says "running" belongs to the update that is running;
                with open(result) as f:   # anything else is old, or the lock is held by something that writes
                    running = json.load(f).get("state") == "running"     # none (a check from a terminal)
            except (OSError, ValueError, TypeError, AttributeError):
                running = False
            if not running:
                report("failed", "another update or check is running")
            return 1
        try:                              # we hold the lock: whatever work folder is there, no update is using it
            for name in u.sweep_scratch():
                print("nxlx-update: removed %s, left by an update that was cut off" % name, flush=True)
        except Exception as e:            # tidying must never be the reason an update does not happen
            print("nxlx-update: could not look for old work folders: %r" % e, file=sys.stderr)
    try:
        if args.cmd == "status":
            print(json.dumps(u.status(), indent=2))
        elif args.cmd == "check":
            info, _tree, scratch = u.check(args.bundle, args.sha256, args.allow_unsigned, args.force)
            shutil.rmtree(scratch, ignore_errors=True)
            print("ok: version %s, settings schema %d" % (info["version"], info["schema"]))
        elif args.cmd == "apply":
            version = u.apply(args.bundle, args.sha256, args.allow_unsigned, args.force)
            report("done", "updated to %s" % version, version)
        elif args.cmd in ("usb", "inbox"):
            require_writable_root()     # before anything is looked for, opened or cleared
            report("running", "looking for an update")
            if args.version is not None and not _VERSION.match(args.version):
                raise UpdateError("not a version: %s" % args.version)
            inbox = u.open_inbox() if args.cmd == "inbox" else None
            try:
                if args.cmd == "usb":
                    bundles = u.usb_bundles()
                else:
                    bundles = u.inbox_bundles(inbox) if inbox is not None else []
                if args.version:
                    bundles = [b for b in bundles if os.path.basename(b) == "nxlx-%s.tar.gz" % args.version]
                if not bundles:
                    raise UpdateError(("version %s is no longer there" % args.version if args.version else "no update bundle found")
                                      + (" (in nxlx-update/ on a USB drive)" if args.cmd == "usb" else " (upload it again)"))
                log("using %s" % os.path.basename(bundles[0]))
                version = u.apply(bundles[0], log=log, dir_fd=inbox)
                report("done", "updated to %s" % version, version)
            finally:
                if inbox is not None:
                    u.clear_inbox(inbox)
                    os.close(inbox)
        elif args.cmd == "rollback":
            if args.version is not None and not _VERSION.match(args.version):
                raise UpdateError("not a version: %s" % args.version)
            report("running", "going back to the previous release")
            # from a terminal (no --version) it is the call it always was; from the panel it names the release that was shown
            version = u.rollback() if args.version is None else u.rollback(log=log, expect=args.version)
            note = (getattr(u, "_settings_note", None) or (lambda: ""))()
            report("done", "rolled back to %s%s" % (version, note), version)
    except UpdateError as e:
        print("nxlx-update: %s" % e, file=sys.stderr)
        report("failed", str(e))
        return 1
    except Exception as e:                # anything unexpected still ends the panel's "Updating..."
        print("nxlx-update: unexpected error: %r" % e, file=sys.stderr)
        report("failed", "unexpected error: %s" % (getattr(e, "strerror", None) or e))
        return 1
    finally:
        if lock is not None:
            lock.close()
    return 0


DEFAULT_LOCK = paths.UPDATE_DIR + "/update.lock"     # the update units' folder: root's, and only root can write it
OLD_LOCK = "/run/lock/pvj-update.lock"                # where it was before (and its name then); any account can make a name there


class _Held:
    """The lock files an updater holds; one close lets go of all of them."""

    def __init__(self, *files):
        self.files = files

    def close(self):
        for f in self.files:
            f.close()


def _old_lock(path):
    """The lock under its old name, so that an updater from before the lock moved and one from after exclude each
    other, also during the update that replaces the one by the other. Best effort: the folder is one where every
    account can make a name, so only a plain file of ours with one name counts, and anything else there (a link, a
    folder, somebody else's file, no folder at all) is passed over and never stops an update. Returns the open
    file, False when there is nothing usable to hold, or None when another updater holds it (D70)."""
    import fcntl
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), 0o600)
    except OSError:
        return False
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_nlink != 1:
        os.close(fd)
        return False
    f = os.fdopen(fd, "r+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def take_lock(path=None, old=None):
    """One update at a time, whoever started it (the panel, a terminal). Returns what is held (close it to let
    go), or None when another update holds it. Raises UpdateError when the lock file cannot be used at all.
    `old` is the lock's earlier name, taken as well where the lock is the default one (see _old_lock)."""
    import fcntl
    named = path or os.environ.get("NXLX_UPDATE_LOCK")
    path = named or DEFAULT_LOCK
    if old is None and not named:
        old = OLD_LOCK
    folder = os.path.dirname(path) or "."
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        if not os.path.isdir(folder):
            os.makedirs(folder, mode=0o755, exist_ok=True)      # a terminal before any update unit ever ran
            os.chmod(folder, 0o755)                             # as the units make it, whatever the umask
        # Whoever can make a name in the folder can put a file of their own where the lock goes, or take the
        # lock's name away, and then two updaters hold different files and both run.
        at = os.stat(folder)
        if stat.S_IMODE(at.st_mode) & 0o022:
            raise UpdateError("cannot use the update lock %s: others can write in its folder" % path)
        if at.st_uid != os.geteuid():
            raise UpdateError("cannot use the update lock %s: its folder is somebody else's" % path)
        fd = os.open(path, flags, 0o600)
    except OSError as e:
        # There is one lock file and everybody uses it. This used to fall back to a file in the caller's own temp
        # folder, and two updaters that fell back differently, or only one of them, both ran (D70). Whoever
        # cannot use the lock does not update; on a desk, name another file in NXLX_UPDATE_LOCK.
        raise UpdateError("cannot use the update lock %s: %s" % (path, e.strerror or e))
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        os.close(fd)
        raise UpdateError("cannot use the update lock %s: it is not a plain file with one name" % path)
    if st.st_uid != os.geteuid():
        os.close(fd)
        raise UpdateError("cannot use the update lock %s: it is somebody else's file" % path)
    f = os.fdopen(fd, "r+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    if not old:
        return _Held(f)
    other = _old_lock(old)
    if other is None:
        f.close()
        return None
    return _Held(f, other) if other else _Held(f)
