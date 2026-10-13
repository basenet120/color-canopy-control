#!/usr/bin/env bash
# What launchd runs for the Mac show box (com.colorcanopy.conductor, D61). Reads the Conductor's settings from
# ~/Library/Application Support/ColorCanopy/conductor.env (never sourced: KEY=value lines only), pins the data folder,
# rotates the log, and becomes the Conductor (exec, so launchd's stop signal reaches uvicorn directly).
#   run-conductor.sh          run the Conductor (launchd does this; you normally use `canopy-mac start`)
#   run-conductor.sh --check  check the settings and print the non-secret ones, then exit
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
CANOPY_MAC_HOME=${CANOPY_MAC_HOME:-"$HOME/Library/Application Support/ColorCanopy"}
ENV_FILE="$CANOPY_MAC_HOME/conductor.env"
DATA_DIR="$CANOPY_MAC_HOME/data"
STATE_DIR="$CANOPY_MAC_HOME/state"
LOG_FILE=${CANOPY_MAC_LOG:-"$HOME/Library/Logs/ColorCanopy/conductor.log"}
LOG_ROTATE_BYTES=$((50 * 1024 * 1024))
PYTHON=${CANOPY_MAC_PYTHON:-"$ROOT/.venv/bin/python"}

die() {
	printf 'STOP: %s\n' "$*" >&2
	exit 1
}

# GNU stat (CI's Linux) reads -f as "file system", so try its -c form first; BSD stat (macOS) has no -c.
file_mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }

[ -f "$ENV_FILE" ] || die "No settings at $ENV_FILE. Run: deploy/mac/canopy-mac install"
case $(file_mode "$ENV_FILE") in
600 | 400) ;;
*) die "$ENV_FILE must be readable by you only. Run: chmod 600 \"$ENV_FILE\"" ;;
esac

# Drop anything the parent might have set, so only the file decides (same list as conductor.workshop._apply).
unset CONDUCTOR_AUTH CONDUCTOR_ENGINE CONDUCTOR_SIM_SPEED RAILWAY_ENVIRONMENT_NAME RAILWAY_GIT_COMMIT_SHA \
	FORWARDED_ALLOW_IPS CONDUCTOR_ACCOUNT_CONNECT

line_no=0
while IFS= read -r line || [ -n "$line" ]; do
	line_no=$((line_no + 1))
	line=${line%$'\r'}
	case $line in '' | '#'*) continue ;; esac
	key=${line%%=*}
	value=${line#*=}
	if [ "$key" = "$line" ] || ! [[ $key =~ ^[A-Z][A-Z0-9_]*$ ]]; then
		die "$ENV_FILE line $line_no isn't KEY=value. Fix it (the line isn't shown here: it may hold a secret)."
	fi
	export "$key=$value"
done <"$ENV_FILE"

for key in CONDUCTOR_BASIC_AUTH CONDUCTOR_SECRET_KEY; do
	[ -n "${!key:-}" ] || die "$key is empty in $ENV_FILE. Run: deploy/mac/canopy-mac install"
done
case ${RAILWAY_ENVIRONMENT_NAME:-} in '') ;; *) die "RAILWAY_ENVIRONMENT_NAME is set: this isn't staging." ;; esac

export CONDUCTOR_DATA_DIR="$DATA_DIR"
BIND=${CANOPY_BIND:-0.0.0.0}
PORT=${CANOPY_PORT:-8000}
[[ $PORT =~ ^[0-9]+$ ]] || die "CANOPY_PORT=$PORT isn't a port number."

if [ "${1:-}" = "--check" ]; then
	printf 'settings   %s\n' "$ENV_FILE"
	printf 'checkout   %s (%s)\n' "$ROOT" "$(git -C "$ROOT" describe --tags --always --dirty 2>/dev/null || echo unknown)"
	printf 'data       %s\n' "$CONDUCTOR_DATA_DIR"
	printf 'listen     %s:%s\n' "$BIND" "$PORT"
	for key in CONDUCTOR_MODE HUBSPACE_LIVE HUBSPACE_DEVICE_ALLOWLIST HUBSPACE_ACCOUNT_ALLOWLIST HUBSPACE_LIVE_UNTIL \
		CONDUCTOR_ENV; do
		printf '%-26s %s\n' "$key" "${!key:-(unset)}"
	done
	printf '%-26s %s\n' CONDUCTOR_BASIC_AUTH "set (user ${CONDUCTOR_BASIC_AUTH%%:*})"
	printf '%-26s %s\n' CONDUCTOR_SECRET_KEY set
	exit 0
fi

[ -x "$PYTHON" ] || die "No Python environment at $ROOT/.venv. Run: deploy/mac/canopy-mac install"
umask 077
mkdir -p "$DATA_DIR" "$STATE_DIR" "$(dirname "$LOG_FILE")"

# launchd appends stdout/stderr to the log forever; keep three old ones of ~50 MB.
if [ -f "$LOG_FILE" ] && [ "$(wc -c <"$LOG_FILE" | tr -d ' ')" -gt "$LOG_ROTATE_BYTES" ]; then
	for n in 2 1; do [ -f "$LOG_FILE.$n" ] && mv -f "$LOG_FILE.$n" "$LOG_FILE.$((n + 1))"; done
	cp "$LOG_FILE" "$LOG_FILE.1" && : >"$LOG_FILE"
fi

date +%s >"$STATE_DIR/started_at" # the watchdog's start-up grace
printf '%s starting the Conductor from %s (mode %s, %s:%s)\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$ROOT" \
	"${CONDUCTOR_MODE:-sim}" "$BIND" "$PORT"
cd "$ROOT"
# Exactly one process: desired state, pending sign-ins, token refresh and the rate budget live in memory.
exec "$PYTHON" -m uvicorn conductor.app:app --host "$BIND" --port "$PORT"
