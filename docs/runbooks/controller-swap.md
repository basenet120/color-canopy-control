# Controller swap — replacing a dead module's kit with a spare

**Who:** Alon or the second on-duty tech on `/ops`, VWBD's crew for the physical swap. **When:** a module stays
offline after one power cycle, or its controller or power supply has failed. Outside public hours if at all
possible. **How long:** 30–45 minutes of software work, plus however long the physical swap takes.

The spares (Spare 1–3) were onboarded to the canopy account and registered as `spare-1` … `spare-3` on install day,
and ideally passed Gates B and C then. If a spare wasn't onboarded, step 3 covers it.

---

1. **Pause guest input** on `/ops`. Tell the floor the canopy is resting for a while.
2. **Confirm it's dead:** Needs attention says *Module N offline*; one power cycle at its switch (restart-matrix.md)
   didn't bring it back within 2 minutes; its controller light is off or blinking.
3. **Physical swap (VWBD):** replace the controller and power supply with the spare kit's (or the whole kit, if the
   strings are the problem). Keep the strings in place if they're fine; **never cut a string**; plug all three
   strings in **before** powering the new controller (it counts pucks at power-up). Put the module's number label
   on the new controller.
   - Spare not onboarded yet: Hubspace app on the canopy account → add it (QR + Bluetooth, 2–5 min), name it
     **Module N**. It shows up on `/ops/accounts` as *Not registered*.
4. **Register it in place of the dead one:** `/ops/accounts/` → Canopy account → **Show devices** → find the spare
   (*Registered as spare-K*, or *Not registered*) → **Swap in as** / **Register as** → `Module N (canopy-rXcY) —
   replaces its current kit` → confirm.
   - The message says *Moved from spare-K to canopy-rXcY* and whether **its bench flags came with it** (they do if
     that kit passed Gates B and C) and whether **its controller map came with it** (it does if that kit was mapped,
     D57). The old kit's id is replaced in the ops overlay; with
     `HUBSPACE_DEVICE_ALLOWLIST=registry` the new kit is writable at once, the old one no longer is.
5. **Gates, only if the flags didn't come along** (5 min): `/ops/bench/` → Module N → Gate B → Apply flags; Gate C
   → Apply flags (`install-day-commissioning.md` step 5). Without them this module shows painted modes at module
   level only. **If the map didn't come along** on a 60-entry kit, map it per `bench-prototype-01.md` §5a
   (`gate_c_safe` → Apply flags → the markers and 30–59 `address_probe_safe` runs → **Apply map**). Until then its
   painted looks are whole-strand only.
6. **Recalibrate** (5 min): `/ops/calibrate/` → Module N → hue ramp → photo → check or flip the wire order → Save →
   close the page. A new controller may number the pucks from the other end. If a map came with the controller onto
   this module's strings, check the markers against it before trusting it.
7. **Recommission its slots** (2 min): `/ops` → **Commission slots…** → Commission. The new kit's slots are empty,
   so the old hashes no longer match; this writes and records them.
8. **Verify** (5 min):
   - [ ] **Re-assert all**, then show Rainbow Row Sunset, Garden Gathering and Golden Hour once each and look up.
   - [ ] 7 am checklist → **Run**: *9/9 modules online*, *Slot hashes* and *Head counts* PASS.
   - [ ] **Run test mode change**: all nine Ready.
9. **Resume guest input.**
10. **Tidy up:** in the Hubspace app, remove the dead kit from the canopy account (otherwise it lingers as an
    unregistered device). Keep the dead kit for Home Depot. `sudo canopy-backup`. Tell Alon which spare went where
    and update `docs/STATUS.md`.
