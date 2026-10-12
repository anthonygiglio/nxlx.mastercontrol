#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
# Runs install/install.sh FOR REAL, as root, under a real systemd, and checks who owns what in /run/pvj (D45):
# an upgrade over an old shared folder with planted names and links, restarts, a crash, a reinstall, an emptying
# that was cut short, and the things the service accounts must not be able to do.
# Then the NDI input, which is opt-in per box (D62): that those plain installs left nothing of it, what root's
# opt-in changes, that a later install keeps it, and that opting out takes it off. Without NDI's library.
# Then an NDI picture all the way, on this layout: a STAND-IN for NDI's library (tests/ndi_standin, our own code,
# built here) is placed by the real command, loaded by the real helper in its sandbox, written through root's pipe
# and read by the real player, asked for through the panel's API. NDI's own library is never here; see pvj/NDI.md,
# "What the tests prove".
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
# What is NOT run here, and cannot be: the real command with NDI's library ("pvj-ndi-runtime install <the NDI SDK>"),
# which only a person with the SDK has. In these steps the command is run only with a file it refuses, and the setup
# it would go on to is called directly (ndi_setup enable). Further down the whole command does run, with a stand-in
# for the library ("an NDI picture all the way"). With NDI's own file it is device step N2.
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

step "an update over a box that an earlier release set up: the folders its tmpfiles lines name are made, and NDI works without a reboot"
# An earlier release of the helper had no pipe folder of root's and no folder for NDI's configuration file. The
# installer writes their lines again; it must also make them, before it starts the helper and the panel again.
systemctl stop pvj-ndi.service
rm -rf /run/pvj-ndi-pipe /run/pvj-ndi-config
rm -f /etc/tmpfiles.d/pvj-ndi.conf
installer
if grep -q "systemd-tmpfiles could not make the NDI folders" "$OUT"; then fail "the installer could not make the NDI folders"; fi
is /etc/tmpfiles.d/pvj-ndi.conf "root:root 644 regular file"
is /run/pvj-ndi-pipe "root:pvj-ndi 750 directory"
is /run/pvj-ndi-pipe/ndi.fifo "root:pvj-ndi 660 fifo"
is /run/pvj-ndi-config "pvj-web:pvj-ndi 750 directory"
wait_for 40 systemctl is-active --quiet pvj-ndi.service || fail "pvj-ndi is not active after an update over an older set-up"
wait_for 40 test -S /run/pvj-ndi/ndi.sock || fail "pvj-ndi made no socket after an update over an older set-up"
runuser -u pvj-web -- touch /run/pvj-ndi-config/probe || fail "the panel's account cannot write NDI's configuration folder"
rm -f /run/pvj-ndi-config/probe
refused pvj-ndi touch /run/pvj-ndi-config/x
refused pvj-ndi rm /run/pvj-ndi-pipe/ndi.fifo
# The helper's own look at the pipe before it writes, as its account and on root's pipe (found on a Pi, 2026-10-12:
# it took only a pipe of its own, so on a box every source ended "the input pipe was replaced").
runuser -u pvj-ndi -- python3 -B -c 'import os, sys; sys.path.insert(0, "/opt/pvj/current"); from pvj import ndi
fd = os.open("/run/pvj-ndi-pipe/ndi.fifo", os.O_RDWR | os.O_NONBLOCK)
sys.exit(0 if ndi.pipe_to_write(os.fstat(fd)) and os.fstat(fd).st_uid != os.getuid() else 1)' \
	|| fail "the helper's account would not write root's pipe"
ndi_page
grep -q '"helper": true' "$OUT" || fail "after an update over an older set-up the panel cannot reach the helper"

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

step "the NDI discovery server's set-up (D85), with a stand-in for NDI's program: the account, the unit, the supervisor idle"
# NDI's program is never on this machine. /usr/bin/true is a program of this processor, which is all the installer's
# checks ask of a file; it is placed, never started here (the module stays off, so the supervisor runs nothing).
if getent passwd pvj-ndisc >/dev/null; then fail "the account pvj-ndisc exists before anyone set the discovery server up"; fi
if /usr/local/bin/pvj-ndi-runtime server install /etc/hostname; then fail "a file that is not a program was accepted as the discovery server"; fi
if [ -e /opt/pvj-ndi-server ] || [ -e /etc/systemd/system/pvj-ndi-discovery.service ]; then fail "a refused file left something of the discovery server behind"; fi
/usr/local/bin/pvj-ndi-runtime server install "$(readlink -f /usr/bin/true)" 2>&1 | tee "$OUT"
grep -q "^sha256 " "$OUT" || fail "the command did not print the program's sha256"
is /opt/pvj-ndi-server "root:root 755 directory"
is /opt/pvj-ndi-server/ndi-discovery-server "root:root 755 regular file"
[ "$(id -nG pvj-ndisc)" = pvj-ndisc ] || fail "the account pvj-ndisc is in: $(id -nG pvj-ndisc)"
[ "$(getent passwd pvj-ndisc | cut -d: -f7)" = /usr/sbin/nologin ] || fail "the account pvj-ndisc has a shell"
is /etc/systemd/system/pvj-ndi-discovery.service "root:root 644 regular file"
sed -e "s|@PVJ_DIR@|/opt/pvj/current|g" "$SRC/pvj/systemd/pvj-ndi-discovery.service" | cmp - /etc/systemd/system/pvj-ndi-discovery.service || fail "the discovery server's unit is not the template"
systemctl is-enabled --quiet pvj-ndi-discovery.service || fail "pvj-ndi-discovery is not enabled after the set-up"
if ! wait_for 40 systemctl is-active --quiet pvj-ndi-discovery.service; then
	systemctl status pvj-ndi-discovery.service --no-pager -l >&2 || true
	journalctl -b --no-pager -u pvj-ndi-discovery -n 40 >&2 || true
	fail "pvj-ndi-discovery is not active after the set-up"
fi
wait_for 40 test -S /run/pvj-ndisc/ndisc.sock || fail "the supervisor made no socket"
is /run/pvj-ndisc "pvj-ndisc:pvj-ndisc 750 directory"
[ "$(systemctl show -p User --value pvj-ndi-discovery.service)" = pvj-ndisc ] || fail "the supervisor does not run as its own account"
if pgrep -f /opt/pvj-ndi-server/ndi-discovery-server >/dev/null; then fail "the program runs although its module is off"; fi
wait_for 40 systemctl is-active --quiet pvj-web.service || fail "the panel did not come back after the set-up"
is /run/pvj-ndisc/ndisc.sock "pvj-ndisc:pvj-ndisc 660 socket"
refused pvj-ndisc cat /run/pvj/web/pin
refused pvj-ndisc cat /var/lib/pvj/settings.json
refused pvj-ndisc ls /run/pvj/web
refused "$A" touch /run/pvj-ndisc/x
# the panel reaches the supervisor (the drop-in's group, the socket, the supervisor's list of who may ask)
ndisc_get() {
	local code
	code="$(curl -sS -m 20 -o "$OUT" -w '%{http_code}' -H "Authorization: Bearer $TOKEN" http://127.0.0.1/api/ndi/discovery)"
	[ "$code" = 200 ] || fail "GET /api/ndi/discovery answered HTTP $code, $(head -c 300 "$OUT")"
}
ndisc_post() {     # ndisc_post PATH JSON
	local code
	code="$(curl -sS -m 30 -o "$OUT" -w '%{http_code}' -H 'Content-Type: application/json' -H 'X-PVJ-Request: 1' -H "Authorization: Bearer $TOKEN" -d "$2" "http://127.0.0.1$1")"
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
systemctl is-active --quiet pvj-ndi-discovery.service || fail "the supervisor went down with the program it runs"
systemctl is-active --quiet pvj-web.service || fail "the panel went down with the discovery server's program"
ndisc_post /api/modules/ndi-discovery '{"enabled": false}'
ndisc_get
grep -q '"state": "off"' "$OUT" || fail "switched off, the server is not off: $(head -c 300 "$OUT")"
ndisc_post /api/ndi/discovery '{"network": "wired", "port": 5959}'
installer
grep -q "this box has the discovery server set up" "$OUT" || fail "the installer did not see the discovery server's set-up"
wait_for 40 systemctl is-active --quiet pvj-ndi-discovery.service || fail "pvj-ndi-discovery is not active after a later install"
/usr/local/bin/pvj-ndi-runtime server remove 2>&1 | tee "$OUT"
for path in /etc/systemd/system/pvj-ndi-discovery.service /etc/systemd/system/pvj-web.service.d/50-pvj-ndisc.conf \
	/etc/systemd/system/multi-user.target.wants/pvj-ndi-discovery.service /opt/pvj-ndi-server; do
	if [ -e "$path" ] || [ -L "$path" ]; then fail "after 'server remove' there is still $path"; fi
done
if systemctl is-active --quiet pvj-ndi-discovery.service; then fail "pvj-ndi-discovery still runs after 'server remove'"; fi
wait_for 40 systemctl is-active --quiet pvj-web.service || fail "the panel did not come back after 'server remove'"
nothing_of_ndi
services_and_their_files

# ---- an NDI picture all the way, on the box layout, with a stand-in for NDI's library ------------------------------
# Why (2026-10-12, found on the owner's Pi): since the pipe became root's, the helper refused to write it, and no NDI
# picture reached the screen of any box. Every test was green, because every test ran the helper where it makes a
# pipe of its own. So here the whole road is taken as a box has it: the real accounts, the real units and the
# helper's sandbox, root's pipe from the tmpfiles lines, the real pvj-ndi service, the panel's API, the real mpv.
# The one thing that is not real is the library: tests/ndi_standin/standin.c, written from what pvj/ndi.py calls,
# which makes up three sources and touches no network. So this never shows that NDI's own library works, finds a
# sender or gets through the sandbox's network rules.
# And this machine has no screen: the player is started with mpv's null video and sound outputs (a drop-in of this
# test's, in /run, gone at the end), because without any output mpv plays nothing and lets go of the pipe. What is
# shown is that the player reads and decodes the pipe, not that a picture is on a display.
NDI_BUILD="$(mktemp -d)"
NDI_STANDIN="$NDI_BUILD/libndi.so.6"
NDI_LIB=/opt/pvj-ndi/libndi.so.6
NDI_PIPE=/run/pvj-ndi-pipe/ndi.fifo
NDI_PLAYER_CI=/run/systemd/system/pvj-player.service.d/90-ci-no-screen.conf
MPV_LOG=/tmp/pvj-ci-mpv.log
NDI_MOVING="PVJ-STANDIN (moving)"
NDI_SOUND="PVJ-STANDIN (moving with sound)"
NDI_STILL="PVJ-STANDIN (one still then sound)"
NDI_CHANGES="PVJ-STANDIN (changes size)"
# 1280 x 720: a picture is 1.8 MB, more than the pipe holds, so the writer is in the middle of a picture most of the time
NDI_BIG_SOUND="PVJ-STANDIN (big moving with sound)"
NDI_BIG_STILL="PVJ-STANDIN (big one still then sound)"
NDI_BIG_A="PVJ-STANDIN (big at once A)"
NDI_BIG_B="PVJ-STANDIN (big at once B)"
NDI_BIG_A_SOUND="PVJ-STANDIN (big at once A with sound)"
NDI_BIG_B_SOUND="PVJ-STANDIN (big at once B with sound)"
NDI_BIG_CHANGES="PVJ-STANDIN (big changes size)"
NDI_BIG_CHANGES_SOUND="PVJ-STANDIN (big changes size with sound)"
box() {            # the questions to the panel and the player (tests/ndi_standin/box_check.py); the token stays off the command line
	PVJ_TOKEN="$TOKEN" python3 -B "$SRC/tests/ndi_standin/box_check.py" "$@"
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
	ls -lan /run/pvj-ndi-pipe /run/pvj-ndi /opt/pvj-ndi >&2 || true
	echo "--- the helper's journal" >&2
	journalctl -b --no-pager -u pvj-ndi -n 60 >&2 || true
	echo "--- the panel's journal" >&2
	journalctl -b --no-pager -u pvj-web -n 40 >&2 || true
	echo "--- the player's command line and the end of its own log" >&2
	tr '\0' ' ' < "/proc/$(systemctl show -p MainPID --value pvj-player.service)/cmdline" >&2 || true
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
PLAYER_EXEC="$(sed -n 's/^ExecStart=\(.*\) serve$/\1/p' /etc/systemd/system/pvj-player.service)"
[ -n "$PLAYER_EXEC" ] || fail "the player's unit has no 'ExecStart=... serve' line to add the null outputs to"
install -d -m 0755 "$(dirname "$NDI_PLAYER_CI")"
printf '%s\n' "# Written by tests/real_install_test.sh for its NDI step and removed by it: this machine has no screen." \
	"[Service]" "ExecStart=" \
	"ExecStart=$PLAYER_EXEC --mpv-arg=--vo=null --mpv-arg=--ao=null --mpv-arg=--log-file=$MPV_LOG serve" > "$NDI_PLAYER_CI"
systemctl daemon-reload
# The real command, whole: its checks of the file (a plain file of this processor, not too large; nothing in them
# knows NDI's file from any other library), the copy to root's folder, the account, the units, the pipe, the restarts.
if ! /usr/local/bin/pvj-ndi-runtime install "$NDI_STANDIN" 2>&1 | tee "$OUT"; then ndi_fail "the real command did not set NDI up with the stand-in"; fi
grep -q "^installed $NDI_LIB\$" "$OUT" || fail "the command did not say where it placed the library"
grep -q "^sha256 $(sha256sum "$NDI_STANDIN" | cut -d' ' -f1) " "$OUT" || fail "the sha256 the command printed is not the stand-in's"
grep -q "the helper pvj-ndi is installed and was running" "$OUT" || fail "the command did not say that the helper stayed up"
is /opt/pvj-ndi "root:root 755 directory"
is "$NDI_LIB" "root:root 644 regular file"
cmp "$NDI_STANDIN" "$NDI_LIB" || fail "what was placed is not what was given"
is /run/pvj-ndi-pipe "root:pvj-ndi 750 directory"
is "$NDI_PIPE" "root:pvj-ndi 660 fifo"
wait_for 40 systemctl is-active --quiet pvj-ndi.service || ndi_fail "pvj-ndi is not active after the real command"
wait_for 40 test -S /run/pvj-ndi/ndi.sock || ndi_fail "pvj-ndi made no socket after the real command"
wait_for 40 systemctl is-active --quiet pvj-web.service || ndi_fail "the panel did not come back after the real command"
wait_for 40 player_socket_is_open || ndi_fail "the player does not stay up with mpv's null outputs; this step needs a player"
PLAYER_PID="$(systemctl show -p MainPID --value pvj-player.service)"
tr '\0' ' ' < "/proc/$PLAYER_PID/cmdline" | grep -q -- '--vo=null' || ndi_fail "the player was not started with the null output: this test's drop-in was not used"
in_group "$PLAYER_PID" "$NDI_GID" || ndi_fail "the running player is not in the group pvj-ndi"
ndi_page
grep -q '"setup": true' "$OUT" || ndi_fail "the panel does not see that the box is set up for NDI"
grep -q '"helper": true' "$OUT" || ndi_fail "the panel cannot reach the helper"
wait_for 30 ndi_loaded || ndi_fail "with the module on, the helper did not load the library that the real command placed"
cat "$OUT"
echo
grep -q 'PVJ STAND-IN' "$OUT" || ndi_fail "the library the helper says it loaded is not the stand-in"
HELPER_PID="$(systemctl show -p MainPID --value pvj-ndi.service)"
[ "$(ps -o user= -p "$HELPER_PID" | tr -d ' ')" = pvj-ndi ] || ndi_fail "the helper's process does not run as pvj-ndi"
grep -q " $NDI_LIB\$" "/proc/$HELPER_PID/maps" || ndi_fail "the helper's process has not got $NDI_LIB loaded"

step "sound off: the helper's account writes root's pipe, the player reads it, the panel says 'playing' and counts the pictures"
# With the check as it was until 2026-10-12 (a pipe is written only if the helper owns it) this is where the run
# ends: 'PVJ-STANDIN (moving)' did not play: it stopped with "the input pipe was replaced; not writing to it".
box play "$NDI_MOVING" raw || ndi_fail "sound off: the stand-in's picture did not get from the helper through root's pipe to the player"
holds_the_pipe "$HELPER_PID" || ndi_fail "the helper's process has not got root's pipe open"
holds_the_pipe "$PLAYER_PID" || ndi_fail "the player's process has not got root's pipe open"
is "$NDI_PIPE" "root:pvj-ndi 660 fifo"

step "the helper is restarted under the playing source: the panel brings the source back by itself"
systemctl restart pvj-ndi.service
[ "$(systemctl show -p MainPID --value pvj-ndi.service)" != "$HELPER_PID" ] || ndi_fail "after a restart the helper is the same process"
box back "$NDI_MOVING" 20 || ndi_fail "the source did not come back by itself after the helper's restart"
HELPER_PID="$(systemctl show -p MainPID --value pvj-ndi.service)"
holds_the_pipe "$HELPER_PID" || ndi_fail "the new helper's process has not got root's pipe open"
[ "$(systemctl show -p MainPID --value pvj-player.service)" = "$PLAYER_PID" ] || ndi_fail "the player was restarted with the helper; it must only load the pipe again"

step "sound on: the same road with the picture and the sound together (Matroska)"
box sound on || ndi_fail "sound could not be switched on"
box play "$NDI_SOUND" matroska || ndi_fail "sound on: the stand-in's picture and sound did not get to the player"

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
# THE ONE SWITCH. Some of these fail today because of that fault (the hand-over of the pipe; being mended on the
# branch ndi-pipe-reuse). Each step but the chain starts clean (a new helper, a new player), so that one finding
# does not make the next; the chain at the end cleans nothing, because carry-over is what it is for. While it says "no", a step that fails prints a line beginning FINDING, the run goes on and
# stays green, and its last line lists every finding. Set it to "yes" (here, or PVJ_NDI_FINDINGS_FATAL=yes in the
# environment) and the first of them fails the run: that is how to see that the fault is mended, and what this
# becomes once it is.
NDI_FINDINGS_FATAL="${PVJ_NDI_FINDINGS_FATAL:-no}"
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
	journalctl -b --no-pager -u pvj-ndi -u pvj-web -n 25 || true
	if [ "$NDI_FINDINGS_FATAL" = yes ]; then ndi_fail "$words"; fi
	echo "FINDING: $words. The line above that begins REASON says what mpv and the page said. Not fatal while NDI_FINDINGS_FATAL is 'no'."
	NDI_FINDINGS="$NDI_FINDINGS [$words]"
}
helper_is_new() {  # helper_is_new OLD_PID: the helper runs, as another process
	local pid
	pid="$(systemctl show -p MainPID --value pvj-ndi.service)"
	[ "$pid" != 0 ] && [ "$pid" != "$1" ] && [ -S /run/pvj-ndi/ndi.sock ]
}
ndi_module() {     # ndi_module true|false
	local code
	code="$(curl -sS -m 20 -o "$OUT" -w '%{http_code}' -H 'Content-Type: application/json' -H 'X-PVJ-Request: 1' -H "Authorization: Bearer $TOKEN" -d "{\"enabled\": $1}" http://127.0.0.1/api/modules/inputs-ndi)"
	[ "$code" = 200 ] || ndi_fail "the NDI module could not be switched to $1: HTTP $code, $(head -c 300 "$OUT")"
}
ndi_clean() {      # every step starts from the same place, whatever the one before left: no source, a new helper, a new player
	local old
	old="$(systemctl show -p MainPID --value pvj-ndi.service)"
	box sound off || ndi_fail "sound could not be switched off between two steps"
	ndi_module false                               # the panel takes the source off and the helper lets go of it
	systemctl restart pvj-ndi.service              # a new helper: the stand-in's source that changes size starts again too
	systemctl restart pvj-player.service           # a player that was left waiting on the pipe is gone with this
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
ndi_case "the same source played twice in a row" case twice "$NDI_STILL"

step "hand-over 2b: from the stuck state of hand-over 1, nothing cleaned: the same source with sound off, then sound switched on (1280 x 720)"
ndi_clean
ndi_case "the same source with sound off straight after its stuck load with sound, then sound on" case stuck "$NDI_BIG_STILL"

step "hand-over 3: source A, then source B at once, sound off (1280 x 720; both have their first picture the moment they are opened)"
ndi_clean
ndi_case "one source straight after another" case follow "$NDI_BIG_A" "$NDI_BIG_B"

step "hand-over 3b: source A with sound, then source B with sound at once (1280 x 720, Matroska on both sides)"
ndi_clean
box sound on || ndi_fail "sound could not be switched on"
ndi_case "one source with sound straight after another with sound" case follow "$NDI_BIG_A_SOUND" "$NDI_BIG_B_SOUND"

step "hand-over 4: sound switched on, and off again, under a playing source"
ndi_clean
ndi_case "sound switched on and off under a playing source" case sound "$NDI_SOUND"

step "hand-over 4b: the same under a 1280 x 720 source, in both directions: off, on, off; then on, off, on"
ndi_clean
ndi_case "sound switched on and off under a big playing source" case sound "$NDI_BIG_SOUND"
ndi_clean
ndi_case "sound switched off and on under a big source that played with sound" case soundon "$NDI_BIG_SOUND"

step "hand-over 5: a source that changes size while it plays, sound off (1280 x 720, then 1264 x 704)"
ndi_clean
ndi_case "a source that changed size while it played" case size "$NDI_BIG_CHANGES"

step "hand-over 5b: a source with sound that changes size while it plays (Matroska on both sides; the Pi lost this one)"
ndi_clean
box sound on || ndi_fail "sound could not be switched on"
ndi_case "a source with sound that changed size while it played" case size "$NDI_BIG_CHANGES_SOUND"

step "hand-over 5c: the small source that changes size, sound off (320 x 180, then 256 x 144: pictures that fit the pipe whole)"
ndi_clean
ndi_case "a small source that changed size while it played" case size "$NDI_CHANGES"

step "hand-over 6: the helper restarted under a playing source, judged by mpv's own word"
ndi_clean
box play "$NDI_MOVING" raw || ndi_fail "sound off: the stand-in's picture did not play before the helper's restart"
systemctl restart pvj-ndi.service
ndi_case "the helper restarted under a playing source" case restart "$NDI_MOVING"

step "hand-over 7, the chain: eight hand-overs back to back with sound, nothing cleaned between them (what one leaves, the next meets)"
ndi_clean
ndi_case "the chain of hand-overs with nothing cleaned between them" case chain

step "the stand-in is taken off by the real command, and this test's drop-in with it: nothing of NDI is left"
box sound off || ndi_fail "sound could not be switched off again"
ndi_module false
rm -f "$NDI_PLAYER_CI"
rmdir "$(dirname "$NDI_PLAYER_CI")"
systemctl daemon-reload
# it restarts the panel and the player: the player as its unit says again
if ! /usr/local/bin/pvj-ndi-runtime remove 2>&1 | tee "$OUT"; then fail "the real command could not take NDI off again"; fi
grep -q "^removed NDI's library ($NDI_LIB)\$" "$OUT" || fail "the command did not say that it removed the library"
if [ -e /opt/pvj-ndi ] || [ -L /opt/pvj-ndi ]; then fail "after 'remove' there is still /opt/pvj-ndi"; fi
rm -rf "$NDI_BUILD"
sleep 2
wait_for 40 panel_answers || fail "the panel does not answer after 'pvj-ndi-runtime remove'"
nothing_of_ndi
services_and_their_files
if [ "$PLAYER_SEEN" = 1 ] && tr '\0' ' ' < "/proc/$(systemctl show -p MainPID --value pvj-player.service)/cmdline" | grep -q -- '--vo=null'; then
	fail "the player still runs with this test's null output"
fi

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
echo "NDI: a picture of the stand-in for NDI's library went the whole road (never NDI's own library, never a screen)."
echo "NDI hand-overs judged by mpv's own word, FINDINGS (not fatal, NDI_FINDINGS_FATAL=$NDI_FINDINGS_FATAL):${NDI_FINDINGS:- none}"
