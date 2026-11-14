#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
# Runs bin/nxlx-convert FOR REAL, as root, under a real systemd (rename PR 2, D136): what the stage tests cannot
# show. usermod and groupmod, visudo, systemctl stopping and starting the units, the undo timer as a unit file,
# the run in its own transient unit, and the timed undo.
#
# The old node is today's release installed by install/install.sh, with a login account pvj-dev that has sudo
# and runs the player (node-02's shape). The release the cutover would install is a stand-in: this checkout with
# every kind of tools/rename-map.txt applied by tools/rename-apply.py, copied into the releases folder and made
# "current", as install.sh will do from rename PR 7 on. Its programs are the renamed code of this commit, which
# nothing else runs yet, so a service that does not start under its new name is shown here first.
#
# It creates and renames accounts, installs to /opt/pvj and writes sudoers. Run it ONLY on a machine that is thrown
# away afterwards (the CI runner). It refuses without the variable below. It is not a test of a Raspberry Pi.
#
#   sudo PVJ_REAL_CONVERT_TEST=yes-this-machine-is-disposable bash tests/real_convert_test.sh
set -euo pipefail

[ "${PVJ_REAL_CONVERT_TEST:-}" = "yes-this-machine-is-disposable" ] || { echo "refusing: read the top of this file" >&2; exit 2; }
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 2; }
[ -d /run/systemd/system ] || { echo "no systemd here" >&2; exit 2; }
if [ -e /opt/pvj ] || [ -e /opt/nxlx ] || getent passwd pvj-dev >/dev/null || getent passwd nxlx-dev >/dev/null; then
	echo "refusing: this machine already has an install" >&2
	exit 2
fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONVERT=(python3 "$SRC/bin/nxlx-convert")
MAIN=(web player sysd supportd sound)
HOLDER=""

fail() {
	echo "FAIL: $*" >&2
	echo "--- the converter's journal" >&2
	tail -n 40 /var/backups/nxlx-convert/journal.jsonl >&2 2>/dev/null || true
	echo "--- the run" >&2
	journalctl -b --no-pager -u nxlx-convert-run -u nxlx-convert-undo -n 80 >&2 || true
	echo "--- units" >&2
	systemctl list-units --all --no-pager 'pvj-*' 'nxlx-*' >&2 || true
	exit 1
}
cleanup() { if [ -n "$HOLDER" ]; then kill "$HOLDER" 2>/dev/null || true; fi; }
trap cleanup EXIT
step() { printf '\n=== %s\n' "$*"; }
has_user() { getent passwd "$1" >/dev/null; }
has_group() { getent group "$1" >/dev/null; }
uid_of() { id -u "$1"; }
names() {      # what a plan must not change: accounts, groups, folders, unit files, links, sudoers
	{
		getent passwd | cut -d: -f1,3,6
		getent group | cut -d: -f1,3,4
		ls -d /opt/pvj* /opt/nxlx* /var/lib/pvj* /var/lib/nxlx* /etc/pvj /etc/nxlx 2>/dev/null || true
		ls /etc/systemd/system /usr/local/bin /etc/sudoers.d
		readlink /opt/pvj/current /opt/nxlx/current 2>/dev/null || true
	} | sort
}

step "the old node: today's release, the login account pvj-dev (sudo, runs the player)"
useradd --create-home --shell /bin/bash pvj-dev
echo 'pvj-dev ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/010_pvj-dev
chmod 440 /etc/sudoers.d/010_pvj-dev
bash "$SRC/install/install.sh" --user pvj-dev
OLD_CURRENT="$(readlink /opt/pvj/current)"
ACTIVE=()
for u in "${MAIN[@]}"; do
	if systemctl is-active --quiet "pvj-$u.service"; then ACTIVE+=("$u"); fi
done
echo "running before: ${ACTIVE[*]:-none}"
UID_WEB="$(uid_of pvj-web)"
UID_DEV="$(uid_of pvj-dev)"
GID_SVC="$(getent group pvj | cut -d: -f3)"

step "the release with the new names (a stand-in made by the rename table)"
NEW="$(mktemp -d)"
git -C "$SRC" archive HEAD | tar -x -C "$NEW"
python3 "$SRC/tools/rename-apply.py" --root "$NEW" --write >/dev/null
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$NEW/mctl/__init__.py")"
[ -n "$VERSION" ] || fail "the stand-in has no mctl/__init__.py"
REL="/opt/pvj/releases/$VERSION-nxlx"
mkdir -p "$REL"
cp -a "$NEW/mctl" "$NEW/bin" "$REL/"
chown -R root:root "$REL"
chmod -R go-w "$REL"
ln -sfn "$REL" /opt/pvj/current.tmp
mv -T /opt/pvj/current.tmp /opt/pvj/current
rm -rf "$NEW"

step "--plan changes nothing; with the old release current it refuses"
ln -sfn "$OLD_CURRENT" /opt/pvj/current.tmp && mv -T /opt/pvj/current.tmp /opt/pvj/current
if out="$("${CONVERT[@]}" --plan)"; then fail "a plan on a release without the new names was not refused"; fi
grep -q "does not have the new names" <<<"$out" || fail "the refusal does not say why: $out"
ln -sfn "$REL" /opt/pvj/current.tmp && mv -T /opt/pvj/current.tmp /opt/pvj/current
before="$(names)"
"${CONVERT[@]}" --plan || fail "the plan refused"
[ "$(names)" = "$before" ] || fail "a plan changed something"
[ ! -e /var/backups/nxlx-convert ] || fail "a plan wrote the backup folder"

step "--plan refuses while pvj-dev is logged in"
su -s /bin/sh pvj-dev -c 'exec sleep 120' &
HOLDER=$!
sleep 1
if out="$("${CONVERT[@]}" --plan)"; then fail "a plan with pvj-dev in use was not refused"; fi
grep -q "the account pvj-dev is in use" <<<"$out" || fail "the refusal does not name pvj-dev: $out"
kill "$HOLDER"
wait "$HOLDER" 2>/dev/null || true
HOLDER=""

step "--run, in its own unit"
"${CONVERT[@]}" --run || fail "the run failed"
for u in web player ndi ndisc sound link raster dev; do
	if has_user "pvj-$u"; then fail "pvj-$u is still there"; fi
done
{ has_user nxlx-web && has_user nxlx-dev && has_group nxlx-svc; } || fail "the new accounts are missing"
{ [ "$(uid_of nxlx-web)" = "$UID_WEB" ] && [ "$(uid_of nxlx-dev)" = "$UID_DEV" ]; } || fail "an account's uid changed"
[ "$(getent group nxlx-svc | cut -d: -f3)" = "$GID_SVC" ] || fail "the shared group's gid changed"
{ [ "$(getent passwd nxlx-dev | cut -d: -f6)" = /home/nxlx-dev ] && [ -d /home/nxlx-dev ]; } || fail "the login account's home"
{ [ -d /opt/nxlx ] && [ ! -e /opt/pvj ] && [ -d /var/lib/nxlx ] && [ -f /etc/nxlx/nxlx.env ]; } || fail "the folders"
{ [ -f /etc/systemd/system/nxlx-web.service ] && [ ! -e /etc/systemd/system/pvj-web.service ]; } || fail "the units"
grep -q '^User=nxlx-dev$' /etc/systemd/system/nxlx-player.service || fail "the player's unit does not run as nxlx-dev"
[ "$(readlink /usr/local/bin/nxlx-player)" = /opt/nxlx/current/bin/nxlx-player ] || fail "the command links"
{ [ -f /etc/sudoers.d/010_nxlx-dev ] && visudo -c >/dev/null; } || fail "sudoers"
sudo -l -U nxlx-dev | grep -q NOPASSWD || fail "nxlx-dev lost sudo"
{ systemctl is-enabled --quiet nxlx-convert-undo.timer && systemctl is-active --quiet nxlx-convert-undo.timer; } || fail "the undo timer is not armed"
grep -q '^OnBootSec=' /etc/systemd/system/nxlx-convert-undo.timer || fail "the undo timer does not survive a restart"
for u in "${ACTIVE[@]}"; do
	systemctl is-active --quiet "nxlx-$u.service" || fail "nxlx-$u did not start under its new name"
done
"${CONVERT[@]}" --run | grep -q "Nothing to convert" || fail "a second run did something"

step "--confirm takes the timer away"
"${CONVERT[@]}" --confirm
if systemctl is-enabled --quiet nxlx-convert-undo.timer 2>/dev/null; then fail "the timer is still enabled"; fi
[ ! -e /etc/systemd/system/nxlx-convert-undo.timer ] || fail "the timer's file is still there"

step "--undo puts the old names back"
"${CONVERT[@]}" --undo || fail "the undo failed"
{ has_user pvj-web && has_user pvj-dev && has_group pvj && ! has_user nxlx-web && ! has_user nxlx-dev; } || fail "the old accounts are not back"
{ [ "$(uid_of pvj-dev)" = "$UID_DEV" ] && [ -d /home/pvj-dev ]; } || fail "the login account and its home"
{ [ -d /opt/pvj ] && [ ! -e /opt/nxlx ] && [ -f /etc/systemd/system/pvj-web.service ]; } || fail "the folders and units"
{ visudo -c >/dev/null && sudo -l -U pvj-dev | grep -q NOPASSWD; } || fail "pvj-dev lost sudo"
# The old names with the old release: what install.sh's rollback to "previous" does.
ln -sfn "$OLD_CURRENT" /opt/pvj/current.tmp && mv -T /opt/pvj/current.tmp /opt/pvj/current
for u in "${ACTIVE[@]}"; do
	systemctl restart "pvj-$u.service"
	systemctl is-active --quiet "pvj-$u.service" || fail "pvj-$u does not start again on the old release"
done

step "the timed undo (its service started at once instead of after 15 minutes)"
ln -sfn "$REL" /opt/pvj/current.tmp && mv -T /opt/pvj/current.tmp /opt/pvj/current
"${CONVERT[@]}" --run || fail "the second run failed"
systemctl start nxlx-convert-undo.service || fail "the timed undo failed"
{ has_user pvj-dev && [ -d /opt/pvj ]; } || fail "the timed undo did not put the old names back"
[ ! -e /etc/systemd/system/nxlx-convert-undo.timer ] || fail "the timed undo left its timer"

step "done"
