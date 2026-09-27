# Strike — Nov 1

**Who:** Alon (accounts and data), the second on-duty tech (box), VWBD (hardware). **When:** Sun Nov 1, before VWBD
takes the canopy down. **How long:** about 1 hour of software work.

Order matters: keep the data you need, sign the box out while it's still running, then take the kits off the
accounts and clear the box.

---

## 1. Stop the show (5 min)

- [ ] `/ops` → **Pause guest input** (the kiosk rests).
- [ ] `/ops` → **Safe look** — a known, calm state for the last photos.

## 2. Keep what's needed (15 min, the Conductor keeps running)

- [ ] **Telemetry for the case study** (the command log: times, results, readbacks; no passwords or sign-ins):
      ```
      sudo canopy-backup --telemetry-only --to /media/<usb stick>
      ```
      The case-study permission is still pending: **publish nothing** and share it with no one outside the team
      until it exists. It contains Hubspace device ids; anonymize before any use outside the team.
- [ ] **Full data backup** (registry overlay, calibration, slot hashes, telemetry; the sign-ins inside are
      encrypted and stop working in step 3): `sudo canopy-backup --to /media/<usb stick>`. The stick goes to Alon.
- [ ] Bench results worth keeping: `/ops/bench/` → **Copy anonymized JSON** for each.

## 3. Sign the box out (10 min)

- [ ] `/ops` → **Set-up devices** → **Revoke** the kiosk and every tablet.
- [ ] `/ops/accounts/` → **Disconnect** each account: this deletes the box's stored sign-in for it.
- [ ] Stop the Conductor for good: `sudo canopy-stop --yes`. The lights keep their look until switched off.
- [ ] Hubspace app → each show account → **Sign out of all other devices** (now it's what we want: it ends every
      other sign-in), and change the account passwords if the accounts stay with us.

## 4. Kits off the accounts (20 min)

Per Home Depot's instructions for the hardware (their kits, their call):
- [ ] Switch the canopy off: the app's **Canopy** group → off, or VWBD switches the modules off as they de-rig.
- [ ] Hubspace app → canopy account → remove each module and spare kit from the account. Same for the vignette
      devices on their accounts.
- [ ] Factory-reset each controller if Home Depot asks: hold power about 10 s until three red flashes.
- [ ] Hand the QR cards back with the kits.

## 5. Clear the box (10 min)

```
sudo systemctl disable --now canopy-watchdog.timer canopy-backup.timer canopy-conductor.service
sudo docker rm -f canopy-conductor
sudo docker volume rm canopy-data
sudo shred -u /etc/canopy/conductor.env
sudo rm -rf /var/backups/canopy/*
sudo tailscale logout
```
- [ ] Remove the box from the Tailscale admin console.
- [ ] Delete the backups on the USB stick once the case-study data and the overlay are kept safely elsewhere (Alon's
      call); the telemetry export stays until the case-study decision.
- [ ] Railway staging never held show accounts; the bench account is Alon's call.

## 6. Return the hardware

- [ ] Kits and spares to Home Depot / VWBD as agreed, dead kits labelled.
- [ ] The box, the touchscreen, the tablets (Guided Access off), the router, the UPS, the staff phones (turn app
      updates back on).
- [ ] Update `docs/STATUS.md`: strike done, where the telemetry and backups are.
