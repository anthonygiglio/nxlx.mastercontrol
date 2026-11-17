# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The node's own clip stick (D143): one exFAT partition, labelled Nxx-CLIPS, whose clips folder this node may write to.

Every USB partition is mounted read-only by mctl/usb.py. Exactly one gets a writable clips folder: an exFAT partition
labelled Nxx-CLIPS (N01-CLIPS, N03-CLIPS) that root claimed on this node with `sudo nxlx-usb claim /dev/sdX2`. A claim
makes a random secret and writes it in two places: the marker on the stick and the claim record on the node, a file
only root can read. The node's id alone proves nothing (it is announced to the network), the label can be set on any
computer, and the sticks in use all report one USB serial, so the serial is never looked at.

The marker, `nxlx-clip-stick.json` at the top of the partition, is a small JSON object:

    {"nxlx_clip_stick": 1, "node_id": "<the node's 32 hex digit id>", "node_name": "nxlx-node-01",
     "label": "N01-CLIPS", "token": "<32 hex digits>", "claimed": "2026-11-17T12:00:00Z"}

The claim record, `clip-stick.json` in the state folder, root's and mode 0600: {"label", "uuid", "token", "claimed"}
(uuid is the exFAT volume serial blkid reports, when it reports one). Beside it `clip-stick-label.json` (0644) holds
only the label and the time, so the panel can say when the stick is missing without reading the secret. The secret is
never logged, never in the status file, never sent by the panel.

Nothing here ever makes a recovery file (D81) on a stick.
"""

import errno
import hmac
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
import time

from . import paths

LABEL = re.compile(r"^N[0-9]{2}-CLIPS$")
MARKER = "nxlx-clip-stick.json"
FORMAT = 1
MARKER_MAX = 4096
CLIP_FOLDER = "clips"
NODE_ID = re.compile(r"^[0-9a-f]{32}$")
TOKEN = re.compile(r"^[0-9a-f]{32}$")
NODE_ID_FILE = "node-id"                  # the same file mctl/fleet.py keeps (D127)
CLAIM_FILE = "clip-stick.json"            # in the state folder, root's, 0600: the label, the volume uuid and the secret
LABEL_FILE = "clip-stick-label.json"      # in the state folder, 0644: the label only, for the panel
DEFAULT_STATUS = os.path.join(paths.USB_DIR, "clip-stick.json")    # written by the mounter (root) for the panel
DEFAULT_CLIP_DIR = "/media/nxlx-clips"    # the stick's clips folder, bound here; the only writable place of any stick
# fsck.exfat (exfatprogs) exit codes: 0 no errors, 1 errors corrected. Anything else (errors left, an operational
# error, a syntax error) means the stick is not known to be sound, so it is mounted read-only.
FSCK_OK = (0, 1)
FSCK_SECONDS = 600


class ClipStickError(Exception):
    pass


def label_ok(label):
    return isinstance(label, str) and bool(LABEL.fullmatch(label))


def candidate(info):
    """True for a partition that could be a clip stick: exFAT with an Nxx-CLIPS label. Only the claim decides."""
    return info.get("TYPE") == "exfat" and label_ok(info.get("LABEL"))


def own_node_id(state_dir):
    """This node's id from the state folder, or None. Only read here, never made (the panel makes it, fleet.py)."""
    try:
        fd = os.open(os.path.join(state_dir, NODE_ID_FILE), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r", encoding="ascii") as f:
            text = f.read(64).strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text if NODE_ID.fullmatch(text) else None


def _read_small_json(path, limit, private=False):
    """A JSON object from a regular file of at most `limit` bytes, never through a link; None otherwise. With
    `private`, the file must also belong to this process's user and give nothing to its group or others."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            return None
        if private and (st.st_uid != os.geteuid() or st.st_mode & 0o077):
            return None
        raw = os.read(fd, limit + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(raw) > limit:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_marker(point):
    """The marker at the top of a mounted partition, or None. The stick is untrusted: nothing in it is run, and only
    four fields are compared."""
    return _read_small_json(os.path.join(point, MARKER), MARKER_MAX)


def _same_secret(a, b):
    return isinstance(a, str) and isinstance(b, str) and bool(TOKEN.fullmatch(a)) and bool(TOKEN.fullmatch(b)) \
        and hmac.compare_digest(a.encode("ascii"), b.encode("ascii"))


def marker_matches(marker, node_id, label, record, uuid=None):
    """True only when the marker is format 1, names this node's id and the partition's label, and carries the secret
    of `record` (the node's claim record, None when there is none) on a partition with the record's label and uuid."""
    ok = (isinstance(marker, dict) and type(marker.get("nxlx_clip_stick")) is int and marker.get("nxlx_clip_stick") == FORMAT
          and isinstance(node_id, str) and NODE_ID.fullmatch(node_id) is not None
          and marker.get("node_id") == node_id and label_ok(label) and marker.get("label") == label)
    return ok and record_matches(record, label, uuid) and _same_secret(marker.get("token"), record.get("token"))


def record_matches(record, label, uuid):
    """The claim record is for this partition: same label, and the same volume uuid when the claim saw one."""
    if not isinstance(record, dict) or not label_ok(label) or record.get("label") != label or not TOKEN.fullmatch(str(record.get("token", ""))):
        return False
    want = record.get("uuid") or ""
    return not want or want == (uuid or "")


def make_marker(node_id, node_name, label, token, now=None):
    if not (isinstance(node_id, str) and NODE_ID.fullmatch(node_id)):
        raise ClipStickError("this node has no id yet (start the panel once, then claim the stick)")
    if not label_ok(label):
        raise ClipStickError("the label %r is not Nxx-CLIPS" % label)
    if not (isinstance(token, str) and TOKEN.fullmatch(token)):
        raise ClipStickError("no secret for the claim")
    name = re.sub(r"[^A-Za-z0-9._-]", "", node_name or "")[:63]
    return {"nxlx_clip_stick": FORMAT, "node_id": node_id, "node_name": name, "label": label, "token": token,
            "claimed": _stamp(now)}


def new_token():
    return secrets.token_hex(16)


def _stamp(now=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))


def fsync_dir(path):
    """Flush a folder's entries (a rename) to the disk. Errors are passed over: the file itself was synced."""
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def write_file(folder, name, data, mode=None):
    """Write `data` (bytes) as folder/name: a hidden temporary name, fsync, rename over the old one, then fsync the
    folder, so a pulled stick or a power cut leaves the old file or the new one, never half of one. `mode` is set on
    the temporary file before any byte is written (not on exFAT, which has no modes)."""
    fd, tmp = tempfile.mkstemp(prefix="." + name + ".", suffix=".tmp", dir=folder)    # 0600 from the start
    try:
        if mode is not None:
            os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, os.path.join(folder, name))
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    fsync_dir(folder)


def write_marker(point, marker):
    write_file(point, MARKER, (json.dumps(marker, indent=1, sort_keys=True) + "\n").encode("utf-8"))


def ensure_clip_folder(point):
    """The clips folder on the stick (made when missing). A link or a file at that name is refused."""
    path = os.path.join(point, CLIP_FOLDER)
    try:
        os.mkdir(path, 0o775)
        fsync_dir(point)
    except FileExistsError:
        pass
    if os.path.islink(path) or not os.path.isdir(path):
        raise ClipStickError("%s on the stick is not a folder" % CLIP_FOLDER)
    return path


def has_clip_folder(point):
    path = os.path.join(point, CLIP_FOLDER)
    return not os.path.islink(path) and os.path.isdir(path)


def fsck(devnode, runner, log=print):
    """Run `fsck.exfat -p` (repair what is safe to repair, ask nothing) on an unmounted partition.
    Returns (ok, why): ok is True for exit 0 or 1."""
    try:
        r = runner(["fsck.exfat", "-p", devnode], capture_output=True, text=True, timeout=FSCK_SECONDS)
    except FileNotFoundError:
        return False, "fsck.exfat is not installed (package exfatprogs)"
    except subprocess.TimeoutExpired:
        log("fsck.exfat on %s did not finish in %d s and was stopped" % (devnode, FSCK_SECONDS))
        return False, "the check did not finish in %d minutes" % (FSCK_SECONDS // 60)
    except OSError as e:
        return False, "fsck.exfat could not run: %s" % (e.strerror or e)
    if r.returncode == 127:
        return False, "fsck.exfat is not installed (package exfatprogs)"
    if r.returncode in FSCK_OK:
        if r.returncode == 1:
            log("fsck.exfat corrected errors on %s" % devnode)
        return True, ""
    tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-1:] or [""]
    return False, "fsck.exfat exit %d: %s" % (r.returncode, tail[0][:200])


# --- the mounter's status and the node's claim -------------------------------------------------------------------------
def write_status(path, status):
    """The status for the panel: {"device", "label", "state": checking|in|read-only|no-clips, "why"}. Never the secret."""
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o755, exist_ok=True)
    clean = {k: status.get(k) for k in ("device", "label", "state", "why")}
    clean["since"] = int(time.time())
    write_file(folder, os.path.basename(path), (json.dumps(clean, sort_keys=True) + "\n").encode("utf-8"), mode=0o644)


def read_status(path):
    return _read_small_json(path, MARKER_MAX) if path else None


def clear_status(path, devnode=None):
    """Remove the status file (only the one for `devnode` when given)."""
    if devnode is not None:
        st = read_status(path)
        if st is None or st.get("device") != devnode:
            return False
    try:
        os.unlink(path)
        return True
    except OSError:
        return False


def read_record(state_dir):
    """The claim record, only when it is a small regular file of this process's user (root on a node) that its group
    and others cannot read or write; else None, and the node has no clip stick."""
    data = _read_small_json(os.path.join(state_dir, CLAIM_FILE), MARKER_MAX, private=True)
    if data is None or not label_ok(data.get("label")) or not TOKEN.fullmatch(str(data.get("token", ""))):
        return None
    return data


def write_claim(state_dir, label, uuid, token, now=None):
    stamp = _stamp(now)
    write_file(state_dir, CLAIM_FILE, (json.dumps({"label": label, "uuid": uuid or "", "token": token, "claimed": stamp},
                                                  sort_keys=True) + "\n").encode("utf-8"), mode=0o600)
    write_file(state_dir, LABEL_FILE, (json.dumps({"label": label, "claimed": stamp}, sort_keys=True) + "\n").encode("utf-8"),
               mode=0o644)


def claimed_label(state_dir):
    """The label of this node's claimed clip stick, as the panel may know it: from the public label file, or "the
    clip stick" when only the root-only record is there. None when no stick is claimed."""
    data = _read_small_json(os.path.join(state_dir, LABEL_FILE), MARKER_MAX)
    if data is not None and label_ok(data.get("label")):
        return data["label"]
    try:
        os.lstat(os.path.join(state_dir, CLAIM_FILE))
        return "clip stick"
    except OSError:
        return None


def forget_claim(state_dir):
    gone = False
    for name in (CLAIM_FILE, LABEL_FILE):
        try:
            os.unlink(os.path.join(state_dir, name))
            gone = True
        except FileNotFoundError:
            pass
    return gone


MISSING_TEXT = ("The clip stick %s is not plugged in. The library shows the test clips on the node's SD card; "
                "plug the stick in to see and add your clips.")
CHECKING_STALE = FSCK_SECONDS + 300      # a "checking" status older than this was left by a unit that was killed
DEVNODE = re.compile(r"^/dev/sd[a-z]{1,2}[0-9]{0,2}$")
CHECKING_TEXT = ("Checking the clip stick %s. The library shows the test clips on the node's SD card until the check "
                 "is done.")
READ_ONLY_TEXT = ("The clip stick %s is in but read only (%s). Its clips play; to add clips, check the stick on a "
                  "computer and plug it in again.")
NO_CLIPS_TEXT = ("The clip stick %s is in but has no clips folder and is read only (%s). The library shows the test "
                 "clips on the node's SD card.")


class Chooser:
    """Which folder is the node's media folder right now: the clip stick's clips folder (always at `clip_dir`) while the
    mounter says it is there and it is a mount point, else the SD folder. Asked on every use of the media folder, so it
    is cached for a moment; nothing here raises. The status file names no path: it cannot point the panel elsewhere."""

    def __init__(self, sd_dir, clip_dir, state_dir, status_path=DEFAULT_STATUS, clock=time.monotonic, ttl=1.0,
                 ismount=os.path.ismount, now=time.time, exists=os.path.exists):
        self.sd_dir, self.clip_dir, self.state_dir, self.status_path = sd_dir, clip_dir, state_dir, status_path
        self.clock, self.ttl, self.ismount, self.now, self.exists = clock, ttl, ismount, now, exists
        self._cache = (None, None)

    def _live(self, st):
        """A status the mounter could still stand behind: its device is there and, while checking, it is not older
        than the longest check (a unit killed mid-check never cleared it)."""
        dev = st.get("device")
        if not (isinstance(dev, str) and DEVNODE.fullmatch(dev) and self.exists(dev)):
            return False
        if st.get("state") == "checking":
            since = st.get("since")
            return type(since) is int and 0 <= self.now() - since <= CHECKING_STALE
        return True

    def claimed(self):
        return claimed_label(self.state_dir) is not None if self.state_dir else False

    def _look(self):
        st = read_status(self.status_path)
        if st is not None and label_ok(st.get("label")) and self._live(st):
            label, state, why = st["label"], st.get("state"), str(st.get("why") or "")[:200]
            if state == "checking":
                return self.sd_dir, {"state": "checking", "label": label, "text": CHECKING_TEXT % label}
            if state == "no-clips":
                return self.sd_dir, {"state": "no-clips", "label": label, "text": NO_CLIPS_TEXT % (label, why or "not checked clean")}
            if state in ("in", "read-only"):
                try:
                    there = (not os.path.islink(self.clip_dir) and os.path.isdir(self.clip_dir)
                             and os.path.realpath(self.clip_dir) == self.clip_dir and self.ismount(self.clip_dir))
                except (OSError, TypeError, ValueError):
                    there = False
                if there and state == "in":
                    return self.clip_dir, {"state": "in", "label": label, "text": ""}
                if there:
                    return self.clip_dir, {"state": "read-only", "label": label,
                                           "text": READ_ONLY_TEXT % (label, why or "it was not checked clean")}
        label = claimed_label(self.state_dir) if self.state_dir else None
        if label is not None:
            return self.sd_dir, {"state": "missing", "label": label, "text": MISSING_TEXT % label}
        return self.sd_dir, {"state": "none", "label": None, "text": ""}

    def choose(self):
        """(folder, state) read together, so one operation sees one answer."""
        now = self.clock()
        stamp, value = self._cache
        if stamp is None or now - stamp >= self.ttl:
            value = self._look()
            self._cache = (now, value)
        return value[0], dict(value[1])

    def media_dir(self):
        return self.choose()[0]

    def state(self):
        return self.choose()[1]


REFUSE_TEXT = {
    "missing": "The clip stick is not plugged in, so nothing was stored. Plug it in and try again.",
    "checking": "The clip stick is still being checked, so nothing was stored. Try again in a minute.",
    "read-only": "The clip stick is read only, so nothing was stored. Check it on a computer and plug it in again.",
    "no-clips": "The clip stick has no clips folder and is read only, so nothing was stored.",
}


def is_unsupported(err):
    """An OSError meaning the filesystem has no hard links or no modes (exFAT, FAT)."""
    return isinstance(err, OSError) and err.errno in (errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP, errno.EMLINK, errno.ENOSYS)
