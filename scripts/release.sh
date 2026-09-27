#!/usr/bin/env bash
# Cut a release: tag main as vYYYY.MM.DD-N, build the image from exactly the tagged files, and save it to
# dist/canopy-conductor-<tag>.tar.gz with a .sha256. Never pushes anything. docs/runbooks/release-and-freeze.md.
set -euo pipefail

usage() {
	cat <<'USAGE'
Usage: scripts/release.sh [--dry-run] [--from REF]

  1. checks the checkout: on main (or --from REF), no local changes, same commit as origin/main if that is known
  2. tags it vYYYY.MM.DD-N (today's date, N = the next free number today), an annotated tag, locally only
  3. docker build --platform linux/amd64 (the venue box's CPU, whatever this laptop is) from `git archive <tag>`
     (only committed files), labelled with the tag and commit; checks the image really is linux/amd64
  4. docker save → dist/canopy-conductor-<tag>.tar.gz and dist/canopy-conductor-<tag>.tar.gz.sha256

Nothing is pushed. To share the tag afterwards: git push origin <tag>
To put it on the venue box: copy both dist files over and run install.sh --image-tar (or check out the tag there
and run install.sh, which builds the same files).

  --from REF  release another commit than main (a hotfix branch that CI passed); still refuses local changes
  --dry-run   check and print the tag it would make, changing nothing
  -h, --help  this help
USAGE
}

dry=0
from=main
while [ $# -gt 0 ]; do
	case $1 in
	-h | --help) usage && exit 0 ;;
	--dry-run) dry=1 ;;
	--from) from=${2:?--from needs a branch or commit} && shift ;;
	*) usage >&2 && exit 2 ;;
	esac
	shift
done

die() {
	printf 'STOP: %s\n' "$*" >&2
	exit 1
}
root=$(git rev-parse --show-toplevel 2>/dev/null) || die "Run this inside the color-canopy-control checkout."
cd "$root"

commit=$(git rev-parse --verify --quiet "$from^{commit}") || die "There's no branch or commit called $from."
if [ "$from" = main ]; then
	[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || die "Check out main first (git checkout main && git pull)."
	if upstream=$(git rev-parse --verify --quiet origin/main); then
		[ "$upstream" = "$commit" ] ||
			die "main isn't the same as origin/main (the last fetch). Run git pull (or push what's merged), then try again."
	fi
fi
[ -z "$(git status --porcelain --untracked-files=no)" ] || die "There are local changes. Commit or stash them first."

day=$(date +%Y.%m.%d)
n=1
while git rev-parse --verify --quiet "refs/tags/v$day-$n" >/dev/null; do
	n=$((n + 1))
done
tag=v$day-$n
out=dist/canopy-conductor-$tag.tar.gz
image=canopy-conductor:$tag
# The venue box is x86-64 (venue-box.md). An Apple-silicon laptop would otherwise build linux/arm64, which passes the
# installer's checksum and commit checks and then dies on the box with "exec format error".
platform=linux/amd64

if existing=$(git tag --points-at "$commit" | grep -E '^v[0-9]{4}\.[0-9]{2}\.[0-9]{2}-[0-9]+$' | head -1) &&
	[ -n "$existing" ]; then
	printf 'Note: %s is already released as %s.\n' "${commit:0:12}" "$existing"
fi
printf 'Release %s = %s (%s)\n' "$tag" "${commit:0:12}" "$(git log -1 --format=%s "$commit")"
if [ "$dry" -eq 1 ]; then
	printf 'Dry run: would tag %s, build %s and write %s (+ .sha256). Nothing changed.\n' "$tag" "$image" "$out"
	exit 0
fi
command -v docker >/dev/null 2>&1 || die "Docker isn't installed here; the release needs it to build the image."

git tag --annotate "$tag" --message "Color Canopy release $tag" "$commit"
cleanup_tag() {
	git tag --delete "$tag" >/dev/null && printf 'Removed the tag %s again because the build failed.\n' "$tag" >&2
}
trap cleanup_tag ERR

git archive --format=tar "$tag" | docker build --platform "$platform" --tag "$image" \
	--label "org.opencontainers.image.version=$tag" \
	--label "org.opencontainers.image.revision=$commit" \
	--label "org.opencontainers.image.title=Color Canopy Conductor" -

arch=$(docker image inspect --format '{{ .Architecture }}' "$image")
if [ "linux/$arch" != "$platform" ]; then
	trap - ERR
	cleanup_tag
	die "The image came out as linux/$arch, not $platform. Is Docker's multi-platform build (buildx, QEMU) set up?"
fi

mkdir -p dist
docker save "$image" | gzip >"$out.partial"
mv -f "$out.partial" "$out"
(cd dist && sha256sum "$(basename "$out")" >"$(basename "$out").sha256")
trap - ERR

image_id=$(docker image inspect --format '{{ .Id }}' "$image")
cat <<DONE

Released $tag
  commit  $commit
  image   $image ($image_id, $platform)
  file    $out ($(du -h "$out" | cut -f1))
  sha256  $(cut -d' ' -f1 "$out.sha256")
Nothing was pushed. Next (docs/runbooks/release-and-freeze.md):
  git push origin $tag                                  # share the tag (Alon)
  copy $out and its .sha256 to the venue box, then:
  sudo deploy/venue/install.sh --image-tar $out         # on the box, from a checkout of $tag
DONE
