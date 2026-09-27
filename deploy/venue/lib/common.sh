# shellcheck shell=bash
# Shared by the venue-box scripts (install.sh, bin/canopy-*, the systemd helpers). Source it; don't run it.
#
# Layout on the box (docs/runbooks/venue-box.md):
#   /opt/canopy/                     docker-compose.yml, bin/, lib/, images/ (saved image tarballs),
#                                    kiosk/resting.html (what the touchscreen shows until the Conductor answers)
#   /etc/canopy/conductor.env        the Conductor's settings and secrets (root, 0600; never in git)
#   /etc/canopy/box.env              non-secret box settings: CONDUCTOR_TAG (+ CONDUCTOR_PREVIOUS_TAG, the release
#                                    it replaced), CONDUCTOR_LAN_IP, kiosk user, backups
#   /var/lib/canopy/                 the break-glass hold flag, watchdog state and log
#   /var/backups/canopy/             nightly backups of the canopy-data volume
# Every path can be overridden by the environment (tests and dry runs use temporary directories).

CANOPY_HOME=${CANOPY_HOME:-/opt/canopy}
CANOPY_ETC=${CANOPY_ETC:-/etc/canopy}
CANOPY_ENV_FILE=${CANOPY_ENV_FILE:-$CANOPY_ETC/conductor.env}
CANOPY_BOX_FILE=${CANOPY_BOX_FILE:-$CANOPY_ETC/box.env}
CANOPY_STATE=${CANOPY_STATE:-/var/lib/canopy}
CANOPY_HOLD=${CANOPY_HOLD:-$CANOPY_STATE/hold}
CANOPY_WATCHDOG_LOG=${CANOPY_WATCHDOG_LOG:-$CANOPY_STATE/watchdog.log}
CANOPY_BACKUP_DIR=${CANOPY_BACKUP_DIR:-/var/backups/canopy}
CANOPY_TIMESYNC_DIR=${CANOPY_TIMESYNC_DIR:-/run/canopy/timesync}
CANOPY_UNIT=canopy-conductor.service
CANOPY_KIOSK_UNIT=canopy-kiosk.service
CANOPY_CONTAINER=canopy-conductor
CANOPY_VOLUME=canopy-data
CANOPY_IMAGE=canopy-conductor
CANOPY_PROJECT=canopy
CANOPY_URL=${CANOPY_URL:-http://127.0.0.1:8000}
CANOPY_TAG_RE='^v[0-9]{4}\.[0-9]{2}\.[0-9]{2}-[0-9]+$'

say() { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() {
	printf 'STOP: %s\n' "$*" >&2
	exit 1
}

need_root() {
	[ "$(id -u)" -eq 0 ] || die "Run this with sudo: sudo $(basename "$0") $*"
}

# env_value FILE KEY → the value of the last KEY=... line, without surrounding quotes. Never echo secrets with it.
env_value() {
	local file=$1 key=$2 line value=""
	[ -r "$file" ] || return 0
	while IFS= read -r line || [ -n "$line" ]; do
		case $line in
		"$key="*) value=${line#"$key="} ;;
		esac
	done <"$file"
	value=${value%$'\r'}
	case $value in
	\"*\") value=${value#\"} value=${value%\"} ;;
	\'*\') value=${value#\'} value=${value%\'} ;;
	esac
	printf '%s' "$value"
}

# env_has FILE KEY → true when the file assigns KEY (even to an empty value).
env_has() {
	grep -Eq "^$2=" "$1" 2>/dev/null
}

# set_env_value FILE KEY VALUE → replace or append KEY=VALUE (for the non-secret box.env only).
set_env_value() {
	local file=$1 key=$2 value=$3 tmp
	tmp=$(mktemp "$file.XXXXXX")
	if [ -f "$file" ]; then
		grep -Ev "^$key=" "$file" >"$tmp" || true
	fi
	printf '%s=%s\n' "$key" "$value" >>"$tmp"
	chmod 0644 "$tmp"
	mv -f "$tmp" "$file"
}

# switch_release FILE TAG → make TAG the release the box runs (CONDUCTOR_TAG), remembering the release it replaces
# as CONDUCTOR_PREVIOUS_TAG (the rollback target). Switching to the tag it already runs changes nothing. Call it only
# once the image TAG is on the box and checked: the next restart, reboot or watchdog run starts whatever this names.
switch_release() {
	local file=$1 tag=$2 current
	current=$(env_value "$file" CONDUCTOR_TAG)
	[ "$current" = "$tag" ] && return 0
	if printf '%s' "$current" | grep -Eq "$CANOPY_TAG_RE"; then
		set_env_value "$file" CONDUCTOR_PREVIOUS_TAG "$current"
	fi
	set_env_value "$file" CONDUCTOR_TAG "$tag"
}

# box_architecture → the box's CPU as Docker names it (amd64, arm64).
box_architecture() {
	local arch
	arch=$(dpkg --print-architecture 2>/dev/null) || arch=$(uname -m)
	case $arch in
	x86_64) arch=amd64 ;;
	aarch64) arch=arm64 ;;
	esac
	printf '%s' "$arch"
}

# check_image_arch IMAGE → true when IMAGE runs on this box's CPU; otherwise says why (an image built on an
# Apple-silicon laptop is linux/arm64 and dies with "exec format error" on the x86-64 box).
check_image_arch() {
	local image=$1 have want
	have=$(docker image inspect --format '{{ .Architecture }}' "$image" 2>/dev/null) || {
		say "Can't read the architecture of $image."
		return 1
	}
	want=$(box_architecture)
	[ "$have" = "$want" ] && return 0
	say "$image is built for linux/$have, but this box is $want: it would stop with \"exec format error\"."
	say "Cut the release with scripts/release.sh (it builds for linux/amd64), or build on the box without --image-tar."
	return 1
}

# check_env_file FILE [--allow-own] → prints every problem with the Conductor's env file and returns non-zero when
# there is one. Checks names and shapes only; it never prints a value. --allow-own also accepts a file owned by the
# user running the check (dry runs and tests), not just root.
check_env_file() {
	local file=$1 allow_own=${2:-} problems=0 mode owner key value
	if [ ! -e "$file" ]; then
		say "  - $file doesn't exist. Create it from conductor.env.example (see docs/runbooks/venue-box.md, step 3):"
		say "      sudo install -o root -g root -m 0600 -D deploy/venue/conductor.env.example $file"
		say "      sudo nano $file"
		return 1
	fi
	if [ ! -f "$file" ] || [ -L "$file" ]; then
		say "  - $file must be a plain file (not a link or a folder)."
		return 1
	fi
	mode=$(stat -c '%a' "$file")
	owner=$(stat -c '%u' "$file")
	if [ "$mode" != "600" ] && [ "$mode" != "400" ]; then
		say "  - $file is mode $mode; it holds secrets, so it must be 0600: sudo chmod 0600 $file"
		problems=1
	fi
	if [ "$owner" != "0" ] && { [ "$allow_own" != "--allow-own" ] || [ "$owner" != "$(id -u)" ]; }; then
		say "  - $file must belong to root: sudo chown root:root $file"
		problems=1
	fi
	for key in CONDUCTOR_MODE HUBSPACE_LIVE HUBSPACE_DEVICE_ALLOWLIST CONDUCTOR_ENV CONDUCTOR_DATA_DIR; do
		value=$(env_value "$file" "$key")
		case $key=$value in
		CONDUCTOR_MODE=live | HUBSPACE_LIVE=1 | HUBSPACE_DEVICE_ALLOWLIST=registry | CONDUCTOR_ENV=venue | CONDUCTOR_DATA_DIR=/data) ;;
		CONDUCTOR_MODE=*) say "  - CONDUCTOR_MODE must be live on the venue box." && problems=1 ;;
		HUBSPACE_LIVE=*) say "  - HUBSPACE_LIVE must be 1 on the venue box." && problems=1 ;;
		HUBSPACE_DEVICE_ALLOWLIST=*) say "  - HUBSPACE_DEVICE_ALLOWLIST must be registry on the venue box." && problems=1 ;;
		CONDUCTOR_ENV=*) say "  - CONDUCTOR_ENV must be venue (it binds the stored sign-ins to this box)." && problems=1 ;;
		CONDUCTOR_DATA_DIR=*) say "  - CONDUCTOR_DATA_DIR must be /data (the canopy-data volume)." && problems=1 ;;
		esac
	done
	value=$(env_value "$file" HUBSPACE_ACCOUNT_ALLOWLIST)
	if [ -z "$value" ] || ! printf '%s' "$value" | grep -q '@'; then
		say "  - HUBSPACE_ACCOUNT_ALLOWLIST must list the show accounts' emails (comma-separated)."
		problems=1
	fi
	value=$(env_value "$file" CONDUCTOR_BASIC_AUTH)
	if ! printf '%s' "$value" | grep -Eq '^[^:]+:.{12,}$'; then
		say "  - CONDUCTOR_BASIC_AUTH must be user:password, with a password of at least 12 characters."
		problems=1
	fi
	value=$(env_value "$file" CONDUCTOR_SECRET_KEY)
	if ! printf '%s' "$value" | grep -Eq '^[A-Za-z0-9_-]{43}=$'; then
		say "  - CONDUCTOR_SECRET_KEY must be a Fernet key (44 characters ending in =). Make one with:"
		say "      head -c 32 /dev/urandom | base64 | tr '+/' '-_'"
		problems=1
	fi
	for key in FORWARDED_ALLOW_IPS CONDUCTOR_AUTH CONDUCTOR_ENGINE CONDUCTOR_SIM_SPEED PORT \
		RAILWAY_ENVIRONMENT_NAME RAILWAY_GIT_COMMIT_SHA; do
		if env_has "$file" "$key"; then
			say "  - Remove $key from $file: it must not be set on the venue box."
			problems=1
		fi
	done
	if [ "$(env_value "$file" CONDUCTOR_ACCOUNT_CONNECT)" = "1" ]; then
		warn "CONDUCTOR_ACCOUNT_CONNECT=1 turns on the sign-in form on /ops/accounts. Remove it once accounts are connected."
	fi
	return "$problems"
}

# check_box_file FILE → problems with box.env (tag and show-LAN address).
check_box_file() {
	local file=$1 problems=0 tag ip
	if [ ! -f "$file" ]; then
		say "  - $file doesn't exist. Run the installer (sudo deploy/venue/install.sh --lan-ip <box's show-LAN IP>)."
		return 1
	fi
	tag=$(env_value "$file" CONDUCTOR_TAG)
	if ! printf '%s' "$tag" | grep -Eq "$CANOPY_TAG_RE"; then
		say "  - CONDUCTOR_TAG in $file must be a release tag like v2026.10.12-1 (it is '${tag}')."
		problems=1
	fi
	ip=$(env_value "$file" CONDUCTOR_LAN_IP)
	if ! valid_lan_ip "$ip"; then
		say "  - CONDUCTOR_LAN_IP in $file must be the box's fixed address on the show network, e.g. 192.168.50.10."
		problems=1
	fi
	return "$problems"
}

valid_lan_ip() {
	local ip=$1 part
	printf '%s' "$ip" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' || return 1
	case $ip in 0.* | 127.* | 255.*) return 1 ;; esac
	for part in ${ip//./ }; do
		[ "$part" -le 255 ] || return 1
	done
}

# compose ARGS... → docker compose for the canopy project, with box.env for ${CONDUCTOR_TAG} / ${CONDUCTOR_LAN_IP}.
compose() {
	CANOPY_ENV_FILE=$CANOPY_ENV_FILE docker compose --project-name "$CANOPY_PROJECT" \
		--env-file "$CANOPY_BOX_FILE" --file "$CANOPY_HOME/docker-compose.yml" "$@"
}

conductor_active() { systemctl is-active --quiet "$CANOPY_UNIT"; }
held() { [ -e "$CANOPY_HOLD" ]; }

healthy() { curl -fsS -m 5 -o /dev/null "$CANOPY_URL/healthz" 2>/dev/null; }

# wait_healthy SECONDS → true once /healthz answers; prints how long it took.
wait_healthy() {
	local limit=$1 start now
	start=$(date +%s)
	while :; do
		if healthy; then
			now=$(date +%s)
			say "The Conductor answers (after $((now - start)) s)."
			return 0
		fi
		now=$(date +%s)
		[ $((now - start)) -ge "$limit" ] && return 1
		sleep 2
	done
}

kiosk_user() {
	local user
	user=$(env_value "$CANOPY_BOX_FILE" CANOPY_KIOSK_USER)
	printf '%s' "${user:-kiosk}"
}

# kiosk_systemctl ARGS... → systemctl --user for the kiosk user's session (from root).
kiosk_systemctl() {
	systemctl --user --machine="$(kiosk_user)@" "$@"
}

# sha256_matches FILE → true when FILE's hash equals the one in FILE.sha256 (by value, so a renamed or copied file
# still checks).
sha256_matches() {
	local expected actual
	expected=$(cut -d' ' -f1 "$1.sha256" 2>/dev/null)
	actual=$(sha256sum "$1" | cut -d' ' -f1)
	[ -n "$expected" ] && [ "$expected" = "$actual" ]
}

volume_path() {
	docker volume inspect --format '{{ .Mountpoint }}' "$CANOPY_VOLUME" 2>/dev/null
}
