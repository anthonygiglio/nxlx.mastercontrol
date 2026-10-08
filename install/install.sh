#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
# nxlx.mastercontrol installer. Idempotent: run it again to update or repair.
#
#   sudo install/install.sh [options]
#
# Options
#   --prefix DIR     install location (default /opt/pvj)
#   --user NAME      account that owns the screen and sound card (default: the
#                    user who ran sudo, else a new system user "pvj-player")
#   --web-user NAME  optional: add another account (a separate web app) to group "pvj"; NOT needed for
#                    the built-in panel, which has its own pvj-web account. Members can reach the player's socket.
#   --media DIR      video folder (default /var/lib/pvj/video)
#   --offline        never touch the network; fail if a dependency is missing
#   --no-start       install and enable the service but do not start it
#   --stage DIR      install into DIR without users, apt or systemctl (for tests and image builds)
#   --dry-run        print what would happen
#   --uninstall      remove the service and program files (keeps /etc/pvj and media)
#   --purge          with --uninstall, also remove /etc/pvj
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX=/opt/pvj
PVJ_USER=""
WEB_USER=""
MEDIA=/var/lib/pvj/video
OFFLINE=0
START=1
STAGE=""
DRY=0
UNINSTALL=0
PURGE=0

log() { printf 'pvj-install: %s\n' "$*"; }
die() { printf 'pvj-install: error: %s\n' "$*" >&2; exit 1; }
run() {
	if [ "$DRY" = 1 ]; then printf '  would run: %s\n' "$*"; else "$@"; fi
}

while [ $# -gt 0 ]; do
	case "$1" in
	--prefix) PREFIX="${2:?}"; shift 2 ;;
	--user) PVJ_USER="${2:?}"; shift 2 ;;
	--web-user) WEB_USER="${2:?}"; shift 2 ;;
	--media) MEDIA="${2:?}"; shift 2 ;;
	--offline) OFFLINE=1; shift ;;
	--no-start) START=0; shift ;;
	--stage) STAGE="${2:?}"; shift 2 ;;
	--dry-run) DRY=1; shift ;;
	--uninstall) UNINSTALL=1; shift ;;
	--purge) PURGE=1; shift ;;
	-h | --help) sed -n '4,19p' "${BASH_SOURCE[0]}"; exit 0 ;;
	*) die "unknown option $1 (try --help)" ;;
	esac
done

# One way of writing a folder: no slash at the end and no doubled one. The prefix goes into install.json and from
# there into units (the NDI helper's, pvj/ndisetup.py), which take it only as this script would have written it.
while [ "${#PREFIX}" -gt 1 ] && [ "${PREFIX%/}" != "$PREFIX" ]; do PREFIX="${PREFIX%/}"; done
case "$PREFIX" in *//*) die "--prefix must not contain //" ;; esac
case "$PREFIX" in /*) ;; *) die "--prefix must be an absolute path" ;; esac
case "$MEDIA" in /*) ;; *) die "--media must be an absolute path" ;; esac
for name in "$PVJ_USER" "$WEB_USER"; do
	[[ "$name" =~ ^([a-z_][a-z0-9_-]*)?$ ]] || die "user names may only use a-z, 0-9, _ and -"
done
# Refuse prefixes that would make uninstall dangerous (/, /opt, /usr, ...).
[[ "$PREFIX" =~ ^/[^/]+/[^/]+ ]] || die "--prefix must be at least two levels deep, e.g. /opt/pvj"
case "$PREFIX" in /usr/* | /etc/* | /bin/* | /sbin/* | /lib/* | /boot/* | /var/lib/dpkg*) die "--prefix must not be inside a system directory" ;; esac
for path in "$PREFIX" "$MEDIA"; do
	[[ "$path" =~ ^[A-Za-z0-9/_.-]+$ ]] || die "paths may only use letters, digits, / _ . and -"
	[[ "$path" =~ (^|/)\.\.(/|$) ]] && die "paths may not contain .. components"
	[[ "$path" =~ (^|/)\.(/|$) ]] && die "paths may not contain . components"
done

if [ -n "$STAGE" ]; then
	ROOT="${STAGE%/}"
else
	ROOT=""
	if [ "$DRY" = 0 ] && [ "$(id -u)" -ne 0 ]; then die "run as root (sudo), or use --dry-run or --stage"; fi
fi
REAL=$([ -z "$STAGE" ] && echo 1 || echo 0)

ETC="$ROOT/etc/pvj"
UNIT="$ROOT/etc/systemd/system/pvj-player.service"
USB_UNIT="$ROOT/etc/systemd/system/pvj-usb@.service"
WEB_UNIT="$ROOT/etc/systemd/system/pvj-web.service"
NET_UNIT="$ROOT/etc/systemd/system/pvj-netd.service"
SYS_UNIT="$ROOT/etc/systemd/system/pvj-sysd.service"
SUP_UNIT="$ROOT/etc/systemd/system/pvj-supportd.service"
# The NDI input is opt-in per box (D62): none of these three files exists unless root ran "pvj-ndi-runtime install"
# at the box (pvj/ndisetup.py). The panel's drop-in is the mark of that; this installer never writes it.
NDI_UNIT="$ROOT/etc/systemd/system/pvj-ndi.service"
NDI_MARK="$ROOT/etc/systemd/system/pvj-web.service.d/50-pvj-ndi.conf"
NDI_PLAYER_DROPIN="$ROOT/etc/systemd/system/pvj-player.service.d/50-pvj-ndi.conf"
NDI_WANTS="$ROOT/etc/systemd/system/multi-user.target.wants/pvj-ndi.service"      # the link "systemctl enable" makes
JOURNAL_CONF="$ROOT/etc/systemd/journald.conf.d/50-pvj-persistent-log.conf"
UPD_USB_UNIT="$ROOT/etc/systemd/system/pvj-update-usb@.service"
UPD_INBOX_UNIT="$ROOT/etc/systemd/system/pvj-update-inbox@.service"
WG_LOAD="$ROOT/etc/modules-load.d/pvj-wireguard.conf"
USB_RULE="$ROOT/etc/udev/rules.d/99-pvj-usb.rules"
TMPFILES="$ROOT/etc/tmpfiles.d/pvj.conf"
BIN_LINKS="$ROOT/usr/local/bin"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC/pvj/__init__.py")"
[ -n "$VERSION" ] || die "cannot read version from pvj/__init__.py"
RELEASE="$ROOT$PREFIX/releases/$VERSION"

uninstall() {
	log "uninstalling"
	if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && [ -d /run/systemd/system ]; then
		systemctl disable --now pvj-player.service 2>/dev/null || true
	fi
	if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && [ -d /run/systemd/system ]; then
		systemctl disable --now pvj-ndi.service 2>/dev/null || true
	fi
	# A box that was set up for NDI: the mark first, then the rest; the two folders only if nothing else is in them.
	run rm -f "$NDI_MARK" "$NDI_PLAYER_DROPIN" "$NDI_UNIT" "$NDI_WANTS" "$BIN_LINKS/pvj-ndi-runtime" || log "WARNING: could not remove everything of the NDI helper; look at $NDI_MARK, $NDI_PLAYER_DROPIN and $NDI_UNIT and remove them by hand"
	if [ "$DRY" = 0 ]; then rmdir "$(dirname "$NDI_MARK")" "$(dirname "$NDI_PLAYER_DROPIN")" 2>/dev/null || true; fi
	run rm -f "$SUP_UNIT" "$WG_LOAD" "$UPD_USB_UNIT" "$UPD_INBOX_UNIT" "$JOURNAL_CONF" "$TMPFILES"
	run rm -f "$UNIT" "$WEB_UNIT" "$NET_UNIT" "$SYS_UNIT" "$USB_UNIT" "$USB_RULE" "$BIN_LINKS/pvj-player" "$BIN_LINKS/pvj-selftest" "$BIN_LINKS/pvj-usb" "$BIN_LINKS/pvj-rootfs" "$BIN_LINKS/pvj-pin" "$BIN_LINKS/pvj-update"
	run rm -rf "${ROOT}${PREFIX:?}"
	[ "$PURGE" = 1 ] && run rm -rf "$ETC"
	# The owner's copy of NDI's library is theirs: it goes only with --purge.
	if [ "$PURGE" = 1 ]; then run rm -rf "${ROOT}/opt/pvj-ndi"; fi
	if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && [ -d /run/systemd/system ]; then systemctl daemon-reload; fi
	if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && command -v udevadm >/dev/null; then udevadm control --reload || true; fi
	log "done. Kept ${ETC}, media and /opt/pvj-ndi (your copy of the NDI runtime, if this box was set up for NDI) unless --purge; the users (pvj-web, the player's, and pvj-ndi on a box that was set up for NDI) and the groups pvj and pvj-ndi are left in place (remove them with deluser and delgroup if you want them gone)."
}

if [ "$UNINSTALL" = 1 ]; then uninstall; exit 0; fi

# --- dependencies -------------------------------------------------------
need=()
command -v mpv >/dev/null || need+=(mpv)
command -v python3 >/dev/null || need+=(python3)
if command -v python3 >/dev/null && ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
	die "Python 3.9 or newer is required"
fi
if [ ${#need[@]} -gt 0 ]; then
	if [ "$OFFLINE" = 1 ]; then die "offline mode and missing: ${need[*]}"; fi
	if [ "$REAL" = 0 ]; then log "stage mode: not installing ${need[*]}"
	elif command -v apt-get >/dev/null; then
		log "installing ${need[*]}"
		run apt-get install -y --no-install-recommends "${need[@]}"
	else
		die "unsupported system (no apt-get). Install manually: ${need[*]}"
	fi
fi

# Optional: remote support needs WireGuard's tools and nftables. Missing ones are installed when the network allows;
# without them remote support is simply reported as unavailable in the panel.
optional=()
command -v wg >/dev/null || [ -x /usr/bin/wg ] || optional+=(wireguard-tools)
command -v nft >/dev/null || [ -x /usr/sbin/nft ] || optional+=(nftables)
if [ ${#optional[@]} -gt 0 ]; then
	if [ "$OFFLINE" = 1 ] || [ "$REAL" = 0 ] || ! command -v apt-get >/dev/null; then
		log "remote support will be unavailable until these are installed: ${optional[*]}"
	else
		log "installing ${optional[*]} (for remote support)"
		run apt-get install -y --no-install-recommends "${optional[@]}" || log "could not install ${optional[*]}; remote support stays unavailable"
	fi
fi

# Nothing is installed for the NDI input here, not avahi either: that is opt-in per box (pvj/ndisetup.py, D62).

# --- accounts -----------------------------------------------------------
if [ -z "$PVJ_USER" ] && [ -f "$ETC/install.json" ]; then
	PVJ_USER="$(sed -n 's/.*"user": "\([a-z_][a-z0-9_-]*\)".*/\1/p' "$ETC/install.json" | head -n 1)"
fi
if [ -z "$PVJ_USER" ]; then
	if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then PVJ_USER="$SUDO_USER"; else PVJ_USER=pvj-player; fi
fi

if [ "$REAL" = 1 ]; then
	getent group pvj >/dev/null || run groupadd --system pvj
	if ! id "$PVJ_USER" >/dev/null 2>&1; then
		log "creating system user $PVJ_USER"
		run useradd --system --create-home --home-dir /var/lib/pvj-player --shell /usr/sbin/nologin --gid pvj "$PVJ_USER"
	fi
	# Older installs made /var/lib/pvj the player's home, which let the player account replace settings.json.
	# Give it its own home, changing only the home entry: nothing is moved out of /var/lib/pvj.
	if [ "$(getent passwd "$PVJ_USER" | cut -d: -f6)" = /var/lib/pvj ]; then
		log "moving the home of $PVJ_USER from /var/lib/pvj to /var/lib/pvj-player (no files are moved)"
		run mkdir -p /var/lib/pvj-player
		run chown "$PVJ_USER":pvj /var/lib/pvj-player
		run chmod 0750 /var/lib/pvj-player
		# usermod refuses while the account has a running process (the player; found on a real Pi 4), and this
		# script stops at the first error. The player is started again at the end.
		if [ "$DRY" = 0 ] && [ -d /run/systemd/system ]; then systemctl stop pvj-player.service 2>/dev/null || true; fi
		run usermod -d /var/lib/pvj-player "$PVJ_USER"
	fi
	for g in pvj video render audio input; do
		getent group "$g" >/dev/null && run usermod -aG "$g" "$PVJ_USER"
	done
	if [ -n "$WEB_USER" ]; then run usermod -aG pvj "$WEB_USER"; fi
	# The web panel runs as its own account with no screen or sound access.
	if ! id pvj-web >/dev/null 2>&1; then
		log "creating system user pvj-web"
		run useradd --system --no-create-home --shell /usr/sbin/nologin --gid pvj pvj-web
	fi
fi

# --- program files: releases/<version>, "current" points at the active one --
log "installing version $VERSION to $PREFIX"
run mkdir -p "$ROOT$PREFIX/releases"
run rm -rf "$RELEASE.new"
run mkdir -p "$RELEASE.new"
if [ "$DRY" = 0 ]; then
	cp -a "$SRC/pvj" "$SRC/bin" "$RELEASE.new/"
	find "$RELEASE.new" -name __pycache__ -type d -prune -exec rm -rf {} +
fi
previous=""
[ -L "$ROOT$PREFIX/current" ] && previous="$(readlink "$ROOT$PREFIX/current")"
# Same version installed again: move the old folder aside and swap in the new one, so the folder
# "current" points at is never deleted first.
if [ -e "$RELEASE" ]; then run mv "$RELEASE" "$RELEASE.old.$$"; fi
run mv "$RELEASE.new" "$RELEASE"
run rm -rf "$RELEASE.old.$$"
# Atomic switch: rename a fresh symlink over "current".
run ln -sfn "$PREFIX/releases/$VERSION" "$ROOT$PREFIX/current.tmp"
run mv -T "$ROOT$PREFIX/current.tmp" "$ROOT$PREFIX/current"
if [ -n "$previous" ] && [ "$previous" != "$PREFIX/releases/$VERSION" ]; then
	[ "$DRY" = 0 ] && printf '%s\n' "$previous" > "$ROOT$PREFIX/previous"
fi
if [ "$REAL" = 1 ] && [ "$DRY" = 0 ]; then chown -R root:root "$ROOT$PREFIX"; fi
run chmod -R go-w "$ROOT$PREFIX"

run mkdir -p "$BIN_LINKS"
run ln -sfn "$PREFIX/current/bin/pvj-player" "$BIN_LINKS/pvj-player"
run ln -sfn "$PREFIX/current/bin/pvj-selftest" "$BIN_LINKS/pvj-selftest"
run ln -sfn "$PREFIX/current/bin/pvj-usb" "$BIN_LINKS/pvj-usb"
run ln -sfn "$PREFIX/current/bin/pvj-rootfs" "$BIN_LINKS/pvj-rootfs"
run ln -sfn "$PREFIX/current/bin/pvj-pin" "$BIN_LINKS/pvj-pin"
run ln -sfn "$PREFIX/current/bin/pvj-update" "$BIN_LINKS/pvj-update"
run ln -sfn "$PREFIX/current/bin/pvj-ndi-runtime" "$BIN_LINKS/pvj-ndi-runtime"

# --- settings and media (never overwritten if they exist) -----------------
run mkdir -p "$ETC"
if [ ! -e "$ETC/pvj.env" ]; then
	if [ "$DRY" = 0 ]; then
		cat > "$ETC/pvj.env" <<ENV
# Settings for the pvj-player service. Edit, then: sudo systemctl restart pvj-player
PVJ_MEDIA_DIR=$MEDIA
PVJ_USB_DIR=/media/usb
# USB drives are mounted read-only under /media/pvj/<label>. Set to 1 to allow writing.
PVJ_USB_RW=0
# Web panel: port and address. The panel is for a private network; do not expose it to the internet.
# PVJ_PORT=80
# PVJ_BIND=0.0.0.0
# Largest single upload from the panel, in MB.
# PVJ_MAX_UPLOAD_MB=8192
ENV
	fi
else
	log "keeping existing $ETC/pvj.env"
fi
# Public keys whose signatures pvj-update accepts (one line per key, see install/README.md).
if [ ! -e "$ETC/allowed_signers" ] && [ "$DRY" = 0 ]; then
	cat > "$ETC/allowed_signers" <<KEYS
# Add your release signing key here. Format:
#   pvj-release namespaces="pvj-release" ssh-ed25519 AAAA... comment
# Until a key is listed, pvj-update refuses every bundle.
KEYS
	chmod 644 "$ETC/allowed_signers"
fi
run mkdir -p "$ROOT$MEDIA"
if [ "$REAL" = 1 ] && [ "$DRY" = 0 ]; then
	chown "$PVJ_USER":pvj "$MEDIA"; chmod 2775 "$MEDIA"
	# settings.json lives in /var/lib/pvj and only the web panel may write there. The player (group pvj) may pass
	# through to the media folder and read, but not create, rename or replace files: 2750, owned by pvj-web.
	mkdir -p /var/lib/pvj; chown pvj-web:pvj /var/lib/pvj; chmod 2750 /var/lib/pvj
	[ -e /var/lib/pvj/settings.json ] && chown pvj-web:pvj /var/lib/pvj/settings.json* 2>/dev/null
	true
fi
if [ "$DRY" = 0 ]; then
	printf '{"version": "%s", "prefix": "%s", "user": "%s", "installed": "%s"}\n' \
		"$VERSION" "$PREFIX" "$PVJ_USER" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$ETC/install.json"
fi

# --- service ------------------------------------------------------------
run mkdir -p "$(dirname "$UNIT")"
if [ "$DRY" = 0 ]; then
	sed -e "s|@PVJ_USER@|$PVJ_USER|g" -e "s|@PVJ_DIR@|$PREFIX/current|g" "$SRC/install/pvj-player.service" > "$UNIT"
	sed -e "s|@PVJ_DIR@|$PREFIX/current|g" "$SRC/install/pvj-web.service" > "$WEB_UNIT"
	sed -e "s|@PVJ_DIR@|$PREFIX/current|g" "$SRC/install/pvj-netd.service" > "$NET_UNIT"
	sed -e "s|@PVJ_DIR@|$PREFIX/current|g" "$SRC/install/pvj-sysd.service" > "$SYS_UNIT"
	sed -e "s|@PVJ_DIR@|$PREFIX/current|g" "$SRC/install/pvj-supportd.service" > "$SUP_UNIT"
	# Updates from the panel: started on request by pvj-sysd, never enabled.
	sed -e "s|@PVJ_DIR@|$PREFIX/current|g" "$SRC/install/pvj-update-usb@.service" > "$UPD_USB_UNIT"
	sed -e "s|@PVJ_DIR@|$PREFIX/current|g" "$SRC/install/pvj-update-inbox@.service" > "$UPD_INBOX_UNIT"
	run rm -f "$ROOT/etc/systemd/system/pvj-update-usb.service" "$ROOT/etc/systemd/system/pvj-update-inbox.service"  # the names before they took a version
	# The support helper may not load kernel modules itself (its sandbox), so WireGuard's is loaded at boot.
	mkdir -p "$(dirname "$WG_LOAD")"
	echo wireguard > "$WG_LOAD"
	# The system log survives restarts (capped at 64 MB), so an unexpected restart can be explained afterwards.
	mkdir -p "$(dirname "$JOURNAL_CONF")"
	cp "$SRC/install/50-pvj-persistent-log.conf" "$JOURNAL_CONF"
	# The parent of the services' runtime folders belongs to root, at every boot (D45).
	mkdir -p "$(dirname "$TMPFILES")"
	cp "$SRC/install/pvj-tmpfiles.conf" "$TMPFILES"
fi

# Runtime folders (D45). Older versions shared one RuntimeDirectory, /run/pvj, between the player, the panel and the
# root network helper, and systemd gave it to whichever started last; it stays like that until the next boot unless
# it is put right here. Rules for this part, each from the independent review:
#   * root never creates, writes, chowns or chmods a name inside a folder that an unprivileged account owns (a link
#     planted there would send it anywhere); it only takes /run/pvj itself, in /run, and deletes below it;
#   * the folder counts as done only when a marker says so, and the marker is written last (by tmpfiles, as root, in
#     the folder that is root's by then), so an emptying that was cut short is finished by the next run;
#   * the three services are stopped BEFORE the reload and the emptying, so none can restart by itself in between
#     and have systemd chown a planted name.
# With --stage all of it works on DIR/run/pvj and leaves owners alone (the tests).
RUN_DIR="$ROOT/run/pvj"
RUN_MARK="$RUN_DIR/.d45"
RUN_UNITS=(pvj-player.service pvj-web.service pvj-netd.service)
RUN_WAS_ACTIVE=()
RUN_EMPTY=0

# True when /run/pvj is root's, has the marker, and holds nothing but the services' folders, each with its own owner.
run_folder_is_sound() {
	local entry want
	if [ ! -d "$RUN_DIR" ] || [ -L "$RUN_DIR" ]; then return 1; fi
	if [ ! -f "$RUN_MARK" ] || [ -L "$RUN_MARK" ]; then return 1; fi
	if [ "$REAL" = 1 ]; then
		if [ "$(stat -c '%U:%G %a' "$RUN_DIR")" != "root:root 755" ]; then return 1; fi
		if [ "$(stat -c '%U:%G' "$RUN_MARK")" != "root:root" ]; then return 1; fi
	elif [ "$(stat -c '%a' "$RUN_DIR")" != "755" ]; then
		return 1
	fi
	while IFS= read -r -d '' entry; do
		case "${entry##*/}" in
		.d45) continue ;;
		player) want="$PVJ_USER:pvj 750" ;;
		web) want="pvj-web:pvj 750" ;;
		netd) want="root:pvj 750" ;;
		*)
			if [ -d "$entry" ] && [ ! -L "$entry" ]; then return 1; fi     # a folder nobody should have made
			continue ;;                                                    # a stray file or link: removed below
		esac
		if [ ! -d "$entry" ] || [ -L "$entry" ]; then return 1; fi
		if [ "$REAL" = 1 ] && [ "$(stat -c '%U:%G %a' "$entry")" != "$want" ]; then return 1; fi
	done < <(find "$RUN_DIR" -mindepth 1 -maxdepth 1 -print0)
	return 0
}

# Before daemon-reload: decide, and stop what could write there or be restarted by systemd in the middle.
prepare_run_folder() {
	local u
	if [ ! -e "$RUN_DIR" ] && [ ! -L "$RUN_DIR" ]; then return 0; fi
	if run_folder_is_sound; then return 0; fi
	RUN_EMPTY=1
	if [ "$REAL" = 1 ]; then
		for u in "${RUN_UNITS[@]}"; do
			if systemctl is-active --quiet "$u"; then RUN_WAS_ACTIVE+=("$u"); fi
		done
		log "stopping the player, the panel and the network helper while their runtime folder is rebuilt"
		systemctl stop "${RUN_UNITS[@]}" 2>/dev/null || true
	fi
}

# After daemon-reload and before any (re)start.
fix_run_folder() {
	if [ "$RUN_EMPTY" = 1 ]; then
		log "moving the runtime files to a folder per service ($RUN_DIR now belongs to root)"
		if [ -L "$RUN_DIR" ]; then rm -f "$RUN_DIR"; fi
		if [ -d "$RUN_DIR" ]; then
			# First take the folder away from its owner, then empty it: the old sockets, the PIN, and anything an
			# account that owned the folder may have left, also under the names of the new folders. The marker goes
			# first, so a run that stops half-way is seen as unfinished.
			if [ "$REAL" = 1 ]; then chown root:root "$RUN_DIR"; fi
			chmod 0755 "$RUN_DIR"
			rm -f "$RUN_MARK"
			find "$RUN_DIR" -mindepth 1 -delete || die "could not empty $RUN_DIR (something is still writing there); the player, the panel and the network helper are stopped: run the installer again"
		fi
	elif [ -d "$RUN_DIR" ]; then
		# Nothing but the marker and the services' folders belongs here.
		find "$RUN_DIR" -mindepth 1 -maxdepth 1 ! -type d ! -name .d45 -delete || die "could not tidy $RUN_DIR; run the installer again"
	fi
	if [ "$REAL" = 1 ]; then
		# Creates /run/pvj and the marker if they are missing (see pvj-tmpfiles.conf), as at boot.
		systemd-tmpfiles --create "$TMPFILES" || log "systemd-tmpfiles reported a problem; /run/pvj is checked again at the next install and set up at the next boot"
	elif [ -d "$RUN_DIR" ]; then
		: > "$RUN_MARK"
	fi
}
if [ "$REAL" = 0 ] && [ "$DRY" = 0 ]; then prepare_run_folder; fix_run_folder; fi

# The NDI helper, ONLY on a box that opted in (the mark is there): its unit and the two drop-ins are written again
# by the program just installed, which is their one writer (pvj/ndisetup.py; it also makes sure the account and the
# group are there, because the player and the panel name the group in their drop-ins and systemd does not start a
# unit that names a group the system does not have). A box without the mark gets nothing: no unit, no account, no
# package. If this fails the install goes on: the files of the earlier install are still there and still right.
NDI_OPTED_IN=0
ndi_mark() { [ -f "$NDI_MARK" ] && [ ! -L "$NDI_MARK" ]; }
# A link where the mark goes is not an opt-in (the setup writes a plain file), but systemd would read a drop-in
# through it and give the panel the group and the name of the helper's folder. It is removed: rm takes the link
# itself away, never what it points to.
if [ -L "$NDI_MARK" ]; then
	log "WARNING: $NDI_MARK is a link, which the NDI setup never makes. Removing the link (not what it points to). This box is NOT set up for NDI."
	run rm -f "$NDI_MARK" || log "WARNING: could not remove the link $NDI_MARK; remove it by hand"
	if [ "$DRY" = 0 ]; then rmdir "$(dirname "$NDI_MARK")" 2>/dev/null || true; fi
fi
if ndi_mark; then
	NDI_OPTED_IN=1
	log "this box has NDI set up: writing the NDI helper's unit again"
	# --root is empty on a real box and the staged folder with --stage (where no account is touched).
	run python3 -B "$RELEASE/bin/pvj-ndi-runtime" refresh --root "$ROOT" --prefix "$PREFIX" || log "could not write the NDI helper's unit again; it stays as it was. To repair: sudo pvj-ndi-runtime install <the NDI SDK folder>"
elif [ -e "$NDI_UNIT" ] || [ -L "$NDI_UNIT" ] || [ -e "$NDI_PLAYER_DROPIN" ] || [ -L "$NDI_PLAYER_DROPIN" ] || [ -L "$NDI_WANTS" ]; then
	# Something of the helper without the mark: a setup that was cut short, or a box that ran this branch while it
	# still put the helper on every box. Nobody opted this box in, so it goes (the account pvj-ndi, if any, stays).
	log "removing an NDI helper that nobody set up on this box (to set NDI up: sudo pvj-ndi-runtime install <the NDI SDK folder>)"
	if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && [ -d /run/systemd/system ]; then
		systemctl disable --now pvj-ndi.service 2>/dev/null || true
	fi
	if ndi_mark; then
		# Looked at once more, right before anything is removed: a setup that finished in this very moment stays.
		NDI_OPTED_IN=1
		log "NDI was set up on this box just now; its helper is left in place"
		if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && [ -d /run/systemd/system ]; then
			systemctl enable --now pvj-ndi.service || log "could not start the NDI helper again; run: sudo systemctl enable --now pvj-ndi"
		fi
	else
		# Never the end of an install: a folder in the place of one of these makes rm fail, and that is said.
		run rm -f "$NDI_UNIT" "$NDI_PLAYER_DROPIN" "$NDI_WANTS" || log "WARNING: could not remove everything of that NDI helper ($NDI_UNIT, $NDI_PLAYER_DROPIN); look at what is there and remove it by hand"
		if [ "$DRY" = 0 ]; then rmdir "$(dirname "$NDI_PLAYER_DROPIN")" 2>/dev/null || true; fi
	fi
fi
# USB automount: udev starts pvj-usb@<partition>.service, which mounts by label.
run mkdir -p "$(dirname "$USB_RULE")"
if [ "$DRY" = 0 ]; then
	sed -e "s|@PVJ_DIR@|$PREFIX/current|g" "$SRC/install/pvj-usb@.service" > "$USB_UNIT"
	cp "$SRC/install/99-pvj-usb.rules" "$USB_RULE"
fi
if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && command -v udevadm >/dev/null; then udevadm control --reload || true; fi
if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && [ -d /run/systemd/system ]; then
	prepare_run_folder
	systemctl daemon-reload
	fix_run_folder
	if systemctl restart systemd-journald.service 2>/dev/null; then journalctl --flush 2>/dev/null || true; fi
	systemctl enable pvj-player.service pvj-web.service pvj-sysd.service pvj-supportd.service
	modprobe wireguard 2>/dev/null || log "the WireGuard kernel module is not available: remote support stays unavailable"
	if [ "$START" = 1 ]; then systemctl restart pvj-player.service pvj-web.service pvj-sysd.service pvj-supportd.service; fi
	# The network helper only makes sense with NetworkManager (Raspberry Pi OS, most desktops).
	if command -v nmcli >/dev/null; then
		systemctl enable pvj-netd.service
		# Even with --no-start (an update from the panel, which then restarts the player and the panel itself): a
		# helper that is running must move to the socket the new panel looks for. try-restart starts nothing.
		if [ "$START" = 1 ]; then systemctl restart pvj-netd.service; else systemctl try-restart pvj-netd.service || true; fi
	else
		log "NetworkManager not found: network settings in the panel stay unavailable"
	fi
	# The NDI helper, only where the box opted in: an opted-in box stays opted in. Like the network helper it moves
	# to the new program even with --no-start; try-restart starts nothing. It is idle until its module is on.
	if [ "$NDI_OPTED_IN" = 1 ] && [ -f "$NDI_UNIT" ]; then
		systemctl enable pvj-ndi.service || log "could not enable the NDI helper"
		if [ "$START" = 1 ]; then
			systemctl restart pvj-ndi.service || log "the NDI helper did not start; see: journalctl -u pvj-ndi -n 30"
		else
			systemctl try-restart pvj-ndi.service || true
		fi
	fi
	# With --no-start: what was stopped above for the runtime folder is started again, and nothing else.
	if [ "$START" = 0 ] && [ ${#RUN_WAS_ACTIVE[@]} -gt 0 ]; then systemctl start "${RUN_WAS_ACTIVE[@]}" || true; fi
fi

log "installed. Check the device with: pvj-selftest --play"
