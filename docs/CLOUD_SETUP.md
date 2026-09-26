# Launching the cloud build session

Everything below works from a phone or a browser; the laptop is only needed if you want it.

## One-time setup (about 10 minutes)

1. **Give Claude access to the repo.** Install the Claude GitHub app on `basenet120/color-canopy-control` (https://github.com/apps/claude → Configure → Repository access). Connect GitHub at https://claude.ai/code if you haven't already.
2. **Create a cloud environment** at claude.ai/code → Environments → New, e.g. `canopy-build`. Default ("Trusted") network access, **no variables, no secrets** — the build runs entirely on the simulator. (Don't give it a Railway token: it would let unreviewed code deploy into the environment that holds the bench credentials.)
3. **Let merges deploy themselves.** Give Railway's GitHub app access to the repo (https://github.com/settings/installations → Railway → Configure → Repository access → add `color-canopy-control`). Then in Railway: project `color-canopy` → environment `staging` → service `conductor` → Settings → Source → Connect Repo → `basenet120/color-canopy-control`, branch `main`, and turn on **Wait for CI**. Don't enable PR environments (they'd copy staging's bench settings onto unreviewed code). Watch paths are already set in `railway.json`, so doc-only merges don't redeploy.
4. **Seal the staging secrets.** Railway → `conductor` → Variables → seal `CONDUCTOR_BASIC_AUTH` and `CONDUCTOR_SECRET_KEY`.
5. *Optional, if your GitHub plan supports it for private repos:* protect `main` so it requires the CI check before merging.

## Start the session
- **Browser or phone:** claude.ai/code → pick the repo → pick the `canopy-build` environment → choose **Opus 5.5** → paste everything below the line in `docs/ULTRACODE_BUILD_PROMPT.md`.
- **Terminal:** `claude --cloud "<the prompt>" --environment <environment id>`.

## While it runs
- It pushes to a `claude/*` branch and opens pull requests one at a time. Review and merge them on GitHub, **using merge commits** (not squash). CI must be green. Each merge that touches the app redeploys staging.
- Staging: https://conductor-staging-b94b.up.railway.app. Username and password are in `secrets/staging_basic_auth.txt` on the laptop, or in Railway → `conductor` → Variables → `CONDUCTOR_BASIC_AUTH`.
- If the session goes idle, reopen it at claude.ai/code and say "continue from docs/STATUS.md". `claude --teleport` pulls it onto the laptop.

## Bench kit (once the P0 pull requests have merged)
1. Onboard the kit in the Hubspace app to a **dedicated bench account** (MFA off) on a 2.4 GHz network.
2. On Railway staging set `CONDUCTOR_MODE=bench`, `HUBSPACE_LIVE=1`, `HUBSPACE_ACCOUNT_ALLOWLIST=<bench account email>` and `HUBSPACE_LIVE_UNTIL=2026-10-19`.
3. Connect the bench account: from the laptop with `python -m conductor.auth login`, or on your phone at `/ops/accounts` (enable it with `CONDUCTOR_ACCOUNT_CONNECT=1`; it may ask for a code Hubspace emails you).
4. Copy the kit's metadevice id from the ops page into `HUBSPACE_DEVICE_ALLOWLIST`.
5. Open `/ops/bench/clock` on a second screen next to the kit, then run Gates A, B and C from `/ops/bench` while filming both. Use "copy anonymized JSON" and paste the results into the session.
6. For hands-on testing — and for VWBD's Prototype 01 lighting tests — open `/ops/module` (once the P0b pull requests have merged). VWBD's crew can use it from their shop with a **tester** login, which reaches only that page and only the devices you grant.
7. Don't merge pull requests during a bench session — a redeploy restarts staging mid-test.

**Never connect the show accounts (canopy + vignettes) to staging or to a cloud session — they live only on the venue box.** Keep MFA off on show and bench accounts, and don't open the Hubspace app on show accounts during public hours.
