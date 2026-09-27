#!/usr/bin/env bash
# ExecStartPre of canopy-conductor.service. Refuses to start the Conductor while /etc/canopy/conductor.env is missing,
# readable by anyone but root, or wrong for the venue box; checks box.env, the image and the data volume; then waits
# up to 60 s for the box's show-LAN address (Docker can't publish the port on an address the box doesn't have yet).
# It never prints a secret. Run it by hand to see what's wrong: sudo /opt/canopy/lib/preflight.sh
set -euo pipefail
# shellcheck source=common.sh
. "$(dirname "$(readlink -f "$0")")/common.sh"

case ${1:-} in
-h | --help)
	sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'
	exit 0
	;;
esac

problems=0
check_env_file "$CANOPY_ENV_FILE" || problems=1
check_box_file "$CANOPY_BOX_FILE" || problems=1
if [ "$problems" -ne 0 ]; then
	die "The Conductor won't start until the problems above are fixed. Then: sudo systemctl restart $CANOPY_UNIT"
fi

tag=$(env_value "$CANOPY_BOX_FILE" CONDUCTOR_TAG)
if ! docker image inspect "$CANOPY_IMAGE:$tag" >/dev/null 2>&1; then
	die "There's no image $CANOPY_IMAGE:$tag on this box. Load it (docs/runbooks/release-and-freeze.md) or go back to the previous release: sudo canopy-restart --tag <previous tag>"
fi
if ! docker volume inspect "$CANOPY_VOLUME" >/dev/null 2>&1; then
	die "The data volume $CANOPY_VOLUME is missing. Restore the latest backup (sudo canopy-restore), or on a brand-new box run the installer."
fi
mkdir -p "$CANOPY_TIMESYNC_DIR"

lan_ip=$(env_value "$CANOPY_BOX_FILE" CONDUCTOR_LAN_IP)
for _ in $(seq 1 30); do
	if ip -o -4 addr show | grep -q " $lan_ip/"; then
		exit 0
	fi
	sleep 2
done
die "This box doesn't have the address $lan_ip (CONDUCTOR_LAN_IP in $CANOPY_BOX_FILE). Check the show-network cable and the box's fixed address (ip -4 addr), then: sudo systemctl restart $CANOPY_UNIT"
