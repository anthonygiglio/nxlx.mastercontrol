# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Safe updates and rollback.

A release is a signed tar.gz bundle. `apply` checks it (SHA-256, an OpenSSH
Ed25519 signature made with `ssh-keygen -Y sign`, safe extraction, version,
schema, disk space), backs up settings, installs it with install/install.sh
(new folder under /opt/pvj/releases, atomic switch of /opt/pvj/current),
restarts the services and checks that the panel answers. If it does not,
the previous release and the settings backup are put back automatically.

`rollback` does the same by hand. Settings migrations only go forward, so a
rollback restores the settings backup taken before the update; changes made
since then are set aside, not deleted.
"""

import glob
import hashlib
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.request

PRINCIPAL = "pvj-release"
NAMESPACE = "pvj-release"
MAX_BUNDLE_BYTES = 300 * 1024 * 1024
MAX_FILES = 5000
KEEP_BACKUPS = 5
DEFAULT_RESULT = "/run/pvj-update/result.json"     # what the panel's Updates card reads (pvj-update-*@.service)
_VERSION = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)$")


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
            # a single top-level folder (pvj-1.2.3/) is stripped; a lone file is not
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
    version = re.search(r'^__version__ = "([^"]+)"', read("pvj/__init__.py"), re.M)
    schema = re.search(r"^SCHEMA = (\d+)", read("pvj/settings.py"), re.M)
    if not version or not schema:
        raise UpdateError("cannot read version or schema from the bundle")
    for rel in ("bin/pvj-player", "bin/pvj-web", "install/install.sh"):
        if not os.path.isfile(os.path.join(directory, rel)):
            raise UpdateError("bundle is missing %s" % rel)
    parse_version(version.group(1))
    return {"version": version.group(1), "schema": int(schema.group(1))}


class Updater:
    def __init__(self, root="/", prefix="/opt/pvj", state_dir="/var/lib/pvj", etc_dir="/etc/pvj",
                 run=subprocess.run, restart=None, health=None, install=None, now=time.time):
        self.root = root.rstrip("/")
        self.prefix = prefix
        self.state_dir = state_dir
        self.etc_dir = etc_dir
        self.run = run
        self._restart = restart or self._systemd_restart
        self._health = health or self._http_health
        self._install = install or self._run_installer
        self.now = now

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
        return self.real(self.state_dir + "/backups")

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

    def _settings_schema(self):
        try:
            with open(self.settings_path()) as f:
                return int(json.load(f).get("schema", 0))
        except (OSError, ValueError):
            return None

    # --- backups --------------------------------------------------------
    def backup_settings(self, version):
        src = self.settings_path()
        if not os.path.isfile(src):
            return None
        os.makedirs(self.backup_dir(), mode=0o700, exist_ok=True)
        dst = os.path.join(self.backup_dir(), "settings-%s-%d.json" % (version, int(self.now())))
        shutil.copyfile(src, dst)
        os.chmod(dst, 0o600)
        old = sorted(glob.glob(os.path.join(self.backup_dir(), "settings-*.json")), key=os.path.getmtime)
        for path in old[:-KEEP_BACKUPS]:
            os.unlink(path)
        return dst

    def restore_settings(self, backup):
        """Put a backup back as settings.json with the SAME owner as before: the panel runs as its
        own user and could not read a root-owned 0600 file, leaving the box without a panel."""
        current = self.settings_path()
        owner = None
        if os.path.isfile(current):
            st = os.stat(current)
            owner = (st.st_uid, st.st_gid)
            shutil.move(current, "%s.rolled-back-%d" % (current, int(self.now())))
        else:
            try:
                web = pwd.getpwnam("pvj-web")
                owner = (web.pw_uid, web.pw_gid)
            except KeyError:
                pass
        shutil.copyfile(backup, current)
        os.chmod(current, 0o600)
        if owner is not None and os.geteuid() == 0:
            os.chown(current, *owner)

    def latest_backup(self, version):
        found = sorted(glob.glob(os.path.join(self.backup_dir(), "settings-%s-*.json" % version)), key=os.path.getmtime)
        return found[-1] if found else None

    # --- actions --------------------------------------------------------
    def check(self, bundle, sha256=None, allow_unsigned=False, force=False, dir_fd=None):
        """Verify a bundle without installing it. Returns (info, tree, scratch).

        The bundle, its signature and its checksum are opened without following links (regular files only, size
        limited) and copied into a private folder; everything (checksum, signature, unpacking) is done on that
        copy, so a file on a USB stick or in the upload folder cannot be swapped between the check and the use.
        With `dir_fd`, `bundle` is a name inside that (already opened) folder. A signed bundle needs no checksum
        (the signature covers the content); an unsigned one (development only) must have one. The caller removes
        `scratch`."""
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
            parent = self.real(self.prefix)
            scratch = tempfile.mkdtemp(prefix=".update-", dir=parent if os.path.isdir(parent) else None)  # mode 0700
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

    def apply(self, bundle, sha256=None, allow_unsigned=False, force=False, log=print, dir_fd=None):
        info, tree, scratch = self.check(bundle, sha256, allow_unsigned, force, dir_fd=dir_fd)
        old_version = self._link_version()
        previous_before = self._read_previous()
        try:
            backup = self.backup_settings(old_version or "none")
            log("installing %s (was %s)" % (info["version"], old_version or "nothing"))
            try:
                self._install(tree, info)
                self._restart()
                healthy = self._health()
            except BaseException:
                if old_version:
                    self._switch_to(old_version, backup)
                self._restore_previous(previous_before)
                raise
            if not healthy:
                log("the new version did not come up; rolling back")
                self._switch_to(old_version, backup)
                self._restore_previous(previous_before)  # a failed update must not disturb rollback history
                raise UpdateError("update to %s failed its health check and was rolled back" % info["version"])
            log("update complete: %s" % info["version"])
            return info["version"]
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def rollback(self, log=print):
        st = self.status()
        target = st["previous"]
        if not target or not os.path.isdir(self.real("%s/releases/%s" % (self.prefix, target))):
            raise UpdateError("there is no previous release to go back to")
        log("rolling back %s -> %s" % (st["current"], target))
        self._switch_to(target, self.latest_backup(target), current=st["current"])
        if not self._health():
            raise UpdateError("rolled back, but the panel did not answer; check the services")
        return target

    def _switch_to(self, version, backup, current=None):
        if version is None:
            raise UpdateError("nothing to switch back to")
        tmp = self.current_link + ".tmp"
        if os.path.lexists(tmp):
            os.unlink(tmp)
        os.symlink("%s/releases/%s" % (self.prefix, version), tmp)
        os.replace(tmp, self.current_link)
        if backup:
            self.restore_settings(backup)
        if current:
            with open(self.real(self.prefix + "/previous"), "w") as f:
                f.write("%s/releases/%s\n" % (self.prefix, current))
        self._restart()

    # --- USB ------------------------------------------------------------
    BUNDLE_NAME = re.compile(r"pvj-(\d+\.\d+\.\d+)\.tar\.gz")

    def open_inbox(self):
        """The upload folder, opened without following a link: pvj-web owns /var/lib/pvj and could replace the
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
            if name.startswith("pvj-"):
                try:
                    os.unlink(name, dir_fd=dir_fd)
                except OSError:
                    pass

    def usb_bundles(self, base="/media/pvj"):
        found = []
        for path in glob.glob(os.path.join(base, "*", "pvj-update", "*.tar.gz")):
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
            self.run(["systemctl", "restart", "pvj-player.service", "pvj-web.service"], capture_output=True)

    def configured_port(self):
        """The panel's port as the service sees it: /etc/pvj/pvj.env, else our environment, else 80."""
        try:
            with open(self.real(self.etc_dir + "/pvj.env")) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("PVJ_PORT="):
                        value = line.split("=", 1)[1].strip().strip("'\"")
                        if value.isdigit():
                            return value
        except OSError:
            pass
        return os.environ.get("PVJ_PORT", "80")

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
    ap = argparse.ArgumentParser(prog="pvj-update", description="Update or roll back nxlx.mastercontrol")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    for name in ("check", "apply"):
        p = sub.add_parser(name)
        p.add_argument("bundle")
        p.add_argument("--sha256")
        p.add_argument("--allow-unsigned", action="store_true", help="development only")
        p.add_argument("--force", action="store_true")
    for name, text in (("usb", "apply the newest bundle in <usb>/pvj-update/"),
                       ("inbox", "apply the newest bundle uploaded through the panel")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--result", help="write the progress and outcome as JSON here (for the panel)")
        p.add_argument("--version", help="only this version (what the person confirmed in the panel)")
    p = sub.add_parser("finish", help="mark an unfinished panel update as failed (the unit's ExecStopPost)")
    p.add_argument("--result", required=True)
    sub.add_parser("rollback")
    args = ap.parse_args(argv)
    if args.cmd != "status" and os.geteuid() != 0:
        print("pvj-update: run as root (sudo)", file=sys.stderr)
        return 1
    u = Updater()
    result = getattr(args, "result", None)
    if result is None and args.cmd in ("apply", "rollback"):
        # Run from a terminal: the panel's Updates card reads this file, and it would go on saying "updated to X"
        # after a rollback. It is only written where it can already be (the folder is made by the update units).
        result = os.environ.get("PVJ_UPDATE_RESULT", DEFAULT_RESULT)

    def report(state, message, version=None):
        if not result:
            return
        data = {"state": state, "message": message, "version": version, "at": int(time.time())}
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
    if args.cmd in ("apply", "usb", "inbox", "rollback"):
        lock = take_lock()
        if lock is None:
            print("pvj-update: another update is running", file=sys.stderr)
            return 1                      # the result file belongs to the update that is running
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
                    bundles = [b for b in bundles if os.path.basename(b) == "pvj-%s.tar.gz" % args.version]
                if not bundles:
                    raise UpdateError(("version %s is no longer there" % args.version if args.version else "no update bundle found")
                                      + (" (in pvj-update/ on a USB drive)" if args.cmd == "usb" else " (upload it again)"))
                log("using %s" % os.path.basename(bundles[0]))
                version = u.apply(bundles[0], log=log, dir_fd=inbox)
                report("done", "updated to %s" % version, version)
            finally:
                if inbox is not None:
                    u.clear_inbox(inbox)
                    os.close(inbox)
        elif args.cmd == "rollback":
            version = u.rollback()
            report("done", "rolled back to %s" % version, version)
    except UpdateError as e:
        print("pvj-update: %s" % e, file=sys.stderr)
        report("failed", str(e))
        return 1
    except Exception as e:                # anything unexpected still ends the panel's "Updating..."
        print("pvj-update: unexpected error: %r" % e, file=sys.stderr)
        report("failed", "unexpected error: %s" % (getattr(e, "strerror", None) or e))
        return 1
    finally:
        if lock is not None:
            lock.close()
    return 0


def take_lock(path=None):
    """One update at a time, whoever started it (the panel, a terminal). Returns the open lock file, or None
    when another update holds it."""
    import fcntl
    path = path or os.environ.get("PVJ_UPDATE_LOCK", "/run/lock/pvj-update.lock")
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError:
        fd = os.open(os.path.join(tempfile.gettempdir(), "pvj-update.lock"), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    f = os.fdopen(fd, "r+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f
