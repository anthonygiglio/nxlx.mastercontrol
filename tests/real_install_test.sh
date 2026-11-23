#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
# Runs install/install.sh FOR REAL, as root, under a real systemd, and checks who owns what in /run/nxlx (D45):
# an upgrade over an old shared folder with planted names and links, restarts, a crash, a reinstall, an emptying
# that was cut short, and the things the service accounts must not be able to do.
# Then the NDI input, which is opt-in per box (D62): that those plain installs left nothing of it, what root's
# opt-in changes, that a later install keeps it, and that opting out takes it off. Without NDI's library.
# Then an NDI picture all the way, on this layout: a STAND-IN for NDI's library (tests/ndi_standin, our own code,
# built here) is placed by the real command, loaded by the real helper in its sandbox, written through root's pipe
# and read by the real player, asked for through the panel's API. NDI's own library is never here; see mctl/NDI.md,
# "What the tests prove".
#
# It creates accounts, installs to /opt/nxlx, writes units and starts services. Run it ONLY on a machine that is
# thrown away afterwards (the CI runner). It refuses without the variable below. It is not a test of a Raspberry
# Pi: there is no display here, and the systemd is the runner's, not the box's.
#
#   sudo NXLX_REAL_INSTALL_TEST=yes-this-machine-is-disposable bash tests/real_install_test.sh
set -euo pipefail

[ "${NXLX_REAL_INSTALL_TEST:-}" = "yes-this-machine-is-disposable" ] || { echo "refusing: read the top of this file" >&2; exit 2; }
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 2; }
[ -d /run/systemd/system ] || { echo "no systemd here" >&2; exit 2; }
if [ -e /opt/pvj ] || [ -e /opt/nxlx ] || getent passwd pvj-dev >/dev/null || getent passwd nxlx-dev >/dev/null || [ -e /run/nxlx ] || [ -e /opt/nxlx-ndi ] || getent passwd nxlx-ndi >/dev/null; then echo "refusing: this machine already has an install" >&2; exit 2; fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
A=nxlxdisplay                       # the display account of this test
OUT="$(mktemp)"
PLANTED=/root/nxlx-planted-by-root
if dpkg -s avahi-daemon >/dev/null 2>&1; then AVAHI_BEFORE=installed; else AVAHI_BEFORE=absent; fi      # a plain install must not change it

listing() {
	{ find /run/nxlx /run/nxlx-sysd /run/nxlx-supportd /run/nxlx-update -printf '%u:%g %m %y %p %l\n' 2>&1 || true; } | sort -k4
}
fail() {
	echo "FAIL: $*" >&2
	echo "--- listing" >&2
	listing >&2 || true
	echo "--- journal" >&2
	journalctl -b --no-pager -u 'nxlx-*' -u 'pvj-*' -n 200 >&2 || true
	echo "--- units and jobs" >&2
	systemctl list-units --all --no-pager --plain 'nxlx-*' 'pvj-*' >&2 || true
	systemctl list-jobs --no-pager >&2 || true
	systemctl list-timers --all --no-pager 'nxlx-*' >&2 || true
	exit 1
}
step() { printf '\n=== %s\n' "$*"; }
# is PATH "owner:group mode type"
is() {
	local have
	have="$(stat -c '%U:%G %a %F' "$1" 2>&1)" || true
	[ "$have" = "$2" ] || fail "$1 is '$have', expected '$2'"
}
wait_for() {      # wait_for SECONDS test-args...
	local n="$1"
	shift
	while [ "$n" -gt 0 ]; do
		if "$@" 2>/dev/null; then return 0; fi
		sleep 1
		n=$((n - 1))
	done
	return 1
}
player_socket_is_open() { [ "$(stat -c '%a' /run/nxlx/player/player.sock 2>/dev/null)" = 660 ]; }
refused() {       # refused ACCOUNT command...: the command must fail
	local who="$1"
	shift
	if runuser -u "$who" -- "$@" >/dev/null 2>&1; then fail "as $who this worked and must not: $*"; fi
	echo "refused, as it must be ($who): $*"
}
logged() {        # logged COMMAND...: run it into $OUT; when it fails, dump the journal (fail), the pipe alone would just stop
	local rc=0
	"$@" > "$OUT" 2>&1 || rc=$?
	cat "$OUT"
	[ "$rc" = 0 ] || fail "the installer stopped with exit code $rc: $*"
}
installer() {
	logged "$SRC/install/install.sh" --offline --user "$A" "$@"
}
only_the_new_layout() {
	local extra
	is /run/nxlx "root:root 755 directory"
	is /run/nxlx/.d45 "root:root 644 regular empty file"
	is /run/nxlx/web "nxlx-web:nxlx-svc 750 directory"
	[ "$(readlink /run/nxlx/web/player.sock)" = /run/nxlx/player/player.sock ] || fail "the player.sock link is missing or wrong"
	[ "$(readlink /run/nxlx/web/netd.sock)" = /run/nxlx/netd/netd.sock ] || fail "the netd.sock link is missing or wrong"
	extra="$(find /run/nxlx -mindepth 1 -maxdepth 1 ! -name .d45 ! -name web ! -name player ! -name netd)"
	[ -z "$extra" ] || fail "unexpected names directly in /run/nxlx: $extra"
	if [ -e "$PLANTED" ] || [ -L "$PLANTED" ]; then fail "root created $PLANTED through a planted link"; fi
	is /etc "root:root 755 directory"
	is /etc/passwd "root:root 644 regular file"
}
services_and_their_files() {      # [the player's account, $A if not given]
	local A="${1:-$A}"
	wait_for 40 systemctl is-active --quiet nxlx-web.service || fail "nxlx-web is not active"
	wait_for 40 test -f /run/nxlx/web/pin || fail "the panel wrote no PIN file"
	is /run/nxlx/web/pin "nxlx-web:nxlx-svc 600 regular file"
	wait_for 40 test -S /run/nxlx/netd/netd.sock || fail "nxlx-netd made no socket"
	is /run/nxlx/netd "root:nxlx-svc 750 directory"
	is /run/nxlx/netd/netd.sock "root:nxlx-svc 660 socket"
	wait_for 40 test -S /run/nxlx-sysd/sysd.sock || fail "nxlx-sysd made no socket"
	is /run/nxlx-sysd "root:nxlx-svc 750 directory"
	is /run/nxlx-sysd/sysd.sock "root:nxlx-svc 660 socket"
	# The player needs a display. If mpv stays up without one, its folder and socket are checked; if it does not,
	# that is said and not counted as a pass.
	if wait_for 30 test -S /run/nxlx/player/player.sock; then
		is /run/nxlx/player "$A:nxlx-svc 750 directory"
		wait_for 20 player_socket_is_open || true        # the player's helper opens it to the group a moment later
		is /run/nxlx/player/player.sock "$A:nxlx-svc 660 socket"
		PLAYER_SEEN=1
	else
		echo "NOTE: the player did not stay up on this machine (no display); its folder and socket were NOT checked"
		PLAYER_SEEN=0
	fi
}

step "systemd here"
systemctl --version | head -n 1
if [ ! -e /usr/bin/nmcli ]; then
	# nxlx-netd only starts where nmcli exists. A stand-in that does nothing lets the helper start, so that its
	# socket and folder can be checked; nothing here tests the network.
	printf '#!/bin/sh\nexit 0\n' > /usr/bin/nmcli
	chmod 0755 /usr/bin/nmcli
fi

# --- the release before the rename of the system names, upgraded through install.sh (rename PR 7, D136) ---------
# A node of today has the old names: it is installed here by the installer of its own release (master at 0ff0ddb),
# in node-02's shape (the login account pvj-dev has sudo and runs the player), and this release's installer is run
# over it, as a deploy does. It must convert the node (accounts with the same ids, folders, units, sudoers, udev,
# tmpfiles), start the services under their new names with the same sockets and owners, keep a paired device and
# the settings, and be undone back to the old release by nxlx-convert --undo.
BEFORE=0ff0ddbeb662efb405186a0e774839cb1f61b2de     # master with release 0.1.1, before rename PR 7 (fetched by the job)
NEW_VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC/mctl/__init__.py")"
units_with_old_names() { find /etc/systemd/system /etc/udev/rules.d /etc/tmpfiles.d /etc/sudoers.d /usr/local/bin /etc/modules-load.d /etc/systemd/journald.conf.d -name '*pvj*' 2>/dev/null; }
status_code() { curl -sS -m 20 -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $1" http://127.0.0.1/api/status || true; }

step "the old node: the release before the system rename, installed by its own installer"
OLDSRC="$(mktemp -d)"
git -C "$SRC" archive "$BEFORE" mctl bin install render | tar -x -C "$OLDSRC" || fail "no $BEFORE here (the job fetches it)"
useradd --create-home --shell /bin/bash pvj-dev
printf 'pvj-dev ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/010_pvj-dev
chmod 440 /etc/sudoers.d/010_pvj-dev
logged "$OLDSRC/install/install.sh" --offline --user pvj-dev
OLD_CURRENT="$(readlink /opt/pvj/current)"
wait_for 40 systemctl is-active --quiet pvj-web.service || fail "the old release's panel did not start"
wait_for 40 test -f /run/pvj/web/pin || fail "the old panel wrote no PIN file"
OLD_PIN="$(/usr/local/bin/pvj-pin)"
OLD_TOKEN="$(curl -sS -m 20 -H 'Content-Type: application/json' -H 'X-MCTL-Request: 1' -d "{\"pin\": \"$OLD_PIN\", \"name\": \"ci-old\"}" http://127.0.0.1/api/pair | sed -n 's/.*"token": *"\([^"]*\)".*/\1/p')"
[ -n "$OLD_TOKEN" ] || fail "could not pair with the old panel"
[ "$(status_code "$OLD_TOKEN")" = 200 ] || fail "the old panel does not take its own token"
UID_WEB="$(id -u pvj-web)"
UID_DEV="$(id -u pvj-dev)"
GID_SVC="$(getent group pvj | cut -d: -f3)"
wait_for 20 test -f /var/lib/pvj/settings.json || fail "the old panel wrote no settings"
# The panel makes a new PIN at every start and writes its hash into the auth section of settings.json, so a panel
# that starts again rewrites the file. The converter checks the file byte for byte before it starts anything (its
# journal says so, below); after the start everything but the auth section must still be the same, key for key.
settings_but_auth() { python3 -c 'import json, sys; d = json.load(open(sys.argv[1])); d.pop("auth", None); print(json.dumps(d, sort_keys=True))' "$1"; }
SETTINGS_REST="$(settings_but_auth /var/lib/pvj/settings.json)"

step "this release over it, through install.sh: the node is converted"
logged "$SRC/install/install.sh" --offline
grep -q "CONVERTED from the old names" "$OUT" || fail "the installer did not convert the node"
for u in pvj-web pvj-player pvj-sound pvj-link pvj-raster pvj-dev; do
	if getent passwd "$u" >/dev/null; then fail "the account $u is still there"; fi
done
for g in pvj pvj-link pvj-raster pvj-dev; do
	if getent group "$g" >/dev/null; then fail "the group $g is still there"; fi
done
[ "$(id -u nxlx-web)" = "$UID_WEB" ] || fail "nxlx-web has another uid than pvj-web had"
[ "$(id -u nxlx-dev)" = "$UID_DEV" ] || fail "nxlx-dev has another uid than pvj-dev had"
[ "$(getent group nxlx-svc | cut -d: -f3)" = "$GID_SVC" ] || fail "nxlx-svc has another gid than pvj had"
if getent group nxlx >/dev/null; then fail "a group called nxlx was made (the shared group is nxlx-svc)"; fi
id -nG nxlx-web | tr ' ' '\n' | grep -qx nxlx-svc || fail "nxlx-web is not in nxlx-svc"
if [ "$(getent passwd nxlx-dev | cut -d: -f6)" != /home/nxlx-dev ] || [ ! -d /home/nxlx-dev ]; then fail "the login account's home"; fi
for d in /opt/pvj /etc/pvj /var/lib/pvj; do if [ -e "$d" ]; then fail "$d is still there"; fi; done
[ "$(readlink /opt/nxlx/current)" = "/opt/nxlx/releases/$NEW_VERSION" ] || fail "current is $(readlink /opt/nxlx/current)"
[ -d "/opt/nxlx/releases/${OLD_CURRENT##*/}/bin" ] || fail "the old release is not kept for the undo"
[ "$(cat /opt/nxlx/previous)" = "/opt/nxlx/releases/${OLD_CURRENT##*/}" ] || fail "previous is $(cat /opt/nxlx/previous)"
left="$(units_with_old_names)"
[ -z "$left" ] || fail "system files with the old names are left: $left"
for f in nxlx-web.service nxlx-player.service nxlx-netd.service nxlx-sysd.service nxlx-supportd.service nxlx-sound.service \
	nxlx-raster.socket nxlx-usb@.service nxlx-update-usb@.service nxlx-update-inbox@.service; do
	[ -f "/etc/systemd/system/$f" ] || fail "the unit $f is missing"
done
# What systemd, tmpfiles and udev read, not the comments (a comment may name the old layout to explain it).
for f in /etc/systemd/system/nxlx-* /etc/tmpfiles.d/nxlx*.conf /etc/udev/rules.d/99-nxlx-usb.rules; do
	[ -f "$f" ] || continue
	if grep -v '^[[:space:]]*#' "$f" | grep -qE '(/opt|/etc|/var/lib|/run|/media)/pvj|pvj-[a-z]|=pvj$'; then
		fail "$f still names an old account, folder or unit outside its comments"
	fi
done
grep -q '^User=nxlx-dev$' /etc/systemd/system/nxlx-player.service || fail "the player does not run as nxlx-dev"
is /etc/sudoers.d/010_nxlx-dev "root:root 440 regular file"
visudo -c -q -f /etc/sudoers.d/010_nxlx-dev || fail "the converted sudoers file does not pass visudo"
sudo -l -U nxlx-dev | grep -q NOPASSWD || fail "nxlx-dev lost sudo"
is /etc/udev/rules.d/99-nxlx-usb.rules "root:root 644 regular file"
is /etc/tmpfiles.d/nxlx.conf "root:root 644 regular file"
grep -q '"state": "carried-same"' /var/backups/nxlx-convert/journal.jsonl || fail "the converter did not find the settings and the node id the same before it started the services"
settings_now="$(settings_but_auth /var/lib/nxlx/settings.json)"
if [ "$settings_now" != "$SETTINGS_REST" ]; then
	diff <(tr ',' '\n' <<<"$SETTINGS_REST") <(tr ',' '\n' <<<"$settings_now") >&2 || true
	fail "the settings changed in the conversion (more than the PIN the panel makes at every start)"
fi
only_the_new_layout
services_and_their_files nxlx-dev
panel_takes_token_after_conversion() { [ "$(status_code "$OLD_TOKEN")" = 200 ]; }
# The converter starts the panel again a moment before this point; give it time to listen (two CI runs on
# 2026-11-22 met port 80 closed one second after the restart and failed here, then passed on a rerun).
wait_for 40 panel_takes_token_after_conversion || fail "the device paired before the conversion was signed out"
[ "$(status_code not-a-token)" != 200 ] || fail "the panel takes any token"
if out="$(/usr/local/bin/nxlx-update rollback 2>&1)"; then fail "a rollback to the release with the old names was not refused"; fi
grep -q "nxlx-convert --undo" <<<"$out" || fail "the refused rollback does not name the way back: $out"
[ "$(readlink /opt/nxlx/current)" = "/opt/nxlx/releases/$NEW_VERSION" ] || fail "the refused rollback moved current"
systemctl is-active --quiet nxlx-convert-undo.timer || fail "the undo timer is not armed"
listing

step "nxlx-convert --undo: the old names and the release before come back"
/usr/local/bin/nxlx-convert --undo || fail "the undo failed"
for u in pvj-web pvj-dev; do getent passwd "$u" >/dev/null || fail "the account $u is not back"; done
[ "$(id -u pvj-web)" = "$UID_WEB" ] || fail "pvj-web came back with another uid"
[ "$(getent group pvj | cut -d: -f3)" = "$GID_SVC" ] || fail "the group pvj came back with another gid"
[ "$(readlink /opt/pvj/current)" = "$OLD_CURRENT" ] || fail "current is not the old release again: $(readlink /opt/pvj/current)"
[ ! -e /opt/nxlx ] || fail "/opt/nxlx is still there after the undo"
visudo -c -q -f /etc/sudoers.d/010_pvj-dev || fail "the sudoers file of pvj-dev does not pass visudo after the undo"
sudo -l -U pvj-dev | grep -q NOPASSWD || fail "pvj-dev lost sudo in the undo"
wait_for 40 systemctl is-active --quiet pvj-web.service || fail "the old panel does not run again after the undo"
panel_takes_old_token() { [ "$(status_code "$OLD_TOKEN")" = 200 ]; }
wait_for 40 panel_takes_old_token || fail "the old panel does not take the token again after the undo"      # it needs about a second to listen
# Only the converter's own command link (new with this release, no change of the run) is left of the new names.
left="$(find /etc/systemd/system /usr/local/bin -name '*nxlx-*' ! -name 'nxlx-convert' ! -path '*/nxlx-convert-*' 2>/dev/null)"
[ -z "$left" ] || fail "the undo left names of this release: $left"

step "this release again, then kept with nxlx-convert --confirm"
logged "$SRC/install/install.sh" --offline
grep -q "CONVERTED from the old names" "$OUT" || fail "the second conversion did not happen"
/usr/local/bin/nxlx-convert --confirm || fail "the confirm failed"
if systemctl is-enabled --quiet nxlx-convert-undo.timer 2>/dev/null; then fail "the undo timer is still enabled after the confirm"; fi
services_and_their_files nxlx-dev
wait_for 40 panel_takes_token_after_conversion || fail "the device paired on the old release was signed out by the second conversion"

step "the tests below start from a plain machine of their own: the converted node's services are stopped and its runtime folders taken away"
systemctl stop nxlx-raster.socket 2>/dev/null || true
for u in nxlx-web nxlx-player nxlx-netd nxlx-sysd nxlx-supportd nxlx-sound nxlx-link; do systemctl stop "$u.service" 2>/dev/null || true; done
rm -rf /run/nxlx /run/pvj /run/pvj-* /var/lib/nxlx-player

step "the state an older version leaves: one shared folder owned by a service account, with things planted in it"
getent group nxlx-svc >/dev/null || groupadd --system nxlx-svc
id nxlx-web >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin --gid nxlx-svc nxlx-web
useradd --system --create-home --home-dir /var/lib/nxlx-player --shell /usr/sbin/nologin --gid nxlx-svc "$A"
install -d -o "$A" -g nxlx-svc -m 0770 /run/nxlx
runuser -u "$A" -- sh -c '
	cd /run/nxlx
	echo 1234 > pin
	echo 1759570000 > undervoltage-seen
	: > player.sock
	: > netd.sock
	mkdir netd web
	: > netd/netd.sock
	echo 9999 > web/pin
	ln -s /root/nxlx-planted-by-root web/undervoltage-seen
	ln -s /etc player
	ln -s /etc/passwd shader-1-1.glsl
	: > .d45
'
listing

step "upgrade: the real installer over that folder"
installer
grep -q "a folder per service" "$OUT" || fail "the installer did not see the old layout"
if grep -q "systemd-tmpfiles reported a problem" "$OUT"; then fail "systemd-tmpfiles reported a problem"; fi
only_the_new_layout
services_and_their_files
listing

step "restart each service: nothing changes hands"
for u in nxlx-player nxlx-web nxlx-netd nxlx-sysd; do
	systemctl restart "$u.service"
	sleep 3
	only_the_new_layout
	services_and_their_files
done

step "a crash: the helper and the player are killed and come back by themselves"
systemctl kill -s KILL nxlx-netd.service
systemctl kill -s KILL nxlx-player.service || true
sleep 6
only_the_new_layout
services_and_their_files
listing

step "what the accounts must not be able to do"
refused "$A" cat /run/nxlx/web/pin
refused "$A" touch /run/nxlx/x
refused "$A" ln -s /etc /run/nxlx/x
refused "$A" mv /run/nxlx/netd /run/nxlx/n2
refused "$A" rm /run/nxlx/netd/netd.sock
refused "$A" touch /run/nxlx/web/x
refused "$A" rm /run/nxlx/.d45
refused nxlx-web touch /run/nxlx/x
refused nxlx-web ln -s /etc /run/nxlx/x
refused nxlx-web mv /run/nxlx/netd /run/nxlx/n2
refused nxlx-web rm /run/nxlx/netd/netd.sock
refused nxlx-web touch /run/nxlx/netd/x
refused nxlx-web rm /run/nxlx/.d45
if [ "$PLAYER_SEEN" = 1 ]; then
	refused nxlx-web touch /run/nxlx/player/x
	refused nxlx-web rm /run/nxlx/player/player.sock
fi
only_the_new_layout

step "the preview through the panel, inside its sandbox (it must not fail on a read-only file system)"
PIN="$("$SRC/bin/nxlx-pin")"
TOKEN="$(curl -sS -m 20 -H 'Content-Type: application/json' -H 'X-MCTL-Request: 1' -d "{\"pin\": \"$PIN\", \"name\": \"ci\"}" http://127.0.0.1/api/pair | sed -n 's/.*"token": *"\([^"]*\)".*/\1/p')"
[ -n "$TOKEN" ] || fail "could not pair with the panel"
CODE="$(curl -sS -m 30 -o /tmp/mctl-preview.out -w '%{http_code}' -H "Authorization: Bearer $TOKEN" http://127.0.0.1/api/preview.jpg)"
echo "preview: HTTP $CODE, $(wc -c < /tmp/mctl-preview.out) bytes"
if grep -a -q -i "read-only" /tmp/mctl-preview.out; then fail "the preview failed on a read-only file system: $(head -c 300 /tmp/mctl-preview.out)"; fi
if [ "$CODE" = 200 ]; then
	echo "a picture came back"
else
	echo "NOTE: no picture on this machine (no display): $(head -c 300 /tmp/mctl-preview.out)"
fi

step "reinstall over the new layout: nothing is emptied, a link planted in the panel's folder leads root nowhere"
runuser -u nxlx-web -- ln -s "$PLANTED" /run/nxlx/web/undervoltage-seen
: > /run/nxlx/stray
installer
if grep -q "a folder per service" "$OUT"; then fail "a sound folder was emptied again"; fi
if grep -q "systemd-tmpfiles reported a problem" "$OUT"; then fail "systemd-tmpfiles reported a problem on a reinstall"; fi
[ ! -e /run/nxlx/stray ] || fail "a stray file directly in /run/nxlx was left"
[ "$(readlink /run/nxlx/web/undervoltage-seen)" = "$PLANTED" ] || fail "the installer touched a name in the panel's folder"
runuser -u nxlx-web -- rm /run/nxlx/web/undervoltage-seen
only_the_new_layout
services_and_their_files

step "an update from a release that ran the renderer as pvj-render (rename PR 5): the new release has no pvj-render, the old one keeps its own"
# The release before the rename, as a copy of this one with the old program in it (this runner has no libmpv headers,
# so a plain file stands in for the built program), made current as an update finds it. A pvj-render built by hand in
# the source folder is planted too: the installer must not carry it into the new release.
OLD_REL=/opt/nxlx/releases/0.0.1
NEW_REL="$(readlink -f /opt/nxlx/current)"
PREV_SAVED="$(mktemp)"
had_previous=0
if [ -f /opt/nxlx/previous ]; then cp /opt/nxlx/previous "$PREV_SAVED"; had_previous=1; fi
cp -a "$NEW_REL" "$OLD_REL"
printf '#!/bin/sh\nexit 0\n' > "$OLD_REL/render/pvj-render"
chmod 755 "$OLD_REL/render/pvj-render"
ln -sfn "$OLD_REL" /opt/nxlx/current.tmp && mv -T /opt/nxlx/current.tmp /opt/nxlx/current
cp "$OLD_REL/render/pvj-render" "$SRC/render/pvj-render"
installer
rm -f "$SRC/render/pvj-render"
[ "$(readlink -f /opt/nxlx/current)" = "$NEW_REL" ] || fail "the update did not make the new release current: $(readlink /opt/nxlx/current)"
[ ! -e "$NEW_REL/render/pvj-render" ] || fail "the new release holds the old renderer's program"
[ -x "$OLD_REL/render/pvj-render" ] || fail "the old release lost its own program (a rollback needs it)"
[ "$(cat /opt/nxlx/previous)" = "$OLD_REL" ] || fail "the old release is not the one a rollback goes to: $(cat /opt/nxlx/previous)"
# What runs or starts the renderer: the package, render/, the commands in bin/ and the node's units. bin/nxlx-convert
# is left out: its rename table names the old program on purpose, as the name it converts from.
ls -d /etc/systemd/system/nxlx-* >/dev/null 2>&1 || fail "no nxlx-* units to search (renamed? update this check)"
if grep -rIl "pvj-render" "$NEW_REL/mctl" "$NEW_REL/render" /etc/systemd/system/nxlx-* \
	|| grep -rIl --exclude=nxlx-convert "pvj-render" "$NEW_REL/bin"; then
	fail "a file of the new release or a unit still names pvj-render"
fi
grep -q "mctl-render" "$NEW_REL/mctl/renderer.py" || fail "the player does not look for mctl-render"
rm -rf "$OLD_REL"
if [ "$had_previous" = 1 ]; then cp "$PREV_SAVED" /opt/nxlx/previous; else rm -f /opt/nxlx/previous; fi
rm -f "$PREV_SAVED"
only_the_new_layout
services_and_their_files

step "an emptying that was cut short (no marker) with a planted helper folder is finished by the next run, also with --no-start"
systemctl stop nxlx-netd.service
rm -f /run/nxlx/.d45
install -d -o "$A" -g nxlx-svc -m 0750 /run/nxlx/netd
runuser -u "$A" -- sh -c ': > /run/nxlx/netd/netd.sock'
installer --no-start
grep -q "a folder per service" "$OUT" || fail "an unfinished emptying was not noticed"
if [ -e /run/nxlx/netd/netd.sock ] && [ ! -S /run/nxlx/netd/netd.sock ]; then fail "the planted netd.sock is still there"; fi
if [ -d /run/nxlx/netd ]; then is /run/nxlx/netd "root:nxlx-svc 750 directory"; fi
wait_for 40 systemctl is-active --quiet nxlx-web.service || fail "the panel, which was running, was not started again after --no-start"
systemctl start nxlx-netd.service
only_the_new_layout
services_and_their_files

step "a service folder with the wrong owner is not repaired by chown: everything is emptied and made again"
systemctl stop nxlx-web.service
chown -R "$A":nxlx-svc /run/nxlx/web
installer
grep -q "a folder per service" "$OUT" || fail "a folder with the wrong owner was accepted"
only_the_new_layout
services_and_their_files

# ---- the NDI input is opt-in per box (D62, the owner's answer of 2026-10-08) --------------------------------------
# Everything above ran plain installs. NDI's own library is never here (it is not in the repository and is not
# fetched), so what is checked is root's part: that a plain install leaves nothing of NDI, what the opt-in changes
# under a real systemd (the group must exist before the panel and the player start with it), that a later plain
# install keeps and refreshes it, and that opting out takes it off again. Not a test that NDI works.
# What is NOT run here, and cannot be: the real command with NDI's library ("nxlx-ndi-runtime install <the NDI SDK>"),
# which only a person with the SDK has. In these steps the command is run only with a file it refuses, and the setup
# it would go on to is called directly (ndi_setup enable). Further down the whole command does run, with a stand-in
# for the library ("an NDI picture all the way"). With NDI's own file it is device step N2.
NDI_WEB_DROPIN=/etc/systemd/system/nxlx-web.service.d/50-nxlx-ndi.conf
NDI_PLAYER_DROPIN=/etc/systemd/system/nxlx-player.service.d/50-nxlx-ndi.conf
ndi_setup() {      # ndi_setup enable|disable: root's code as `nxlx-ndi-runtime install|remove` runs it, without a library
	python3 -B -c 'import sys; sys.path.insert(0, "/opt/nxlx/current"); from mctl import ndisetup; getattr(ndisetup, sys.argv[1])()' "$1"
}
groups_of() { systemctl show -p SupplementaryGroups --value "$1"; }
panel_answers() { curl -sS -m 5 -o /dev/null http://127.0.0.1/api/hello; }
ndi_page() {       # the panel's own account of NDI, with the module on
	wait_for 40 panel_answers || fail "the panel does not answer"
	local code
	code="$(curl -sS -m 20 -o "$OUT" -w '%{http_code}' -H 'Content-Type: application/json' -H 'X-MCTL-Request: 1' -H "Authorization: Bearer $TOKEN" -d '{"enabled": true}' http://127.0.0.1/api/modules/inputs-ndi)"
	[ "$code" = 200 ] || fail "the NDI module could not be switched on: HTTP $code, $(head -c 300 "$OUT")"
	code="$(curl -sS -m 20 -o "$OUT" -w '%{http_code}' -H "Authorization: Bearer $TOKEN" http://127.0.0.1/api/ndi)"
	cat "$OUT"
	echo
	[ "$code" = 200 ] || fail "GET /api/ndi answered HTTP $code"
}
in_group() {       # in_group PID GID: the running process has the group
	grep -E "^Groups:.*[[:space:]]$2([[:space:]]|\$)" "/proc/$1/status"
}
avahi_state() { if dpkg -s avahi-daemon >/dev/null 2>&1; then echo installed; else echo absent; fi; }
nothing_of_ndi() {
	local path
	for path in /etc/systemd/system/nxlx-ndi.service /etc/systemd/system/nxlx-web.service.d /etc/systemd/system/nxlx-player.service.d \
		/etc/systemd/system/multi-user.target.wants/nxlx-ndi.service /run/nxlx-ndi /opt/nxlx-ndi; do
		if [ -e "$path" ] || [ -L "$path" ]; then fail "a box that did not opt in has $path"; fi
	done
	if systemctl is-active --quiet nxlx-ndi.service; then fail "nxlx-ndi runs on a box that did not opt in"; fi
	if systemctl is-enabled --quiet nxlx-ndi.service 2>/dev/null; then fail "nxlx-ndi is enabled on a box that did not opt in"; fi
	[ "$(groups_of nxlx-web.service)" = "audio video" ] || fail "without NDI the panel's extra groups are: $(groups_of nxlx-web.service)"
	[ "$(groups_of nxlx-player.service)" = "video render audio input" ] || fail "without NDI the player's extra groups are: $(groups_of nxlx-player.service)"
	if grep -q "nxlx-ndi" /etc/systemd/system/nxlx-web.service /etc/systemd/system/nxlx-player.service; then
		fail "a unit the installer wrote names nxlx-ndi"
	fi
}

step "NDI is opt-in: after every plain install above, this machine has nothing of it"
nothing_of_ndi
if getent passwd nxlx-ndi >/dev/null || getent group nxlx-ndi >/dev/null; then fail "a plain install made the account or the group nxlx-ndi"; fi
if [ -e /opt/nxlx-ndi ]; then fail "a plain install made /opt/nxlx-ndi"; fi
if [ "$(avahi_state)" != "$AVAHI_BEFORE" ]; then fail "a plain install changed avahi-daemon: it was $AVAHI_BEFORE"; fi
[ "$(readlink /usr/local/bin/nxlx-ndi-runtime)" = /opt/nxlx/current/bin/nxlx-ndi-runtime ] || fail "the opt-in command is not on the path"
/usr/local/bin/nxlx-ndi-runtime status > "$OUT"
cat "$OUT"
grep -q "NDI is not set up on this node" "$OUT" || fail "the status command does not say that NDI is not set up"
ndi_page
grep -q '"setup": false' "$OUT" || fail "the panel does not say that NDI is not set up on this box"
grep -q 'sudo nxlx-ndi-runtime install' "$OUT" || fail "the panel does not name the one command"

step "the real command with a file that is not NDI's library: refused, and nothing is set up"
if /usr/local/bin/nxlx-ndi-runtime install /etc/hostname; then fail "a file that is not the library was accepted"; fi
nothing_of_ndi
if getent passwd nxlx-ndi >/dev/null || getent group nxlx-ndi >/dev/null; then fail "a refused library still made the account or the group"; fi
if [ -e /opt/nxlx-ndi ] || [ -L /opt/nxlx-ndi ]; then fail "a refused file left /opt/nxlx-ndi behind"; fi
if [ "$(avahi_state)" != "$AVAHI_BEFORE" ]; then fail "a refused library still changed avahi-daemon"; fi

step "opt in (root's part of 'nxlx-ndi-runtime install', without a library): the helper, its account, the two drop-ins"
ndi_setup enable
[ "$(id -nG nxlx-ndi)" = nxlx-ndi ] || fail "the account nxlx-ndi is in: $(id -nG nxlx-ndi)"
[ "$(getent passwd nxlx-ndi | cut -d: -f7)" = /usr/sbin/nologin ] || fail "the account nxlx-ndi has a shell"
is /etc/systemd/system/nxlx-ndi.service "root:root 644 regular file"
is "$NDI_WEB_DROPIN" "root:root 644 regular file"
is "$NDI_PLAYER_DROPIN" "root:root 644 regular file"
sed -e "s|@NXLX_DIR@|/opt/nxlx/current|g" "$SRC/mctl/systemd/nxlx-ndi.service" | cmp - /etc/systemd/system/nxlx-ndi.service || fail "the helper's unit is not the template"
systemctl is-enabled --quiet nxlx-ndi.service || fail "nxlx-ndi is not enabled after the opt-in"
if ! wait_for 40 systemctl is-active --quiet nxlx-ndi.service; then
	systemctl status nxlx-ndi.service --no-pager -l >&2 || true
	fail "nxlx-ndi is not active after the opt-in"
fi
wait_for 40 test -S /run/nxlx-ndi/ndi.sock || fail "nxlx-ndi made no socket"
is /run/nxlx-ndi "nxlx-ndi:nxlx-ndi 750 directory"
is /run/nxlx-ndi/ndi.sock "nxlx-ndi:nxlx-ndi 660 socket"
[ "$(systemctl show -p User --value nxlx-ndi.service)" = nxlx-ndi ] || fail "nxlx-ndi does not run as its own account"
[ -z "$(groups_of nxlx-ndi.service)" ] || fail "the helper has extra groups: $(groups_of nxlx-ndi.service)"
# the drop-ins add to the units' own groups; they do not replace them
[ "$(groups_of nxlx-web.service)" = "audio video nxlx-ndi" ] || fail "the panel's extra groups are: $(groups_of nxlx-web.service)"
[ "$(groups_of nxlx-player.service)" = "video render audio input nxlx-ndi" ] || fail "the player's extra groups are: $(groups_of nxlx-player.service)"
only_the_new_layout
services_and_their_files
NDI_GID="$(getent group nxlx-ndi | cut -d: -f3)"
in_group "$(systemctl show -p MainPID --value nxlx-web.service)" "$NDI_GID" || fail "the running panel was not restarted into the group nxlx-ndi"
ndi_page
grep -q '"setup": true' "$OUT" || fail "the panel does not see that the box opted in"
grep -q '"helper": true' "$OUT" || fail "the panel cannot reach the helper it was given the group for"
grep -q '"present": false' "$OUT" || fail "the panel says NDI's library is here; it is not on this machine"
/usr/local/bin/nxlx-ndi-runtime status > "$OUT"
cat "$OUT"
grep -q "NDI is set up on this node" "$OUT" || fail "the status command does not see the opt-in"
refused nxlx-ndi cat /run/nxlx/web/pin
refused nxlx-ndi ls /run/nxlx/web
refused nxlx-ndi ls /run/nxlx/netd
refused nxlx-ndi cat /var/lib/nxlx/settings.json
refused "$A" touch /run/nxlx-ndi/x
refused nxlx-web touch /run/nxlx-ndi/x

step "a release without the helper's program (a rollback to one from before NDI): the unit is skipped, not failed for ever"
mv /opt/nxlx/current/bin/nxlx-ndi /opt/nxlx/current/bin/nxlx-ndi.away
systemctl restart nxlx-ndi.service || true
RESTARTS="$(systemctl show -p NRestarts --value nxlx-ndi.service)"
sleep 7            # the unit starts a failed helper again every 2 seconds, without a limit
systemctl status nxlx-ndi.service --no-pager -l || true
[ "$(systemctl show -p NRestarts --value nxlx-ndi.service)" = "$RESTARTS" ] || fail "without its program the helper was started again and again: NRestarts went from $RESTARTS to $(systemctl show -p NRestarts --value nxlx-ndi.service)"
if systemctl is-active --quiet nxlx-ndi.service; then fail "nxlx-ndi counts as running without its program"; fi
if systemctl is-failed --quiet nxlx-ndi.service; then fail "nxlx-ndi counts as failed without its program; a condition that is not met must not be a failure"; fi
[ "$(systemctl show -p ConditionResult --value nxlx-ndi.service)" = no ] || fail "the unit's condition was not what stopped it"
mv /opt/nxlx/current/bin/nxlx-ndi.away /opt/nxlx/current/bin/nxlx-ndi
systemctl restart nxlx-ndi.service
wait_for 40 systemctl is-active --quiet nxlx-ndi.service || fail "nxlx-ndi did not come back with its program"
wait_for 40 test -S /run/nxlx-ndi/ndi.sock || fail "nxlx-ndi made no socket after its program came back"

step "an update over a box that an earlier release set up: the folders its tmpfiles lines name are made, and NDI works without a reboot"
# An earlier release of the helper had no pipe folder of root's and no folder for NDI's configuration file. The
# installer writes their lines again; it must also make them, before it starts the helper and the panel again.
systemctl stop nxlx-ndi.service
rm -rf /run/nxlx-ndi-pipe /run/nxlx-ndi-config
rm -f /etc/tmpfiles.d/nxlx-ndi.conf
installer
if grep -q "systemd-tmpfiles could not make the NDI folders" "$OUT"; then fail "the installer could not make the NDI folders"; fi
is /etc/tmpfiles.d/nxlx-ndi.conf "root:root 644 regular file"
is /run/nxlx-ndi-pipe "root:nxlx-ndi 750 directory"
is /run/nxlx-ndi-pipe/ndi.fifo "root:nxlx-ndi 660 fifo"
is /run/nxlx-ndi-config "nxlx-web:nxlx-ndi 750 directory"
wait_for 40 systemctl is-active --quiet nxlx-ndi.service || fail "nxlx-ndi is not active after an update over an older set-up"
wait_for 40 test -S /run/nxlx-ndi/ndi.sock || fail "nxlx-ndi made no socket after an update over an older set-up"
runuser -u nxlx-web -- touch /run/nxlx-ndi-config/probe || fail "the panel's account cannot write NDI's configuration folder"
rm -f /run/nxlx-ndi-config/probe
refused nxlx-ndi touch /run/nxlx-ndi-config/x
refused nxlx-ndi rm /run/nxlx-ndi-pipe/ndi.fifo
# The helper's own look at the pipe before it writes, as its account and on root's pipe (found on a Pi, 2026-10-12:
# it took only a pipe of its own, so on a box every source ended "the input pipe was replaced").
runuser -u nxlx-ndi -- python3 -B -c 'import os, sys; sys.path.insert(0, "/opt/nxlx/current"); from mctl import ndi
fd = os.open("/run/nxlx-ndi-pipe/ndi.fifo", os.O_RDWR | os.O_NONBLOCK)
sys.exit(0 if ndi.pipe_to_write(os.fstat(fd)) and os.fstat(fd).st_uid != os.getuid() else 1)' \
	|| fail "the helper's account would not write root's pipe"
ndi_page
grep -q '"helper": true' "$OUT" || fail "after an update over an older set-up the panel cannot reach the helper"

step "a later plain install keeps an opted-in box opted in and writes its unit again"
echo "# a line from an older version" >> /etc/systemd/system/nxlx-ndi.service
rm "$NDI_PLAYER_DROPIN"
installer
grep -q "this box has NDI set up" "$OUT" || fail "the installer did not see the opt-in"
if grep -q "could not write the NDI helper" "$OUT"; then fail "the installer could not write the helper's unit again"; fi
sed -e "s|@NXLX_DIR@|/opt/nxlx/current|g" "$SRC/mctl/systemd/nxlx-ndi.service" | cmp - /etc/systemd/system/nxlx-ndi.service || fail "the helper's unit was not written again"
is "$NDI_PLAYER_DROPIN" "root:root 644 regular file"
systemctl is-enabled --quiet nxlx-ndi.service || fail "nxlx-ndi is no longer enabled after a plain install"
wait_for 40 systemctl is-active --quiet nxlx-ndi.service || fail "nxlx-ndi is not active after a plain install"
wait_for 40 test -S /run/nxlx-ndi/ndi.sock || fail "nxlx-ndi made no socket after a plain install"
[ "$(groups_of nxlx-web.service)" = "audio video nxlx-ndi" ] || fail "after a plain install the panel's extra groups are: $(groups_of nxlx-web.service)"
only_the_new_layout
services_and_their_files
installer --no-start        # as an update from the panel runs it
systemctl is-enabled --quiet nxlx-ndi.service || fail "nxlx-ndi is no longer enabled after an update"
wait_for 40 systemctl is-active --quiet nxlx-ndi.service || fail "nxlx-ndi, which was running, is not running after an update"
services_and_their_files

step "opt out (root's part of 'nxlx-ndi-runtime remove'): the helper and the drop-ins go, the panel and the player run on"
ndi_setup disable > "$OUT"
cat "$OUT"
grep -q "Left in place: the account and group nxlx-ndi" "$OUT" || fail "opting out does not say what it leaves"
sleep 2
nothing_of_ndi
getent passwd nxlx-ndi >/dev/null || fail "opting out removed the account, which it says it leaves"
only_the_new_layout
services_and_their_files
if in_group "$(systemctl show -p MainPID --value nxlx-web.service)" "$NDI_GID"; then fail "the panel still runs in the group nxlx-ndi"; fi
installer
if grep -q "this box has NDI set up" "$OUT"; then fail "a plain install after opting out set NDI up again"; fi
nothing_of_ndi
services_and_their_files

step "a helper's unit without the mark (a setup cut short, or a box from when the helper went on every box) is removed by the next install"
sed -e "s|@NXLX_DIR@|/opt/nxlx/current|g" "$SRC/mctl/systemd/nxlx-ndi.service" > /etc/systemd/system/nxlx-ndi.service
systemctl daemon-reload
systemctl enable --now nxlx-ndi.service
wait_for 40 systemctl is-active --quiet nxlx-ndi.service || fail "the helper without a mark did not start (the test's own set-up)"
installer
grep -q "removing an NDI helper that nobody set up" "$OUT" || fail "the installer left a helper that nobody set up"
nothing_of_ndi
services_and_their_files

step "the NDI discovery server's set-up (D85), with a stand-in for NDI's program: the account, the unit, the supervisor idle"
# NDI's program is never on this machine. /usr/bin/true is a program of this processor, which is all the installer's
# checks ask of a file; it is placed, never started here (the module stays off, so the supervisor runs nothing).
if getent passwd nxlx-ndisc >/dev/null; then fail "the account nxlx-ndisc exists before anyone set the discovery server up"; fi
if /usr/local/bin/nxlx-ndi-runtime server install /etc/hostname; then fail "a file that is not a program was accepted as the discovery server"; fi
if [ -e /opt/nxlx-ndi-server ] || [ -e /etc/systemd/system/nxlx-ndi-discovery.service ]; then fail "a refused file left something of the discovery server behind"; fi
/usr/local/bin/nxlx-ndi-runtime server install "$(readlink -f /usr/bin/true)" 2>&1 | tee "$OUT"
grep -q "^sha256 " "$OUT" || fail "the command did not print the program's sha256"
is /opt/nxlx-ndi-server "root:root 755 directory"
is /opt/nxlx-ndi-server/ndi-discovery-server "root:root 755 regular file"
[ "$(id -nG nxlx-ndisc)" = nxlx-ndisc ] || fail "the account nxlx-ndisc is in: $(id -nG nxlx-ndisc)"
[ "$(getent passwd nxlx-ndisc | cut -d: -f7)" = /usr/sbin/nologin ] || fail "the account nxlx-ndisc has a shell"
is /etc/systemd/system/nxlx-ndi-discovery.service "root:root 644 regular file"
sed -e "s|@NXLX_DIR@|/opt/nxlx/current|g" "$SRC/mctl/systemd/nxlx-ndi-discovery.service" | cmp - /etc/systemd/system/nxlx-ndi-discovery.service || fail "the discovery server's unit is not the template"
systemctl is-enabled --quiet nxlx-ndi-discovery.service || fail "nxlx-ndi-discovery is not enabled after the set-up"
if ! wait_for 40 systemctl is-active --quiet nxlx-ndi-discovery.service; then
	systemctl status nxlx-ndi-discovery.service --no-pager -l >&2 || true
	journalctl -b --no-pager -u nxlx-ndi-discovery -n 40 >&2 || true
	fail "nxlx-ndi-discovery is not active after the set-up"
fi
wait_for 40 test -S /run/nxlx-ndisc/ndisc.sock || fail "the supervisor made no socket"
is /run/nxlx-ndisc "nxlx-ndisc:nxlx-ndisc 750 directory"
[ "$(systemctl show -p User --value nxlx-ndi-discovery.service)" = nxlx-ndisc ] || fail "the supervisor does not run as its own account"
if pgrep -f /opt/nxlx-ndi-server/ndi-discovery-server >/dev/null; then fail "the program runs although its module is off"; fi
wait_for 40 systemctl is-active --quiet nxlx-web.service || fail "the panel did not come back after the set-up"
is /run/nxlx-ndisc/ndisc.sock "nxlx-ndisc:nxlx-ndisc 660 socket"
refused nxlx-ndisc cat /run/nxlx/web/pin
refused nxlx-ndisc cat /var/lib/nxlx/settings.json
refused nxlx-ndisc ls /run/nxlx/web
refused "$A" touch /run/nxlx-ndisc/x
# the panel reaches the supervisor (the drop-in's group, the socket, the supervisor's list of who may ask)
ndisc_get() {
	local code
	code="$(curl -sS -m 20 -o "$OUT" -w '%{http_code}' -H "Authorization: Bearer $TOKEN" http://127.0.0.1/api/ndi/discovery)"
	[ "$code" = 200 ] || fail "GET /api/ndi/discovery answered HTTP $code, $(head -c 300 "$OUT")"
}
ndisc_post() {     # ndisc_post PATH JSON
	local code
	code="$(curl -sS -m 30 -o "$OUT" -w '%{http_code}' -H 'Content-Type: application/json' -H 'X-MCTL-Request: 1' -H "Authorization: Bearer $TOKEN" -d "$2" "http://127.0.0.1$1")"
	[ "$code" = 200 ] || fail "POST $1 answered HTTP $code, $(head -c 300 "$OUT")"
}
wait_for 40 panel_answers || fail "the panel does not answer after the discovery server's set-up"
ndisc_get
cat "$OUT"
echo
grep -q '"setup": true' "$OUT" || fail "the panel does not see the discovery server's set-up"
grep -q '"supervisor": true' "$OUT" || fail "the panel cannot reach the supervisor it was given the group for"
grep -q '"installed": true' "$OUT" || fail "the supervisor does not accept the program root placed"
grep -q '"state": "off"' "$OUT" || fail "the server is not off before anyone switched it on"
# Switched on, the stand-in program is started under the unit's sandbox and ends at once (it is /usr/bin/true): the
# supervisor says so and starts it again with a pause, and nothing else on the box goes down with it.
ndisc_post /api/ndi/discovery '{"network": "all", "port": 5959}'
ndisc_post /api/modules/ndi-discovery '{"enabled": true}'
restarted() { ndisc_get; grep -q '"restarts": [1-9]' "$OUT"; }
wait_for 40 restarted || fail "the program that ends was not started again: $(head -c 400 "$OUT")"
cat "$OUT"
echo
grep -q '"state": "failed"' "$OUT" || grep -q '"state": "running"' "$OUT" || fail "the page is not told that the program ended or runs"
systemctl is-active --quiet nxlx-ndi-discovery.service || fail "the supervisor went down with the program it runs"
systemctl is-active --quiet nxlx-web.service || fail "the panel went down with the discovery server's program"
ndisc_post /api/modules/ndi-discovery '{"enabled": false}'
ndisc_get
grep -q '"state": "off"' "$OUT" || fail "switched off, the server is not off: $(head -c 300 "$OUT")"
ndisc_post /api/ndi/discovery '{"network": "wired", "port": 5959}'
installer
grep -q "this box has the discovery server set up" "$OUT" || fail "the installer did not see the discovery server's set-up"
wait_for 40 systemctl is-active --quiet nxlx-ndi-discovery.service || fail "nxlx-ndi-discovery is not active after a later install"
/usr/local/bin/nxlx-ndi-runtime server remove 2>&1 | tee "$OUT"
for path in /etc/systemd/system/nxlx-ndi-discovery.service /etc/systemd/system/nxlx-web.service.d/50-nxlx-ndisc.conf \
	/etc/systemd/system/multi-user.target.wants/nxlx-ndi-discovery.service /opt/nxlx-ndi-server; do
	if [ -e "$path" ] || [ -L "$path" ]; then fail "after 'server remove' there is still $path"; fi
done
if systemctl is-active --quiet nxlx-ndi-discovery.service; then fail "nxlx-ndi-discovery still runs after 'server remove'"; fi
wait_for 40 systemctl is-active --quiet nxlx-web.service || fail "the panel did not come back after 'server remove'"
nothing_of_ndi
services_and_their_files

# ---- an NDI picture all the way, on the box layout, with a stand-in for NDI's library ------------------------------
# Why (2026-10-12, found on the owner's Pi): since the pipe became root's, the helper refused to write it, and no NDI
# picture reached the screen of any box. Every test was green, because every test ran the helper where it makes a
# pipe of its own. So here the whole road is taken as a box has it: the real accounts, the real units and the
# helper's sandbox, root's pipe from the tmpfiles lines, the real nxlx-ndi service, the panel's API, the real mpv.
# The one thing that is not real is the library: tests/ndi_standin/standin.c, written from what mctl/ndi.py calls,
# which makes up three sources and touches no network. So this never shows that NDI's own library works, finds a
# sender or gets through the sandbox's network rules.
# And this machine has no screen: the player is started with mpv's null video and sound outputs (a drop-in of this
# test's, in /run, gone at the end), because without any output mpv plays nothing and lets go of the pipe. What is
# shown is that the player reads and decodes the pipe, not that a picture is on a display.
NDI_BUILD="$(mktemp -d)"
NDI_STANDIN="$NDI_BUILD/libndi.so.6"
NDI_LIB=/opt/nxlx-ndi/libndi.so.6
NDI_PIPE=/run/nxlx-ndi-pipe/ndi.fifo
NDI_PLAYER_CI=/run/systemd/system/nxlx-player.service.d/90-ci-no-screen.conf
MPV_LOG=/tmp/mctl-ci-mpv.log
NDI_MOVING="MCTL-STANDIN (moving)"
NDI_SOUND="MCTL-STANDIN (moving with sound)"
NDI_STILL="MCTL-STANDIN (one still then sound)"
NDI_CHANGES="MCTL-STANDIN (changes size)"
# 1280 x 720: a picture is 1.8 MB, more than the pipe holds, so the writer is in the middle of a picture most of the time
NDI_BIG_SOUND="MCTL-STANDIN (big moving with sound)"
NDI_BIG_STILL="MCTL-STANDIN (big one still then sound)"
NDI_BIG_A="MCTL-STANDIN (big at once A)"
NDI_BIG_B="MCTL-STANDIN (big at once B)"
NDI_BIG_A_SOUND="MCTL-STANDIN (big at once A with sound)"
NDI_BIG_B_SOUND="MCTL-STANDIN (big at once B with sound)"
NDI_BIG_CHANGES="MCTL-STANDIN (big changes size)"
NDI_BIG_CHANGES_SOUND="MCTL-STANDIN (big changes size with sound)"
NDI_SOUND_ONLY="MCTL-STANDIN (sound only)"
box() {            # the questions to the panel and the player (tests/ndi_standin/box_check.py); the token stays off the command line
	NXLX_TOKEN="$TOKEN" python3 -B "$SRC/tests/ndi_standin/box_check.py" "$@"
}
sound_connections_of() {   # sound_connections_of PID: how often that helper process said it opened a connection for the box's sound source
	journalctl --sync >/dev/null 2>&1 || true
	journalctl -b --no-pager -o cat _SYSTEMD_UNIT=nxlx-ndi.service _PID="$1" | grep -c "nxlx-ndi: sound comes from " || true
}
ndi_get() {        # GET /api/ndi into $OUT
	local code
	code="$(curl -sS -m 20 -o "$OUT" -w '%{http_code}' -H "Authorization: Bearer $TOKEN" http://127.0.0.1/api/ndi)"
	[ "$code" = 200 ]
}
ndi_loaded() { ndi_get && grep -q '"loaded": true' "$OUT"; }
ndi_fail() {       # everything one run needs to say what went wrong, then fail
	echo "--- what the panel and the player say" >&2
	box dump >&2 || true
	echo "--- the pipe, the helper's folder, the library" >&2
	ls -lan /run/nxlx-ndi-pipe /run/nxlx-ndi /opt/nxlx-ndi >&2 || true
	echo "--- the helper's journal" >&2
	journalctl -b --no-pager -u nxlx-ndi -n 60 >&2 || true
	echo "--- the panel's journal" >&2
	journalctl -b --no-pager -u nxlx-web -n 40 >&2 || true
	echo "--- the player's command line and the end of its own log" >&2
	tr '\0' ' ' < "/proc/$(systemctl show -p MainPID --value nxlx-player.service)/cmdline" >&2 || true
	echo >&2
	tail -n 60 "$MPV_LOG" >&2 || true
	fail "$@"
}
holds_the_pipe() { # holds_the_pipe PID: that process has root's pipe open
	find "/proc/$1/fd" -lname "$NDI_PIPE" 2>/dev/null | grep -q .
}

step "an NDI picture all the way: the stand-in for NDI's library is built here and placed by the real command"
command -v cc >/dev/null || fail "no C compiler (cc) on this machine: the stand-in for NDI's library cannot be built"
cc -shared -fPIC -pthread -O2 -Wall -Wextra -o "$NDI_STANDIN" "$SRC/tests/ndi_standin/standin.c" || fail "the stand-in for NDI's library did not build"
# The player without a screen (see above): the unit's own command, with mpv's null outputs and a log added.
PLAYER_EXEC="$(sed -n 's/^ExecStart=\(.*\) serve$/\1/p' /etc/systemd/system/nxlx-player.service)"
[ -n "$PLAYER_EXEC" ] || fail "the player's unit has no 'ExecStart=... serve' line to add the null outputs to"
install -d -m 0755 "$(dirname "$NDI_PLAYER_CI")"
printf '%s\n' "# Written by tests/real_install_test.sh for its NDI step and removed by it: this machine has no screen." \
	"[Service]" "ExecStart=" \
	"ExecStart=$PLAYER_EXEC --mpv-arg=--vo=null --mpv-arg=--ao=null --mpv-arg=--log-file=$MPV_LOG serve" > "$NDI_PLAYER_CI"
systemctl daemon-reload
# The real command, whole: its checks of the file (a plain file of this processor, not too large; nothing in them
# knows NDI's file from any other library), the copy to root's folder, the account, the units, the pipe, the restarts.
if ! /usr/local/bin/nxlx-ndi-runtime install "$NDI_STANDIN" 2>&1 | tee "$OUT"; then ndi_fail "the real command did not set NDI up with the stand-in"; fi
grep -q "^installed $NDI_LIB\$" "$OUT" || fail "the command did not say where it placed the library"
grep -q "^sha256 $(sha256sum "$NDI_STANDIN" | cut -d' ' -f1) " "$OUT" || fail "the sha256 the command printed is not the stand-in's"
grep -q "the helper nxlx-ndi is installed and was running" "$OUT" || fail "the command did not say that the helper stayed up"
is /var/lib/nxlx-update "root:root 700 directory"        # J-055, J-178: where an update keeps settings copies, root's own folder outside the panel's
is /opt/nxlx-ndi "root:root 755 directory"
is "$NDI_LIB" "root:root 644 regular file"
cmp "$NDI_STANDIN" "$NDI_LIB" || fail "what was placed is not what was given"
is /run/nxlx-ndi-pipe "root:nxlx-ndi 750 directory"
is "$NDI_PIPE" "root:nxlx-ndi 660 fifo"
wait_for 40 systemctl is-active --quiet nxlx-ndi.service || ndi_fail "nxlx-ndi is not active after the real command"
wait_for 40 test -S /run/nxlx-ndi/ndi.sock || ndi_fail "nxlx-ndi made no socket after the real command"
wait_for 40 systemctl is-active --quiet nxlx-web.service || ndi_fail "the panel did not come back after the real command"
wait_for 40 player_socket_is_open || ndi_fail "the player does not stay up with mpv's null outputs; this step needs a player"
PLAYER_PID="$(systemctl show -p MainPID --value nxlx-player.service)"
tr '\0' ' ' < "/proc/$PLAYER_PID/cmdline" | grep -q -- '--vo=null' || ndi_fail "the player was not started with the null output: this test's drop-in was not used"
in_group "$PLAYER_PID" "$NDI_GID" || ndi_fail "the running player is not in the group nxlx-ndi"
ndi_page
grep -q '"setup": true' "$OUT" || ndi_fail "the panel does not see that the box is set up for NDI"
grep -q '"helper": true' "$OUT" || ndi_fail "the panel cannot reach the helper"
wait_for 30 ndi_loaded || ndi_fail "with the module on, the helper did not load the library that the real command placed"
cat "$OUT"
echo
grep -q 'PVJ STAND-IN' "$OUT" || ndi_fail "the library the helper says it loaded is not the stand-in"
HELPER_PID="$(systemctl show -p MainPID --value nxlx-ndi.service)"
[ "$(ps -o user= -p "$HELPER_PID" | tr -d ' ')" = nxlx-ndi ] || ndi_fail "the helper's process does not run as nxlx-ndi"
grep -q " $NDI_LIB\$" "/proc/$HELPER_PID/maps" || ndi_fail "the helper's process has not got $NDI_LIB loaded"

step "sound off: the helper's account writes root's pipe, the player reads it, the panel says 'playing' and counts the pictures"
# With the check as it was until 2026-10-12 (a pipe is written only if the helper owns it) this is where the run
# ends: 'MCTL-STANDIN (moving)' did not play: it stopped with "the input pipe was replaced; not writing to it".
box play "$NDI_MOVING" raw || ndi_fail "sound off: the stand-in's picture did not get from the helper through root's pipe to the player"
holds_the_pipe "$HELPER_PID" || ndi_fail "the helper's process has not got root's pipe open"
holds_the_pipe "$PLAYER_PID" || ndi_fail "the player's process has not got root's pipe open"
is "$NDI_PIPE" "root:nxlx-ndi 660 fifo"

step "the helper is restarted under the playing source: the panel brings the source back by itself"
systemctl restart nxlx-ndi.service
[ "$(systemctl show -p MainPID --value nxlx-ndi.service)" != "$HELPER_PID" ] || ndi_fail "after a restart the helper is the same process"
box back "$NDI_MOVING" 20 || ndi_fail "the source did not come back by itself after the helper's restart"
HELPER_PID="$(systemctl show -p MainPID --value nxlx-ndi.service)"
holds_the_pipe "$HELPER_PID" || ndi_fail "the new helper's process has not got root's pipe open"
[ "$(systemctl show -p MainPID --value nxlx-player.service)" = "$PLAYER_PID" ] || ndi_fail "the player was restarted with the helper; it must only load the pipe again"

step "sound on: the same road with the picture and the sound together (Matroska)"
box sound on || ndi_fail "sound could not be switched on"
box play "$NDI_SOUND" matroska || ndi_fail "sound on: the stand-in's picture and sound did not get to the player"
# Sound against picture by mpv's own account, for the record: the numbers are printed, and a breach is printed and
# not judged here, because the bounds were measured on the Pi's mpv (0.40; the job ndi-mpv judges them) and this
# runner's is another. It fails only if the source does not play.
box case syncinfo "$NDI_SOUND" || ndi_fail "sound on: the source did not play while its sound against its picture was looked at"

# ---- the hand-overs: every way a source comes to the player after another, judged by mpv's own word ----------------
# Why (2026-10-12, measured on the owner's Pi through mpv's control socket): the helper's counts are not proof that
# the player plays. With one still picture and then only sound, the helper said "still" and wrote sound steadily
# while mpv sat with the pipe as its path, no track and no clock, for minutes; the same source played again ended
# with the page saying neither that it plays nor why not; a source that changed size was opened again and lost the
# same way. So each step below hands a source over in one way and then asks mpv for ten seconds: the pipe is its
# path, it has a picture track of the right size, its clock moves. A step is good when mpv plays at the END of that
# time, or when GET /api/ndi gives a reason in "ended". It is bad when the page says neither (SILENT LOSS) or says
# it plays while mpv does not (STUCK); the line that begins REASON says which, with mpv's words second by second.
#
# TWO KINDS OF STEP. A hand-over that passed on the code before the mending (the run of 6671dcc) is asked with
# ndi_must and is fatal. The four that failed there (1, 2b, 3b and the chain) are asked with ndi_case and are under
# THE ONE SWITCH below, until a run on the mended code has been read: no run had got past the helper's restart when
# this was written. They failed because of that fault (the hand-over of the pipe; mended on the branch
# ndi-pipe-reuse). Each step but the chain starts clean (a new helper, a new player), so that one finding
# does not make the next; the chain at the end cleans nothing, because carry-over is what it is for. While it says "no", a step that fails prints a line beginning FINDING, the run goes on and
# stays green, and its last line lists every finding. Set it to "yes" (here, or NXLX_NDI_FINDINGS_FATAL=yes in the
# environment) and the first of them fails the run: that is how to see that the fault is mended, and what this
# becomes once it is.
NDI_FINDINGS_FATAL="${NXLX_NDI_FINDINGS_FATAL:-yes}"       # "yes" since the mending (2026-10-12); "no" brings the FINDING lines back
NDI_FINDINGS=""
ndi_case() {       # ndi_case "what is handed over" box_check-arguments...
	local words="$1"
	shift
	if box "$@"; then
		echo "GOOD: $words"
		return 0
	fi
	echo "--- the end of the player's own log"
	tail -n 25 "$MPV_LOG" || true
	echo "--- the end of the helper's and the panel's journals"
	journalctl -b --no-pager -u nxlx-ndi -u nxlx-web -n 25 || true
	if [ "$NDI_FINDINGS_FATAL" = yes ]; then ndi_fail "$words"; fi
	echo "FINDING: $words. The line above that begins REASON says what mpv and the page said. Not fatal while NDI_FINDINGS_FATAL is 'no'."
	NDI_FINDINGS="$NDI_FINDINGS [$words]"
}
ndi_must() {       # ndi_must "what is handed over" box_check-arguments...: a hand-over that passed before; always fatal
	local words="$1"
	shift
	if box "$@"; then
		echo "GOOD: $words"
		return 0
	fi
	ndi_fail "$words"
}
helper_is_new() {  # helper_is_new OLD_PID: the helper runs, as another process
	local pid
	pid="$(systemctl show -p MainPID --value nxlx-ndi.service)"
	[ "$pid" != 0 ] && [ "$pid" != "$1" ] && [ -S /run/nxlx-ndi/ndi.sock ]
}
ndi_module() {     # ndi_module true|false
	local code
	code="$(curl -sS -m 20 -o "$OUT" -w '%{http_code}' -H 'Content-Type: application/json' -H 'X-MCTL-Request: 1' -H "Authorization: Bearer $TOKEN" -d "{\"enabled\": $1}" http://127.0.0.1/api/modules/inputs-ndi)"
	[ "$code" = 200 ] || ndi_fail "the NDI module could not be switched to $1: HTTP $code, $(head -c 300 "$OUT")"
}
ndi_clean() {      # every step starts from the same place, whatever the one before left: no source, a new helper, a new player
	local old
	old="$(systemctl show -p MainPID --value nxlx-ndi.service)"
	box sound off || ndi_fail "sound could not be switched off between two steps"
	ndi_module false                               # the panel takes the source off and the helper lets go of it
	systemctl restart nxlx-ndi.service              # a new helper: the stand-in's source that changes size starts again too
	systemctl restart nxlx-player.service           # a player that was left waiting on the pipe is gone with this
	wait_for 40 player_socket_is_open || ndi_fail "the player did not come back between two steps"
	wait_for 40 helper_is_new "$old" || ndi_fail "the helper did not start again between two steps"
	ndi_module true
	wait_for 30 ndi_loaded || ndi_fail "the helper did not load the library again between two steps"
}

step "hand-over 1: one still picture and then only sound, sound on (what NDI's Test Patterns sends)"
ndi_clean
box sound on || ndi_fail "sound could not be switched on"
ndi_case "one still picture then only sound, with sound on" case still "$NDI_STILL"

step "hand-over 2: the same source played twice in a row (sound off)"
ndi_clean
ndi_must "the same source played twice in a row" case twice "$NDI_STILL"

step "hand-over 2b: from the stuck state of hand-over 1, nothing cleaned: the same source with sound off, then sound switched on (1280 x 720)"
ndi_clean
ndi_case "the same source with sound off straight after its stuck load with sound, then sound on" case stuck "$NDI_BIG_STILL"

step "hand-over 3: source A, then source B at once, sound off (1280 x 720; both have their first picture the moment they are opened)"
ndi_clean
ndi_must "one source straight after another" case follow "$NDI_BIG_A" "$NDI_BIG_B"

step "hand-over 3b: source A with sound, then source B with sound at once (1280 x 720, Matroska on both sides)"
ndi_clean
box sound on || ndi_fail "sound could not be switched on"
ndi_case "one source with sound straight after another with sound" case follow "$NDI_BIG_A_SOUND" "$NDI_BIG_B_SOUND"

step "hand-over 4: sound switched on, and off again, under a playing source"
ndi_clean
ndi_must "sound switched on and off under a playing source" case sound "$NDI_SOUND"

step "hand-over 4b: the same under a 1280 x 720 source, in both directions: off, on, off; then on, off, on"
ndi_clean
ndi_must "sound switched on and off under a big playing source" case sound "$NDI_BIG_SOUND"
ndi_clean
ndi_must "sound switched off and on under a big source that played with sound" case soundon "$NDI_BIG_SOUND"

step "hand-over 5: a source that changes size while it plays, sound off (1280 x 720, then 1264 x 704)"
ndi_clean
ndi_must "a source that changed size while it played" case size "$NDI_BIG_CHANGES"

step "hand-over 5b: a source with sound that changes size while it plays (Matroska on both sides; the Pi lost this one)"
ndi_clean
box sound on || ndi_fail "sound could not be switched on"
ndi_must "a source with sound that changed size while it played" case size "$NDI_BIG_CHANGES_SOUND"

step "hand-over 5c: the small source that changes size, sound off (320 x 180, then 256 x 144: pictures that fit the pipe whole)"
ndi_clean
ndi_must "a small source that changed size while it played" case size "$NDI_CHANGES"

step "hand-over 6: the helper restarted under a playing source, judged by mpv's own word"
ndi_clean
box play "$NDI_MOVING" raw || ndi_fail "sound off: the stand-in's picture did not play before the helper's restart"
systemctl restart nxlx-ndi.service
ndi_must "the helper restarted under a playing source" case restart "$NDI_MOVING"

step "hand-over 7, the chain: eight hand-overs back to back with sound, nothing cleaned between them (what one leaves, the next meets)"
ndi_clean
ndi_case "the chain of hand-overs with nothing cleaned between them" case chain

# ---- sound from another sender (D87): the picture of one source, the sound of another, on the installed box ----------
# The helper under its unit opens a second connection, for sound alone, to the source the Owner chose, and its sound
# goes into the same stream under whatever picture plays. Three of the cases the job ndi-mpv judges on the Pi's mpv
# are asked here too, over HTTP as a paired device asks, because here the helper is the unit's own process in its
# sandbox. All three are fatal. The numbers for sound against picture are printed and not judged (another mpv).
# The stand-in's own count of its receivers cannot be read from here (it is in the helper's process), so "the
# sound connection was opened once" is the helper's own line in the journal, counted for that one process.
step "sound from another sender 1: a moving picture without sound of its own, with the sound of a sound-only source"
ndi_clean
ndi_must "a picture with the sound of a sound-only source" case soundfrom "$NDI_MOVING" "$NDI_SOUND_ONLY" 12

step "sound from another sender 2: the picture changes size while it plays (1280 x 720, then 1264 x 704); the sound connection is opened once"
ndi_clean
HELPER_PID="$(systemctl show -p MainPID --value nxlx-ndi.service)"
ndi_must "a picture that changed size, with the sound of a sound-only source" case soundfromsize "$NDI_BIG_CHANGES" "$NDI_SOUND_ONLY"
[ "$(systemctl show -p MainPID --value nxlx-ndi.service)" = "$HELPER_PID" ] || ndi_fail "the helper became another process while the picture changed size: its count of sound connections says nothing"
SOUND_OPENS="$(sound_connections_of "$HELPER_PID")"
echo "the helper (process $HELPER_PID) said 'sound comes from' $SOUND_OPENS time(s)"
[ "$SOUND_OPENS" = 1 ] || ndi_fail "the helper opened its sound connection $SOUND_OPENS times while the picture was opened twice; it must be once"

step "sound from another sender 3: Play on the sound-only source itself is refused in words, and nothing of it reaches the player"
ndi_clean
ndi_must "Play on the sound-only source is refused in plain words" case soundonlyplay "$NDI_SOUND_ONLY"
box soundown || ndi_fail "the choice of one sound source could not be taken back"

step "the stand-in is taken off by the real command, and this test's drop-in with it: nothing of NDI is left"
box sound off || ndi_fail "sound could not be switched off again"
ndi_module false
rm -f "$NDI_PLAYER_CI"
rmdir "$(dirname "$NDI_PLAYER_CI")"
systemctl daemon-reload
# it restarts the panel and the player: the player as its unit says again
if ! /usr/local/bin/nxlx-ndi-runtime remove 2>&1 | tee "$OUT"; then fail "the real command could not take NDI off again"; fi
grep -q "^removed NDI's library ($NDI_LIB)\$" "$OUT" || fail "the command did not say that it removed the library"
if [ -e /opt/nxlx-ndi ] || [ -L /opt/nxlx-ndi ]; then fail "after 'remove' there is still /opt/nxlx-ndi"; fi
rm -rf "$NDI_BUILD"
sleep 2
wait_for 40 panel_answers || fail "the panel does not answer after 'nxlx-ndi-runtime remove'"
nothing_of_ndi
services_and_their_files
if [ "$PLAYER_SEEN" = 1 ] && tr '\0' ' ' < "/proc/$(systemctl show -p MainPID --value nxlx-player.service)/cmdline" | grep -q -- '--vo=null'; then
	fail "the player still runs with this test's null output"
fi

step "links planted in /var/lib/nxlx are not followed by the install (J-175)"
# The panel account owns /var/lib/nxlx, so it can plant links there. Root's install must not change a target's owner or mode.
LINK_TARGET="$(mktemp /root/j175.XXXXXX)"
echo x > "$LINK_TARGET"
chown root:root "$LINK_TARGET"; chmod 0640 "$LINK_TARGET"
LINK_DIR="$(mktemp -d /root/j175dir.XXXXXX)"
chown root:root "$LINK_DIR"; chmod 0755 "$LINK_DIR"
ln -sfn "$LINK_TARGET" /var/lib/nxlx/settings.json.bak
rm -rf /var/lib/nxlx/backups; ln -sfn "$LINK_DIR" /var/lib/nxlx/backups     # an older version's place
rm -rf /var/lib/nxlx/video; ln -sfn "$LINK_DIR" /var/lib/nxlx/video
logged "$SRC/install/install.sh" --offline
is "$LINK_TARGET" "root:root 640 regular file"
is "$LINK_DIR" "root:root 755 directory"
[ ! -e /var/lib/nxlx/backups ] || fail "the link at /var/lib/nxlx/backups was not set aside (J-178)"
is /var/lib/nxlx-update "root:root 700 directory"
[ -z "$(ls -A "$LINK_DIR")" ] || fail "the move followed the link at /var/lib/nxlx/backups and changed what it points to"
[ ! -L /var/lib/nxlx/video ] || fail "the link at /var/lib/nxlx/video was not removed"
PLAYER_NOW="$(sed -n 's/^User=//p' /etc/systemd/system/nxlx-player.service)"
is /var/lib/nxlx/video "$PLAYER_NOW:nxlx-svc 2775 directory"
rm -rf "$LINK_TARGET" "$LINK_DIR" /var/lib/nxlx/settings.json.bak

step "final listing"
listing

step "uninstall on a box that opted in: the helper and the drop-ins go with the rest"
ndi_setup enable
wait_for 40 systemctl is-active --quiet nxlx-ndi.service || fail "nxlx-ndi is not active before the uninstall"
logged "$SRC/install/install.sh" --uninstall
for path in /etc/systemd/system/nxlx-ndi.service /etc/systemd/system/nxlx-web.service.d /etc/systemd/system/nxlx-player.service.d \
	/etc/systemd/system/multi-user.target.wants/nxlx-ndi.service /usr/local/bin/nxlx-ndi-runtime /run/nxlx-ndi /opt/nxlx; do
	if [ -e "$path" ] || [ -L "$path" ]; then fail "after the uninstall there is still $path"; fi
done
if systemctl is-active --quiet nxlx-ndi.service; then fail "nxlx-ndi still runs after the uninstall"; fi
if systemctl is-enabled --quiet nxlx-ndi.service 2>/dev/null; then fail "nxlx-ndi is still enabled after the uninstall"; fi
getent passwd nxlx-ndi >/dev/null || fail "the uninstall removed the account, which it says it leaves"
grep -q "nxlx-ndi on a box that was set up for NDI" "$OUT" || fail "the uninstall does not say what it leaves of NDI"

echo
echo "PASS (player folder checked: $PLAYER_SEEN). This ran on $(systemctl --version | head -n 1), not on a Raspberry Pi."
echo "NDI: a picture of the stand-in for NDI's library went the whole road (never NDI's own library, never a screen)."
echo "NDI hand-overs judged by mpv's own word, FINDINGS (not fatal, NDI_FINDINGS_FATAL=$NDI_FINDINGS_FATAL):${NDI_FINDINGS:- none}"
