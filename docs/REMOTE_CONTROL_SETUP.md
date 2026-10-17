# The Mac mini: a local Claude Code session you can drive from anywhere (Remote Control)

The Mac mini stays on next to the lights. Claude Code runs **on the Mac mini**: its files, its local network, and the
live control program on port 8000. You drive that session from anywhere through Remote Control, using claude.ai/code
in a browser, the Claude phone app (Code), or the Claude desktop app on another computer. Approval prompts come to
whichever device you're using.
Docs: https://code.claude.com/docs/en/remote-control

Unlike a cloud session (docs/CLOUD_SETUP.md), this one **can** reach the shop's devices, because it runs on the Mac
mini. So the hard rules in CLAUDE.md apply in full: never set HUBSPACE_LIVE in a session, never bind port 8000 (the
show Conductor's), and never touch `~/canopy-show` or the show's data folder.

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
real lights and holds the show's Hubspace sign-in.

## Running the live program on the Mac mini

The Mac mini **is** the show box (D61). The live Conductor runs from its own checkout, `~/canopy-show`, at a release
tag, under launchd, with its settings and sign-ins in `~/Library/Application Support/ColorCanopy/`: see
`docs/runbooks/mac-show-box.md` (set up, look after) and `docs/runbooks/release-and-freeze.md` (the deploy paste
block). A Remote Control session works in the development checkout (`~/color-canopy-control` or a worktree) on the
simulator only: dev servers on other ports (`scripts/dev_design_lab.py --port 82xx`), never port 8000.

Only one control program may drive the lights at a time: never start a second Conductor that could sign in to a
show account.
