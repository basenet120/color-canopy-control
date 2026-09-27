# Releases, the Oct 12 freeze, hotfixes and rollback

**Who:** Alon cuts releases (laptop) and installs them on the box; the second on-duty tech can roll back.
**When:** freeze **Mon Oct 12**; after that only hotfixes. **How long:** a release 5–10 min; install on the box
5–15 min; a rollback about 1 min.

A **release** is three things that name each other:
1. a git tag `vYYYY.MM.DD-N` on a commit of `main` that CI passed (N counts releases that day: `-1`, `-2`, …);
2. the image `canopy-conductor:<tag>`, built from exactly the tag's committed files, labelled with the commit;
3. its saved copy `dist/canopy-conductor-<tag>.tar.gz` plus `.sha256` (on the box: `/opt/canopy/images/`).

The box runs the tag named by `CONDUCTOR_TAG` in `/etc/canopy/box.env`. Nothing is ever pulled from a registry.

---

## Cut a release (Alon, laptop, 5–10 min)

- [ ] All the PRs for it are merged and CI is green on `main`.
- [ ] `git checkout main && git pull`
- [ ] `scripts/release.sh --dry-run` — shows the tag it will make.
- [ ] `scripts/release.sh` — tags, builds, writes `dist/canopy-conductor-<tag>.tar.gz` + `.sha256`, and prints the
      commit, image id and checksum. **It never pushes.** If the build fails it removes the tag again.
- [ ] `git push origin <tag>` so the box (and CI) can see it.

## The Oct 12 freeze (Alon, 30 min)

- [ ] Cut the release as above (e.g. `v2026.10.12-1`).
- [ ] Install it on the box (below). Keep the tarball + `.sha256` on the USB stick with the backups too.
- [ ] Record in `docs/STATUS.md`: tag, commit, image id, sha256. That's the frozen show build.
- [ ] Freeze the box itself (`venue-box.md` step 5) and the staff phones' Hubspace app version.
- From now on the box only changes through a hotfix below. Railway staging keeps following `main`; that's fine,
  staging never holds show accounts.

## Install a release on the box (Alon, 5–15 min)

Do it outside public hours. Pause guest input on `/ops` first (the kiosk rests).

- [ ] `sudo canopy-backup` (a restore point).
- [ ] **Either** build on the box from the tag:
      ```
      cd ~/color-canopy-control && git fetch --tags && git checkout <tag>
      sudo deploy/venue/install.sh
      ```
      **or** copy `dist/canopy-conductor-<tag>.tar.gz` and its `.sha256` to the box (scp over Tailscale, or USB) and
      load it instead of building (still from a checkout of the same tag, which supplies the helpers and units):
      ```
      sudo deploy/venue/install.sh --image-tar ~/canopy-conductor-<tag>.tar.gz
      ```
- [ ] The installer checks the tarball's checksum and that the image was built from that tag's commit, keeps a
      copy under `/opt/canopy/images/`, switches `CONDUCTOR_TAG`, and restarts the Conductor (about 30 s; the
      canopy goes to Lowcountry Calm).
- [ ] `sudo canopy-status` → the right tag, healthy; `/ops` → run the 7 am checklist and a test mode change.
- [ ] Resume guest input.

## Hotfix path (during install week or the show)

1. **PR** with the fix → **CI** green (ruff, pytest, gitleaks, the Docker build and container smoke test) → Alon
   merges.
2. **Tag:** `scripts/release.sh` on `main` (or `scripts/release.sh --from <hotfix branch>` when `main` holds changes
   that shouldn't go to the show yet — the branch must have passed CI too).
3. **Manual pull on the box:** install it as above. Never during public hours unless it fixes something guests
   can see; if so, pause guest input first and say so to the floor.

## Rollback (anyone with the box password, about 1 min)

Every release that has run on the box is still there (`docker images canopy-conductor`), and `box.env` remembers
the one before (`CONDUCTOR_PREVIOUS_TAG`).

- [ ] `sudo canopy-restart --tag <previous tag>` — switches `CONDUCTOR_TAG`, restarts, waits for the Conductor.
      (`sudo canopy-status` shows the current tag; `grep TAG /etc/canopy/box.env` shows both.)
- [ ] If the image isn't on the box any more: `sudo docker load -i /opt/canopy/images/canopy-conductor-<tag>.tar.gz`
      (or the copy on the USB stick), then the command above.
- [ ] If the older release refuses the data (the Conductor doesn't come up; `sudo canopy-logs service` or
      `sudo canopy-logs conductor` shows a config error about the overlay), restore the backup taken before the
      upgrade: `sudo canopy-restore /var/backups/canopy/<file from before>.tar.gz`.
- `--tag` switches only the Conductor. The helpers and units stay from the newer install; if they must go back
  too, check out the older tag and run `sudo deploy/venue/install.sh` again (it doesn't rebuild an image that
  exists).
- Tell Alon, and write what happened in `docs/STATUS.md`.
