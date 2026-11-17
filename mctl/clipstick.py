# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The node's own clip stick (D143): one exFAT partition, labelled Nxx-CLIPS, that this node may write to.

Every USB partition is mounted read-only by mctl/usb.py. Exactly one is mounted writable: an exFAT partition whose
label is Nxx-CLIPS (N01-CLIPS, N03-CLIPS) AND whose top level holds a marker file naming this node's id. The sticks
the nodes use all report the same USB serial, so the serial is never looked at. The label alone is not enough either
(two sticks can carry the same label); the marker is what makes a stick this node's.

The marker, `nxlx-clip-stick.json` at the top of the partition, is a small JSON object:

    {"nxlx_clip_stick": 1, "node_id": "<the node's 32 hex digit id>", "node_name": "nxlx-node-01",
     "label": "N01-CLIPS", "claimed": "2026-11-17T12:00:00Z"}

Only `nxlx_clip_stick` (the format, 1), `node_id` and `label` are compared; the name and the time are for a person
who opens the stick on a computer. A stick is claimed by root with `sudo nxlx-usb claim /dev/sdX2`, which also keeps
a record on the node (`clip-stick.json` in the state folder) so the panel can say when the stick is missing.

Nothing here ever makes a recovery file (D81) on a stick.
"""

import errno
import json
import os
import re
import tempfile
import time

from . import paths

LABEL = re.compile(r"^N[0-9]{2}-CLIPS$")
MARKER = "nxlx-clip-stick.json"
FORMAT = 1
MARKER_MAX = 4096
CLIP_FOLDER = "clips"
NODE_ID = re.compile(r"^[0-9a-f]{32}$")
NODE_ID_FILE = "node-id"                  # the same file mctl/fleet.py keeps (D127)
CLAIM_FILE = "clip-stick.json"            # in the state folder: this node expects a clip stick
DEFAULT_STATUS = os.path.join(paths.USB_DIR, "clip-stick.json")    # written by the mounter (root) while the stick is mounted
# fsck.exfat (exfatprogs) exit codes: 0 no errors, 1 errors corrected. Anything else (errors left, an operational
# error, a syntax error) means the stick is not known to be sound, so it is mounted read-only.
FSCK_OK = (0, 1)
FSCK_SECONDS = 600


class ClipStickError(Exception):
    pass


def label_ok(label):
    return isinstance(label, str) and bool(LABEL.fullmatch(label))


def candidate(info):
    """True for a partition that could be a clip stick: exFAT with an Nxx-CLIPS label. Only the marker decides."""
    return info.get("TYPE") == "exfat" and label_ok(info.get("LABEL"))


def own_node_id(state_dir):
    """This node's id from the state folder, or None. It is only read here, never made: the panel makes it (fleet.py),
    and a node without one has no clip stick."""
    try:
        fd = os.open(os.path.join(state_dir, NODE_ID_FILE), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r", encoding="ascii") as f:
            text = f.read(64).strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text if NODE_ID.fullmatch(text) else None


def _read_small_json(path, limit):
    """A JSON object from a regular file of at most `limit` bytes, never through a link; None otherwise."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not (st.st_mode & 0o170000 == 0o100000) or st.st_size > limit:
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
    """The marker at the top of a mounted partition, or None when there is none or it is not a small regular file
    holding a JSON object. The stick is untrusted: nothing in it is run, and only three fields are compared."""
    return _read_small_json(os.path.join(point, MARKER), MARKER_MAX)


def marker_matches(marker, node_id, label):
    """True only when the marker is format 1, names this node's id and the partition's own label."""
    return (isinstance(marker, dict) and type(marker.get("nxlx_clip_stick")) is int and marker.get("nxlx_clip_stick") == FORMAT
            and isinstance(node_id, str) and NODE_ID.fullmatch(node_id) is not None
            and marker.get("node_id") == node_id and label_ok(label) and marker.get("label") == label)


def make_marker(node_id, node_name, label, now=None):
    if not (isinstance(node_id, str) and NODE_ID.fullmatch(node_id)):
        raise ClipStickError("this node has no id yet (start the panel once, then claim the stick)")
    if not label_ok(label):
        raise ClipStickError("the label %r is not Nxx-CLIPS" % label)
    name = re.sub(r"[^A-Za-z0-9._-]", "", node_name or "")[:63]
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))
    return {"nxlx_clip_stick": FORMAT, "node_id": node_id, "node_name": name, "label": label, "claimed": stamp}


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


def write_file(folder, name, data):
    """Write `data` (bytes) as folder/name: a hidden temporary name, fsync, rename over the old one, then fsync the
    folder, so a pulled stick or a power cut leaves the old file or the new one, never half of one."""
    fd, tmp = tempfile.mkstemp(prefix="." + name + ".", suffix=".tmp", dir=folder)
    try:
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


def ensure_clip_folder(point, gid=None):
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


def fsck(devnode, runner, log=print):
    """Run `fsck.exfat -p` (repair what is safe to repair, ask nothing) on an unmounted partition.
    Returns (ok, why): ok is True for exit 0 or 1."""
    import subprocess
    try:
        r = runner(["fsck.exfat", "-p", devnode], capture_output=True, text=True, timeout=FSCK_SECONDS)
    except FileNotFoundError:
        return False, "fsck.exfat is not installed (package exfatprogs)"
    except subprocess.TimeoutExpired:
        return False, "fsck.exfat took longer than %d s" % FSCK_SECONDS
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


# --- the mounter's status and the node's claim, read by the panel ----------------------------------------------------
def write_status(path, status):
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o755, exist_ok=True)
    write_file(folder, os.path.basename(path), (json.dumps(status, sort_keys=True) + "\n").encode("utf-8"))
    os.chmod(path, 0o644)


def clear_status(path, devnode=None):
    """Remove the status file (only the one for `devnode` when given)."""
    if devnode is not None:
        st = _read_small_json(path, MARKER_MAX)
        if st is None or st.get("device") != devnode:
            return False
    try:
        os.unlink(path)
        return True
    except OSError:
        return False


def read_claim(state_dir):
    data = _read_small_json(os.path.join(state_dir, CLAIM_FILE), MARKER_MAX)
    return data if data is not None and label_ok(data.get("label")) else None


def write_claim(state_dir, label, now=None):
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))
    write_file(state_dir, CLAIM_FILE, (json.dumps({"label": label, "claimed": stamp}, sort_keys=True) + "\n").encode("utf-8"))
    os.chmod(os.path.join(state_dir, CLAIM_FILE), 0o644)


def forget_claim(state_dir):
    try:
        os.unlink(os.path.join(state_dir, CLAIM_FILE))
        return True
    except FileNotFoundError:
        return False


MISSING_TEXT = ("The clip stick %s is not plugged in. The library shows the test clips on the node's SD card; "
                "plug the stick in to see and add your clips.")
READ_ONLY_TEXT = ("The clip stick %s is in but read only (%s). Its clips play; to add clips, check the stick on a "
                  "computer and plug it in again.")


class Chooser:
    """Which folder is the node's media folder right now: the clips folder on the mounted clip stick, else the SD
    folder. Asked on every use of the media folder, so it is cached for a moment; nothing here raises."""

    def __init__(self, sd_dir, usb_root, state_dir, status_path=DEFAULT_STATUS, clock=time.monotonic, ttl=1.0,
                 ismount=os.path.ismount):
        self.sd_dir, self.usb_root, self.state_dir, self.status_path = sd_dir, usb_root, state_dir, status_path
        self.clock, self.ttl, self.ismount = clock, ttl, ismount
        self._cache = (None, None)

    def _look(self):
        st = _read_small_json(self.status_path, MARKER_MAX) if self.status_path else None
        if st is not None:
            point, label = st.get("point"), st.get("label")
            try:
                root = os.path.realpath(self.usb_root)
                ok = (isinstance(point, str) and label_ok(label) and not os.path.islink(point)
                      and os.path.dirname(os.path.realpath(point)) == root and os.path.realpath(point) == point
                      and self.ismount(point))
                clips = os.path.join(point, CLIP_FOLDER) if ok else None
                ok = ok and not os.path.islink(clips) and os.path.isdir(clips)
            except (OSError, TypeError, ValueError):
                ok = False
            if ok:
                if st.get("writable") is True:
                    return clips, {"state": "in", "label": label, "text": ""}
                return clips, {"state": "read-only", "label": label,
                               "text": READ_ONLY_TEXT % (label, st.get("why") or "it was not checked clean")}
        claim = read_claim(self.state_dir) if self.state_dir else None
        if claim is not None:
            return self.sd_dir, {"state": "missing", "label": claim["label"], "text": MISSING_TEXT % claim["label"]}
        return self.sd_dir, {"state": "none", "label": None, "text": ""}

    def choose(self):
        now = self.clock()
        stamp, value = self._cache
        if stamp is None or now - stamp >= self.ttl:
            value = self._look()
            self._cache = (now, value)
        return value

    def media_dir(self):
        return self.choose()[0]

    def state(self):
        return dict(self.choose()[1])


def is_unsupported(err):
    """An OSError meaning the filesystem has no hard links (exFAT, FAT)."""
    return isinstance(err, OSError) and err.errno in (errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP, errno.EMLINK, errno.ENOSYS)
