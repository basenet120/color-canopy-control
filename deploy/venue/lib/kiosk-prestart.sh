#!/usr/bin/env bash
# ExecStartPre of the kiosk's user unit (canopy-kiosk.service). Waits, for as long as it takes, until the Conductor
# answers /healthz, so the touchscreen never shows a browser error page; and marks the kiosk's Chromium profile as
# having exited cleanly, so a crash or power cut never brings up a "Restore pages?" bubble.
set -uo pipefail

case ${1:-} in
-h | --help)
	sed -n '2,4p' "$0" | sed 's/^# \{0,1\}//'
	exit 0
	;;
esac

health_url=${CANOPY_HEALTH_URL:-http://localhost:8000/healthz}
profile=${CANOPY_KIOSK_PROFILE:-$HOME/snap/chromium/common/canopy-kiosk}

prefs=$profile/Default/Preferences
if [ -f "$prefs" ]; then
	sed -i -e 's/"exited_cleanly":false/"exited_cleanly":true/' \
		-e 's/"exit_type":"[A-Za-z]*"/"exit_type":"Normal"/' "$prefs" || true
fi

waited=0
until curl -fsS -m 3 -o /dev/null "$health_url"; do
	if [ "$waited" -eq 0 ]; then
		echo "Waiting for the Conductor ($health_url) before opening the kiosk…"
	fi
	waited=$((waited + 2))
	sleep 2
done
[ "$waited" -gt 0 ] && echo "The Conductor answered after about $waited s."
exit 0
