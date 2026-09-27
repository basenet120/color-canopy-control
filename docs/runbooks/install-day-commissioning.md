# Install-day commissioning — accounts, modules, slots, kiosk and tablets

**Who:** Alon, with VWBD's crew for the physical side and the second on-duty tech watching (they'll need to do
parts of this alone later). **When:** Oct 20–22, after the canopy is hung and powered; finished before Fri Oct 23.
**How long:** about 4 hours for nine modules and three spares (onboarding ~45 min, gates ~1 h, calibration ~1 h,
commissioning and checks ~30 min, enrollment ~20 min).

Nothing here happens on staging: show accounts are only ever signed in on the venue box.

---

## Before you start

- [ ] The venue box is built, frozen on the release tag and healthy (`venue-box.md`, `sudo canopy-status`).
- [ ] **Home Depot's written authorization** is in hand, or Alon has said go (`06` #20). Until then: no writes to
      show accounts.
- [ ] The canopy Hubspace account exists, MFA **off**, its email is in `HUBSPACE_ACCOUNT_ALLOWLIST`, and whoever
      signs in can open that account's inbox (for the emailed code).
- [ ] Two staff phones with the Hubspace app, **auto-update off** (`show-operation.md`).
- [ ] The show router is on, its **SSID, password and security (WPA2) are final**.

## 1. Freeze the Wi-Fi first (5 min)

Every kit stores the Wi-Fi name and password when it's onboarded, and changing them later means onboarding every
kit again, one at a time.
- [ ] Write the SSID/password/security on the ops card. From now on nobody changes them, or the router's band
      settings (2.4 GHz must stay on).

## 2. Onboard the kits in the Hubspace app (45–60 min)

One at a time, about 2–5 minutes each, phone next to the controller:
- [ ] Sign the phone's Hubspace app in to the **canopy** account.
- [ ] Add each module's kit (QR code + Bluetooth). Name it **Module 1** … **Module 9** — the same numbers as `/ops`:
      Module 1 is the top-left panel *as seen looking up* (the plan's top-left), counting left to right, row by row
      (Module 5 is the middle). Put a label with the number on each controller.
- [ ] Add the three spare kits the same way, named **Spare 1** … **Spare 3**. Keep them powered on a shelf on the
      show Wi-Fi, so they stay ready and testable.
- [ ] Make an app **Group** "Canopy" with the nine modules (not the spares): it's the break-glass control.
- [ ] Lock the QR cards away: scanning a kit's QR code from another account takes the kit over.

## 3. Connect the canopy account on the box (5 min)

On the box (keyboard, or `ssh` over Tailscale):
```
sudo docker exec -it canopy-conductor python -m conductor.auth login --account canopy
```
It asks for the email, the password (not shown) and, if Hubspace emails one, the 6-digit code. The password is used
once and never stored; the box keeps only an encrypted sign-in. `/ops` → **Hubspace accounts** shows it connected.

## 4. Register the modules and spares (15 min)

- [ ] `/ops/accounts/` → Canopy account → **Show devices**. Every kit shows up as *Not registered* and *Read-only*. (Ignore the page's note about adding ids to `HUBSPACE_DEVICE_ALLOWLIST` and restarting: the venue box uses `registry`, so no restart is needed.)
- [ ] For each module: **Register as** → `Module N (canopy-rXcY)` → **Register** → confirm. Use the name you gave it
      in the app to match numbers. For the spares: `Spare kit K (spare-K)`.
- [ ] `HUBSPACE_DEVICE_ALLOWLIST=registry` (already in `conductor.env`) makes every registered kit writable at once:
      each should now say **Writable**. Nothing else on the account can be written.
- [ ] `/ops` → **Devices**: 9 modules + 3 spares, none "Needs attention" for being unknown.

## 5. Bench gates on every module (about 5 min each)

The Conductor only uses a painted look (T1 frames, T2 slots) on a kit that proved it, and a proof belongs to the kit
that passed it (`D19`): **run the gates on all nine modules** (and the spares if there's time — a spare that
passed carries its flags into a swap). A kit that hasn't passed shows the painted modes at module level (T3).
Pause guest input if anyone is near the kiosk.

On `/ops/bench/` → pick **Module N**, film the module with the clock page if Alon wants footage:
- [ ] **Gate A** (read-only): 3 slots with a 30-entry frame. Copy the anonymized JSON for the build session.
- [ ] **Gate B** → answer the question → **Apply flags** (`t2_select`).
- [ ] **Hue ramp** into `custom-2` (only if Gate C says slot 2 already matches slot 1), then **Gate C** → answer →
      **Apply flags** (`t1_frames`).
- [ ] If Gate C fails on the platform (not just one kit), painted modes can't use frames: see the note at the end.

## 6. Calibrate each module's wire order (about 5 min each)

`/ops/calibrate/` walks you through it (the page has the steps):
- [ ] **Calibrate** on Module N → **Tests → Hue ramp** → photograph from directly below, top of the plan at the top.
- [ ] Red must be where the map shows head 0 and the colours must follow the wire line. If not: **Flip wire order**,
      or set start corner / cable path / orientation, **Save**, run the hue ramp again.
- [ ] Close the module page (it hands the module back to the canopy).

## 7. Commission the slots (5 min)

- [ ] `/ops` → **Commission slots…** → **Commission**. It writes Rainbow Row Sunset, Garden Gathering and Golden Hour
      into slots 1–3 of every module and records each slot's hash from Hubspace's readback.
- [ ] The result says "Wrote N slot(s)" with nothing needing attention (27 for nine modules × three slots). A module
      listed as needing attention: open it on `/ops/module`, check it's online, then Commission again (it only
      rewrites what's missing; a slot is written at most once per 10 minutes).

## 8. Verify (15 min)

- [ ] `/ops` → **Change** the mode to Rainbow Row Sunset, Garden Gathering and Golden Hour in turn, and look up:
      every module shows its slice, no module left behind.
- [ ] 7 am checklist → **Run**: every item PASS (see `seven-am-check.md`). *Slot hashes* passes once each painted
      mode has been shown once.
- [ ] **Run test mode change**: all nine Ready within 10 s.
- [ ] Walk under the canopy: all 270 heads light (a string that lights only 12 pucks is a known fault: check the
      connector, power-cycle that controller — never cut the strings).

## 9. Enroll the kiosk and the vignette tablets (20 min)

- [ ] Kiosk: `/ops` → **Set up a device** → **Kiosk**, name "Canopy kiosk" → **Create setup link** → **Copy**. On the
      box: `sudo canopy-kiosk-enroll`, paste, tap **Set up this device** on the touchscreen, press Enter.
- [ ] Each vignette iPad: **Set up a device** → **Vignette**, name it ("Living room iPad"), its vignette id →
      **Create setup link**. Open the link **on that iPad** (AirDrop or a message to it), using the box's address
      (`http://192.168.50.10:8000/…`), tap **Set up this device**. Then turn on Guided Access.
      (If the vignettes run the stock Hubspace app instead — `06` #11, decided in `docs/STATUS.md` — skip this.)
- [ ] `/ops` → **Set-up devices** lists them all. A lost tablet: **Revoke** there.

## 10. Wrap up (5 min)

- [ ] `sudo canopy-backup` and copy the newest backup to the USB stick.
- [ ] Hubspace app on the staff phones: signed out of the canopy account again, or at least closed (hands off during
      public hours — `show-operation.md`).
- [ ] Tell Alon the counts: modules registered, gates passed, slots written. Update `docs/STATUS.md`.

---

**If Gate C fails on the platform (T2, hand-painted slots).** The fallback is painting slots 1–3 in the Hubspace app
(about 15–20 min per module) and having the Conductor select them (Gate B). **Not buildable yet:** the Conductor has
no "record this hand-painted slot's hash" action, and it won't select a hand-painted slot without a recorded hash
(it can't tell a repainted slot from a staff edit otherwise). Until that exists, painted modes run at module level
(T3) and the kiosk copy says so. Tell Alon at once if Gate C fails.
