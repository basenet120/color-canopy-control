# The venue box — build, look after, replace

**Who:** Alon (build); the second on-duty tech (look after). **When:** built and frozen by **Oct 8**, on site Oct 20.
**How long:** about 2 hours the first time, 20 minutes for the cold spare.

The show runs on one Ubuntu 24.04 mini PC at the venue. It is the **only** place the show's Hubspace accounts are
ever signed in. It starts everything by itself when it gets power: the Conductor (in Docker), a watchdog, the
nightly backup and the kiosk browser on the canopy touchscreen.

---

## What's on the box

| Thing | Where | What it does |
|---|---|---|
| The Conductor | Docker container `canopy-conductor`, started by `canopy-conductor.service` | Talks to Hubspace, serves `/kiosk`, `/ops`, tablets |
| Its data | Docker volume `canopy-data` (`/data` inside) | Sign-ins (encrypted), registry overlay, slot hashes, enrolled devices, telemetry |
| Its settings | `/etc/canopy/conductor.env` (root, 0600) | Mode, allowlists, the ops password, the encryption key |
| Box settings | `/etc/canopy/box.env` | Release tag, show-LAN address, kiosk user, backup options |
| Watchdog | `canopy-watchdog.timer` (every 30 s) | 3 failed health checks in a row → restarts the Conductor, logs it |
| Backups | `canopy-backup.timer` (04:30 nightly) | `/var/backups/canopy`, 14 days, optional USB copy |
| Kiosk | `canopy-kiosk.service` (user unit of `kiosk`) | Chromium full screen on `http://localhost:8000/kiosk/` |
| Helpers | `/usr/local/sbin/canopy-*` | See the table at the end. Every one has `--help`. |

---

## 1. Hardware and BIOS (15 min)

- [ ] Mini PC (x86-64, 8 GB RAM, 128 GB SSD or more), wired Ethernet to the show router, HDMI + USB to the
      canopy touchscreen, a USB keyboard kept in the tech kit.
- [ ] **BIOS: power on after AC loss.** Usually *Advanced → Power → Restore on AC Power Loss → Power On* (names
      vary: "AC Recovery", "After Power Failure"). Test it: pull the plug while running, plug it back — it must
      boot with no button press.
- [ ] BIOS: disable any "wake on" sleep states you don't need; leave Secure Boot as it is.
- [ ] **UPS** for the box, the show router and its switch. Either a UPS that simply rides through short blips (the
      box needs nothing), or one with USB and NUT (`sudo apt install nut`, driver `usbhid-ups`) so the box shuts
      down cleanly before the battery runs out; it powers on again by itself when mains returns (BIOS setting).
      A dead battery isn't a disaster: the lights keep their look, the box reboots when power is back.

## 2. Ubuntu (30 min)

- [ ] Install **Ubuntu Desktop 24.04 LTS** (minimal). Admin user: `canopy-admin` (a strong password, on the ops
      card). Timezone: New York.
- [ ] Settings → Users → add a standard user **`kiosk`** → **Automatic Login: on**. (The installer warns if it's off.)
- [ ] As `kiosk`, once, in a terminal (no sleep, no lock screen, no pop-ups on the kiosk):
      ```
      gsettings set org.gnome.desktop.session idle-delay 0
      gsettings set org.gnome.desktop.screensaver lock-enabled false
      gsettings set org.gnome.desktop.notifications show-banners false
      gsettings set org.gnome.desktop.a11y.applications screen-keyboard-enabled false
      ```
      Settings → Power → Screen Blank: Never; Automatic Suspend: Off.
- [ ] **Fixed address on the show network**, e.g. `192.168.50.10`: a DHCP reservation on the show router, or
      Settings → Network → Wired → IPv4 → Manual. Tablets and staff phones will use `http://<address>:8000/`.
- [ ] `sudo apt install git openssh-server`
- [ ] **Remote access: Tailscale.** `curl -fsSL https://tailscale.com/install.sh | sh`, then `sudo tailscale up`
      and sign in with the team's Tailscale account. Only Alon and the second on-duty tech are in the tailnet.
      From a laptop: `ssh -L 8000:127.0.0.1:8000 canopy-admin@<box's Tailscale name>` then open
      `http://localhost:8000/ops/`. **Never make the box an exit node or route its internet through one:** the
      box's traffic to Hubspace must come from the venue's own connection (never rotate IPs, CLAUDE.md).
- [ ] **NTP: chrony** (the installer installs it; it replaces systemd-timesyncd). The clock matters: a PUT whose
      timestamp lags may be accepted with HTTP 200 and then dropped (**UNVERIFIED** — bench test: a PUT with
      `lastUpdateTime` = now − 10 min, `04` edge cases). Check with `chronyc tracking` (System time within a few ms)
      and `timedatectl` (System clock synchronized: yes). The watchdog passes the sync state to the Conductor, so
      `/ops` → 7 am checklist → *Clock synced* shows it.

## 3. The Conductor's settings — secrets, by hand (10 min)

The installer never writes secrets. Get the repo onto the box (a read-only deploy key, or a `git bundle` on a USB
stick if GitHub isn't reachable), check out the release tag, then:

```
git clone <repo URL> ~/color-canopy-control && cd ~/color-canopy-control
git fetch --tags && git checkout v2026.10.12-1          # the frozen release (release-and-freeze.md)
sudo install -o root -g root -m 0600 -D deploy/venue/conductor.env.example /etc/canopy/conductor.env
sudo nano /etc/canopy/conductor.env
```

Fill in (each line is explained in the file):
- `HUBSPACE_ACCOUNT_ALLOWLIST` — the show accounts' emails, comma-separated.
- `CONDUCTOR_BASIC_AUTH` — `ops:` + the output of `openssl rand -hex 12`. Write it on the ops card.
- `CONDUCTOR_SECRET_KEY` — the output of `head -c 32 /dev/urandom | base64 | tr '+/' '-_'`.

Leave everything else as it is. Never copy this file off the box, never paste it anywhere.

## 4. Install (20 min, most of it the image build)

```
sudo deploy/venue/install.sh --lan-ip 192.168.50.10
```

It checks the box and the settings (and stops, saying why, if anything is off), installs Docker, chrony, curl and
the Chromium snap, builds `canopy-conductor:<tag>` from the tag's committed files, keeps a copy of the image in
`/opt/canopy/images/`, installs the helpers, runbooks (`/opt/canopy/docs/`) and systemd units, creates the
`canopy-data` volume, and starts everything. Run `sudo deploy/venue/install.sh --dry-run --lan-ip …` first to see
the plan. Running it again is safe.

Then:
- [ ] `sudo canopy-status` — Conductor active and healthy, watchdog and backup timers active.
- [ ] Reboot (`sudo canopy-restart box`) — everything comes back by itself; the kiosk shows the canopy page.
- [ ] Pull the power plug, plug it back — same result (BIOS setting).
- [ ] Enroll the kiosk: on a phone, `/ops` → **Set up a device** → Kiosk → name "Canopy kiosk" → **Create setup
      link** → Copy. On the box: `sudo canopy-kiosk-enroll`, paste, tap **Set up this device** on the touchscreen.
      (Accounts, modules and slots: `install-day-commissioning.md`.)

## 5. Freeze the box (5 min, after install)

- [ ] `sudo systemctl disable --now unattended-upgrades apt-daily.timer apt-daily-upgrade.timer` — no surprise
      upgrades or reboots during the show. (Re-enable after strike if the box is kept.)
- [ ] The installer already holds the Docker packages (`apt-mark hold`) and Chromium's snap
      (`snap refresh --hold=forever chromium`). Check: `snap refresh --list` shows nothing for chromium.
- [ ] Staff phones: turn off automatic updates for the Hubspace app (show-operation.md).

## 6. Backups and restore

- Nightly at 04:30: `/var/backups/canopy/canopy-data-<date>-nightly.tar.gz` + `.sha256`, kept 14 days.
  Set `CANOPY_BACKUP_USB=/media/usb` (a mounted stick) in `/etc/canopy/box.env` for a second copy.
- Now: `sudo canopy-backup`. List: `sudo canopy-backup --list`.
- **Restore** (1–2 min): `sudo canopy-restore /var/backups/canopy/<file>.tar.gz`. It checks the file, stops the
  Conductor, saves what's there now (`…-before-restore`), unpacks, and starts it again.
- The stored sign-ins in a backup are encrypted with `CONDUCTOR_SECRET_KEY`, which is **not** in the backup. A
  sign-in from last night may be stale (Hubspace replaces it every few minutes); if `/ops` says *re-sign-in
  required*, sign in again (`outages.md`).

## 7. The cold spare

A second mini PC (or a laptop) that can take over in about 20 minutes.

**Prepare it (before Oct 20):**
- [ ] Steps 1–4 above on the spare, with its **own** `conductor.env` (same settings, its own
      `CONDUCTOR_SECRET_KEY` and the same ops password) and the same `--lan-ip` as the main box. Install from the
      image tarball instead of building: copy `/opt/canopy/images/canopy-conductor-<tag>.tar.gz` and its `.sha256`
      from the main box, then `sudo deploy/venue/install.sh --lan-ip 192.168.50.10 --image-tar <file>`
      (add `--no-kiosk` on a laptop without the touchscreen).
- [ ] Then **stop it and keep it stopped:** `sudo canopy-stop --yes`, and keep it **off the show network**. Two
      Conductors must never run at once: they would fight over the lights and over the Hubspace sign-in.

**Take over (about 20 min):**
1. Unplug the main box (power and network). Plug the spare into its network cable, the touchscreen and the UPS.
2. Copy the newest backup from the USB stick (or the main box's disk) and restore it **without the sign-ins**:
   `sudo canopy-restore <file> --skip-tokens --yes` (sign-ins are never copied between boxes).
3. `sudo canopy-start`, then sign in to each account again (`outages.md` → re-sign-in), about 3 min each.
4. Tablets keep working: same address, and their enrollments came back with the backup. The kiosk browser on
   the spare is a new browser: enroll it (`sudo canopy-kiosk-enroll`, a new Kiosk link from `/ops`).
5. `/ops` → 7 am checklist, all items. Tell Alon.

## Helpers (all need `sudo`; all have `--help`)

| Command | What it does |
|---|---|
| `canopy-status` | Everything on one screen, including the 7 am checklist |
| `canopy-restart [conductor\|kiosk\|box] [--tag TAG]` | Restart one piece (restart-matrix.md); `--tag` switches release |
| `canopy-stop` / `canopy-start` | Break-glass: stop the Conductor and keep it stopped / hand back (break-glass.md) |
| `canopy-logs [conductor\|watchdog\|backup\|kiosk\|service]` | Logs, `--since 2h`, `--follow` |
| `canopy-backup [--to DIR] [--telemetry-only] [--list]` | Back up the data volume |
| `canopy-restore FILE [--skip-tokens]` | Put a backup back |
| `canopy-kiosk-enroll` | Sign the kiosk in with a one-time setup link |

On show days use these, not plain `docker compose` (they know the right files and never delete the data volume).
