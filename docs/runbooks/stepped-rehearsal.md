# The stepped rehearsal (Gate R)

Stepped looks (D65) send each canopy module its next colour every few seconds through the Hubspace cloud. They
stay **off on real lights** (`motion.stepped.live_enabled: false`) until this rehearsal has measured, from the venue
IP, that Hubspace takes that write rate without throttling us. The rehearsal is one bounded stepped
run that only someone at `/ops` can start. The code is `conductor/rehearsal.py`, `CanopyEngine.rehearse`, and
`POST /api/canopy/stepped-rehearsal`.

**Since D79 (Oct 19) `live_enabled` is on:** Gate R passed on one kit (String light 1, Module 2, 10 min of Rainbow
Row Sunset #2 at 13 writes a minute). That doesn't measure the nine-kit rate: run step 2 below (nine kits, Rainbow Row
Sunset #2, 10 minutes) during install before tours, and if it fails, Alon turns `live_enabled` back off.

**Who:** Alon, or the on-duty tech with Alon on the phone, plus one observer standing under the lights.
**When:** never during tours or the event, and never with guests at the kiosk.
1. On the two VWBD test kits (Modules 1–2), from the Mac show box, on the bench account, as soon as convenient.
2. On the nine show kits, on the canopy account, during install week (Oct 20–22) before tours start.

**How long:** 10 minutes of running, plus 5 minutes to set up and read the report.

Nothing starts it automatically. No cron job, routine, CI job or restart starts it (07 §9). It runs only when someone
presses **Rehearse** in the confirm dialog on `/ops`.

---

## Before

- [ ] The 7 am checklist passes, apart from items you already know about. In particular: signed in, every module
      online, and the **Rate budget** card showing no recent 429s.
- [ ] Guest input is **on**. A pause holds every step, so the rehearsal refuses to start while guests are paused.
      It keeps the kiosk out by itself while it runs: guests see "Staff are testing the canopy's lights. Try again in
      a few minutes."
- [ ] No module is held on `/ops/module` or a bench job. A held module is left alone, so it isn't measured.
- [ ] The observer is under the canopy with a phone ready to film.

## Run it

1. `/ops` → **Controls** → **Stepped rehearsal (Gate R)**.
2. Choose the look. Every scene and option steps since D77 (eleven travel, five breathe, two shimmer), and each
   rehearses its own stepped rung in `config/modes.yaml`, with the copy guests would see. Any look can be rehearsed,
   the idle look (Lowcountry Calm #1) included (D75): the rehearsal checks that look's own rung for up to 10 minutes.
   It is not `motion.idle`'s 30-minute soak, which Alon runs separately with `live_enabled` on. If a look has no
   stepped design of its own in `config/modes.yaml` (none since D77, but a Lab edit can take one off), its
   module-level design is stepped at the D65 defaults: 6 steps per cycle, 6 s steps, forward. To watch level steps on
   the nine show kits (bench item 6: does a step read as a breath or a blink?), rehearse a level breath such as
   **The Goodnight Glow #1** or **Lowcountry Calm #2**. **For the run on nine, rehearse Rainbow Row Sunset #2**
   (`rainbow-row-sunset-2`, travel): its compiled steps change all nine modules every step, so it writes 9 PUTs a
   step, the worst case Gate R has to carry. Every travel option and The Goodnight Glow #1 (a breathe with
   `lag_steps: 0`) also write 9 a step. A lagged breath or a shimmer writes less, so a pass on one under-tests the
   worst case: from the compiled steps on nine, Coastal Tides #1 writes 4–9 a step, Lowcountry Calm #1 2–9, Calm #2
   and Goodnight #3 4–9, Garden Gathering #3 and Goodnight #2 3–9, and a small brightness step can round to no change.
   On the nine show kits the runner stretches the step to 7.5 s: at the full rate it paces all nine
   modules on one account at 72 writes a minute, the account's cap of 90 less a 20% reserve for 429 retries and
   re-sends (`motion.stepped.budget_reserve`).
3. Minutes: **10** for Gate R (1–10 are allowed; use 2 to try the page out).
4. Press **Rehearse…**. The dialog names every module it will write to, the account, the minutes and the write
   rate (paced at up to 72 a minute; a step that would leave the account less than one write per module of room
   under 90 in a minute is held back, D75). Read it.
   If a module is missing, cancel and fix that first. Press **Rehearse**.
5. The canopy card shows "Stepped look: step n of N · every 7.5 s · writes/min on account … (cap 90)". The line
   under the controls says when it ends. The kiosk's feature line says what the canopy is doing. For every scene
   (Lowcountry Calm, Rainbow Row Sunset, Garden Gathering, Coastal Tides, Golden Hour and The Goodnight Glow, each
   with its #2 and #3, D77) it is that look's own moving copy, the one guests would see. A travel look, or a breath
   that drifts its colours (Lowcountry Calm #1, Coastal Tides #1), says "Colour and dimming through the Hubspace
   cloud: each Hubspace strand set to a single colour and sent its next colour every few seconds, so …".
   A level breath or a shimmer (D75) says what it sends instead: "Colour and dimming through the Hubspace cloud: each
   Hubspace strand set to a single colour and sent a brighter or softer level every few seconds, so …", and a breath
   that does both says "Colour and dimming through the Hubspace cloud: each Hubspace strand set to a single colour
   and sent its next colour and a brighter or softer level every few seconds, so …".
   Only a look without a stepped rung of its own shows "Staff are testing the canopy: each Hubspace strand is sent
   its next colour every few seconds, so …".

It ends by itself after the minutes and the still look holds. It also stops early:
- at the **first 403** from Hubspace on any account. Nothing more is sent for it: no still-look burst. The canopy
  keeps the step it was on (the kiosk draws that), ops can't rehearse again and no stepped look runs on that account
  for `governor_cooloff_s` (5 min), and only then does the look's still design go out (to the modules that differ),
  unless a guest or the idle return chose a look first;
- when the **throttle governor** trips;
- when someone presses **Stop rehearsal**, pauses guest input, chooses another look or presses Safe look;
- when the reconciler moves the look down its ladder;
- when the Conductor stops.

A rehearsal that stopped early always fails. Its report still says what it measured.

## What the observer records

Write these down in the session log (`docs/current-live-test-session.md` or the install-day log) and attach the video:

- **Cut or fade (bench item 6):** at each step, does a strand jump straight to its new colour or fade into it? Does a
  fade blur the step into the next? For a breathe look (D75), does a level change read as a breath or as a blink?
  Note which: the moving copy never says "fade" until the bench says so.
- **Does the flow read?** Does the colour visibly move the way the design says ("from one edge", "diagonally")?
  Does it read as the ripple order from where guests stand, or as nine strands changing at random?
- **Start skew:** in one step, how far apart do the first and last strands change (in seconds, from the video)?
- Anything that flickers, goes dark or stays behind.

## Read the report

When it ends, `/ops` shows "Last rehearsal: … — pass/fail · report rehearsal-….json" under the controls, and the log
gets a "Stepped rehearsal: …" line. A fail also appears under Needs attention until someone acknowledges it. The full report is on the box at
`<CONDUCTOR_DATA_DIR>/bench/rehearsals/rehearsal-<UTC time>-<look>.json`. On the show box that is
`~/Library/Application Support/ColorCanopy/data`. A copy of the counts is in the telemetry as a `rehearsal` event.

**Pass criteria (all must hold):**

| Criterion | Report field |
|---|---|
| The run went its full length | `completed` |
| 0 commands failed throttled after retries | `failed_throttled` |
| Throttled attempts (429 + 403) under 5% of the rehearsal's attempts | `throttled_share` |
| No 403 on any account | `forbidden_403`, criterion `no_403` |
| At most 90 writes on the rehearsal's account in any 60 s, by send time (steps, retries, re-sends, everything) | `max_writes_60s` against `cap_writes_per_min`, criterion `max_writes_60s` |

Also look at these. They don't decide pass or fail on their own:
- `max_writes_60s` (also a pass criterion above): note the number for Alon. The runner plans its steps at 72 a
  minute, leaving 18 for retries and re-sends. In the simulator that was 72–81 with no faults and at most 82 with
  the pinned 429s and drops (12 seeds).
- `steps.budget_holds`: steps held back because they would have left the account less than one write per module of
  room under 90 (D75). Should be 0; anything
  else means something else was writing on the canopy account during the rehearsal.
- `verification_s`: p50 and p95 from send to verified readback. The step can't usefully be shorter than p95 plus
  about a second.
- `superseded` and `skipped_steps`: step writes replaced before they finished, and steps skipped because they came
  due late. Both should be 0 or close to it.
- `governor_trips` and `outages`.

A report with `"simulated": true` (made on a laptop or staging) **never counts for Gate R**.

## After

- **Pass on the show kits:** tell Alon, with the report file and the observer's notes. **Only Alon** sets
  `motion.stepped.live_enabled: true`, in a PR (since D77 every option has a stepped rung in `config/modes.yaml`;
  that setting is all that keeps them off the real lights). Nobody else flips it, whatever the report says.
- **Fail:** stepped motion stays off. Nothing else changes for the show. Send Alon the report. If the failure was a 403,
  check `/ops/accounts` before anything else. Don't run it again the same day without Alon.
- In both cases the canopy is back on the look's best still tier: its canvas or slot where that can run, else its
  still module-level design (D75; T3 on hardware before Gate C). It returns to Lowcountry Calm after the usual idle
  time, shown still (an idle return never starts a run unless `motion.idle` is on).
