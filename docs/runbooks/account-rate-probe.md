# Account rate probe — how fast can one Hubspace account be written? (D72)

The only published Afero limit is ~1 write/s **per device**. Nobody has measured an **account** limit: the 90 writes a
minute the stepped looks plan at (06 #27) is a planning number. Fast guest pacing (D72, `kiosk.fast_pacing`) assumes
there is no tighter account limit and backs off only when Hubspace actually throttles. This probe gives the first real
number: both of Modules 1–2 written at their per-device limit for 3 minutes ≈ **120 writes a minute** on the `bench`
account — 33% over the planning cap.

**Who runs it:** Alon (the ops password is yours to type; it is never stored). **When:** not during a client demo —
the kits flip between two looks once a second for 3 minutes. **Where:** on the Mac (the show box).

**Run it before `motion.stepped.live_enabled` is on.** The probe flips `garden-gathering` and `lowcountry-calm`
through ops. Both have stepped rungs now (D77), so with `live_enabled` on, a flip may start a stepped run instead of
sending one PUT per kit, and the count below no longer measures what it says.

## Run
```
read -s "P?ops password: "; echo
for i in $(seq 1 180); do
  if (( i % 2 )); then m=garden-gathering; else m=lowcountry-calm; fi
  curl -s -o /dev/null -w "%{http_code} " -u "ops:$P" -H 'content-type: application/json' \
    -d "{\"mode\":\"$m\"}" http://127.0.0.1:8000/api/canopy/mode
  sleep 1
done; echo; unset P
```
Each line of `200`s is one look change; ops changes bypass the guest pacing, so the only limiter is the executor's
1 write/s per device (each change is one PUT per kit, latest wins). Stop it any time with Ctrl-C. Afterwards the show
goes back to its look on its own (the idle return, or pick one on /ops).

## Read the result
Tell Claude "the probe ran" — or check `data/telemetry/<date>.jsonl` yourself: count `attempt` events in the 3
minutes (≈ 360 is the target), and any with `http_status` 429/403 (throttling), `outcome: throttled`, or
`readback` events whose `mismatched` rows persist (silent drops).
- **No 429/403, no persistent mismatch:** the account takes ≥ 120 writes/min → the 90 cap is not a hard limit;
  fast pacing's 2 s gap on nine modules (≤ 270/min while a guest mashes) is the next thing to try at install.
- **Throttled:** note the minute it started and the writes/min at that point; set `kiosk.fast_pacing: false`
  (the D70 budget rule) until the nine kits are measured, and record the number in 06 #28.
