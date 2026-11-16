#!/bin/bash
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
#
# Add a box or a support laptop to the support hub made by setup-hub.sh. Run as root on the hub.
#   ./add-peer.sh box NAME BOX-KEY      a box: its key is shown in its panel (System > Remote support)
#   ./add-peer.sh support NAME          a support laptop: prints a WireGuard config to import on the laptop
#   ./add-peer.sh remove NAME           take a box or laptop off the hub (for example a stolen box)
set -euo pipefail

DIR=/etc/wireguard
CONF="$DIR/nxlx0.conf"
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ -f "$CONF" ] || { echo "no hub here; run setup-hub.sh first" >&2; exit 1; }

kind="${1:-}"; name="${2:-}"
case "$name" in ''|*[!A-Za-z0-9._-]*) echo "give a name made of letters, digits, . _ -" >&2; exit 1;; esac

used() { grep -o '^AllowedIPs = 10\.77\.0\.[0-9]*' "$CONF" | sed 's/.*\.//'; }
free() {  # first free host number from $1 to $2
	local n
	for n in $(seq "$1" "$2"); do
		if ! used | grep -qx "$n"; then echo "$n"; return; fi
	done
	echo "no free address left" >&2; exit 1
}
reload() { wg syncconf nxlx0 <(wg-quick strip nxlx0); }
exists() { grep -qxF "# $1" "$CONF"; }

case "$kind" in
box)
	key="${3:-}"
	# the whole string, one line: a key with a line break after it could add lines to the hub's config
	if ! [[ $key =~ ^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=$ ]]; then echo "that is not a WireGuard key" >&2; exit 1; fi
	if grep -qxF "PublicKey = $key" "$CONF"; then echo "that key is already on this hub" >&2; exit 1; fi
	if exists "$name"; then echo "$name exists; remove it first" >&2; exit 1; fi
	n=$(free 32 254)
	printf '\n# %s\n[Peer]\nPublicKey = %s\nAllowedIPs = 10.77.0.%s/32\n' "$name" "$key" "$n" >> "$CONF"
	reload
	echo "Added box $name as 10.77.0.$n. In its panel, set 'This box on the support network' to 10.77.0.$n."
	;;
support)
	if exists "$name"; then echo "$name exists; remove it first" >&2; exit 1; fi
	n=$(free 2 31)
	umask 077
	priv=$(wg genkey); pub=$(printf '%s' "$priv" | wg pubkey)
	printf '\n# %s\n[Peer]\nPublicKey = %s\nAllowedIPs = 10.77.0.%s/32\n' "$name" "$pub" "$n" >> "$CONF"
	reload
	endpoint=$(cat "$DIR/nxlx0.endpoint")
	cat <<CLIENT
# WireGuard config for support laptop $name. Import it in the WireGuard app; keep it private.
[Interface]
PrivateKey = $priv
Address = 10.77.0.$n/32

[Peer]
PublicKey = $(cat "$DIR/nxlx0.pub")
Endpoint = $endpoint
AllowedIPs = 10.77.0.0/24
PersistentKeepalive = 25
CLIENT
	;;
remove)
	if ! exists "$name"; then echo "no peer called $name" >&2; exit 1; fi
	# drop the "# name" line and the [Peer] block after it
	awk -v n="# $name" '$0 == n {skip = 1; next} skip && /^\[Peer\]$/ {next} skip && /^(PublicKey|AllowedIPs) = / {next} {skip = 0; print}' "$CONF" > "$CONF.new"
	mv "$CONF.new" "$CONF"
	reload
	echo "Removed $name."
	;;
*)
	sed -n '5,9p' "$0" >&2; exit 1;;
esac
