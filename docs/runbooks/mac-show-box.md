# The Mac show box — set up, look after, replace

**Who:** Alon (set up); the second on-duty tech (look after). **What:** the show runs on a Mac mini (Apple silicon),
the show box (D61): one Conductor, live mode, the only place the show's Hubspace accounts are signed in, the lights
reached through the Hubspace cloud. The Conductor controls the canopy only (D67).
The guest panel is the iPad (`/kiosk/`), so the Mac doesn't need a kiosk screen.

---

## What's on the Mac

| Thing | Where | What it does |
|---|---|---|
| The show checkout | `~/canopy-show` (a clone at the release tag) | The code the show runs. Never the development checkout. |
| The Conductor | LaunchAgent `com.colorcanopy.conductor` → `deploy/mac/run-conductor.sh` | Starts at login, restarted by launchd if it exits |
| Its settings | `~/Library/Application Support/ColorCanopy/conductor.env` (0600) | Mode, allowlists, the ops login, the encryption key |
| Its data | `~/Library/Application Support/ColorCanopy/data/` | Sign-ins (encrypted), registry overlay, slot hashes, enrolled devices, telemetry |
| Watchdog | LaunchAgent `com.colorcanopy.watchdog` (every 30 s) | 3 failed `/healthz` checks in a row (2 min grace after a start) → restarts the Conductor |
| Backups | LaunchAgent `com.colorcanopy.backup` (04:30 nightly) | `…/ColorCanopy/backups/`, 14 days. The key is never in a backup. |
| Logs | `~/Library/Logs/ColorCanopy/` | `conductor.log` (kept to ~50 MB × 4), `watchdog.log`, `backup.log` |
| The helper | `~/canopy-show/deploy/mac/canopy-mac` | See the table at the end. `--help` lists everything. |

Tip: `alias canopy-mac=~/canopy-show/deploy/mac/canopy-mac` in `~/.zshrc`.

## 1. The Mac itself (Alon, 20 min, needs the admin password)

- [ ] **Wired Ethernet** to the show router, with a **DHCP reservation** so the address never changes. The guest iPad and
      staff phones open `http://<address>:8000/`. (The Mac also works on Wi-Fi, but don't run the show on it.)
- [ ] **Never sleep, start after a power cut:** System Settings → Energy → *Prevent automatic sleeping when the
      display is off* on, *Start up automatically after a power failure* on, *Wake for network access* on. Or in
      Terminal: `sudo pmset -a sleep 0 disksleep 0 autorestart 1 womp 1`. Check: `pmset -g` shows `autorestart 1`.
- [ ] **Automatic login.** The Conductor is a LaunchAgent: it starts when the user logs in. For it to come back by
      itself after a power cut, the Mac must log in by itself: System Settings → Users & Groups → *Automatically
      log in as* this user. **FileVault blocks automatic login.** Choose one, and write it on the ops card:
      - FileVault **off** + automatic login: comes back unattended after a power cut. The stored sign-ins are
        encrypted with a key that sits beside them anyway (07 §3), so keep the Mac physically secured.
      - FileVault **on**: after any power cut or restart, someone types the Mac's password at the screen before
        anything starts. The UPS makes that rare; it can't be done remotely.
- [ ] **Allow in the background:** after install, macOS says "Background Items Added". System Settings → General
      → Login Items & Extensions → *Allow in the Background*: leave the `canopy-mac` / `run-conductor.sh` / `bash`
      entries **on**. If they're off, launchd won't start the Conductor.
- [ ] **No surprise updates during the show:** System Settings → General → Software Update → Automatic Updates:
      everything off until strike. Same for Homebrew: don't `brew upgrade` (uv and the Python under `.venv`
      are what the show runs on).
- [ ] **Clock:** System Settings → General → Date & Time → *Set time and date automatically* on.
      `canopy-mac status` shows the offset from Apple's time server (should be well under a second). The 7 am
      checklist's *Clock synced* item can't see the Mac's NTP state yet and shows a warning; use `status`.
- [ ] **UPS** for the Mac, the show router and its switch. A USB UPS needs no software: System Settings → Energy
      (or Battery) → *UPS* shows it, and the Mac shuts down cleanly before the battery runs out and starts again
      with the power (*Start up automatically* above).
- [ ] **Remote access:** Tailscale (signed in with the show's own account) or macOS Screen Sharing/SSH for Alon and the second
      tech. **Never route the Mac's internet through an exit node or a VPN:** the traffic to Hubspace must come from
      the venue's own connection (never rotate IPs, CLAUDE.md).
- [ ] **Two Conductors never run at once.** Stop any Conductor on another laptop that could sign in to the same
      accounts (a laptop's `make run`): they would fight over the lights and over the sign-in.

## 2. Install (10 min)

```
git clone https://github.com/basenet120/color-canopy-control.git ~/canopy-show
cd ~/canopy-show && git fetch --tags && git checkout <release tag>      # release-and-freeze.md
deploy/mac/canopy-mac install --accounts canopy@…
```

`install` creates the settings file (it **generates** the ops login and the encryption key itself, so no secret is
ever typed or pasted), installs the Python environment (`uv sync --frozen --no-dev --extra live`; needs `brew install
uv` once), writes the three LaunchAgents, starts them and prints `status`. Running it again is safe: it keeps the
secrets and the accounts unless you pass `--accounts` again. It refuses to run from Desktop, Documents or Downloads
(macOS keeps LaunchAgents out of those folders).

Then:
- [ ] `canopy-mac status` — Conductor running, `/api/version` says `"mode":"live"`, watchdog and backup loaded.
- [ ] `canopy-mac password` (in Terminal on the Mac) — write the ops login on the ops card.
- [ ] From a phone on the show network: `http://<address>:8000/ops/` asks for the ops login and opens.
- [ ] Restart the Mac (Apple menu → Restart): after login the Conductor answers again within a minute.
- [ ] Pull the Mac's power plug, plug it back: it starts by itself (and logs in by itself if FileVault is off).

## 3. Accounts, modules, slots and the kiosk iPad

Follow `install-day-commissioning.md`. To sign in, run `canopy-mac login --account canopy` in Terminal on the
Mac. It asks for the email, the password (not shown) and the
emailed code if Hubspace sends one; the password goes once to the running Conductor and is never stored.
**Home Depot's written authorization (06 #20) comes before any write to a show account.**

## 4. Look after it

| What | On the Mac |
|---|---|
| Status (Conductor, watchdog, backups, clock, preflight) | `canopy-mac status` |
| Restart the Conductor | `canopy-mac restart` |
| Break-glass hold / hand back | `canopy-mac stop --yes` / `canopy-mac start` |
| Logs | `canopy-mac logs -f` |
| Backup / list them | `canopy-mac backup` / `--list` |
| Restore | `canopy-mac restore FILE [--skip-tokens] --yes` |
| Restart the box | Apple menu → Restart |

`stop --yes` is the break-glass hold: the Conductor stops and stays stopped, even after a restart or a login, until
`canopy-mac start`. The lights keep their last look (`break-glass.md`).

## 5. Update or roll back (5 min)

```
cd ~/canopy-show && git fetch --tags && git checkout <tag>
deploy/mac/canopy-mac install          # re-syncs the Python environment, runs the preflight, restarts; settings
                                       # and data are kept
```

`install` runs `run-conductor.sh --check` before it restarts anything, and `--check` runs `python -m
conductor.preflight`: the new checkout's code loads the live data folder (settings, the registry overlay, the saved
iPad session), offline and read-only. If it can't, install stops there and the running Conductor keeps the show
going (D67). The full paste block, with the backup and the previous commit recorded first, is in
`release-and-freeze.md`.

Rollback is the same with the previous tag. Never point the show at the development checkout or at a branch.

## 6. The cold spare

A second Mac (or the MacBook) set up the same way, with its **own** settings: `canopy-mac install --accounts …`
generates its own key and ops login (give it the same ops password by editing its `conductor.env` if you want one
card). Then `canopy-mac stop --yes` so it is held and can't run next to the main Mac.

Take over: unplug the main Mac's network; on the spare restore the newest backup **without the sign-ins**
(`canopy-mac restore <file> --skip-tokens --yes`; sign-ins are never copied between machines), sign in to each
account again (`canopy-mac login --account …`), re-enroll the kiosk iPad if its address changed,
and run the 7 am checklist.

## 7. Remove it

`canopy-mac uninstall` removes the LaunchAgents. The settings, data and backups stay in
`~/Library/Application Support/ColorCanopy/` until you delete that folder (which also deletes the stored sign-ins).
