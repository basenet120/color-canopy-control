#!/usr/bin/env bash
# Install or update the Color Canopy Conductor on the venue box (Ubuntu 24.04), from a checkout of a release tag.
# Idempotent: run it again after checking out a newer tag (hotfix) and it only changes what differs.
# It never writes secrets: it refuses to go on until /etc/canopy/conductor.env exists, belongs to root and is 0600.
# Full procedure: docs/runbooks/venue-box.md.
set -euo pipefail

here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
repo=$(cd "$here/../.." && pwd)
# shellcheck source=lib/common.sh
. "$here/lib/common.sh"

usage() {
	cat <<'USAGE'
Usage: sudo deploy/venue/install.sh [--lan-ip IP] [--kiosk-user NAME] [--image-tar FILE] [--tag TAG]
                                   [--docker-source ubuntu|docker] [--spare] [--no-kiosk] [--dry-run]

Run from a checkout of a release tag (git checkout v2026.10.12-1). Steps: check the box (Ubuntu 24.04) and
/etc/canopy/conductor.env; install Docker, chrony, curl and the Chromium snap; build the image canopy-conductor:<tag> from the tag (or load a saved one);
check that the image was built from the tag for this box's CPU; copy the compose file, helpers (canopy-status,
canopy-restart, ...), the kiosk's resting page, runbooks and systemd units; create the canopy-data volume; enable the
Conductor, the watchdog and the nightly backup; only then write /etc/canopy/box.env (show-LAN address, kiosk user,
the release tag, remembering the release it replaces as CONDUCTOR_PREVIOUS_TAG) and restart the Conductor on it.

  --lan-ip IP          the box's fixed address on the show network (needed the first time; kept afterwards)
  --kiosk-user NAME    the auto-login desktop user that runs the kiosk browser (default: kiosk)
  --spare              the cold spare: install everything, then stop the Conductor and hold it (canopy-stop) so it
                       never starts next to the main box; it needs neither the show network nor the internet
                       afterwards. Takes over with venue-box.md → The cold spare. Needs the kiosk user.
  --no-kiosk           don't set up the kiosk browser (a box that never drives the touchscreen; not a spare)
  --image-tar FILE     load dist/canopy-conductor-<tag>.tar.gz (from scripts/release.sh) instead of building
  --tag TAG            the release tag (default: the tag the checkout is on)
  --docker-source SRC  ubuntu (default: Ubuntu's docker.io packages) or docker (Docker's own apt repository)
  --dry-run            check everything and print what would be done, changing nothing
  -h, --help           this help
USAGE
}

dry=0
lan_ip=""
kiosk_user_arg=""
kiosk=1
spare=0
image_tar=""
tag=""
docker_source=ubuntu
while [ $# -gt 0 ]; do
	case $1 in
	-h | --help) usage && exit 0 ;;
	--dry-run) dry=1 ;;
	--lan-ip) lan_ip=${2:?--lan-ip needs an address} && shift ;;
	--kiosk-user) kiosk_user_arg=${2:?--kiosk-user needs a user name} && shift ;;
	--no-kiosk) kiosk=0 ;;
	--spare) spare=1 ;;
	--image-tar) image_tar=${2:?--image-tar needs a file} && shift ;;
	--tag) tag=${2:?--tag needs a release tag} && shift ;;
	--docker-source) docker_source=${2:?--docker-source needs ubuntu or docker} && shift ;;
	*) usage >&2 && exit 2 ;;
	esac
	shift
done
case $docker_source in ubuntu | docker) ;; *) die "--docker-source is ubuntu or docker." ;; esac
if [ "$spare" -eq 1 ] && [ "$kiosk" -eq 0 ]; then
	die "A cold spare drives the touchscreen when it takes over, so it needs the kiosk browser: drop --no-kiosk."
fi
os_release=${CANOPY_OS_RELEASE:-/etc/os-release}

# Every change goes through run: in a dry run it's printed instead.
run() {
	if [ "$dry" -eq 1 ]; then
		printf '  would run: %s\n' "$*"
	else
		"$@"
	fi
}
step() { printf '\n==> %s\n' "$*"; }
soft() { # soft MESSAGE: a hard stop, except in a dry run (where it's a warning so the rest can be checked)
	if [ "$dry" -eq 1 ]; then warn "$1 (dry run: carrying on)"; else die "$1"; fi
}
have_docker() { command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; }

[ "$dry" -eq 1 ] && say "DRY RUN: nothing will be changed."

# ---- 1. The box --------------------------------------------------------------------------------------------------
step "Checking this box"
[ "$dry" -eq 1 ] || need_root "$@"
os_id=$(. "$os_release" 2>/dev/null && printf '%s' "${ID:-}")
os_version=$(. "$os_release" 2>/dev/null && printf '%s' "${VERSION_ID:-}")
os_codename=$(. "$os_release" 2>/dev/null && printf '%s' "${VERSION_CODENAME:-noble}")
if [ "$os_id" = ubuntu ] && [ "$os_version" = "24.04" ]; then
	say "Ubuntu 24.04: OK."
else
	soft "This installer is for Ubuntu 24.04; this is ${os_id:-unknown} ${os_version:-?}."
fi

# ---- 2. The Conductor's settings (secrets: checked, never written) ----------------------------------------------
step "Checking $CANOPY_ENV_FILE"
own=""
[ "$dry" -eq 1 ] && own=--allow-own
if ! check_env_file "$CANOPY_ENV_FILE" $own; then
	die "Fix $CANOPY_ENV_FILE as above, then run the installer again. (It never writes secrets itself.)"
fi
say "OK: private (0600, root), set up for the venue box. Values not shown."

# ---- 3. The release -----------------------------------------------------------------------------------------------
step "Release"
if [ -z "$tag" ] && [ -n "$image_tar" ]; then
	tag=$(basename "$image_tar")
	tag=${tag#"$CANOPY_IMAGE"-}
	tag=${tag%.tar.gz}
fi
if [ -z "$tag" ]; then
	tag=$(git -C "$repo" describe --tags --exact-match 2>/dev/null || true)
fi
if ! printf '%s' "$tag" | grep -Eq "$CANOPY_TAG_RE"; then
	soft "This checkout isn't on a release tag (like v2026.10.12-1). Check one out first: git checkout <tag>"
	tag=v0000.00.00-0
fi
# The helpers, units and runbooks come from this checkout, so it must be the tag too (even with --image-tar).
commit=$(git -C "$repo" rev-list -n 1 "$tag" 2>/dev/null || true)
head=$(git -C "$repo" rev-parse HEAD 2>/dev/null || true)
if [ -z "$commit" ] || [ "$commit" != "$head" ]; then
	soft "The checkout isn't at $tag. Run: git fetch --tags && git checkout $tag"
fi
if [ -n "$(git -C "$repo" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
	soft "The checkout has local changes. The box only runs exactly what was tagged: git stash, or a fresh clone."
fi
say "Release: $tag"

# ---- 4. Box settings (not secret) ---------------------------------------------------------------------------------
# Worked out now, written only in step 9, once the image is on the box and checked: box.env names what the next
# restart, reboot or watchdog run starts, so a hotfix that fails half-way must leave it on the running release.
step "Box settings ($CANOPY_BOX_FILE)"
running_tag=$(env_value "$CANOPY_BOX_FILE" CONDUCTOR_TAG)
lan_ip=${lan_ip:-$(env_value "$CANOPY_BOX_FILE" CONDUCTOR_LAN_IP)}
valid_lan_ip "$lan_ip" || die "Give the box's fixed show-network address: --lan-ip 192.168.50.10 (see venue-box.md)."
kiosk_user=${kiosk_user_arg:-$(env_value "$CANOPY_BOX_FILE" CANOPY_KIOSK_USER)}
kiosk_user=${kiosk_user:-kiosk}
if [ "$kiosk" -eq 1 ] && ! id "$kiosk_user" >/dev/null 2>&1; then
	soft "There's no user $kiosk_user for the kiosk. Create it in Ubuntu's Settings → Users (auto-login on), or pass --no-kiosk."
fi
if [ ! -f "$CANOPY_BOX_FILE" ]; then
	# A first install: nothing runs from this file yet (the Conductor's unit isn't installed), so it names the new
	# release straight away rather than the example's, which must never become a rollback target.
	run install -o root -g root -m 0644 -D "$here/box.env.example" "$CANOPY_BOX_FILE"
	run set_env_value "$CANOPY_BOX_FILE" CONDUCTOR_TAG "$tag"
	running_tag=""
fi
say "Release $tag on $lan_ip:8000; kiosk user $kiosk_user$([ -n "$running_tag" ] && printf '; running now: %s' "$running_tag")."

# ---- 5. Packages ----------------------------------------------------------------------------------------------------
step "Packages: Docker, chrony (NTP), curl$([ "$kiosk" -eq 1 ] && printf ', Chromium')"
packages=(chrony curl ca-certificates)
docker_packages=()
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
	say "Docker and Compose are already installed: keeping them."
elif [ "$docker_source" = ubuntu ]; then
	docker_packages=(docker.io docker-compose-v2 docker-buildx)
else
	docker_packages=(docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin)
	run install -m 0755 -d /etc/apt/keyrings
	run curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
	run sh -c "echo 'deb [arch=$(dpkg --print-architecture 2>/dev/null || echo amd64) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $os_codename stable' > /etc/apt/sources.list.d/docker.list"
fi
run apt-get update
run env DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}" "${docker_packages[@]}"
if [ "${#docker_packages[@]}" -gt 0 ]; then
	# Frozen for the show: an unattended Docker upgrade restarts every container.
	run apt-mark hold "${docker_packages[@]}"
fi
run systemctl enable --now docker.service chrony.service
run timedatectl set-timezone America/New_York
if [ "$kiosk" -eq 1 ]; then
	[ -x /snap/bin/chromium ] || run snap install chromium
	run snap refresh --hold=forever chromium || warn "Couldn't hold Chromium's snap updates; see venue-box.md (Freeze)."
	if ! grep -Eqs "^[[:space:]]*AutomaticLogin[[:space:]]*=[[:space:]]*$kiosk_user[[:space:]]*$" /etc/gdm3/custom.conf; then
		warn "Auto-login for $kiosk_user isn't set: Settings → Users → $kiosk_user → Automatic Login (venue-box.md step 2)."
	fi
fi

# ---- 6. The image -----------------------------------------------------------------------------------------------------
step "Image $CANOPY_IMAGE:$tag"
image=$CANOPY_IMAGE:$tag
saved=$CANOPY_HOME/images/$CANOPY_IMAGE-$tag.tar.gz
existing_rev=""
if have_docker && docker image inspect "$image" >/dev/null 2>&1; then
	existing_rev=$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image" 2>/dev/null || true)
fi
if [ -n "$image_tar" ]; then
	[ -f "$image_tar" ] || die "There's no file $image_tar."
	if [ -f "$image_tar.sha256" ]; then
		sha256_matches "$image_tar" || die "$image_tar doesn't match its .sha256: copy it again."
	else
		soft "There's no $image_tar.sha256 next to it, so the image can't be checked."
	fi
	if [ -n "$existing_rev" ]; then
		say "Already on this box (commit $existing_rev): not loading it again."
	else
		run docker load --input "$image_tar"
	fi
	if [ "$dry" -eq 0 ]; then
		loaded_rev=$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image" 2>/dev/null) ||
			die "$image_tar doesn't hold $image. Use the file scripts/release.sh wrote for $tag."
		[ "$loaded_rev" = "$commit" ] || die "$image was built from commit $loaded_rev, but $tag is $commit. Use the right file."
		check_image_arch "$image" || die "$image_tar can't run on this box (above). Nothing was switched."
	fi
	if [ ! -f "$saved" ]; then
		run install -d -m 0700 "$CANOPY_HOME/images"
		run cp "$image_tar" "$saved"
		[ -f "$image_tar.sha256" ] && run cp "$image_tar.sha256" "$saved.sha256"
	fi
elif [ -n "$existing_rev" ] && [ "$existing_rev" = "$commit" ]; then
	say "Already built from $tag (commit $commit): not building again."
	[ "$dry" -eq 1 ] || check_image_arch "$image" || die "$image can't run on this box (above). Nothing was switched."
elif [ -n "$existing_rev" ]; then
	die "$image exists but was built from commit $existing_rev, not $tag's $commit. A tag names one build: make a new tag."
else
	say "Building from the tag's committed files (git archive $tag), a few minutes the first time…"
	if [ "$dry" -eq 1 ]; then
		say "  would run: git archive $tag | docker build --tag $image --label …revision=$commit -"
	else
		git -C "$repo" archive --format=tar "$tag" | docker build --tag "$image" \
			--label "org.opencontainers.image.version=$tag" \
			--label "org.opencontainers.image.revision=$commit" \
			--label "org.opencontainers.image.title=Color Canopy Conductor" -
		check_image_arch "$image" || die "The image built here can't run on this box (above)."
	fi
fi
if [ ! -f "$saved" ]; then
	# A copy of every release that has run here: rollback and the cold spare load it without building.
	run install -d -m 0700 "$CANOPY_HOME/images"
	if [ "$dry" -eq 1 ]; then
		say "  would run: docker save $image | gzip > $saved (+ .sha256)"
	else
		docker save "$image" | gzip >"$saved.partial" && mv -f "$saved.partial" "$saved"
		(cd "$(dirname "$saved")" && sha256sum "$(basename "$saved")" >"$(basename "$saved").sha256")
		chmod 0600 "$saved" "$saved.sha256"
	fi
fi

# ---- 7. Files ---------------------------------------------------------------------------------------------------------
step "Files: $CANOPY_HOME, helpers in /usr/local/sbin, systemd units"
run install -d -m 0755 "$CANOPY_HOME" "$CANOPY_HOME/bin" "$CANOPY_HOME/lib" "$CANOPY_HOME/docs" "$CANOPY_HOME/kiosk"
run install -d -m 0700 "$CANOPY_STATE" "$CANOPY_BACKUP_DIR"
run install -m 0644 "$here/docker-compose.yml" "$here/conductor.env.example" "$here/box.env.example" "$CANOPY_HOME/"
run install -m 0644 "$here/lib/common.sh" "$CANOPY_HOME/lib/"
run install -m 0755 "$here/lib/preflight.sh" "$here/lib/watchdog.sh" "$here/lib/kiosk-prestart.sh" "$CANOPY_HOME/lib/"
# The touchscreen opens this local page first; it moves on to /kiosk/ once the Conductor answers (kiosk-prestart.sh).
run install -m 0644 "$repo/web/kiosk-resting/index.html" "$CANOPY_HOME/kiosk/resting.html"
for helper in "$here"/bin/canopy-*; do
	name=$(basename "$helper")
	run install -m 0755 "$helper" "$CANOPY_HOME/bin/$name"
	run ln -sfn "$CANOPY_HOME/bin/$name" "/usr/local/sbin/$name"
done
run install -m 0644 "$repo"/docs/runbooks/*.md "$CANOPY_HOME/docs/"
run install -m 0644 "$here"/systemd/canopy-conductor.service "$here"/systemd/canopy-watchdog.service \
	"$here"/systemd/canopy-watchdog.timer "$here"/systemd/canopy-backup.service "$here"/systemd/canopy-backup.timer \
	/etc/systemd/system/
run install -m 0644 "$here/systemd/canopy.tmpfiles" /etc/tmpfiles.d/canopy.conf
run systemd-tmpfiles --create /etc/tmpfiles.d/canopy.conf
if [ "$kiosk" -eq 1 ]; then
	run install -m 0644 "$here/systemd/canopy-kiosk.service" /etc/systemd/user/
fi

# ---- 8. Data volume and compose check ---------------------------------------------------------------------------------
step "Data volume $CANOPY_VOLUME"
if have_docker && docker volume inspect "$CANOPY_VOLUME" >/dev/null 2>&1; then
	say "Exists: keeping it (sign-ins, registry overlay, slot hashes, enrolled devices, telemetry)."
else
	run docker volume create "$CANOPY_VOLUME"
fi
# Checked with the values step 9 will write (the shell's values win over box.env's).
CONDUCTOR_TAG=$tag CONDUCTOR_LAN_IP=$lan_ip run compose config --quiet

# ---- 9. Switch the release, then the services ---------------------------------------------------------------------------
step "Release and services"
# Only now: the image is on the box and checked, so whatever restarts the Conductor next can start it.
run set_env_value "$CANOPY_BOX_FILE" CONDUCTOR_LAN_IP "$lan_ip"
run set_env_value "$CANOPY_BOX_FILE" CANOPY_KIOSK_USER "$kiosk_user"
run switch_release "$CANOPY_BOX_FILE" "$tag"
if [ -n "$running_tag" ] && [ "$running_tag" != "$tag" ]; then
	say "The release goes from $running_tag to $tag (to go back: sudo canopy-restart --tag $running_tag)."
fi
run systemctl daemon-reload
run systemctl enable canopy-conductor.service canopy-watchdog.timer canopy-backup.timer
run systemctl start canopy-watchdog.timer canopy-backup.timer
if [ "$kiosk" -eq 1 ]; then
	run systemctl --global enable canopy-kiosk.service
fi
if [ "$spare" -eq 1 ]; then
	# The same hold as canopy-stop: neither the watchdog nor a reboot starts it until canopy-start at takeover.
	run touch "$CANOPY_HOLD"
	if [ "$dry" -eq 1 ] || conductor_active; then
		run systemctl stop "$CANOPY_UNIT"
	fi
	say "Cold spare: installed and held (break-glass hold); its Conductor stays stopped until takeover (sudo canopy-start)."
elif held; then
	say "Break-glass hold is on: not starting the Conductor. Hand it back with: sudo canopy-start"
elif [ "$dry" -eq 1 ]; then
	say "  would run: systemctl restart $CANOPY_UNIT, then wait for /healthz"
else
	systemctl restart "$CANOPY_UNIT"
	wait_healthy 180 || die "The Conductor hasn't answered after 3 minutes. Why: sudo canopy-logs service"
fi

step "Done"
if [ "$spare" -eq 1 ]; then
	cat <<DONE
This is the cold spare, on $tag, held. Keep it off the show network. Check: sudo canopy-status (hold on, release $tag).
Every release installed on the main box goes on this spare too (release-and-freeze.md).
Takeover: docs/runbooks/venue-box.md → The cold spare.
DONE
	exit 0
fi
cat <<DONE
The Conductor runs $tag on http://$lan_ip:8000/ (ops: /ops/, kiosk: /kiosk/).
Next (docs/runbooks/venue-box.md):
  sudo canopy-status             check everything
  sudo canopy-kiosk-enroll       once, with a Kiosk setup link from /ops
  docs/runbooks/install-day-commissioning.md for accounts, modules and slots.
DONE
