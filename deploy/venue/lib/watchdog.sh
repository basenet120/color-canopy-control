#!/usr/bin/env bash
# Run every 30 s by canopy-watchdog.timer. Asks the Conductor's /healthz; after 3 failures in a row (about 90 s) it
# restarts canopy-conductor.service and logs that (journal + /var/lib/canopy/watchdog.log). It stands down while the
# Conductor is stopped on purpose (break-glass hold, or systemctl stop) and for 2 minutes after each start.
# It also keeps /run/canopy/timesync/synchronized in step with the box's NTP state, for the 7 am checklist.
set -uo pipefail
# shellcheck source=common.sh
. "$(dirname "$(readlink -f "$0")")/common.sh"

case ${1:-} in
-h | --help)
	sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'
	exit 0
	;;
esac

FAILURES_TO_RESTART=3
START_GRACE_S=120
counter=$CANOPY_STATE/watchdog.failures
mkdir -p "$CANOPY_STATE"

# <3> / <4> / <6> set the journal priority (error / warning / info) of a line.
log() { printf '<%s>%s\n' "$1" "$2"; }

# Chrony (or timesyncd) clears the kernel's "unsynchronized" flag; timedatectl reads it. The Conductor's container
# sees this folder at /run/systemd/timesync, where the checklist looks for a "synchronized" file.
mkdir -p "$CANOPY_TIMESYNC_DIR"
if [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = "yes" ]; then
	touch "$CANOPY_TIMESYNC_DIR/synchronized"
else
	if [ -e "$CANOPY_TIMESYNC_DIR/synchronized" ]; then
		log 4 "The box's clock is no longer NTP-synchronized (check chrony: chronyc tracking)."
	fi
	rm -f "$CANOPY_TIMESYNC_DIR/synchronized"
fi

failures=$(cat "$counter" 2>/dev/null || echo 0)
case $failures in '' | *[!0-9]*) failures=0 ;; esac

if held || ! conductor_active; then
	echo 0 >"$counter" # stopped on purpose, or still starting: nothing to watch
	exit 0
fi
entered_us=$(systemctl show -p ActiveEnterTimestampMonotonic --value "$CANOPY_UNIT" 2>/dev/null || echo 0)
now_us=$(awk '{ printf "%d", $1 * 1000000 }' /proc/uptime)
case $entered_us in '' | *[!0-9]*) entered_us=0 ;; esac
if [ "$entered_us" -gt 0 ] && [ $((now_us - entered_us)) -lt $((START_GRACE_S * 1000000)) ]; then
	echo 0 >"$counter"
	exit 0
fi

if healthy; then
	if [ "$failures" -gt 0 ]; then
		log 6 "The Conductor answers /healthz again (after $failures failed check(s))."
	fi
	echo 0 >"$counter"
	exit 0
fi

failures=$((failures + 1))
if [ "$failures" -lt "$FAILURES_TO_RESTART" ]; then
	echo "$failures" >"$counter"
	log 4 "No answer from the Conductor's /healthz ($failures of $FAILURES_TO_RESTART before a restart)."
	exit 0
fi
echo 0 >"$counter"
log 3 "Restarting the Conductor: /healthz failed $FAILURES_TO_RESTART times in a row."
printf '%s restarted the Conductor after %s failed health checks\n' "$(date -Is)" "$FAILURES_TO_RESTART" \
	>>"$CANOPY_WATCHDOG_LOG"
systemctl restart --no-block "$CANOPY_UNIT"
