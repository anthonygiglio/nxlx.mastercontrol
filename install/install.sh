#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
# nxlx.mastercontrol installer. Idempotent: run it again to update or repair.
#
#   sudo install/install.sh [options]
#
# Options
#   --prefix DIR     install location (default /opt/nxlx)
#   --user NAME      account that owns the screen and sound card (default: the
#                    user who ran sudo, else a new system user "nxlx-player")
#   --web-user NAME  optional: add another account (a separate web app) to group "nxlx-svc"; NOT needed for
#                    the built-in panel, which has its own nxlx-web account. Members can reach the player's socket.
#   --media DIR      video folder (default /var/lib/nxlx/video)
#   --offline        never touch the network; fail if a dependency is missing
#   --no-start       install and enable the service but do not start it
#   --stage DIR      install into DIR without users, apt or systemctl (for tests and image builds)
#   --dry-run        print what would happen
#   --uninstall      remove the service and program files (keeps /etc/nxlx and media)
#   --purge          with --uninstall, also remove /etc/nxlx
#   --signer URL     the owner's signing service, e.g. http://hp.local:8781 (D139): the node asks it for its
#                    HTTPS certificate by itself; written into /etc/nxlx/nxlx.env as NXLX_SIGNER_URL
#   --root-fingerprint HEX   required with --signer: the SHA-256 of the owner's root (boxcert.py root); a node with
#                    no root yet takes only that root (NXLX_ROOT_FINGERPRINT)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX=/opt/nxlx
NXLX_USER=""
WEB_USER=""
MEDIA=/var/lib/nxlx/video
OFFLINE=0
START=1
STAGE=""
DRY=0
UNINSTALL=0
PURGE=0
SIGNER=""
ROOT_FP=""

log() { printf 'nxlx-install: %s\n' "$*"; }
die() { printf 'nxlx-install: error: %s\n' "$*" >&2; exit 1; }
run() {
	if [ "$DRY" = 1 ]; then printf '  would run: %s\n' "$*"; else "$@"; fi
}

while [ $# -gt 0 ]; do
	case "$1" in
	--prefix) PREFIX="${2:?}"; shift 2 ;;
	--user) NXLX_USER="${2:?}"; shift 2 ;;
	--web-user) WEB_USER="${2:?}"; shift 2 ;;
	--media) MEDIA="${2:?}"; shift 2 ;;
	--offline) OFFLINE=1; shift ;;
	--no-start) START=0; shift ;;
	--stage) STAGE="${2:?}"; shift 2 ;;
	--dry-run) DRY=1; shift ;;
	--uninstall) UNINSTALL=1; shift ;;
	--purge) PURGE=1; shift ;;
	--signer) SIGNER="${2:?}"; shift 2 ;;
	--root-fingerprint) ROOT_FP="${2:?}"; shift 2 ;;
	-h | --help) sed -n '4,24p' "${BASH_SOURCE[0]}"; exit 0 ;;
	*) die "unknown option $1 (try --help)" ;;
	esac
done
SIGNER_RE='^https?://[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?/?$'
if [ -n "$SIGNER" ] && ! [[ "$SIGNER" =~ $SIGNER_RE ]]; then
	die "--signer must look like http://name-or-address:port"
fi
if [ -n "$SIGNER" ] && [ -z "$ROOT_FP" ]; then
	# a node with no root yet must know which root to take (D139): never the first that answers over plain http
	die "--signer needs --root-fingerprint too (the 64 hex digits python3 tools/boxcert.py root prints)"
fi
if [ -n "$ROOT_FP" ]; then
	ROOT_FP="$(printf '%s' "$ROOT_FP" | tr 'A-F' 'a-f' | tr -cd '0-9a-f')"
	[ "${#ROOT_FP}" = 64 ] || die "--root-fingerprint must be the root's SHA-256: 64 hex digits (boxcert.py root prints it)"
fi

# One way of writing a folder: no slash at the end and no doubled one. The prefix goes into install.json and from
# there into units (the NDI helper's, mctl/ndisetup.py), which take it only as this script would have written it.
while [ "${#PREFIX}" -gt 1 ] && [ "${PREFIX%/}" != "$PREFIX" ]; do PREFIX="${PREFIX%/}"; done
case "$PREFIX" in *//*) die "--prefix must not contain //" ;; esac
case "$PREFIX" in /*) ;; *) die "--prefix must be an absolute path" ;; esac
case "$MEDIA" in /*) ;; *) die "--media must be an absolute path" ;; esac
for name in "$NXLX_USER" "$WEB_USER"; do
	[[ "$name" =~ ^([a-z_][a-z0-9_-]*)?$ ]] || die "user names may only use a-z, 0-9, _ and -"
done
# Refuse prefixes that would make uninstall dangerous (/, /opt, /usr, ...).
[[ "$PREFIX" =~ ^/[^/]+/[^/]+ ]] || die "--prefix must be at least two levels deep, e.g. /opt/nxlx"
case "$PREFIX" in /usr/* | /etc/* | /bin/* | /sbin/* | /lib/* | /boot/* | /var/lib/dpkg*) die "--prefix must not be inside a system directory" ;; esac
for path in "$PREFIX" "$MEDIA"; do
	[[ "$path" =~ ^[A-Za-z0-9/_.-]+$ ]] || die "paths may only use letters, digits, / _ . and -"
	[[ "$path" =~ (^|/)\.\.(/|$) ]] && die "paths may not contain .. components"
	[[ "$path" =~ (^|/)\.(/|$) ]] && die "paths may not contain . components"
done

# With the read-only root on (nxlx-rootfs enable), everything this writes lands in RAM and is gone at the next restart,
# while the installer would report success. Same test as mctl/rootfs.py current_state(): the / line of /proc/mounts is of
# type overlay or from the device overlayroot. Not with --stage or --dry-run, which write elsewhere or nothing.
if [ -z "$STAGE" ] && [ "$DRY" = 0 ]; then
	root_line=$(awk '$2 == "/" { print $1, $3; exit }' "${NXLX_PROC_MOUNTS:-/proc/mounts}" 2>/dev/null || true)
	case "$root_line" in
	overlayroot\ * | *\ overlay)
		die "The system disk is read-only (power-loss protection is on), so an update would be lost at the next restart. Turn it off with: sudo nxlx-rootfs disable, restart, update, then sudo nxlx-rootfs enable and restart." ;;
	esac
fi

if [ -n "$STAGE" ]; then
	ROOT="${STAGE%/}"
else
	ROOT=""
	if [ "$DRY" = 0 ] && [ "$(id -u)" -ne 0 ]; then die "run as root (sudo), or use --dry-run or --stage"; fi
fi
REAL=$([ -z "$STAGE" ] && echo 1 || echo 0)

ETC="$ROOT/etc/nxlx"
UNIT="$ROOT/etc/systemd/system/nxlx-player.service"
USB_UNIT="$ROOT/etc/systemd/system/nxlx-usb@.service"
WEB_UNIT="$ROOT/etc/systemd/system/nxlx-web.service"
NET_UNIT="$ROOT/etc/systemd/system/nxlx-netd.service"
SYS_UNIT="$ROOT/etc/systemd/system/nxlx-sysd.service"
SUP_UNIT="$ROOT/etc/systemd/system/nxlx-supportd.service"
SOUND_UNIT="$ROOT/etc/systemd/system/nxlx-sound.service"      # sound analysis (D96): on every box, idle until the Owner chooses a device
LINK_UNIT="$ROOT/etc/systemd/system/nxlx-link.service"        # the Ableton Link follower (D117): on every box that could build it, off until the Owner says
RASTER_UNIT="$ROOT/etc/systemd/system/nxlx-raster@.service"   # the picture helper (D129): decodes uploaded logos, one process per job
RASTER_SOCKET_UNIT="$ROOT/etc/systemd/system/nxlx-raster.socket"
# The NDI input is opt-in per box (D62): none of these four files exists unless root ran "nxlx-ndi-runtime install"
# at the box (mctl/ndisetup.py). The panel's drop-in is the mark of that; this installer never writes it.
NDI_UNIT="$ROOT/etc/systemd/system/nxlx-ndi.service"
NDI_MARK="$ROOT/etc/systemd/system/nxlx-web.service.d/50-nxlx-ndi.conf"
NDI_PLAYER_DROPIN="$ROOT/etc/systemd/system/nxlx-player.service.d/50-nxlx-ndi.conf"
NDI_WANTS="$ROOT/etc/systemd/system/multi-user.target.wants/nxlx-ndi.service"      # the link "systemctl enable" makes
NDI_TMPFILES="$ROOT/etc/tmpfiles.d/nxlx-ndi.conf"                                  # root's pipe folder and pipe (M3, 2026-10-11)
# The NDI discovery server is opt-in per box too (D85): these exist only where root ran "nxlx-ndi-runtime server install"
# (mctl/ndiscovery.py). The panel's second drop-in is the mark of that; this installer never writes it.
NDISC_UNIT="$ROOT/etc/systemd/system/nxlx-ndi-discovery.service"
NDISC_MARK="$ROOT/etc/systemd/system/nxlx-web.service.d/50-nxlx-ndisc.conf"
NDISC_WANTS="$ROOT/etc/systemd/system/multi-user.target.wants/nxlx-ndi-discovery.service"
JOURNAL_CONF="$ROOT/etc/systemd/journald.conf.d/50-nxlx-persistent-log.conf"
UPD_USB_UNIT="$ROOT/etc/systemd/system/nxlx-update-usb@.service"
UPD_INBOX_UNIT="$ROOT/etc/systemd/system/nxlx-update-inbox@.service"
WG_LOAD="$ROOT/etc/modules-load.d/nxlx-wireguard.conf"
USB_RULE="$ROOT/etc/udev/rules.d/99-nxlx-usb.rules"
TMPFILES="$ROOT/etc/tmpfiles.d/nxlx.conf"
BIN_LINKS="$ROOT/usr/local/bin"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC/mctl/__init__.py")"
[ -n "$VERSION" ] || die "cannot read version from mctl/__init__.py"
RELEASE="$ROOT$PREFIX/releases/$VERSION"

uninstall() {
	log "uninstalling"
	if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && [ -d /run/systemd/system ]; then
		systemctl disable --now nxlx-player.service 2>/dev/null || true
	fi
	if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && [ -d /run/systemd/system ]; then
		systemctl disable --now nxlx-ndi.service 2>/dev/null || true
		systemctl disable --now nxlx-ndi-discovery.service 2>/dev/null || true
		systemctl disable --now nxlx-sound.service 2>/dev/null || true
		systemctl disable --now nxlx-link.service 2>/dev/null || true
		systemctl disable --now nxlx-raster.socket 2>/dev/null || true
	fi
	# The discovery server's set-up, where there is one: its mark first (it lies in the same folder as the NDI mark).
	run rm -f "$NDISC_MARK" "$NDISC_UNIT" "$NDISC_WANTS" || log "WARNING: could not remove everything of the discovery server's set-up; look at $NDISC_MARK and $NDISC_UNIT and remove them by hand"
	# A box that was set up for NDI: the mark first, then the rest; the two folders only if nothing else is in them.
	run rm -f "$NDI_MARK" "$NDI_PLAYER_DROPIN" "$NDI_TMPFILES" "$NDI_UNIT" "$NDI_WANTS" "$BIN_LINKS/nxlx-ndi-runtime" || log "WARNING: could not remove everything of the NDI helper; look at $NDI_MARK, $NDI_PLAYER_DROPIN and $NDI_UNIT and remove them by hand"
	if [ "$DRY" = 0 ]; then rmdir "$(dirname "$NDI_MARK")" "$(dirname "$NDI_PLAYER_DROPIN")" 2>/dev/null || true; fi
	run rm -f "$SUP_UNIT" "$SOUND_UNIT" "$LINK_UNIT" "$RASTER_UNIT" "$RASTER_SOCKET_UNIT" "$WG_LOAD" "$UPD_USB_UNIT" "$UPD_INBOX_UNIT" "$JOURNAL_CONF" "$TMPFILES"
	run rm -f "$UNIT" "$WEB_UNIT" "$NET_UNIT" "$SYS_UNIT" "$USB_UNIT" "$USB_RULE" "$BIN_LINKS/nxlx-player" "$BIN_LINKS/nxlx-selftest" "$BIN_LINKS/nxlx-usb" "$BIN_LINKS/nxlx-rootfs" "$BIN_LINKS/nxlx-pin" "$BIN_LINKS/nxlx-update" "$BIN_LINKS/nxlx-convert"
	run rm -rf "${ROOT}${PREFIX:?}"
	[ "$PURGE" = 1 ] && run rm -rf "$ETC"
	# The owner's copy of NDI's library is theirs: it goes only with --purge.
	if [ "$PURGE" = 1 ]; then run rm -rf "${ROOT}/opt/nxlx-ndi"; fi
	# So is their copy of NDI's discovery server program (D85).
	if [ "$PURGE" = 1 ]; then run rm -rf "${ROOT}/opt/nxlx-ndi-server"; fi
	if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && [ -d /run/systemd/system ]; then systemctl daemon-reload; fi
	if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && command -v udevadm >/dev/null; then udevadm control --reload || true; fi
	log "done. Kept ${ETC}, media and /opt/nxlx-ndi (your copy of the NDI runtime, if this box was set up for NDI) unless --purge; the users (nxlx-web, nxlx-sound, nxlx-link, nxlx-raster, the player's, and nxlx-ndi on a box that was set up for NDI) and the groups nxlx-svc and nxlx-ndi are left in place, the groups nxlx-link and nxlx-raster too (remove them with deluser and delgroup if you want them gone)."
}

if [ "$UNINSTALL" = 1 ]; then
	if [ -e "$ROOT/opt/pvj" ] || [ -e "$ROOT/etc/pvj" ]; then
		die "this node still has the old names (/opt/pvj): run the installer once without --uninstall (it converts the node), then --uninstall"
	fi
	uninstall; exit 0
fi

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

# Optional: the box's own renderer (render/, D95) is built here from its source, so it needs a compiler and the
# headers of libmpv, EGL, GLES, GBM and libdrm. Missing ones are installed when the network allows; without them
# the renderer is not built and the panel says so. Classic mpv, the default, needs none of this.
render_need=()
for p in gcc make pkg-config libc6-dev libmpv-dev libegl-dev libgles-dev libgbm-dev libdrm-dev; do
	dpkg-query -W -f '${Status}' "$p" 2>/dev/null | grep -q 'install ok installed' || render_need+=("$p")
done
if [ ${#render_need[@]} -gt 0 ] && command -v dpkg-query >/dev/null; then
	if [ "$OFFLINE" = 1 ] || [ "$REAL" = 0 ] || ! command -v apt-get >/dev/null; then
		log "mctl-render will not be built until these are installed: ${render_need[*]}"
	else
		log "installing ${render_need[*]} (to build mctl-render)"
		run apt-get install -y --no-install-recommends "${render_need[@]}" || log "could not install ${render_need[*]}; mctl-render stays unavailable"
	fi
fi

# Optional: Ableton Link (D117). The helper nxlx-link is built here from its source against Debian's ableton-link-dev
# (header only, it brings libasio-dev), and needs a C++ compiler. Missing ones are installed when the network allows;
# without them nxlx-link is not built, the panel says Link is not available, and nothing else changes.
link_need=()
for p in g++ make ableton-link-dev libasio-dev; do
	dpkg-query -W -f '${Status}' "$p" 2>/dev/null | grep -q 'install ok installed' || link_need+=("$p")
done
if [ ${#link_need[@]} -gt 0 ] && command -v dpkg-query >/dev/null; then
	if [ "$OFFLINE" = 1 ] || [ "$REAL" = 0 ] || ! command -v apt-get >/dev/null; then
		log "Ableton Link will be unavailable until these are installed: ${link_need[*]}"
	else
		log "installing ${link_need[*]} (for Ableton Link)"
		run apt-get install -y --no-install-recommends "${link_need[@]}" || log "could not install ${link_need[*]}; Ableton Link stays unavailable"
	fi
fi

# The picture helper (D129): uploaded logos are decoded, and text and logos are drawn, by nxlx-raster, built here from its source
# against libpng, libjpeg and FreeType (and the vendored SVG reader). It is on every board, lean or full. Missing build tools are installed when the
# network allows; without them it is not built, the panel says picture upload is not available, and nothing else changes.
raster_need=()
for p in gcc make pkg-config libc6-dev libpng-dev libjpeg-dev libfreetype-dev; do
	dpkg-query -W -f '${Status}' "$p" 2>/dev/null | grep -q 'install ok installed' || raster_need+=("$p")
done
if [ ${#raster_need[@]} -gt 0 ] && command -v dpkg-query >/dev/null; then
	if [ "$OFFLINE" = 1 ] || [ "$REAL" = 0 ] || ! command -v apt-get >/dev/null; then
		log "uploading logos will be unavailable until these are installed: ${raster_need[*]}"
	else
		log "installing ${raster_need[*]} (to build nxlx-raster)"
		run apt-get install -y --no-install-recommends "${raster_need[@]}" || log "could not install ${raster_need[*]}; uploading logos stays unavailable"
	fi
fi

# Optional: the node's clip stick (D143) is checked with fsck.exfat, from exfatprogs, before it is mounted writable
# (Raspberry Pi OS has it; Debian's netinst does not). Without it the clip stick is mounted read-only and the panel says so.
if ! command -v fsck.exfat >/dev/null && [ ! -x /usr/sbin/fsck.exfat ] && [ ! -x /sbin/fsck.exfat ]; then
	if [ "$OFFLINE" = 1 ] || [ "$REAL" = 0 ] || ! command -v apt-get >/dev/null; then
		log "the clip stick will be mounted read-only until exfatprogs is installed"
	else
		log "installing exfatprogs (to check the clip stick)"
		run apt-get install -y --no-install-recommends exfatprogs || log "could not install exfatprogs; the clip stick will be mounted read-only"
	fi
fi

# Optional: sound reactivity (D96) reads a capture device with arecord, from alsa-utils (in Raspberry Pi OS already).
# Without it the sound service idles and the panel says arecord is missing.
if ! command -v arecord >/dev/null && [ ! -x /usr/bin/arecord ]; then
	if [ "$OFFLINE" = 1 ] || [ "$REAL" = 0 ] || ! command -v apt-get >/dev/null; then
		log "sound reactivity will be unavailable until alsa-utils is installed"
	else
		log "installing alsa-utils (for sound reactivity)"
		run apt-get install -y --no-install-recommends alsa-utils || log "could not install alsa-utils; sound reactivity stays unavailable"
	fi
fi

# Optional: hardware video decoding on a PC node (J-152). mpv decodes through VA-API there, which needs the driver for the
# graphics: without it mpv falls back to software decoding without a word (an Intel M710q dropped frames on 4K60 HEVC
# until intel-media-va-driver was in, and dropped none with it). The graphics is read from sysfs (PCI class 0x03, display):
# Intel (vendor 8086) gets intel-media-va-driver, AMD (1002) gets mesa-va-drivers, and vainfo comes with either, so a
# person can see what the driver decodes. Best effort: a failed install is said and the install goes on, in software.
# A Pi decodes its own way and gets none of this. In stage mode (tests) NXLX_INSTALL_ARCH and NXLX_INSTALL_PCI stand in
# for the machine and /sys/bus/pci/devices; a real install reads the real ones.
va_arch="$(uname -m)"
va_pci=/sys/bus/pci/devices
if [ "$REAL" = 0 ]; then va_arch="${NXLX_INSTALL_ARCH:-$va_arch}"; va_pci="${NXLX_INSTALL_PCI:-$va_pci}"; fi
if [ "$va_arch" = x86_64 ]; then
	va_pkgs=()
	va_names=()
	for dev in "$va_pci"/*; do
		if [ ! -r "$dev/class" ] || [ ! -r "$dev/vendor" ]; then continue; fi
		case "$(cat "$dev/class" 2>/dev/null)" in 0x03*) ;; *) continue ;; esac
		case "$(cat "$dev/vendor" 2>/dev/null)" in
		0x8086) p=intel-media-va-driver; n=Intel ;;
		0x1002) p=mesa-va-drivers; n=AMD ;;
		*) continue ;;
		esac
		case " ${va_pkgs[*]-} " in *" $p "*) ;; *) va_pkgs+=("$p"); va_names+=("$n") ;; esac
	done
	if [ ${#va_pkgs[@]} -gt 0 ]; then
		log "graphics: ${va_names[*]}; hardware video decoding needs ${va_pkgs[*]}"
		va_need=()
		for p in "${va_pkgs[@]}" vainfo; do
			if ! command -v dpkg-query >/dev/null || ! dpkg-query -W -f '${Status}' "$p" 2>/dev/null | grep -q 'install ok installed'; then va_need+=("$p"); fi
		done
		if [ ${#va_need[@]} -eq 0 ]; then
			log "the VA-API driver is installed (${va_pkgs[*]})"
		elif [ "$OFFLINE" = 1 ] || [ "$REAL" = 0 ] || ! command -v apt-get >/dev/null; then
			log "hardware video decoding will be unavailable (mpv decodes in software) until these are installed: ${va_need[*]}"
		else
			log "installing ${va_need[*]} (for hardware video decoding)"
			run apt-get install -y --no-install-recommends "${va_need[@]}" || log "could not install ${va_need[*]}; the install goes on, and mpv decodes in software until they are installed"
		fi
	else
		log "graphics: no Intel or AMD graphics found; no VA-API driver is installed (mpv decodes in software unless the graphics has its own way)"
	fi
fi

# Time sync (J-154). A plain Debian PC install has no time sync at all: a PC node ran 5 hours slow ("NTP service: n/a"
# in timedatectl), and a node with the wrong time gets certificates, logs and the clock wrong. When none of
# systemd-timesyncd, chrony or ntpd (ntp, ntpsec, openntpd) is there, systemd-timesyncd is installed and turned on. Best
# effort: a failed install is said and the install goes on. A system that already has one (Raspberry Pi OS ships
# systemd-timesyncd) is left as it is. The programs are looked for under the install root, so in stage mode (tests and
# image builds) it is the staged system that is looked at; stage mode, --offline and a system without apt install
# nothing and only say it.
ts_have=""
for f in usr/lib/systemd/systemd-timesyncd lib/systemd/systemd-timesyncd usr/sbin/chronyd sbin/chronyd usr/sbin/ntpd sbin/ntpd; do
	if [ -e "$ROOT/$f" ]; then ts_have="$f"; break; fi
done
if [ -n "$ts_have" ]; then
	log "time sync: ${ts_have##*/} is installed"
elif [ "$OFFLINE" = 1 ] || [ "$REAL" = 0 ] || ! command -v apt-get >/dev/null; then
	log "time sync: none installed; the clock is not kept right until systemd-timesyncd (or chrony) is installed"
else
	log "time sync: none installed; installing systemd-timesyncd"
	if run apt-get install -y --no-install-recommends systemd-timesyncd; then
		if [ -d /run/systemd/system ]; then
			run systemctl enable --now systemd-timesyncd.service || log "could not start systemd-timesyncd; the install goes on"
			run timedatectl set-ntp true || log "could not turn on time sync with timedatectl; the install goes on"
		fi
	else
		log "could not install systemd-timesyncd; the install goes on, and the clock is not kept right until it is installed"
	fi
fi

# Nothing is installed for the NDI input here, not avahi either: that is opt-in per box (mctl/ndisetup.py, D62).

# --- the release folder -------------------------------------------------
# put_release DIR: this release as DIR/<version>, built next to it as DIR/<version>.new and swapped in, so a folder
# that "current" points at is never deleted first (the same version installed again). DIR is $PREFIX/releases, or the
# old releases folder of a node that is converted from the old names (below). "current" is not touched here.
put_release() {
	local RELEASE="$1/$VERSION"
	run mkdir -p "$1"
	run rm -rf "$RELEASE.new"
	run mkdir -p "$RELEASE.new"
	if [ "$DRY" = 0 ]; then
		cp -a "$SRC/mctl" "$SRC/bin" "$RELEASE.new/"
		[ -d "$SRC/render" ] && cp -a "$SRC/render" "$RELEASE.new/"
		find "$RELEASE.new" -name __pycache__ -type d -prune -exec rm -rf {} +
		# The renderer (D95), built from source here and never shipped as a binary. A failed build leaves the box as it
		# was: classic mpv draws and the panel says mctl-render is not built. Not in stage mode (tests, the image's own
		# copy is installed for real in the image's chroot).
		if [ "$REAL" = 1 ] && [ -f "$RELEASE.new/render/Makefile" ]; then
			# pvj-render is the renderer's name before the rename (rename PR 5): a source folder where it was built by hand
			# must not carry it into a release, where nothing runs it any more.
			rm -f "$RELEASE.new/render/mctl-render" "$RELEASE.new/render/pvj-render" "$RELEASE.new"/render/src/*.o
			if command -v make >/dev/null && command -v pkg-config >/dev/null && pkg-config --exists mpv egl glesv2 gbm libdrm; then
				log "building mctl-render (a minute or two on a Pi 4)"
				if timeout 1200 make -C "$RELEASE.new/render" > "$RELEASE.new/render/build.log" 2>&1; then
					log "mctl-render built"
				else
					log "mctl-render was not built (render/build.log in the release); classic mpv stays the only renderer"
				fi
				rm -f "$RELEASE.new"/render/src/*.o
			else
				log "mctl-render was not built: the build tools or headers are missing"
			fi
		fi
		# The Link follower (D117), built here in the same way. It never stops an install: without it the panel says Link is
		# not available, and its unit's condition leaves the service alone. Niced, because a box may be playing.
		if [ "$REAL" = 1 ] && [ -f "$RELEASE.new/render/Makefile" ]; then
			rm -f "$RELEASE.new/render/nxlx-link" "$RELEASE.new/render/nxlx-link-peer"
			if command -v g++ >/dev/null && command -v make >/dev/null && [ -f /usr/include/ableton/Link.hpp ] && [ -f /usr/include/asio.hpp ]; then
				log "building nxlx-link (a minute or two on a Pi 4)"
				if nice -n 10 timeout 1200 make -C "$RELEASE.new/render" nxlx-link > "$RELEASE.new/render/link-build.log" 2>&1; then
					log "nxlx-link built"
				else
					log "nxlx-link was not built (render/link-build.log in the release); Ableton Link stays unavailable"
				fi
			else
				log "nxlx-link was not built: g++ or the headers of ableton-link-dev are missing; Ableton Link stays unavailable"
			fi
		fi
		# The picture helper (D129), built the same way and never a reason to stop an install: without it the panel says
		# uploading logos is not available, and the unit's condition leaves the socket alone.
		if [ "$REAL" = 1 ] && [ -f "$RELEASE.new/render/Makefile" ]; then
			rm -f "$RELEASE.new/render/nxlx-raster"
			if command -v make >/dev/null && command -v pkg-config >/dev/null && pkg-config --exists libpng libjpeg freetype2; then
				log "building nxlx-raster"
				if nice -n 10 timeout 600 make -C "$RELEASE.new/render" nxlx-raster > "$RELEASE.new/render/raster-build.log" 2>&1; then
					log "nxlx-raster built"
				else
					log "nxlx-raster was not built (render/raster-build.log in the release); uploading logos stays unavailable"
				fi
			else
				log "nxlx-raster was not built: the build tools or the libpng, libjpeg and FreeType headers are missing; uploading logos and drawing text stay unavailable"
			fi
		fi
	fi
	if [ -e "$RELEASE" ]; then run mv "$RELEASE" "$RELEASE.old.$$"; fi
	run mv "$RELEASE.new" "$RELEASE"
	run rm -rf "$RELEASE.old.$$"
}

# --- a node that still has the old names (rename PR 7, D136) -------------
# A node installed before the rename has /opt/pvj, /etc/pvj and /var/lib/pvj, the pvj-* accounts and units and the
# shared group pvj. This release runs under the new names only, so such a node is converted first, by
# bin/nxlx-convert of this release: the release is put into the old releases folder, and the converter renames the
# accounts (user and group ids stay, so no file changes its owner), the folders, the units and the other system
# files (a sudoers file is checked with visudo -cf before it replaces anything), points "current" at this release as
# one of its own changes and starts the services under their new names. The rest of this script then runs as on
# any node with the new names. The converter refuses, changing nothing, when it cannot convert safely (a show is
# playing, an update is running, the root file system is read-only, an account to be renamed is logged in: run this
# from another admin login); this script then stops too. The converter's undo is armed: unless
# `sudo nxlx-convert --confirm` is run within 15 minutes, the old names and the release the node ran before come back
# by themselves (also after a power cut). Updates from the panel cannot do this: the updater of a release from before
# the rename refuses this release's bundle (it has no bin/pvj-player), so the first install of it is this script.
OLD_LAYOUT=0
for old in opt/pvj etc/pvj var/lib/pvj; do
	if [ -e "$ROOT/$old" ] || [ -L "$ROOT/$old" ]; then OLD_LAYOUT=1; fi
done
# The converter's journal says whether a conversion is under way: "pending" when a run stopped before it finished
# (the old folders may be renamed already, so the test above no longer sees it), "unconfirmed" when it finished and
# its undo is still armed. In both cases nothing here may move "current" or write "previous": only the converter
# does, in its journal, so that its undo can put them back.
CONV_STATE=none
if [ -e "$ROOT/var/backups/nxlx-convert/journal.jsonl" ]; then
	conv_root=()
	if [ -n "$STAGE" ]; then conv_root=(--root "$ROOT"); fi
	CONV_STATE="$(python3 -B "$SRC/bin/nxlx-convert" "${conv_root[@]+"${conv_root[@]}"}" --state)" || die "cannot read the converter's journal: run sudo nxlx-convert --state, which says which line is unreadable and what to do"
fi
# A node that took the system rename (rename PR 7) and not the variable and signing names (rename PR 8) has the new
# folders, accounts and units, with PVJ_* names in nxlx.env, its units and drop-ins and pvj-release in allowed_signers.
# This release reads none of those, and no node is meant to be in that state (the nodes go from the old names to this
# release in one conversion), so it is refused here, before anything on the node changes. Only these leftovers count
# (not an old name in a comment, a file of the owner's, or anything else the converter would rewrite): the converter
# is asked (`--stale-names`: exit 0 none, 4 some, one "file: name" line each), so the two cannot disagree. The reason and
# the first file come first, as the panel shows only the end of what an installer says.
if [ "$OLD_LAYOUT" = 0 ] && [ "$CONV_STATE" != pending ] && [ "$CONV_STATE" != unconfirmed ] \
	&& [ ! -e "$ROOT/etc/systemd/system/nxlx-convert-undo.timer" ] && [ -f "$SRC/bin/nxlx-convert" ]; then
	conv_root=()
	if [ -n "$STAGE" ]; then conv_root=(--root "$ROOT"); fi
	stale_rc=0
	stale="$(python3 -B "$SRC/bin/nxlx-convert" "${conv_root[@]+"${conv_root[@]}"}" --stale-names 2>&1)" || stale_rc=$?
	case "$stale_rc" in
	0) ;;
	4)
		stale_first="$(printf '%s\n' "$stale" | head -n 1)"
		stale_more="$(printf '%s\n' "$stale" | sed -n '2,$p' | wc -l | tr -d ' ')"
		if [ "$stale_more" = 0 ]; then stale_more=""; else stale_more=" (and $stale_more more)"; fi
		die "old names in ${stale_first%%: *}$stale_more: this node has the new folders but still the old variable or signing names (PVJ_*, pvj-release), a release between rename steps 7 and 8. This release cannot be installed over it and nothing was changed. There is no supported way on from here; see mctl/README.md, Updates." ;;
	*) die "cannot ask the converter what this node still holds (python3 $SRC/bin/nxlx-convert --stale-names, exit $stale_rc): $(printf '%s' "$stale" | tail -n 2)" ;;
	esac
fi
CONVERTED=0          # this run converted the node, or carried a conversion on: the converter set current and previous
KEEP_RELEASE=0       # a conversion waits for its confirm: the release in place is left as it is
if [ "$OLD_LAYOUT" = 1 ] || [ "$CONV_STATE" = pending ]; then
	[ "$PREFIX" = /opt/nxlx ] || die "this node has the old names (/opt/pvj) or a conversion to /opt/nxlx that was not finished: run the installer without --prefix, which converts the node or carries the conversion on"
	# The accounts the converter renames; any other old-style name given with --user is not one of them.
	case "$NXLX_USER" in
	"" | pvj-player | pvj-dev | pvj-web | pvj-sound | pvj-link | pvj-raster | pvj-ndi | pvj-ndisc) ;;
	pvj-*) die "--user $NXLX_USER: the conversion renames only the node's own accounts (pvj-player, pvj-dev and the services'); give the account's name as it will be after the conversion, or leave --user out" ;;
	esac
	old_top=/opt/pvj
	[ -d "$ROOT/opt/pvj" ] || old_top=/opt/nxlx      # a conversion that stopped half-way, carried on below
	[ -L "$ROOT$old_top/current" ] || die "this node has the old names but $old_top/current is not a link to a release; look at it by hand: sudo python3 $SRC/bin/nxlx-convert --plan"
	old_target="$(readlink "$ROOT$old_top/current")"
	if [ "${old_target##*/}" = "$VERSION" ]; then
		[ "$old_top" = /opt/nxlx ] || die "this node runs version $VERSION under the old names, so its release cannot be replaced in place; install a newer version"
		log "carrying on with a conversion that stopped half-way (the release in place is kept)"
	else
		if [ "$CONV_STATE" = pending ]; then log "carrying on with a conversion that stopped half-way"; fi
		log "putting version $VERSION into $old_top/releases, then converting the node"
		put_release "$ROOT$old_top/releases"
		if [ "$REAL" = 1 ] && [ "$DRY" = 0 ]; then chown -R root:root "$ROOT$old_top/releases/$VERSION"; fi
		run chmod -R go-w "$ROOT$old_top/releases/$VERSION"
	fi
	if [ "$DRY" = 1 ]; then
		log "would convert the node: python3 $old_top/releases/$VERSION/bin/nxlx-convert --run --release $VERSION; what follows here is for after the conversion"
		KEEP_RELEASE=1
	else
		conv=(python3 -B "$ROOT$old_top/releases/$VERSION/bin/nxlx-convert")
		if [ -n "$STAGE" ]; then conv+=(--root "$ROOT"); fi
		rc=0
		"${conv[@]}" --run --release "$VERSION" || rc=$?
		case "$rc" in
		0) CONVERTED=1 ;;
		2) die "the conversion was refused and nothing on the node was changed by it (the reasons are above); version $VERSION waits in $old_top/releases. Put the cause right and run this installer again" ;;
		*) die "the conversion stopped half-way (see above). Run this installer again to carry on, or go back with: sudo python3 /var/backups/nxlx-convert/nxlx-convert --undo (it also runs by itself 15 minutes after the start)" ;;
		esac
	fi
elif [ "$CONV_STATE" = unconfirmed ] || [ -e "$ROOT/etc/systemd/system/nxlx-convert-undo.timer" ]; then
	# Waiting for its confirm: by the journal, or by the undo timer that is still armed (whatever the journal says).
	cur_target="$(readlink "$ROOT$PREFIX/current" 2>/dev/null || true)"
	[ "${cur_target##*/}" = "$VERSION" ] || die "a conversion of this node waits for its confirm: sudo nxlx-convert --confirm (or --undo), then run the installer again"
	log "a conversion of this node waits for its confirm: the release in place is kept, the rest is installed again"
	KEEP_RELEASE=1
fi

# --- accounts -----------------------------------------------------------
if [ -z "$NXLX_USER" ] && [ -f "$ETC/install.json" ]; then
	NXLX_USER="$(sed -n 's/.*"user": "\([a-z_][a-z0-9_-]*\)".*/\1/p' "$ETC/install.json" | head -n 1)"
fi
if [ -z "$NXLX_USER" ]; then
	if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then NXLX_USER="$SUDO_USER"; else NXLX_USER=nxlx-player; fi
fi
# From here on the node has the new names (converted by this run, or before), so the accounts the converter renames
# are known only by their new names: an old one given again (--user pvj-player, on every later install) is the same
# account, never a second one made beside it. Any other old-style name is refused, as before a conversion.
case "$NXLX_USER" in
pvj-player | pvj-dev | pvj-web | pvj-sound | pvj-link | pvj-raster | pvj-ndi | pvj-ndisc)
	if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && id "$NXLX_USER" >/dev/null 2>&1 && ! id "nxlx-${NXLX_USER#pvj-}" >/dev/null 2>&1; then
		die "--user $NXLX_USER: this node has that account but not nxlx-${NXLX_USER#pvj-}, and no old layout the conversion would rename it with; look at it by hand"
	fi
	log "--user $NXLX_USER: since the conversion this account is called nxlx-${NXLX_USER#pvj-}"
	NXLX_USER="nxlx-${NXLX_USER#pvj-}" ;;
pvj-*) die "--user $NXLX_USER: an old-style name that is not one of the node's own accounts; give the account's new name, or leave --user out" ;;
esac

if [ "$REAL" = 1 ]; then
	getent group nxlx-svc >/dev/null || run groupadd --system nxlx-svc
	if ! id "$NXLX_USER" >/dev/null 2>&1; then
		log "creating system user $NXLX_USER"
		run useradd --system --create-home --home-dir /var/lib/nxlx-player --shell /usr/sbin/nologin --gid nxlx-svc "$NXLX_USER"
	fi
	# Older installs made /var/lib/nxlx the player's home, which let the player account replace settings.json.
	# Give it its own home, changing only the home entry: nothing is moved out of /var/lib/nxlx.
	if [ "$(getent passwd "$NXLX_USER" | cut -d: -f6)" = /var/lib/nxlx ]; then
		log "moving the home of $NXLX_USER from /var/lib/nxlx to /var/lib/nxlx-player (no files are moved)"
		run mkdir -p /var/lib/nxlx-player
		run chown "$NXLX_USER":nxlx-svc /var/lib/nxlx-player
		run chmod 0750 /var/lib/nxlx-player
		# usermod refuses while the account has a running process (the player; found on a real Pi 4), and this
		# script stops at the first error. The player is started again at the end.
		if [ "$DRY" = 0 ] && [ -d /run/systemd/system ]; then systemctl stop nxlx-player.service 2>/dev/null || true; fi
		run usermod -d /var/lib/nxlx-player "$NXLX_USER"
	fi
	# A display account that an installer from before the rename made (--user with a new name) has its home at
	# /var/lib/pvj-player, which the conversion moved to /var/lib/nxlx-player with everything in it. The converter
	# moves the homes of the pvj-* accounts it renames; this one keeps its name, so only its home entry is changed.
	if [ "$(getent passwd "$NXLX_USER" | cut -d: -f6)" = /var/lib/pvj-player ] && [ -d /var/lib/nxlx-player ] && [ ! -e /var/lib/pvj-player ]; then
		log "the home of $NXLX_USER moved with the conversion: /var/lib/pvj-player is now /var/lib/nxlx-player (no files are moved)"
		if [ "$DRY" = 0 ] && [ -d /run/systemd/system ]; then systemctl stop nxlx-player.service 2>/dev/null || true; fi
		run usermod -d /var/lib/nxlx-player "$NXLX_USER"
	fi
	for g in nxlx-svc video render audio input; do
		getent group "$g" >/dev/null && run usermod -aG "$g" "$NXLX_USER"
	done
	if [ -n "$WEB_USER" ]; then run usermod -aG nxlx-svc "$WEB_USER"; fi
	# The web panel runs as its own account with no screen or sound access.
	if ! id nxlx-web >/dev/null 2>&1; then
		log "creating system user nxlx-web"
		run useradd --system --no-create-home --shell /usr/sbin/nologin --gid nxlx-svc nxlx-web
	fi
	# The sound analysis (D96) runs as its own account whose only group is audio: never nxlx-svc (the player's socket,
	# the settings, the PIN). Whoever is in audio can already open the microphone.
	getent group audio >/dev/null || run groupadd --system audio
	if ! id nxlx-sound >/dev/null 2>&1; then
		log "creating system user nxlx-sound"
		run useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin --gid audio nxlx-sound
	fi
	# The Link follower (D117) meets the network, so it runs as its own account in its own group, with nothing else of
	# the box's. The panel joins that group to reach its folder, and nobody else does; the panel's next start takes it.
	getent group nxlx-link >/dev/null || run groupadd --system nxlx-link
	if ! id nxlx-link >/dev/null 2>&1; then
		log "creating system user nxlx-link"
		run useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin --gid nxlx-link nxlx-link
	fi
	run usermod -aG nxlx-link nxlx-web
	# The picture helper (D129) decodes files from phones and laptops, so it too runs as its own account in its own group,
	# with nothing else of the box's. The panel joins that group to reach its socket, and nobody else does.
	getent group nxlx-raster >/dev/null || run groupadd --system nxlx-raster
	if ! id nxlx-raster >/dev/null 2>&1; then
		log "creating system user nxlx-raster"
		run useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin --gid nxlx-raster nxlx-raster
	fi
	run usermod -aG nxlx-raster nxlx-web
fi

# --- program files: releases/<version>, "current" points at the active one --
log "installing version $VERSION to $PREFIX"
# After a conversion, or while one waits for its confirm, "current" and "previous" are the converter's (journaled,
# so that its undo puts them back): nothing is switched or written here.
if [ "$CONVERTED" = 0 ] && [ "$KEEP_RELEASE" = 0 ]; then
	previous=""
	[ -L "$ROOT$PREFIX/current" ] && previous="$(readlink "$ROOT$PREFIX/current")"
	put_release "$ROOT$PREFIX/releases"
	# Atomic switch: rename a fresh symlink over "current".
	run ln -sfn "$PREFIX/releases/$VERSION" "$ROOT$PREFIX/current.tmp"
	run mv -T "$ROOT$PREFIX/current.tmp" "$ROOT$PREFIX/current"
	if [ -n "$previous" ] && [ "$previous" != "$PREFIX/releases/$VERSION" ]; then
		if [ "$DRY" = 0 ]; then printf '%s\n' "$previous" > "$ROOT$PREFIX/previous"; fi
	fi
fi
if [ "$REAL" = 1 ] && [ "$DRY" = 0 ]; then chown -R root:root "$ROOT$PREFIX"; fi
run chmod -R go-w "$ROOT$PREFIX"

run mkdir -p "$BIN_LINKS"
run ln -sfn "$PREFIX/current/bin/nxlx-player" "$BIN_LINKS/nxlx-player"
run ln -sfn "$PREFIX/current/bin/nxlx-selftest" "$BIN_LINKS/nxlx-selftest"
run ln -sfn "$PREFIX/current/bin/nxlx-usb" "$BIN_LINKS/nxlx-usb"
run ln -sfn "$PREFIX/current/bin/nxlx-rootfs" "$BIN_LINKS/nxlx-rootfs"
run ln -sfn "$PREFIX/current/bin/nxlx-pin" "$BIN_LINKS/nxlx-pin"
run ln -sfn "$PREFIX/current/bin/nxlx-update" "$BIN_LINKS/nxlx-update"
run ln -sfn "$PREFIX/current/bin/nxlx-ndi-runtime" "$BIN_LINKS/nxlx-ndi-runtime"
run ln -sfn "$PREFIX/current/bin/nxlx-convert" "$BIN_LINKS/nxlx-convert"      # sudo nxlx-convert --confirm (or --undo) after a conversion

# --- settings and media (never overwritten if they exist) -----------------
run mkdir -p "$ETC"
if [ ! -e "$ETC/nxlx.env" ]; then
	if [ "$DRY" = 0 ]; then
		cat > "$ETC/nxlx.env" <<ENV
# Settings for the nxlx-player service. Edit, then: sudo systemctl restart nxlx-player
NXLX_MEDIA_DIR=$MEDIA
NXLX_USB_DIR=/media/usb
# USB drives are mounted read-only under /media/nxlx/<label>. Set to 1 to allow writing.
NXLX_USB_RW=0
# Web panel: port and address. The panel is for a private network; do not expose it to the internet.
# NXLX_PORT=80
# NXLX_BIND=0.0.0.0
# Largest single upload from the panel, in MB.
# NXLX_MAX_UPLOAD_MB=8192
ENV
	fi
else
	log "keeping existing $ETC/nxlx.env"
fi
# The signing service and the expected root (D139): set or replaced in nxlx.env only when given; both values were
# checked above, so they hold no character sed or the env file would read specially.
set_env() {
	if [ "$DRY" = 1 ]; then printf '  would set %s=%s in %s\n' "$1" "$2" "$ETC/nxlx.env"; return; fi
	if grep -q "^$1=" "$ETC/nxlx.env" 2>/dev/null; then
		sed -i "s|^$1=.*|$1=$2|" "$ETC/nxlx.env"
	else
		printf '%s=%s\n' "$1" "$2" >> "$ETC/nxlx.env"
	fi
	log "set $1 in $ETC/nxlx.env"
}
if [ -n "$SIGNER" ]; then set_env NXLX_SIGNER_URL "${SIGNER%/}"; fi
if [ -n "$ROOT_FP" ]; then set_env NXLX_ROOT_FINGERPRINT "$ROOT_FP"; fi
# Public keys whose signatures nxlx-update accepts (one line per key, see install/README.md).
if [ ! -e "$ETC/allowed_signers" ] && [ "$DRY" = 0 ]; then
	cat > "$ETC/allowed_signers" <<KEYS
# Add your release signing key here. Format:
#   nxlx-release namespaces="nxlx-release" ssh-ed25519 AAAA... comment
# Until a key is listed, nxlx-update refuses every bundle.
KEYS
	chmod 644 "$ETC/allowed_signers"
fi
run mkdir -p "$ROOT$MEDIA"
if [ "$REAL" = 1 ] && [ "$DRY" = 0 ]; then
	chown "$NXLX_USER":nxlx-svc "$MEDIA"; chmod 2775 "$MEDIA"
	# settings.json lives in /var/lib/nxlx and only the web panel may write there. The player (group nxlx-svc) may pass
	# through to the media folder and read, but not create, rename or replace files: 2750, owned by nxlx-web.
	mkdir -p /var/lib/nxlx; chown nxlx-web:nxlx-svc /var/lib/nxlx; chmod 2750 /var/lib/nxlx
	[ -e /var/lib/nxlx/settings.json ] && chown nxlx-web:nxlx-svc /var/lib/nxlx/settings.json* 2>/dev/null
	# The settings copies an update keeps (J-055): root's, so the path exists when nxlx-sysd starts (its unit can write only a
	# path that is there then) and a factory reset can empty it. Already there from an update: left as it is, owner and mode set.
	mkdir -p /var/lib/nxlx/backups; chown root:root /var/lib/nxlx/backups; chmod 0700 /var/lib/nxlx/backups
	true
fi
if [ "$DRY" = 0 ]; then
	printf '{"version": "%s", "prefix": "%s", "user": "%s", "installed": "%s"}\n' \
		"$VERSION" "$PREFIX" "$NXLX_USER" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$ETC/install.json"
fi

# --- service ------------------------------------------------------------
run mkdir -p "$(dirname "$UNIT")"
if [ "$DRY" = 0 ]; then
	sed -e "s|@NXLX_USER@|$NXLX_USER|g" -e "s|@NXLX_DIR@|$PREFIX/current|g" "$SRC/install/nxlx-player.service" > "$UNIT"
	sed -e "s|@NXLX_DIR@|$PREFIX/current|g" "$SRC/install/nxlx-web.service" > "$WEB_UNIT"
	sed -e "s|@NXLX_DIR@|$PREFIX/current|g" "$SRC/install/nxlx-netd.service" > "$NET_UNIT"
	sed -e "s|@NXLX_DIR@|$PREFIX/current|g" "$SRC/install/nxlx-sysd.service" > "$SYS_UNIT"
	sed -e "s|@NXLX_DIR@|$PREFIX/current|g" "$SRC/install/nxlx-supportd.service" > "$SUP_UNIT"
	sed -e "s|@NXLX_DIR@|$PREFIX/current|g" "$SRC/install/nxlx-sound.service" > "$SOUND_UNIT"
	sed -e "s|@NXLX_DIR@|$PREFIX/current|g" "$SRC/install/nxlx-link.service" > "$LINK_UNIT"
	sed -e "s|@NXLX_DIR@|$PREFIX/current|g" "$SRC/install/nxlx-raster@.service" > "$RASTER_UNIT"
	sed -e "s|@NXLX_DIR@|$PREFIX/current|g" "$SRC/install/nxlx-raster.socket" > "$RASTER_SOCKET_UNIT"
	# Updates from the panel: started on request by nxlx-sysd, never enabled.
	sed -e "s|@NXLX_DIR@|$PREFIX/current|g" "$SRC/install/nxlx-update-usb@.service" > "$UPD_USB_UNIT"
	sed -e "s|@NXLX_DIR@|$PREFIX/current|g" "$SRC/install/nxlx-update-inbox@.service" > "$UPD_INBOX_UNIT"
	run rm -f "$ROOT/etc/systemd/system/nxlx-update-usb.service" "$ROOT/etc/systemd/system/nxlx-update-inbox.service"  # the names before they took a version
	# The support helper may not load kernel modules itself (its sandbox), so WireGuard's is loaded at boot.
	mkdir -p "$(dirname "$WG_LOAD")"
	echo wireguard > "$WG_LOAD"
	# The system log survives restarts (capped at 64 MB), so an unexpected restart can be explained afterwards.
	mkdir -p "$(dirname "$JOURNAL_CONF")"
	cp "$SRC/install/50-nxlx-persistent-log.conf" "$JOURNAL_CONF"
	# The parent of the services' runtime folders belongs to root, at every boot (D45).
	mkdir -p "$(dirname "$TMPFILES")"
	cp "$SRC/install/nxlx-tmpfiles.conf" "$TMPFILES"
fi

# Runtime folders (D45). Older versions shared one RuntimeDirectory, /run/nxlx, between the player, the panel and the
# root network helper, and systemd gave it to whichever started last; it stays like that until the next boot unless
# it is put right here. Rules for this part, each from the independent review:
#   * root never creates, writes, chowns or chmods a name inside a folder that an unprivileged account owns (a link
#     planted there would send it anywhere); it only takes /run/nxlx itself, in /run, and deletes below it;
#   * the folder counts as done only when a marker says so, and the marker is written last (by tmpfiles, as root, in
#     the folder that is root's by then), so an emptying that was cut short is finished by the next run;
#   * the three services are stopped BEFORE the reload and the emptying, so none can restart by itself in between
#     and have systemd chown a planted name.
# With --stage all of it works on DIR/run/nxlx and leaves owners alone (the tests).
RUN_DIR="$ROOT/run/nxlx"
RUN_MARK="$RUN_DIR/.d45"
RUN_UNITS=(nxlx-player.service nxlx-web.service nxlx-netd.service)
RUN_WAS_ACTIVE=()
RUN_EMPTY=0

# True when /run/nxlx is root's, has the marker, and holds nothing but the services' folders, each with its own owner.
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
		player) want="$NXLX_USER:nxlx-svc 750" ;;
		web) want="nxlx-web:nxlx-svc 750" ;;
		netd) want="root:nxlx-svc 750" ;;
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
		# Creates /run/nxlx and the marker if they are missing (see nxlx-tmpfiles.conf), as at boot.
		systemd-tmpfiles --create "$TMPFILES" || log "systemd-tmpfiles reported a problem; /run/nxlx is checked again at the next install and set up at the next boot"
	elif [ -d "$RUN_DIR" ]; then
		: > "$RUN_MARK"
	fi
}
if [ "$REAL" = 0 ] && [ "$DRY" = 0 ]; then prepare_run_folder; fix_run_folder; fi

# The NDI helper, ONLY on a box that opted in (the mark is there): its unit and the two drop-ins are written again
# by the program just installed, which is their one writer (mctl/ndisetup.py; it also makes sure the account and the
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
	run python3 -B "$RELEASE/bin/nxlx-ndi-runtime" refresh --root "$ROOT" --prefix "$PREFIX" || log "could not write the NDI helper's unit again; it stays as it was. To repair: sudo nxlx-ndi-runtime install <the NDI SDK folder>"
	# The folders those lines name (root's pipe folder and pipe, the folder of NDI's configuration file) are made
	# now, as at boot: a release may have added one, and the helper and the panel are started again further down.
	if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && [ -f "$NDI_TMPFILES" ]; then systemd-tmpfiles --create "$NDI_TMPFILES" || log "systemd-tmpfiles could not make the NDI folders; NDI works again after a restart of the box"; fi
elif [ -e "$NDI_UNIT" ] || [ -L "$NDI_UNIT" ] || [ -e "$NDI_PLAYER_DROPIN" ] || [ -L "$NDI_PLAYER_DROPIN" ] || [ -L "$NDI_WANTS" ] || [ -e "$NDI_TMPFILES" ] || [ -L "$NDI_TMPFILES" ]; then
	# Something of the helper without the mark: a setup that was cut short, or a box that ran this branch while it
	# still put the helper on every box. Nobody opted this box in, so it goes (the account nxlx-ndi, if any, stays).
	log "removing an NDI helper that nobody set up on this box (to set NDI up: sudo nxlx-ndi-runtime install <the NDI SDK folder>)"
	if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && [ -d /run/systemd/system ]; then
		systemctl disable --now nxlx-ndi.service 2>/dev/null || true
	fi
	if ndi_mark; then
		# Looked at once more, right before anything is removed: a setup that finished in this very moment stays.
		NDI_OPTED_IN=1
		log "NDI was set up on this box just now; its helper is left in place"
		if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && [ -d /run/systemd/system ]; then
			systemctl enable --now nxlx-ndi.service || log "could not start the NDI helper again; run: sudo systemctl enable --now nxlx-ndi"
		fi
	else
		# Never the end of an install: a folder in the place of one of these makes rm fail, and that is said.
		run rm -f "$NDI_UNIT" "$NDI_PLAYER_DROPIN" "$NDI_TMPFILES" "$NDI_WANTS" || log "WARNING: could not remove everything of that NDI helper ($NDI_UNIT, $NDI_PLAYER_DROPIN, $NDI_TMPFILES); look at what is there and remove it by hand"
		if [ "$DRY" = 0 ]; then rmdir "$(dirname "$NDI_PLAYER_DROPIN")" 2>/dev/null || true; fi
	fi
fi
# The NDI discovery server (D85), only where root set it up: its unit is written again from the new release's
# template. A box without the mark gets nothing. If this fails the install goes on with the unit as it was.
NDISC_SET_UP=0
if [ -f "$NDISC_MARK" ] && [ ! -L "$NDISC_MARK" ]; then
	NDISC_SET_UP=1
	log "this box has the discovery server set up: writing its unit again"
	run python3 -B "$RELEASE/bin/nxlx-ndi-runtime" server refresh --root "$ROOT" --prefix "$PREFIX" || log "could not write the discovery server's unit again; it stays as it was. To repair: sudo nxlx-ndi-runtime server install <the folder with the program>"
fi
# USB automount: udev starts nxlx-usb@<partition>.service, which mounts by label.
run mkdir -p "$(dirname "$USB_RULE")"
if [ "$DRY" = 0 ]; then
	sed -e "s|@NXLX_DIR@|$PREFIX/current|g" "$SRC/install/nxlx-usb@.service" > "$USB_UNIT"
	cp "$SRC/install/99-nxlx-usb.rules" "$USB_RULE"
fi
if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && command -v udevadm >/dev/null; then udevadm control --reload || true; fi
log "a clip stick (an exFAT partition labelled like N01-CLIPS) becomes this node's writable clip folder with: sudo nxlx-usb claim /dev/sdX2"
if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && [ -d /run/systemd/system ]; then
	prepare_run_folder
	systemctl daemon-reload
	fix_run_folder
	if systemctl restart systemd-journald.service 2>/dev/null; then journalctl --flush 2>/dev/null || true; fi
	systemctl enable nxlx-player.service nxlx-web.service nxlx-sysd.service nxlx-supportd.service
	systemctl enable nxlx-sound.service || log "could not enable the sound service"
	modprobe wireguard 2>/dev/null || log "the WireGuard kernel module is not available: remote support stays unavailable"
	# Right after a conversion the services were just started by it, and a restart that meets a start still being
	# settled (or a service that restarted itself) is cancelled by systemd ("Job for nxlx-web.service canceled", seen once
	# in CI). Say what the services were doing, wait a moment and try once more.
	if [ "$START" = 1 ]; then
		if ! systemctl restart nxlx-player.service nxlx-web.service nxlx-sysd.service nxlx-supportd.service; then
			log "restarting the services failed or was cancelled; what they were doing:"
			journalctl --no-pager -n 25 -u nxlx-player -u nxlx-web -u nxlx-sysd -u nxlx-supportd 2>&1 | sed 's/^/nxlx-install:   /' || true
			sleep 3
			systemctl restart nxlx-player.service nxlx-web.service nxlx-sysd.service nxlx-supportd.service
		fi
	fi
	# The sound service moves to the new program even with --no-start, and is started where it was not running
	# (it is idle until the Owner chooses a device): a box updated from before D96 gets it at once.
	if [ "$START" = 1 ]; then systemctl restart nxlx-sound.service || log "the sound service did not start; see: journalctl -u nxlx-sound -n 30"
	else systemctl try-restart nxlx-sound.service || true; systemctl start nxlx-sound.service 2>/dev/null || true; fi
	# The Link follower the same way. Where it could not be built its condition skips it, which is no failure.
	systemctl enable nxlx-link.service || log "could not enable the Ableton Link service"
	if [ "$START" = 1 ]; then systemctl restart nxlx-link.service || log "the Ableton Link service did not start; see: journalctl -u nxlx-link -n 30"
	else systemctl try-restart nxlx-link.service || true; systemctl start nxlx-link.service 2>/dev/null || true; fi
	# The picture helper's socket, on every board (D129). systemd starts one helper process behind it for each job, so there
	# is nothing to restart: a socket that is listening is moved to the new program by the next job. Where the program
	# could not be built, the socket's condition skips it, which is no failure.
	systemctl enable nxlx-raster.socket || log "could not enable the picture helper's socket"
	if [ "$START" = 1 ]; then systemctl restart nxlx-raster.socket || log "the picture helper's socket did not start; see: journalctl -u nxlx-raster.socket -n 30"
	else systemctl start nxlx-raster.socket 2>/dev/null || true; fi
	# The network helper only makes sense with NetworkManager (Raspberry Pi OS, most desktops).
	if command -v nmcli >/dev/null; then
		systemctl enable nxlx-netd.service
		# Even with --no-start (an update from the panel, which then restarts the player and the panel itself): a
		# helper that is running must move to the socket the new panel looks for. try-restart starts nothing.
		if [ "$START" = 1 ]; then systemctl restart nxlx-netd.service; else systemctl try-restart nxlx-netd.service || true; fi
	else
		log "NetworkManager not found: network settings in the panel stay unavailable"
	fi
	# The NDI helper, only where the box opted in: an opted-in box stays opted in. Like the network helper it moves
	# to the new program even with --no-start; try-restart starts nothing. It is idle until its module is on.
	if [ "$NDI_OPTED_IN" = 1 ] && [ -f "$NDI_UNIT" ]; then
		systemctl enable nxlx-ndi.service || log "could not enable the NDI helper"
		if [ "$START" = 1 ]; then
			systemctl restart nxlx-ndi.service || log "the NDI helper did not start; see: journalctl -u nxlx-ndi -n 30"
		else
			systemctl try-restart nxlx-ndi.service || true
		fi
	fi
	# The discovery server's supervisor, only where it is set up: it moves to the new program; try-restart starts
	# nothing. It runs nothing of its own until its module is on.
	if [ "$NDISC_SET_UP" = 1 ] && [ -f "$NDISC_UNIT" ]; then
		systemctl enable nxlx-ndi-discovery.service || log "could not enable the discovery server's supervisor"
		systemctl try-restart nxlx-ndi-discovery.service || true
	fi
	# With --no-start: what was stopped above for the runtime folder is started again, and nothing else.
	if [ "$START" = 0 ] && [ ${#RUN_WAS_ACTIVE[@]} -gt 0 ]; then systemctl start "${RUN_WAS_ACTIVE[@]}" || true; fi
fi

log "installed. Check the device with: nxlx-selftest --play"
if [ "$CONVERTED" = 1 ]; then log "CONVERTED from the old names (pvj to nxlx)."; fi
if [ -e "$ROOT/etc/systemd/system/nxlx-convert-undo.timer" ]; then
	log "A conversion waits for its confirm. Check the node, then keep it with: sudo nxlx-convert --confirm"
	log "Unless that is run within 15 minutes of the conversion's last run (or 5 after a restart), the old names and the release before come back by themselves."
fi
