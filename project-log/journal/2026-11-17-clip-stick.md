<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-17, clip stick, node side (J-162, D143)

- New mctl/clipstick.py: the label (Nxx-CLIPS) and marker (`nxlx-clip-stick.json`, format 1, the node's fleet id) rules, the fsck wrapper (`fsck.exfat -p`, exit 0 or 1 passes), the write helper (hidden temporary name, fsync, rename, fsync of the folder), the status file the mounter writes (`/run/nxlx-usb/clip-stick.json`, `paths.USB_DIR`) and the node's claim record (`/var/lib/nxlx/clip-stick.json`), and the Chooser the panel asks for the media folder.
- mctl/usb.py: an exFAT Nxx-CLIPS partition is looked at read-only in a private folder for its marker; the node's own is checked with fsck.exfat before the writable mount (rw, nosuid, nodev, noexec, noatime, errors=remount-ro, tried once without errors= if the driver refuses it); a failed or missing check mounts it read-only. Other sticks are unchanged. New commands: `nxlx-usb claim DEV [--force]` and `nxlx-usb forget-clip-stick`.
- mctl/api.py: `media_dir` is a property (the stick's clips folder while it is mounted, else the SD folder); `/api/media` carries `clip_stick`; upload, copy and rename fall back to a checked rename where hard links and chmod give EPERM (exFAT). mctl/server.py sets the Chooser. Panel: one text line at the top of the library when there is something to say.
- install.sh installs exfatprogs when missing (node-03's Debian lacks it) and prints the claim command; nxlx-usb@.service gets TimeoutStartSec=15min for the check; nxlx-web.service gets ReadWritePaths=-/media/nxlx (tests/test_units.py's sandbox list updated, with the reason).
- Tests: tests/test_clipstick.py (29): marker and label matching, the marker read (link, size, JSON), the call order blkid, read-only look, umount, fsck, writable mount, fsck exit 1, a failed or missing fsck (read-only even with NXLX_USB_RW=1), another node's stick, label without marker, marker without label or on vfat, no node id, errors= refused, the old switch for other sticks, unmount clearing the status, claim (marker, record, no recovery or temporary file, refusals, --force), the Chooser (none, missing, in, pulled, read-only, a status pointing elsewhere or through a link), the API (missing text with the SD clips, no change without a Chooser, upload and rename without hard links never overwrite).
- Not run: a real node or stick, a real fsck.exfat (exfatprogs is not installed here), a real exFAT mount, systemd's namespace with ReadWritePaths at boot, the panel's browser test.

## 2026-11-17, review of clip-stick 47ebe89 (D143 rewritten)

- (Medium 1) Forged marker: the node id is announced to the network, so a marker naming it proved nothing. A claim now makes a secret (`secrets.token_hex(16)`) written on the stick's marker and in the root-only record `/var/lib/nxlx/clip-stick.json` (0600, owner checked, no link) with the label and the volume serial; the mounter needs the record, the label, the serial (when the claim saw one) and the same secret (`hmac.compare_digest`). No record, no clip stick. The panel reads only `clip-stick-label.json`. The secret is not in the status, the log or any answer (tested).
- (Medium 2) Reset race: `_reset` resolves the media folder once, checks it and hands that folder to `_delete_media`; a node with a claimed stick never deletes clips, even while it reads missing.
- (Medium/Low 3) Only `clips/` is writable: the whole partition is mounted under `/run/nxlx-usb/mnt/` (0700) and `clips/` is bound at `/media/nxlx-clips` (rw only when checked clean); exFAT's modes are per filesystem, so the binding is what limits writes. nxlx-web's extra path is now `-/media/nxlx-clips` only. The top of the clip stick is under no drive folder, so the recovery stick scan never sees it (no change in mctl/recovery.py was needed).
- (Low 4) `Api._media_now` gives one folder and one state per operation (list, upload and copy with free space and sweep, delete, rename). Uploads and copies are refused with a plain sentence while a claimed stick is missing, being checked, read-only or without clips.
- (Low 5) The look's unmount is checked (a failure detaches it and the stick is mounted as any other); time-outs of the look's mount and unmount and of fsck are caught and logged.
- (Low 6) The marker is read again on the final mount; a swapped stick is unmounted and mounted as any other, with no status.
- (Low 7) Status `checking` before fsck; `no-clips` for a read-only stick without `clips/`; the panel says each.
- (Low 8) `flock` on `/run/nxlx-usb/clip.lock` (with a deadline) in claim and in the clip stick mount; each look has its own `mkdtemp` folder.
- Tests: test_clipstick grew to 41, test_boxcare by 2. Each fix was taken out once to see its test fail (the in-lock name check of the upload fallback, the secret check, the swapped stick, the look's unmount, the upload refusal, the reset's one folder and the claimed-stick reset): every one failed.
- Not run: a real node, stick, fsck.exfat, exFAT mount, bind mount or systemd namespace.

## 2026-11-17, re-review of clip-stick 689dfab

- (1) A start killed during the check (stick pulled, time limit) left `checking` for good: nxlx-usb@.service now runs `nxlx-usb unmount` as ExecStopPost too (it clears the status and takes the binding and the private mount of that device down, and does nothing the second time), the status carries the time, and the panel passes over a `checking` older than the longest check plus five minutes and any status whose device is gone. A stick mount waits at most 60 s for the lock (a claim still 15 min), so wait, check and mounts fit the 15 min start limit (a test holds the sum).
- (2) Everything after the private mount is in try/finally: a failure takes the binding and the mount down and clears the status.
- (3) A second stick with the same claim is not mounted while `/media/nxlx-clips` is a mount point.
- (4) `forget-clip-stick` is refused while the stick is mounted (status, binding or private mount).
- (5) A `blkid` time-out is a UsbError.
- (6) A stick with the claim's label and volume serial is the clip stick or not mounted at all, never under /media/nxlx, so its token is never readable to the player or the panel. D143 says the token is a bearer secret: a clone needs the stick in hand, accepted.
- New test that a delete reads the media folder once (it failed when `_real_media_file` read it a second time). Each fix was taken out once and its test failed (9 checks).

## 2026-11-17, final review of clip-stick 8737663

- (1) `nxlx-usb claim` no longer mounts the stick itself: after the marker, the record and its own unmount it lets go of the lock and runs `systemctl restart nxlx-usb@<kernel name>.service`, so the unit owns the mounts and a pull runs its ExecStop and ExecStopPost. A binding at /media/nxlx-clips whose device node is gone is taken down (with that device's private mount and status) before a new clip stick is refused as "already there".
- (2) New state `not-accepted`: a stick with the claim's label and volume whose marker does not match (or was swapped) is not mounted, the unit's start succeeds so the status stays until the pull, the panel says it was not accepted and how to claim it again, and uploads stay refused.
- (3) unmount also takes down a look of that device left in /run/nxlx-usb/peek/.
- Tests: test_clipstick 54; each fix was taken out once and its test failed (5 checks). The systemctl restart from claim is tested with a stand-in that runs the unit's mount; real systemd is not run.
