#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
# Runs install/install.sh FOR REAL, as root, under a real systemd, and checks who owns what in /run/pvj (D45):
# an upgrade over an old shared folder with planted names and links, restarts, a crash, a reinstall, an emptying
# that was cut short, and the things the service accounts must not be able to do.
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
if [ -e /opt/pvj ] || [ -e /run/pvj ]; then echo "refusing: this machine already has an install" >&2; exit 2; fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
A=pvjdisplay                       # the display account of this test
OUT="$(mktemp)"
PLANTED=/root/pvj-planted-by-root

listing() {
	find /run/pvj /run/pvj-sysd /run/pvj-supportd /run/pvj-update -printf '%u:%g %m %y %p %l\n' 2>&1 | sort -k4
}
fail() {
	echo "FAIL: $*" >&2
	echo "--- listing" >&2
	listing >&2 || true
	echo "--- journal" >&2
	journalctl -b --no-pager -u pvj-player -u pvj-web -u pvj-netd -u pvj-sysd -n 60 >&2 || true
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

step "final listing"
listing
echo
echo "PASS (player folder checked: $PLAYER_SEEN). This ran on $(systemctl --version | head -n 1), not on a Raspberry Pi."
