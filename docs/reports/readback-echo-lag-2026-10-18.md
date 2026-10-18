# The first readback after a scene change shows the old look (D71)

_2026-10-18. Read-only investigation of the live show box's telemetry
(`~/Library/Application Support/ColorCanopy/data/telemetry/2026-10-17.jsonl` and `2026-10-18.jsonl`, Modules 1–2 =
VWBD's kits on `bench`), plus a sim change on `claude/readback-echo-lag`. Nothing was written to a device. Nothing
was deployed._

## What happens today
A scene tap sends one PUT per module (ack 0.17–0.25 s). The executor's first verification GET goes out 1.0 s after
the ack (`readback_delay_s`) and answers about 1.2 s after it. On a scene change that GET often still shows the
previous scene's rows. The engine reads any differing first readback as a silent drop and re-sends the module
straight away (`_maybe_early_reassert`). That supersedes the command, so its own 2nd and 3rd readbacks never run.
The re-send's readback matches, and the module reads "ready" about 2.75 s after the tap.

## Answers

**1. Does the mismatch ever clear by itself on the command's own 2nd readback?** On scene changes it never had the
chance. All **35** live commands whose first readback differed (33 scene changes, 12 on Oct 17 and 21 on Oct 18, plus 2
guest brightness changes on Oct 18) ended `superseded` by the early re-send. Not one got a 2nd readback.
The only commands that kept their own readbacks are 8 overnight reconciler true-ups on one module (Oct 17 21:09–
Oct 18 01:01, after the early-re-send limit was used up). 5 of those 8 cleared by themselves on readback #2 or #3
(speed, color-mode ×2, power, brightness #3). 3 ended MISMATCH. That module was misbehaving that night (power and
color-mode drifting every few seconds), so these 8 don't show the daytime lag.

**2. Is it brightness only, and only when brightness changes?** Not brightness only. On first readbacks, Oct 17+18:
brightness + color-rgb 18, brightness 15, color-rgb only 11, color-mode 3, power 2, speed 1. The last three
classes come only from the overnight module. On scene changes it is
**whatever changed**: every daytime case was a change between scenes with different brightness and colour, or a
guest brightness change. Scene changes differed on 33 of 56 first readbacks (Oct 18: 21 of 38), guest brightness on 2 of 2. Re-asserts
differed on 7 of 43, and those 7 were never a first send of anything new. 2 were on the overnight module. 5 were on
Oct 17, inside chains of back-to-back re-sends right after a scene change (below).
On Oct 18, scene changes whose brightness changed were stale at the first GET 21 times and fresh 13 times.

**3. Stale echo, or a clamped or rounded value?** Echo lag. Three findings support it:
- The re-send writes exactly the same rows, and within a few seconds a readback always matches. A clamped or
  rounded value would never match. No silent drop was ever needed to explain a case.
- The same transition sometimes reads fresh at the first GET (13 of 34 brightness changes on Oct 18).
- First-GET answer times are the same for stale and fresh results (stale 1.11–1.27 s after the ack, fresh
  1.13–1.24 s). So the cloud's copy usually lands somewhere near +1.2 s, and on Oct 18 the re-send's readback at
  ≈ +2.6 s always found it.
- Re-sending sooner doesn't make it land sooner. On Oct 17 at 17:03 and 17:07, Golden Hour re-sent the same module
  up to 3 times, about 1.2 s apart. Each re-send's first GET was stale again, and the last module verified 5.7 s
  after the tap. Each re-send writes a new `lastUpdateTime`, which may restart the cloud's echo.

Telemetry did not record values, and the overlay holds no brightness, so "the old value" is inferred. The branch now
logs it: each `readback` event carries `differs: [{class, instance, sent, read}]` for every non-slot row, plus `n`
(which GET this is). The next live scene change will show `read` equal to the previous scene's value (lag), or a
nearby number (clamp). That settles it without touching hardware.

**4. The change (sim, on the branch).** A change's **first** early re-send now waits for the command's **2nd**
readback that still differs (`EARLY_REASSERT_FROM_READBACK = 2` in `conductor/canopy.py`). After one early re-send
for that module in that change, a differing first readback re-sends at once, as before. Nothing else changes: the
limits (3 per module, 1 burst for motion), MISMATCH → reconciler, slot drift and the outage path.
- **Echo lag (the usual live case):** no re-send. The module is verified on its own 2nd readback at ≈ ack + 2.2 s
  (send → verified ≈ 2.4–2.5 s, against 2.65–3.02 s via the re-send on Oct 18, median 2.77). It saves **one PUT per
  stale module per scene change** (35 over these two days, plus the Oct 17 chains' extra 5). On a motion look it saves a full nine-module re-trigger
  burst, which 07 §6 warns may restart phases.
- **A real silent drop:** re-sent at ≈ ack + 2.4 s instead of + 1.2 s, verified about 1.2 s later. Three drops in a
  row on one module still heal in ≤ 10 s, because the 2nd and 3rd re-sends go on the first readback
  (`test_repeated_drops_on_one_module_still_heal_within_10_s` measures 11.0 s if every re-send waits for readback #2,
  which is why this rule exists).
- **07 §5 stays honest:** "ready" still means a readback matched. The only thing that changed is when the engine
  treats a mismatch as a drop.

**About the "~1.3 s" target:** it isn't there to save. The value is not in the cloud at the first GET (≈ +1.2 s).
Verification can only come from a GET after it lands, and here that is the 2nd GET at +2.2 s. So the tap → ready
gain is ≈ 0.3 s, and the real win is the wasted write and burst. Getting closer to 1.5 s needs the lag
distribution, to pick a shorter `readback_interval_s` (for example 0.5 s → a 2nd GET at ≈ +1.7 s). That is the bench
step below. D69 already asked for the bench before readback timing changes.

## Bench step (needs hardware; Alon or VWBD, on the `bench` account, not during a show)
After the branch is deployed:
1. Change scenes on the guest panel between two scenes with different brightness (Lowcountry Calm 70 ↔ Golden Hour 95),
   10 times. Then in telemetry, check every `readback` with `n: 1` and a non-empty `differs`. Lag means `read` is the
   old scene's value. A clamp means `read` is a different nearby number. Also confirm a `re-assert:` label follows
   only a readback with `n ≥ 2`.
2. Measure the lag. On one module, PUT a brightness change, then poll GET every 0.25 s until it matches, 20 times,
   keeping to ≤ 1 write/s. If p95 < 1.5 s, propose `readback_interval_s: 0.5` (a 2nd GET at ≈ +1.7 s).
   If it's ≥ 2 s, leave 1.0.

## Verified
Sim only: `tests/test_readback_echo_lag.py`. In it the device applies 1.3–1.6 s after the write and readback shows the
device. A 60 → 70 brightness scene change costs exactly one PUT per module with no early re-send, every first GET
is stale, every 2nd matches, and the stale event's `differs` says `sent 70, read 60`. With the threshold set back
to 1, the test fails (9 re-sends). `test_each_steps_silent_drop_gets_its_own_early_re_send` now expects a drop
re-sent 2.0–3.6 s after the write (was ≤ 2.6 s). The other drop and self-heal tests pass unchanged.
