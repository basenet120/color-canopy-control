# Releases, the freeze, hotfixes and rollback (the Mac show box)

**Who:** Alon cuts releases and installs them on the show box; the second on-duty tech can roll back.
**When:** freeze before the tours (Oct 23); after that only hotfixes. **How long:** a release 2 min; an install on
the box 5 min; a rollback about 2 min.

A **release** is a git tag `vYYYY.MM.DD-N` on a commit of `main` that CI passed (N counts releases that day: `-1`,
`-2`, …). The show box runs a dedicated checkout, `~/canopy-show`, switched to that tag (D61,
`mac-show-box.md` §5). There is no image to build or copy: `canopy-mac install` syncs the tag's pinned Python
environment (`uv sync --frozen`) and restarts the Conductor under launchd.

---

## Cut a release (Alon, laptop, 2 min)

- [ ] All the PRs for it are merged (merge commits) and CI is green on `main`.
- [ ] `git checkout main && git pull`
- [ ] `git tag -a vYYYY.MM.DD-N -m "<what's in it>"` on that commit, then `git push origin vYYYY.MM.DD-N`.

## The freeze (Alon, 15 min)

- [ ] Cut the release as above.
- [ ] Install it on the box (below).
- [ ] Set up the **cold spare** at the same tag, held (`mac-show-box.md` §6), off the show network.
- [ ] Record in `docs/STATUS.md`: the tag and its commit. That's the frozen show build.
- [ ] Freeze the box itself (no macOS or Homebrew updates, `mac-show-box.md` §1) and the staff phones' Hubspace app
      version.
- From now on the box only changes through a hotfix below. Railway staging keeps following `main`; that's fine,
  staging never holds show accounts.

## Install a release on the box (Alon, 5 min)

Do it outside public hours. Pause guest input on `/ops` first (the iPad rests). The paste block, on the Mac:

```
canopy-mac backup                                        # a restore point (copy it off the box too)
git -C ~/canopy-show status --short                      # must print nothing
git -C ~/canopy-show rev-parse HEAD | tee ~/canopy-prev-sha.txt
git -C ~/canopy-show fetch --tags origin
git -C ~/canopy-show checkout --detach <tag>
~/canopy-show/deploy/mac/canopy-mac install; rc=$?    # uv sync, then --check runs conductor.preflight, then restarts
if [ $rc = 3 ]; then  # the preflight rejected this checkout: nothing restarted, but any restart would start it
  prev=$(cat ~/Library/"Application Support"/ColorCanopy/state/installed_sha 2>/dev/null || cat ~/canopy-prev-sha.txt)
  git -C ~/canopy-show checkout --detach "$prev" && (cd ~/canopy-show && uv sync --frozen --no-dev --extra live --quiet)
fi                    # roll the checkout back at once (install printed the same line), then fix
canopy-mac status; git -C ~/canopy-show describe --tags --always
```

- [ ] `canopy-mac install` re-syncs the Python environment, runs `run-conductor.sh --check` — which runs
      `python -m conductor.preflight`: this checkout's code loads the live data folder (settings, the registry
      overlay, the saved iPad session) offline, read-only — and only then removes any hold and restarts the
      Conductor (about 30 s; the canopy goes to Lowcountry Calm). If the preflight fails, install exits 3 and
      nothing was restarted — **but the checkout is now the rejected code**: the running Conductor is a KeepAlive
      job, so a crash, the watchdog, a reboot or `canopy-mac start`/`restart` would start it. The paste block's
      `rc = 3` lines roll the checkout back at once (no restart needed; install prints the same line, naming the
      last installed commit). Roll back first, then read the preflight's message and fix. Any other non-zero exit
      came after the restart (or before the preflight, e.g. `uv sync`): read `canopy-mac status` and use Rollback.
- [ ] `canopy-mac status` → healthy; `git describe` names the tag (`/api/version` says `commit: local` on the Mac);
      `/ops` → run the 7 am checklist and a test mode change.
- [ ] Resume guest input.

## Hotfix path (during install week or the show)

1. **PR** with the fix → **CI** green (ruff, pytest, gitleaks, the Docker build and container smoke test) → Alon
   merges.
2. **Tag** the merge commit as above.
3. **Install on the box** with the paste block above. Never during public hours unless it fixes something guests
   can see; if so, pause guest input first and say so to the floor.
4. **The cold spare too:** switch its checkout to the same tag and `canopy-mac install` it, then
   `canopy-mac stop --yes` so it stays held, the same day.

## Rollback (anyone with the Mac's login, about 2 min)

- [ ] Switch back to the commit recorded before the install, and install it:
      ```
      git -C ~/canopy-show checkout --detach "$(cat ~/canopy-prev-sha.txt)" && ~/canopy-show/deploy/mac/canopy-mac install
      ```
      (or `git -C ~/canopy-show checkout --detach <previous tag>` when you know the tag).
- [ ] If the older release refuses the data (its preflight fails, or the Conductor doesn't come up: `canopy-mac
      logs` shows a config error about the overlay), restore the backup taken before the upgrade:
      `canopy-mac restore <file from before> --yes`.
- Tell Alon, and write what happened in `docs/STATUS.md`.
