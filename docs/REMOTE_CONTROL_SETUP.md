# The Mac mini: a local Claude Code session you can drive from anywhere (Remote Control)

The Mac mini stays on next to the lights. Claude Code runs **on the Mac mini**: its files, its local network, and the
live control program on port 8000. You drive that session from anywhere through Remote Control, using claude.ai/code
in a browser, the Claude phone app (Code), or the Claude desktop app on another computer. Approval prompts come to
whichever device you're using.
Docs: https://code.claude.com/docs/en/remote-control

Unlike a cloud session (docs/CLOUD_SETUP.md), this one **can** reach the shop's devices, because it runs on the Mac
mini. So the hard rules in CLAUDE.md apply in full, starting with "never set HUBSPACE_LIVE outside the workshop
launcher's own runtime".

## One-time setup on the Mac mini (about 15 minutes)

1. **Accounts.** Install Claude Code (https://code.claude.com), then run `claude` once and sign in with your claude.ai
   account (Pro/Max/Team). Remote Control doesn't work with an API key. Install `git`, `gh` and `uv` (Homebrew), and run
   `gh auth login`.
2. **The repo.**
   ```bash
   git clone https://github.com/basenet120/color-canopy-control.git ~/color-canopy-control
   ```
   Then `cd ~/color-canopy-control && make setup`.
3. **Trust the folder.** Run `claude` once in `~/color-canopy-control` and accept "Trust this folder" and "Enable
   Remote Control". The answers are saved, except in your home folder itself, so always use the repo folder.
4. **Keep the Mac awake and reachable.** In System Settings:
   - Energy: prevent automatic sleeping; start up automatically after a power failure.
   - Lock Screen: never turn the display off, or a long time.
   - Users & Groups: automatic login for this user (Remote Control and the keychain need a logged-in session).
   - General → Software Update: don't install macOS updates automatically during show weeks.
5. **Start Remote Control and keep it running.**
   ```bash
   bash scripts/macmini_remote_control.sh install
   ```
   This starts `claude remote-control` now and at every login, and restarts it if it exits (for example after a long
   network outage). Check it with `bash scripts/macmini_remote_control.sh status`.
6. **Connect.** On any device, open claude.ai/code (or the Claude app → Code) with the same account. The Mac mini's
   session shows with a green dot. Server mode lets you start several sessions on the Mac mini from there.

**Permissions:** keep the default (approve as you go). Don't run it with bypass-permissions: this machine can reach
real lights and holds the workshop's Hubspace sign-in.

## Running the live program on the Mac mini

The workshop launcher holds the Hubspace sign-in in `workshop.env` and `workshop-data/` inside the repo folder, both
gitignored:

```bash
uv run python -m conductor.workshop setup
```

```bash
uv run python -m conductor.workshop run
```

- **Setup:** you type the account email; sign in on `/ops/accounts` yourself.
- **Run:** from a remote session, ask Claude to start it in a terminal tab, or add it as its own LaunchAgent.

Only one control program may drive the shop panels at a time, so stop the one on any laptop first.

## Is the Mac mini also the show machine?

Not yet. The venue box the project was built and tested for is a **Linux** mini PC: `deploy/venue` (systemd services,
watchdog, Chromium kiosk, installer) and docs/runbooks/venue-box.md are Linux-only. Using the Mac mini at the venue
means porting that deployment to macOS (launchd, the kiosk browser, the watchdog, UPS/NTP checks), plus a dry run
before install on Oct 20. That decision is needed now: install is a week away.
