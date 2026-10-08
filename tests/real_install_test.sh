#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
# Runs install/install.sh FOR REAL, as root, under a real systemd, and checks who owns what in /run/pvj (D45):
# an upgrade over an old shared folder with planted names and links, restarts, a crash, a reinstall, an emptying
# that was cut short, and the things the service accounts must not be able to do.
# Then the NDI input, which is opt-in per box (D62): that those plain installs left nothing of it, what root's
# opt-in changes, that a later install keeps it, and that opting out takes it off. Without NDI's library.
#
# It creates accounts, installs to /opt/pvj, writes units and starts services. Run it ONLY on a machine that is
# thrown away afterwards (the CI runner). It refuses without the variable below. It is not a test of a Raspberry
# Pi: there is no display here, and the systemd is the runner's, not the box's.
#
#   sudo PVJ_REAL_INSTALL_TEST=yes-this-machine-is-disposable bash tests/real_install_test.sh
set -euo pipefail

[ "${PVJ_REAL_INSTALL_TEST:-}" = "yes-this-machine-is-disposable" ] || { echo "refusing: read the top of this file" >&2; exit 2; }
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 2; }
[ -d /run/systemd/system ] || { echo "no systemd here" >&2; exit 2; }
if [ -e /opt/pvj ] || [ -e /run/pvj ] || [ -e /opt/pvj-ndi ] || getent passwd pvj-ndi >/dev/null; then echo "refusing: this machine already has an install" >&2; exit 2; fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
A=pvjdisplay                       # the display account of this test
OUT="$(mktemp)"
PLANTED=/root/pvj-planted-by-root
if dpkg -s avahi-daemon >/dev/null 2>&1; then AVAHI_BEFORE=installed; else AVAHI_BEFORE=absent; fi      # a plain install must not change it

listing() {
	{ find /run/pvj /run/pvj-sysd /run/pvj-supportd /run/pvj-update -printf '%u:%g %m %y %p %l\n' 2>&1 || true; } | sort -k4
}
fail() {
	echo "FAIL: $*" >&2
	echo "--- listing" >&2
	listing >&2 || true
	echo "--- journal" >&2
	journalctl -b --no-pager -u pvj-player -u pvj-web -u pvj-netd -u pvj-sysd -u pvj-ndi -n 80 >&2 || true
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
player_socket_is_open() { [ "$(stat -c '%a' /run/pvj/player/player.sock 2>/dev/null)" = 660 ]; }
refused() {       # refused ACCOUNT command...: the command must fail
	local who="$1"
	shift
	if runuser -u "$who" -- "$@" >/dev/null 2>&1; then fail "as $who this worked and must not: $*"; fi
	echo "refused, as it must be ($who): $*"
}
installer() {
	"$SRC/install/install.sh" --offline --user "$A" "$@" 2>&1 | tee "$OUT"
}
only_the_new_layout() {
	local extra
	is /run/pvj "root:root 755 directory"
	is /run/pvj/.d45 "root:root 644 regular empty file"
	is /run/pvj/web "pvj-web:pvj 750 directory"
	[ "$(readlink /run/pvj/web/player.sock)" = /run/pvj/player/player.sock ] || fail "the player.sock link is missing or wrong"
	[ "$(readlink /run/pvj/web/netd.sock)" = /run/pvj/netd/netd.sock ] || fail "the netd.sock link is missing or wrong"
	extra="$(find /run/pvj -mindepth 1 -maxdepth 1 ! -name .d45 ! -name web ! -name player ! -name netd)"
	[ -z "$extra" ] || fail "unexpected names directly in /run/pvj: $extra"
	if [ -e "$PLANTED" ] || [ -L "$PLANTED" ]; then fail "root created $PLANTED through a planted link"; fi
	is /etc "root:root 755 directory"
	is /etc/passwd "root:root 644 regular file"
}
services_and_their_files() {
	wait_for 40 systemctl is-active --quiet pvj-web.service || fail "pvj-web is not active"
	wait_for 40 test -f /run/pvj/web/pin || fail "the panel wrote no PIN file"
	is /run/pvj/web/pin "pvj-web:pvj 600 regular file"
	wait_for 40 test -S /run/pvj/netd/netd.sock || fail "pvj-netd made no socket"
	is /run/pvj/netd "root:pvj 750 directory"
	is /run/pvj/netd/netd.sock "root:pvj 660 socket"
	wait_for 40 test -S /run/pvj-sysd/sysd.sock || fail "pvj-sysd made no socket"
	is /run/pvj-sysd "root:pvj 750 directory"
	is /run/pvj-sysd/sysd.sock "root:pvj 660 socket"
	# The player needs a display. If mpv stays up without one, its folder and socket are checked; if it does not,
	# that is said and not counted as a pass.
	if wait_for 30 test -S /run/pvj/player/player.sock; then
		is /run/pvj/player "$A:pvj 750 directory"
		wait_for 20 player_socket_is_open || true        # the player's helper opens it to the group a moment later
		is /run/pvj/player/player.sock "$A:pvj 660 socket"
		PLAYER_SEEN=1
	else
		echo "NOTE: the player did not stay up on this machine (no display); its folder and socket were NOT checked"
		PLAYER_SEEN=0
	fi
}

step "systemd here"
systemctl --version | head -n 1

step "the state an older version leaves: one shared folder owned by a service account, with things planted in it"
groupadd --system pvj
useradd --system --no-create-home --shell /usr/sbin/nologin --gid pvj pvj-web
useradd --system --create-home --home-dir /var/lib/pvj-player --shell /usr/sbin/nologin --gid pvj "$A"
if [ ! -e /usr/bin/nmcli ]; then
	# pvj-netd only starts where nmcli exists. A stand-in that does nothing lets the helper start, so that its
	# socket and folder can be checked; nothing here tests the network.
	printf '#!/bin/sh\nexit 0\n' > /usr/bin/nmcli
	chmod 0755 /usr/bin/nmcli
fi
install -d -o "$A" -g pvj -m 0770 /run/pvj
runuser -u "$A" -- sh -c '
	cd /run/pvj
	echo 1234 > pin
	echo 1759570000 > undervoltage-seen
	: > player.sock
	: > netd.sock
	mkdir netd web
	: > netd/netd.sock
	echo 9999 > web/pin
	ln -s /root/pvj-planted-by-root web/undervoltage-seen
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
for u in pvj-player pvj-web pvj-netd pvj-sysd; do
	systemctl restart "$u.service"
	sleep 3
	only_the_new_layout
	services_and_their_files
done

step "a crash: the helper and the player are killed and come back by themselves"
systemctl kill -s KILL pvj-netd.service
systemctl kill -s KILL pvj-player.service || true
sleep 6
only_the_new_layout
services_and_their_files
listing

step "what the accounts must not be able to do"
refused "$A" cat /run/pvj/web/pin
refused "$A" touch /run/pvj/x
refused "$A" ln -s /etc /run/pvj/x
refused "$A" mv /run/pvj/netd /run/pvj/n2
refused "$A" rm /run/pvj/netd/netd.sock
refused "$A" touch /run/pvj/web/x
refused "$A" rm /run/pvj/.d45
refused pvj-web touch /run/pvj/x
refused pvj-web ln -s /etc /run/pvj/x
refused pvj-web mv /run/pvj/netd /run/pvj/n2
refused pvj-web rm /run/pvj/netd/netd.sock
refused pvj-web touch /run/pvj/netd/x
refused pvj-web rm /run/pvj/.d45
if [ "$PLAYER_SEEN" = 1 ]; then
	refused pvj-web touch /run/pvj/player/x
	refused pvj-web rm /run/pvj/player/player.sock
fi
only_the_new_layout

step "the preview through the panel, inside its sandbox (it must not fail on a read-only file system)"
PIN="$("$SRC/bin/pvj-pin")"
TOKEN="$(curl -sS -m 20 -H 'Content-Type: application/json' -H 'X-PVJ-Request: 1' -d "{\"pin\": \"$PIN\", \"name\": \"ci\"}" http://127.0.0.1/api/pair | sed -n 's/.*"token": *"\([^"]*\)".*/\1/p')"
[ -n "$TOKEN" ] || fail "could not pair with the panel"
CODE="$(curl -sS -m 30 -o /tmp/pvj-preview.out -w '%{http_code}' -H "Authorization: Bearer $TOKEN" http://127.0.0.1/api/preview.jpg)"
echo "preview: HTTP $CODE, $(wc -c < /tmp/pvj-preview.out) bytes"
if grep -a -q -i "read-only" /tmp/pvj-preview.out; then fail "the preview failed on a read-only file system: $(head -c 300 /tmp/pvj-preview.out)"; fi
if [ "$CODE" = 200 ]; then
	echo "a picture came back"
else
	echo "NOTE: no picture on this machine (no display): $(head -c 300 /tmp/pvj-preview.out)"
fi

step "reinstall over the new layout: nothing is emptied, a link planted in the panel's folder leads root nowhere"
runuser -u pvj-web -- ln -s "$PLANTED" /run/pvj/web/undervoltage-seen
: > /run/pvj/stray
installer
if grep -q "a folder per service" "$OUT"; then fail "a sound folder was emptied again"; fi
if grep -q "systemd-tmpfiles reported a problem" "$OUT"; then fail "systemd-tmpfiles reported a problem on a reinstall"; fi
[ ! -e /run/pvj/stray ] || fail "a stray file directly in /run/pvj was left"
[ "$(readlink /run/pvj/web/undervoltage-seen)" = "$PLANTED" ] || fail "the installer touched a name in the panel's folder"
runuser -u pvj-web -- rm /run/pvj/web/undervoltage-seen
only_the_new_layout
services_and_their_files

step "an emptying that was cut short (no marker) with a planted helper folder is finished by the next run, also with --no-start"
systemctl stop pvj-netd.service
rm -f /run/pvj/.d45
install -d -o "$A" -g pvj -m 0750 /run/pvj/netd
runuser -u "$A" -- sh -c ': > /run/pvj/netd/netd.sock'
installer --no-start
grep -q "a folder per service" "$OUT" || fail "an unfinished emptying was not noticed"
if [ -e /run/pvj/netd/netd.sock ] && [ ! -S /run/pvj/netd/netd.sock ]; then fail "the planted netd.sock is still there"; fi
if [ -d /run/pvj/netd ]; then is /run/pvj/netd "root:pvj 750 directory"; fi
wait_for 40 systemctl is-active --quiet pvj-web.service || fail "the panel, which was running, was not started again after --no-start"
systemctl start pvj-netd.service
only_the_new_layout
services_and_their_files

step "a service folder with the wrong owner is not repaired by chown: everything is emptied and made again"
systemctl stop pvj-web.service
chown -R "$A":pvj /run/pvj/web
installer
grep -q "a folder per service" "$OUT" || fail "a folder with the wrong owner was accepted"
only_the_new_layout
services_and_their_files

# ---- the NDI input is opt-in per box (D62, the owner's answer of 2026-10-08) --------------------------------------
# Everything above ran plain installs. NDI's own library is never here (it is not in the repository and is not
# fetched), so what is checked is root's part: that a plain install leaves nothing of NDI, what the opt-in changes
# under a real systemd (the group must exist before the panel and the player start with it), that a later plain
# install keeps and refreshes it, and that opting out takes it off again. Not a test that NDI works.
# What is NOT run here, and cannot be: the real command succeeding ("pvj-ndi-runtime install <the NDI SDK>"). It
# needs NDI's library, which only a person with the SDK has. Here the command is run only with a file it refuses,
# and the setup it would go on to is called directly (ndi_setup enable). The whole command is device step N2.
NDI_WEB_DROPIN=/etc/systemd/system/pvj-web.service.d/50-pvj-ndi.conf
NDI_PLAYER_DROPIN=/etc/systemd/system/pvj-player.service.d/50-pvj-ndi.conf
ndi_setup() {      # ndi_setup enable|disable: root's code as `pvj-ndi-runtime install|remove` runs it, without a library
	python3 -B -c 'import sys; sys.path.insert(0, "/opt/pvj/current"); from pvj import ndisetup; getattr(ndisetup, sys.argv[1])()' "$1"
}
groups_of() { systemctl show -p SupplementaryGroups --value "$1"; }
panel_answers() { curl -sS -m 5 -o /dev/null http://127.0.0.1/api/hello; }
ndi_page() {       # the panel's own account of NDI, with the module on
	wait_for 40 panel_answers || fail "the panel does not answer"
	local code
	code="$(curl -sS -m 20 -o "$OUT" -w '%{http_code}' -H 'Content-Type: application/json' -H 'X-PVJ-Request: 1' -H "Authorization: Bearer $TOKEN" -d '{"enabled": true}' http://127.0.0.1/api/modules/inputs-ndi)"
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
	for path in /etc/systemd/system/pvj-ndi.service /etc/systemd/system/pvj-web.service.d /etc/systemd/system/pvj-player.service.d \
		/etc/systemd/system/multi-user.target.wants/pvj-ndi.service /run/pvj-ndi /opt/pvj-ndi; do
		if [ -e "$path" ] || [ -L "$path" ]; then fail "a box that did not opt in has $path"; fi
	done
	if systemctl is-active --quiet pvj-ndi.service; then fail "pvj-ndi runs on a box that did not opt in"; fi
	if systemctl is-enabled --quiet pvj-ndi.service 2>/dev/null; then fail "pvj-ndi is enabled on a box that did not opt in"; fi
	[ "$(groups_of pvj-web.service)" = "audio video" ] || fail "without NDI the panel's extra groups are: $(groups_of pvj-web.service)"
	[ "$(groups_of pvj-player.service)" = "video render audio input" ] || fail "without NDI the player's extra groups are: $(groups_of pvj-player.service)"
	if grep -q "pvj-ndi" /etc/systemd/system/pvj-web.service /etc/systemd/system/pvj-player.service; then
		fail "a unit the installer wrote names pvj-ndi"
	fi
}

step "NDI is opt-in: after every plain install above, this machine has nothing of it"
nothing_of_ndi
if getent passwd pvj-ndi >/dev/null || getent group pvj-ndi >/dev/null; then fail "a plain install made the account or the group pvj-ndi"; fi
if [ -e /opt/pvj-ndi ]; then fail "a plain install made /opt/pvj-ndi"; fi
if [ "$(avahi_state)" != "$AVAHI_BEFORE" ]; then fail "a plain install changed avahi-daemon: it was $AVAHI_BEFORE"; fi
[ "$(readlink /usr/local/bin/pvj-ndi-runtime)" = /opt/pvj/current/bin/pvj-ndi-runtime ] || fail "the opt-in command is not on the path"
/usr/local/bin/pvj-ndi-runtime status > "$OUT"
cat "$OUT"
grep -q "NDI is not set up on this box" "$OUT" || fail "the status command does not say that NDI is not set up"
ndi_page
grep -q '"setup": false' "$OUT" || fail "the panel does not say that NDI is not set up on this box"
grep -q 'sudo pvj-ndi-runtime install' "$OUT" || fail "the panel does not name the one command"

step "the real command with a file that is not NDI's library: refused, and nothing is set up"
if /usr/local/bin/pvj-ndi-runtime install /etc/hostname; then fail "a file that is not the library was accepted"; fi
nothing_of_ndi
if getent passwd pvj-ndi >/dev/null || getent group pvj-ndi >/dev/null; then fail "a refused library still made the account or the group"; fi
if [ -e /opt/pvj-ndi ] || [ -L /opt/pvj-ndi ]; then fail "a refused file left /opt/pvj-ndi behind"; fi
if [ "$(avahi_state)" != "$AVAHI_BEFORE" ]; then fail "a refused library still changed avahi-daemon"; fi

step "opt in (root's part of 'pvj-ndi-runtime install', without a library): the helper, its account, the two drop-ins"
ndi_setup enable
[ "$(id -nG pvj-ndi)" = pvj-ndi ] || fail "the account pvj-ndi is in: $(id -nG pvj-ndi)"
[ "$(getent passwd pvj-ndi | cut -d: -f7)" = /usr/sbin/nologin ] || fail "the account pvj-ndi has a shell"
is /etc/systemd/system/pvj-ndi.service "root:root 644 regular file"
is "$NDI_WEB_DROPIN" "root:root 644 regular file"
is "$NDI_PLAYER_DROPIN" "root:root 644 regular file"
sed -e "s|@PVJ_DIR@|/opt/pvj/current|g" "$SRC/pvj/systemd/pvj-ndi.service" | cmp - /etc/systemd/system/pvj-ndi.service || fail "the helper's unit is not the template"
systemctl is-enabled --quiet pvj-ndi.service || fail "pvj-ndi is not enabled after the opt-in"
if ! wait_for 40 systemctl is-active --quiet pvj-ndi.service; then
	systemctl status pvj-ndi.service --no-pager -l >&2 || true
	fail "pvj-ndi is not active after the opt-in"
fi
wait_for 40 test -S /run/pvj-ndi/ndi.sock || fail "pvj-ndi made no socket"
is /run/pvj-ndi "pvj-ndi:pvj-ndi 750 directory"
is /run/pvj-ndi/ndi.sock "pvj-ndi:pvj-ndi 660 socket"
[ "$(systemctl show -p User --value pvj-ndi.service)" = pvj-ndi ] || fail "pvj-ndi does not run as its own account"
[ -z "$(groups_of pvj-ndi.service)" ] || fail "the helper has extra groups: $(groups_of pvj-ndi.service)"
# the drop-ins add to the units' own groups; they do not replace them
[ "$(groups_of pvj-web.service)" = "audio video pvj-ndi" ] || fail "the panel's extra groups are: $(groups_of pvj-web.service)"
[ "$(groups_of pvj-player.service)" = "video render audio input pvj-ndi" ] || fail "the player's extra groups are: $(groups_of pvj-player.service)"
only_the_new_layout
services_and_their_files
NDI_GID="$(getent group pvj-ndi | cut -d: -f3)"
in_group "$(systemctl show -p MainPID --value pvj-web.service)" "$NDI_GID" || fail "the running panel was not restarted into the group pvj-ndi"
ndi_page
grep -q '"setup": true' "$OUT" || fail "the panel does not see that the box opted in"
grep -q '"helper": true' "$OUT" || fail "the panel cannot reach the helper it was given the group for"
grep -q '"present": false' "$OUT" || fail "the panel says NDI's library is here; it is not on this machine"
/usr/local/bin/pvj-ndi-runtime status > "$OUT"
cat "$OUT"
grep -q "NDI is set up on this box" "$OUT" || fail "the status command does not see the opt-in"
refused pvj-ndi cat /run/pvj/web/pin
refused pvj-ndi ls /run/pvj/web
refused pvj-ndi ls /run/pvj/netd
refused pvj-ndi cat /var/lib/pvj/settings.json
refused "$A" touch /run/pvj-ndi/x
refused pvj-web touch /run/pvj-ndi/x

step "a release without the helper's program (a rollback to one from before NDI): the unit is skipped, not failed for ever"
mv /opt/pvj/current/bin/pvj-ndi /opt/pvj/current/bin/pvj-ndi.away
systemctl restart pvj-ndi.service || true
RESTARTS="$(systemctl show -p NRestarts --value pvj-ndi.service)"
sleep 7            # the unit starts a failed helper again every 2 seconds, without a limit
systemctl status pvj-ndi.service --no-pager -l || true
[ "$(systemctl show -p NRestarts --value pvj-ndi.service)" = "$RESTARTS" ] || fail "without its program the helper was started again and again: NRestarts went from $RESTARTS to $(systemctl show -p NRestarts --value pvj-ndi.service)"
if systemctl is-active --quiet pvj-ndi.service; then fail "pvj-ndi counts as running without its program"; fi
if systemctl is-failed --quiet pvj-ndi.service; then fail "pvj-ndi counts as failed without its program; a condition that is not met must not be a failure"; fi
[ "$(systemctl show -p ConditionResult --value pvj-ndi.service)" = no ] || fail "the unit's condition was not what stopped it"
mv /opt/pvj/current/bin/pvj-ndi.away /opt/pvj/current/bin/pvj-ndi
systemctl restart pvj-ndi.service
wait_for 40 systemctl is-active --quiet pvj-ndi.service || fail "pvj-ndi did not come back with its program"
wait_for 40 test -S /run/pvj-ndi/ndi.sock || fail "pvj-ndi made no socket after its program came back"

step "a later plain install keeps an opted-in box opted in and writes its unit again"
echo "# a line from an older version" >> /etc/systemd/system/pvj-ndi.service
rm "$NDI_PLAYER_DROPIN"
installer
grep -q "this box has NDI set up" "$OUT" || fail "the installer did not see the opt-in"
if grep -q "could not write the NDI helper" "$OUT"; then fail "the installer could not write the helper's unit again"; fi
sed -e "s|@PVJ_DIR@|/opt/pvj/current|g" "$SRC/pvj/systemd/pvj-ndi.service" | cmp - /etc/systemd/system/pvj-ndi.service || fail "the helper's unit was not written again"
is "$NDI_PLAYER_DROPIN" "root:root 644 regular file"
systemctl is-enabled --quiet pvj-ndi.service || fail "pvj-ndi is no longer enabled after a plain install"
wait_for 40 systemctl is-active --quiet pvj-ndi.service || fail "pvj-ndi is not active after a plain install"
wait_for 40 test -S /run/pvj-ndi/ndi.sock || fail "pvj-ndi made no socket after a plain install"
[ "$(groups_of pvj-web.service)" = "audio video pvj-ndi" ] || fail "after a plain install the panel's extra groups are: $(groups_of pvj-web.service)"
only_the_new_layout
services_and_their_files
installer --no-start        # as an update from the panel runs it
systemctl is-enabled --quiet pvj-ndi.service || fail "pvj-ndi is no longer enabled after an update"
wait_for 40 systemctl is-active --quiet pvj-ndi.service || fail "pvj-ndi, which was running, is not running after an update"
services_and_their_files

step "opt out (root's part of 'pvj-ndi-runtime remove'): the helper and the drop-ins go, the panel and the player run on"
ndi_setup disable > "$OUT"
cat "$OUT"
grep -q "Left in place: the account and group pvj-ndi" "$OUT" || fail "opting out does not say what it leaves"
sleep 2
nothing_of_ndi
getent passwd pvj-ndi >/dev/null || fail "opting out removed the account, which it says it leaves"
only_the_new_layout
services_and_their_files
if in_group "$(systemctl show -p MainPID --value pvj-web.service)" "$NDI_GID"; then fail "the panel still runs in the group pvj-ndi"; fi
installer
if grep -q "this box has NDI set up" "$OUT"; then fail "a plain install after opting out set NDI up again"; fi
nothing_of_ndi
services_and_their_files

step "a helper's unit without the mark (a setup cut short, or a box from when the helper went on every box) is removed by the next install"
sed -e "s|@PVJ_DIR@|/opt/pvj/current|g" "$SRC/pvj/systemd/pvj-ndi.service" > /etc/systemd/system/pvj-ndi.service
systemctl daemon-reload
systemctl enable --now pvj-ndi.service
wait_for 40 systemctl is-active --quiet pvj-ndi.service || fail "the helper without a mark did not start (the test's own set-up)"
installer
grep -q "removing an NDI helper that nobody set up" "$OUT" || fail "the installer left a helper that nobody set up"
nothing_of_ndi
services_and_their_files

step "final listing"
listing

step "uninstall on a box that opted in: the helper and the drop-ins go with the rest"
ndi_setup enable
wait_for 40 systemctl is-active --quiet pvj-ndi.service || fail "pvj-ndi is not active before the uninstall"
"$SRC/install/install.sh" --uninstall 2>&1 | tee "$OUT"
for path in /etc/systemd/system/pvj-ndi.service /etc/systemd/system/pvj-web.service.d /etc/systemd/system/pvj-player.service.d \
	/etc/systemd/system/multi-user.target.wants/pvj-ndi.service /usr/local/bin/pvj-ndi-runtime /run/pvj-ndi /opt/pvj; do
	if [ -e "$path" ] || [ -L "$path" ]; then fail "after the uninstall there is still $path"; fi
done
if systemctl is-active --quiet pvj-ndi.service; then fail "pvj-ndi still runs after the uninstall"; fi
if systemctl is-enabled --quiet pvj-ndi.service 2>/dev/null; then fail "pvj-ndi is still enabled after the uninstall"; fi
getent passwd pvj-ndi >/dev/null || fail "the uninstall removed the account, which it says it leaves"
grep -q "pvj-ndi on a box that was set up for NDI" "$OUT" || fail "the uninstall does not say what it leaves of NDI"

echo
echo "PASS (player folder checked: $PLAYER_SEEN). This ran on $(systemctl --version | head -n 1), not on a Raspberry Pi."
