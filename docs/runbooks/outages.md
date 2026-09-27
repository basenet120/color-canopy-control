# Outages — internet down, Hubspace errors, sign-in lost

**Who:** the on-duty tech; re-sign-in needs one of the two people who have the account password **and** its inbox.
**When:** whenever `/ops` or the kiosk says the canopy is resting, or `/ops` says *needs a new sign-in*.
**How long:** usually nothing to do (the Conductor recovers by itself within a minute of the cause going away);
a re-sign-in takes about 5 minutes.

**Who can sign in again** (fill in before Oct 20, keep on the ops card, not in git):
1. Alon — has the canopy account password and its inbox.
2. Second person: ______________________ — has the password and can open the inbox from their phone.

The **lights never go dark because of any of this.** Every module keeps showing its last look on its own; only
*changing* the look needs the internet and Hubspace.

---

## Internet down (or the show router lost its uplink)

What you'll see: the kiosk says **"The canopy is resting. It will be back shortly."**; `/ops` shows the canopy
resting; modules may show *offline*. The lights hold their last look (animated looks keep running on the modules).

- [ ] Nothing to do on the canopy or the box. Don't restart the Conductor, don't sign in again.
- [ ] Check the router / the venue's internet. Power-cycle the router only if it's the router (restart-matrix.md).
- [ ] When the internet returns, the Conductor notices within about 10 s and puts the current look back on every
      module within 60 s. The kiosk comes back by itself. (Tested on the simulator, including a 30-minute outage;
      not yet on hardware.)
- [ ] Longer than 15 minutes: tell Alon. Pause guest input so the kiosk stays calm.
- Break-glass doesn't help here: the Hubspace app needs the internet too (it can reach a module over Bluetooth
  from right next to its controller, one at a time — only if something must change).

## Hubspace (Afero) is failing — HTTP 500s, timeouts

What you'll see: the same as internet down, but the internet works (phones browse fine). The log tail on `/ops`
shows server errors or timeouts. There's no Hubspace status page; on Jul 27 2026 they were down for about 43
minutes.

- [ ] Same as above: wait. The Conductor probes one module every 10 s and re-applies the look when Hubspace
      answers again.
- [ ] Don't sign in again, don't restart anything, don't try another network or account.
- [ ] Hubspace phone support is closed on Sundays (Oct 25).

## Hubspace is throttling (429 / 403 / "rate budget" warnings)

- [ ] Pause guest input for a few minutes; let it settle. The Conductor already spaces writes (at most one per
      second per module) and backs off.
- [ ] **Never** work around it: no second account, no other network, no VPN or exit node.

## Re-sign-in required (the sign-in died)

What you'll see: `/ops` → Hubspace accounts → **re-sign-in required**; the 7 am check says *needs a new sign-in*.
The lights hold their look; the kiosk can't change them.

Causes: someone tapped *Sign out of all other devices* in the Hubspace app (it kills the Conductor's sign-in within
about 2 minutes), the password changed, Hubspace expired it, or a restore brought back an old one.

- [ ] Find one of the two people above. Have the account's inbox open (Hubspace may email a 6-digit code).
- [ ] On the box (keyboard, or `ssh` over Tailscale):
      ```
      sudo docker exec -it canopy-conductor python -m conductor.auth login --account canopy
      ```
      (vignette accounts: `--account living`, `bedroom`, `garden`, `entry`). It asks for the email, the password
      (not shown) and the code. The Conductor never retries a dead sign-in by itself.
- [ ] `/ops` → the account shows connected; **Re-assert all**; **Run** the 7 am checklist.
- [ ] Wrong code: it asks again. Too many attempts, or no code arrives: wait 5 minutes and start over.
- [ ] Nobody signs in on staging or a laptop: show accounts only ever sign in on the venue box.

## The box itself

- **Power cut:** the UPS rides through short ones. A long one: the box boots by itself when power returns (BIOS
  setting) and everything starts again in about 2–3 minutes (the kiosk shows "resting" once the desktop is up,
  until the Conductor answers), then the Conductor puts Lowcountry Calm on every
  module. (What a module shows by itself right after losing power — its last look or a default — is
  **UNVERIFIED** until the bench's power test.)
- **Conductor crashed or hung:** the watchdog restarts it after three failed checks (about 90 s). `sudo canopy-status`
  → Watchdog shows it. More than twice in an hour: `sudo canopy-logs service --since 2h` (the Conductor's output
  across restarts; `canopy-logs conductor` starts again at every restart, so it can't show why), call Alon.
- **Box dead:** `venue-box.md` → The cold spare.
