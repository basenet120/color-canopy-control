# Working on the Color Canopy from any computer (Claude Code cloud sessions)

A cloud session runs on an Anthropic-hosted Ubuntu machine with this repo checked out. You start and reopen it from the
Claude Code app's Code tab (choose **Cloud** instead of **Local**), from claude.ai/code in a browser, or from the
Claude app on a phone. All of these run the same session, so you can start on one computer and carry on from another.
Docs: https://code.claude.com/docs/en/claude-code-on-the-web and https://code.claude.com/docs/en/cloud-environments

**What a cloud session can and can't do here:**
- **Can:** read and change the code, run `make test`, `make lint`, `make soak` and `make guest-dev`, all on the
  simulator; push `claude/*` branches; open pull requests.
- **Can't:** control real lights. A cloud machine can't reach the shop's Wi-Fi.
- **Must never:** be given Hubspace credentials, `HUBSPACE_LIVE`, or any show account (CLAUDE.md, 07 §1–§3).

Live work stays on a laptop next to the panels. That laptop runs the workshop launcher (`workshop/README.md`) with its
own private `workshop.env`.

## One-time setup (about 10 minutes)

1. **Give Claude access to the repo.** Install the Claude GitHub app on `basenet120/color-canopy-control`:
   https://github.com/apps/claude/installations/new → Only select repositories → `color-canopy-control`. Then sign in
   at https://claude.ai/code with the same account you use in the Claude Code app, and connect GitHub there.
   - If a session later says the repo is "not in this session's authorized repository set", the app isn't installed
     on this repo. Repeat this step.
2. **Create the cloud environment.** In the Code tab, pick **Cloud**. Then open the environment menu (cloud icon above
   the message box) → **Add cloud environment**, and fill it in:
   - **Name:** `canopy-dev`
   - **Network access:** **Trusted**. It covers PyPI, npm and GitHub, which is all the build needs. Don't pick Full: the
     code never needs the Hubspace/Afero cloud from here, and Trusted keeps it unreachable.
   - **Environment variables:** none. They are visible to everyone who uses the environment, and nothing here needs a
     secret.
   - **Setup script:** paste the contents of `scripts/cloud_env_setup.sh`. It runs once and is then cached.
3. **Paris (or anyone else).** Each person needs their own Claude account (Pro/Max/Team) with GitHub connected, and
   GitHub collaborator access to the repo. The Claude GitHub app installation covers the repo for everyone. Each
   person creates their own `canopy-dev` environment the same way, unless you set it up as a shared environment on a
   Team plan.

The repo does the rest. `.claude/settings.json` runs `scripts/cloud_setup.sh` at the start of every cloud session,
which runs `uv sync --frozen --all-extras`. On your own computer that script does nothing.

## Starting and moving between computers

- **New session:** Code tab → **Cloud** → repo `color-canopy-control` → branch `main` → environment `canopy-dev` →
  paste the starter prompt from `docs/CLOUD_SESSION_PROMPT.md`, followed by your task.
- **From a terminal:** `claude --cloud "<prompt>"`. It clones the branch from GitHub, so push your local work first.
- **Another computer:** open the same session from that computer's Code tab or at claude.ai/code; it's all one
  session.
- **Bring a cloud session's work onto a laptop:** `claude --teleport` (or `/teleport` in the session). It needs a clean
  `git status` and the branch pushed. The local copy doesn't sync back to the cloud.
- **Merging:** sessions push `claude/*` branches and open PRs. Alon reviews and merges on GitHub with merge commits. CI
  must be green; never auto-merge.
- **Stacked PRs:** if PR B is based on PR A's branch, retarget B to `main` before merging it. Otherwise it merges into
  A's branch and never reaches `main` (this happened with #12/#13; fixed by #14).

## Limits worth knowing

- 4 vCPUs, 16 GB RAM. A single command can run up to 10 minutes; the full test suite takes about 4 minutes and the
  soak about 30 seconds.
- `tests/test_deploy.py` is Linux-only, so it passes in the cloud (the cloud machine is Ubuntu, like CI) but fails on a
  Mac.
- `make e2e` needs the Playwright browser that `uv.lock` pins. Trusted network access can't download it (its CDN isn't
  on the list), so e2e stays a local or CI job unless you add Playwright's download host under Custom network access.
- An idle session pauses, and its machine may be reclaimed. Reopening it restores the conversation; the setup and the
  session-start hook run again as needed.

## Live hardware (not in the cloud)

On the shop laptop:

```bash
git pull
uv sync --frozen --all-extras
uv run python -m conductor.workshop run
```

Keep its `workshop.env` and `workshop-data/` (the private sign-in, never in git). Only one control program should
drive the shop panels at a time.
