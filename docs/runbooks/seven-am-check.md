# The 7 am check

**Who:** the on-duty tech (Alon, or the second on-duty tech). **When:** every show day at 7:00, before doors
(Oct 23, 24, 25 and Oct 29), and after any restart or swap. **How long:** 10–15 minutes.

Open `/ops` on your phone (`http://<box address>:8000/ops/`, the ops password) → **7 am checklist** → **Run**.
Each item is PASS, WARN or FAIL, with the fix next to it. The same list is in `sudo canopy-status` on the box.
Work top to bottom: a failure near the top often explains the ones below it.

---

- [ ] **Clock synced** — the box's clock is set by the internet (NTP), and your phone agrees within a few seconds.
  - *FAIL, box not synced:* on the box `timedatectl` → "System clock synchronized: yes"? If not:
    `sudo systemctl restart chrony`, check the internet, wait 2 min, **Run** again. (A lagging clock may get
    Hubspace writes accepted and silently dropped — unverified, so don't ignore it.)
  - *WARN, phone differs:* your phone's clock is off; set it to automatic time. Harmless for the show.
  - *WARN, "can't see whether the box's clock is synced":* the Conductor can't read the box's clock state (on the
    venue box the watchdog passes it along every 30 s). Check `sudo canopy-status` → Box → NTP synchronized. If it
    says yes, carry on; if not, treat it as the FAIL above.

- [ ] **Sign-in: Canopy account** (and one line per vignette account) — the box is signed in to Hubspace and the
      sign-in refreshed in the last few minutes.
  - *FAIL "needs a new sign-in":* someone must sign in again with the password and the account's inbox open for
    the emailed code (`outages.md` → Re-sign-in). The lights hold their look meanwhile; the kiosk can't change them.
  - *WARN "hasn't refreshed":* watch it for 2 minutes (Run again). If it doesn't recover, sign in again.

- [ ] **9/9 modules online** — every module answers and shows its look.
  - *FAIL "Offline: Module N":* look at Module N's controller. Power-cycle it at its switch (restart-matrix.md),
    wait about a minute, **Re-assert all**, **Run** again. Still offline: `controller-swap.md`.
  - *FAIL "open on the module page by …":* someone left `/ops/module` open on that module; close that page (or
    wait 10 minutes: it lets go by itself).
  - *WARN "No confirmed readback yet":* press **Re-assert all** (or run the test mode change below).

- [ ] **Head counts** — each module reports its 30 heads. Hubspace may not report a dark string at all
      (unverified), so **also look up: all 270 heads lit.**
  - *FAIL:* check that module's three strings are plugged in, power-cycle its controller (it counts pucks when it
    powers up). Never cut a string.

- [ ] **Slot hashes** — the painted looks stored in each module (Rainbow Row Sunset, Garden Gathering, Golden
      Hour) are exactly what was commissioned.
  - *FAIL "differ":* someone probably edited a slot in the Hubspace app. Press **Commission slots…** (it rewrites a
    slot at most once per 10 minutes). If it keeps drifting, find out who has the app open.
  - *WARN "no readback":* show those three modes once each from the mode picker, then **Run** again.
  - *WARN "no recorded hash":* the slots were never commissioned on those modules: `install-day-commissioning.md`
    step 7. (Fine if those modes run at module level, T3.)

- [ ] **Rate budget headroom** — nothing is writing to Hubspace constantly, and Hubspace isn't pushing back.
  - *WARN/FAIL:* pause guest input, look at the log tail on `/ops`, and let the canopy settle for a few minutes.
    Never try to work around throttling (other accounts, other networks).

- [ ] **Test mode change** — press **Run test mode change**: it changes the mode once and times all nine modules to
      Ready (should be within 10 s), then puts the look back.
  - *FAIL:* the modules that didn't reach Ready are named; open each on `/ops/module`, then run it again.

- [ ] **Guest input** — the kiosk can change the canopy.
  - *WARN "paused":* press **Resume guest input** just before doors open.
  - *FAIL "the canopy is resting":* guest input is on but the kiosk can't change anything. Hubspace down → it comes
    back by itself (`outages.md`). Otherwise press **Safe look**; if that is refused too (the result line turns red
    with the reason), restart the Conductor (`restart-matrix.md`) and read the log tail.

**Needs attention** (top of `/ops`) also lists the engine's own alerts: a slot someone repainted in the app, a rewrite
that didn't take, a mode running at a lower tier, a commissioning run with problems. Each one links to its module;
press **Acknowledge** once it's handled (it comes back if it happens again). A mode back at its normal tier, a
successful rewrite or Hubspace answering again clears its line by itself.

**Also, by eye (2 min):**
- [ ] The kiosk shows the canopy page (not "resting"); tap one mood, the canopy changes, the kiosk says Ready.
- [ ] Each vignette tablet (or the stock app on it) loads and controls its room.
- [ ] `sudo canopy-status` → Backups: last night's backup is there.

All PASS (or WARN you understand) → doors can open. Anything else: call Alon.
