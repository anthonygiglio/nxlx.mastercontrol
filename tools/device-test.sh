#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
# Device test: run on a real board (Pi, mini PC) and write a report folder.
#   tools/device-test.sh [OUT_DIR]
# Environment:
#   NXLX_DEVICE_PLAY=1   also play a test pattern on the real display (needs a screen attached)
# It never changes the network, never installs anything and never needs root.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/device-report}"
mkdir -p "$OUT"
cd "$ROOT" || exit 2
status=0

{
  echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "commit: $(git rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "kernel: $(uname -a)"
  # shellcheck source=/dev/null
  echo "os: $(. /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-unknown}")"
  echo "model: $( [ -r /proc/device-tree/model ] && tr -d '\0' </proc/device-tree/model || echo unknown )"
  echo "python: $(python3 --version 2>&1)"
  echo "mpv: $(mpv --version 2>/dev/null | head -n 1 || echo missing)"
  echo "nmcli: $(nmcli --version 2>/dev/null || echo missing)"
  echo "temp_c: $(awk '{printf "%.1f", $1/1000}' /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo n/a)"
  echo "throttled: $(vcgencmd get_throttled 2>/dev/null || echo n/a)"
  echo "display: ${DISPLAY:-none}${WAYLAND_DISPLAY:+ wayland=$WAYLAND_DISPLAY}"
} >"$OUT/system.txt" 2>&1

echo "== self-test"
args=(--json "$OUT/selftest.json")
[ "${NXLX_DEVICE_PLAY:-0}" = "1" ] && args+=(--play)
python3 bin/nxlx-selftest "${args[@]}" >"$OUT/selftest.txt" 2>&1 || status=1
tail -n 25 "$OUT/selftest.txt"

echo "== unit tests (real mpv, real hardware paths)"
python3 -m unittest discover -s tests -v >"$OUT/unittest.txt" 2>&1 || status=1
tail -n 6 "$OUT/unittest.txt"

echo "$status" >"$OUT/exit-status.txt"
echo "report written to $OUT (exit status $status)"
exit "$status"
