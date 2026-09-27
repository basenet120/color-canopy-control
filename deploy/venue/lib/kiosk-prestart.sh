#!/usr/bin/env bash
# ExecStartPre of the kiosk's user unit (canopy-kiosk.service). Puts the local resting page where the Chromium snap
# can read it, so the touchscreen shows "The canopy is resting" at once, and never a desktop or an error page, while
# the Conductor is down, held or starting; the page opens the canopy itself once /healthz answers. Also marks the
# kiosk's Chromium profile as having exited cleanly, so a crash or power cut never brings up "Restore pages?".
set -uo pipefail

case ${1:-} in
-h | --help)
	sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'
	exit 0
	;;
esac

profile=${CANOPY_KIOSK_PROFILE:-$HOME/snap/chromium/common/canopy-kiosk}
# Shipped by install.sh. The Chromium snap can't read /opt, only its own folders under ~/snap/chromium.
source=${CANOPY_RESTING_SRC:-/opt/canopy/kiosk/resting.html}
resting=${CANOPY_KIOSK_RESTING:-$HOME/snap/chromium/common/canopy-resting.html}

prefs=$profile/Default/Preferences
if [ -f "$prefs" ]; then
	sed -i -e 's/"exited_cleanly":false/"exited_cleanly":true/' \
		-e 's/"exit_type":"[A-Za-z]*"/"exit_type":"Normal"/' "$prefs" || true
fi

if ! mkdir -p "$(dirname "$resting")" || ! install -m 0644 "$source" "$resting"; then
	echo "Can't put the kiosk's resting page ($source) at $resting. Run the installer again (install.sh ships it)."
	exit 1
fi
exit 0
