# Bench runbook — Prototype 01 kit

For Alon at the bench, on a phone. One kit, one table, Wi-Fi. About an hour and a half the first time.
Every job is one tap on `/ops/bench` (or one command in a terminal). Nothing here touches show hardware.

**Before you start**
- The kit is onboarded in the Hubspace app to the **dedicated bench account** (MFA off, 2.4 GHz network).
- The P0 pull requests are merged and staging has redeployed.
- You have two phones (or a phone and a laptop): one to run jobs, one to show the clock next to the kit.
- **Don't merge pull requests during a bench session.** A merge redeploys staging and restarts it mid-test.

---

## 1. Set up Railway staging (5 min)

Railway → project `color-canopy` → environment `staging` → service `conductor` → **Variables**. Set:

- `CONDUCTOR_MODE` = `bench`
- `HUBSPACE_LIVE` = `1`
- `HUBSPACE_ACCOUNT_ALLOWLIST` = the bench account's email (only that one — never a show account)
- `HUBSPACE_LIVE_UNTIL` = `2026-10-19` (after that date staging refuses every write)
- Leave `HUBSPACE_DEVICE_ALLOWLIST` **empty for now**: with it empty nothing can be written, which is what you
  want until you've seen the kit's id.

Already there from setup (check, don't change): `CONDUCTOR_BASIC_AUTH`, `CONDUCTOR_SECRET_KEY`,
`CONDUCTOR_DATA_DIR=/data`, `FORWARDED_ALLOW_IPS=*`.

Railway redeploys when you save. Wait for it, then open the staging URL and sign in with the ops password.
`/api/version` should say `"mode": "bench"` and `"live": true`.

## 2. Connect the bench account (5 min)

Either way works. Hubspace may email a 6-digit code — have the bench account's inbox open.

- **Terminal (preferred):** `python -m conductor.auth login --server https://<staging URL>`. Without
  `--server` it talks to `http://127.0.0.1:8000` (your laptop), not staging. It asks for the email, the password
  (never shown) and the code if one is needed. It talks to the running server; nothing is stored on your laptop.
- **Phone:** set `CONDUCTOR_ACCOUNT_CONNECT=1` on Railway, wait for the redeploy, open `/ops/accounts`, sign in.
  The form turns itself off after a successful connect; remove the variable afterwards.

Don't redeploy while a code is pending — a restart forgets the half-finished sign-in and you start again.

## 3. Register the kit and allow it (5 min)

1. On `/ops/accounts`, the bench account lists the devices it can see. Find the kit and register it as
   **bench-kit**. That saves its metadevice id in the ops overlay on the `/data` volume (never in git).
2. Copy that metadevice id into Railway → Variables → `HUBSPACE_DEVICE_ALLOWLIST`. Save and wait for the
   redeploy. The sign-in survives the restart.
3. Open `/ops/bench`. The device picker should show **Bench kit (bench-kit)** without "simulated", and the orange
   **SIMULATED** banner must be gone. If the banner is still there, the bench is on the simulator: re-check steps
   1–3 before going on — results from a simulated run prove nothing about the kit.

## 4. The clock on the second phone

Open `/ops/bench/clock/` on the second phone (sign in with the ops password), tap **Full screen**, and stand it
next to the kit so the camera sees both. The big digits are the server's time; the small line shows how far the
phone is from the server. Every time a job sends a write the screen flashes orange and shows
`PUT sent HH:MM:SS.mmm`. Film the kit and the clock together for every job that writes.

## 5. The jobs, in order

On `/ops/bench`: pick **bench-kit**, then tap **Run** on each card below, in this order (the commissioning cards VWBD
added are in 5a — they're for commissioning a kit, not this list). Each card says what to look for.
Steps appear live under the job. When it finishes you get a verdict and, for most jobs, questions with big
**Yes / No** buttons — answer them from what you saw. After each job, tap **Copy anonymized JSON** and paste it
into the build session (see section 7).

1. **Gate A — read-only profile dump.** Nothing changes on the lights.
   Pass = 3 slots with a 30- or 60-entry frame. Fail = "T3 only" (no per-puck path). The real kit reports
   **60** entries with 30 pucks attached — the controller's frame, not its pucks (CLOUD, Kingston, Sep 28 and Oct 2:
   `docs/bench-capability-check/gate-a-result.json`, `docs/vwbd-commissioning-transfer-2026-10-02.json`; see 5a).
   Tap **Download profile (sqhdk03.json)** as well and send that file to the build session — it becomes the
   simulator's model of the real kit.
2. **Index order.** First, in the Hubspace app, paint slot 1: puck 1 red, 2 green, 3 blue, 4 white (the rest
   anything dim), and save. Then Run. It reads slot 1 (nothing written) and says whether the red puck is
   index 0 or the last entry (on a 60-entry kit that's entry 59, which drives nothing on Kingston — see 5a). Answer
   whether the red puck is the one nearest the controller.
   While you're in the app: switch slot 1 through each effect and a few speeds, run Index order after each, and
   copy the JSON each time — that builds the effect-code table. Each result carries the slot's `header`
   (`motionEffect`, `motionSpeed`, `brightnessEffect`, `brightnessSpeed`, `brightnessDepth`, `id`, `version`,
   `headerFlags`, `sequenceFlags`, `frameBuffer.flags`) and `slot_headers` for all three slots, so say which
   effect and speed you picked in the app when you paste it.
3. **Gate B — select a slot.** Film it. Pass = the strand switches to slot 2 within about 2 s.
   Answer "Did the strand switch to slot 2…?" (the question names whichever slot you picked). Yes proves T2
   (store-and-select). It refuses to run — nothing sent — when that slot is already showing, or holds the same
   pattern as the slot that is showing, because you couldn't see a switch; pick another slot under Options.
4. **Gate B — unplugged.** Switch the kit off at the wall (unplug the power supply), wait 10 s, tick
   **I've switched the kit off** (in a terminal: `--param confirmed=1`), Run. It selects another slot and reads
   it back. Answer whether the kit was off the whole time, then **plug it back in**. The result says whether
   readback shows what the cloud stored or what the device did.
5. **Gate C — replay slot 1 into slot 2.** Film it. First it checks that slot 2 does *not* already hold slot 1's
   pattern (if it does, it stops with nothing sent: run **Hue ramp** with `slot=custom-2`, or paint slot 2 in
   the app, then run Gate C again — otherwise the lights would "match" even if the kit ignored the write).
   Then it switches the lights to a **BEFORE** look that isn't slot 1's pattern (slot 3, or plain warm white if
   slot 3 matches slot 1) and holds it about 4 s. Then one write copies slot 1 into slot 2 and selects it.
   Pass = HTTP 200, slot 2 reads back identical to slot 1, and you saw the lights **change** from the BEFORE
   look to exactly slot 1's pattern. Answer "Did the lights change from the BEFORE look to exactly slot 1's
   pattern?". Yes proves T1 (frames by API). If the lights never left the BEFORE look, answer No.
6. **Hue ramp.** Every puck a different colour, running through the rainbow. **Take a clear photo of the whole
   strand** (it becomes the index map) and answer the question. On a 60-entry kit the same rainbow repeats in entries
   30–59, so the photo can't tell which half drives the pucks: map that kit with the address probes (5a).
7. **Latency trials.** 20 changes, one every 3 s, between slot 2 and slot 3 (after Gate C and the hue ramp they
   hold slot 1's pattern and the rainbow, so every trial is a visible change). It starts with whichever of the
   two isn't showing. If the two slots hold the same pattern, it first writes a rainbow into one of them (only
   once Gate C is proven on this kit), otherwise it alternates a slot with plain warm white — the steps say
   which. At most 100 trials and 30 s spacing. Film the whole run with the clock in frame. The result gives
   send→ack and send→readback times; PUT-to-light comes from the video (the flash marks each send).
   **Stop this run** (next to the Running pill) stops any job that is going and frees the kit.
8. **Whole-strand looks.** Warmest and coolest white, 10% and 1%, HD orange, and a preset at slowest / middle /
   fastest. Each holds about 2 s. Film it and answer one question per look.
9. **Stale clock — a write stamped 10 minutes ago** (07 §10). Film it with the clock in frame. It selects another
   slot (one whose pattern differs from what is showing) with every row's `lastUpdateTime` set **10 minutes in
   the past** — what a venue box with a lagging clock would send — then reads back. If the cloud **stored** it, the
   result says so and asks whether the strand switched. If the cloud answered 200 but **dropped** it, a second
   select with a current stamp follows about 3 s later as the control, and the question is whether the lights
   stayed put after the first write and switched only after the second. Either answer settles the venue-box NTP
   question; send the JSON.
10. **Rewrite the showing slot — no reselect** (04 gate #6). A slot must be showing (after step 9 one usually
    is; if the result says none is, run the **Hue ramp** first). It rewrites the slot that is **showing** with a
    visibly different pattern (the rainbow, or the reversed rainbow if it already shows the rainbow) in one small
    write — no select, no colour-mode or power rows — and reads it back. Watch the strand and answer "Did the
    lights change to the new pattern while slot N stayed selected?". Yes = rewriting a showing slot re-renders
    it; No = every slot rewrite must be followed by a reselect. Refuses (nothing sent) when no slot is showing,
    and on a real kit until Gate C's flag is applied.
11. **Rate ramp** (04 gate #11). About 1½ minutes with the defaults: 10 frame writes into slot 3 at 5 s spacing,
    10 at 2 s, 10 at 1 s — never faster than one write a second — each read back and compared with what was sent.
    A 2xx whose readback doesn't show the frame counts as a **silent drop**. It **stops at the first 429/403**
    and never retries. Pass = everything stored with no throttling down to 1 write/s. Fail = Hubspace throttled
    (the result says at which spacing and write). `writes_per_step` (1–50, under Options) sets the length; the
    full 04 plan is 50. Also needs Gate C's flag on a real kit.
12. **Head count at boot — strings 2–3 unplugged** (07 §6, 04 gate #10). Read-only. Before running: switch the
    kit off at the wall, **unplug strings 2 and 3** (only string 1 stays on the controller), switch it back on and
    wait about 30 s for it to reconnect. Count the lit pucks. Tick **I've booted the kit with strings 2 and 3
    unplugged** (terminal: `--param confirmed=1`) and Run. It dumps the segment hints and all three slots' frame
    lengths (from the profile and from readback) and says whether the cloud noticed the missing pucks (it doesn't read `connected-light-count`; check that in the dump
    too). If it
    didn't, the 12-puck fault can only be caught by eye at the 7 am check. Answer whether only string 1 lit.
    Then switch the kit off, plug strings 2 and 3 back in, switch it on and run **Gate A** — it must report the same
    frame length as before (60 on Kingston).
    If Gate A reported 60-entry frames, set `full_heads` to 60 under Options.
13. **Restore.** Puts the kit back to calm warm white 2700 K at 50%. Run it at the end of every session.

A job that says **ERROR** stopped without a verdict: the device was busy, not connected or not allowed, a
check before the first write refused (the message says why), a read failed, or the run was stopped. **Read the
end of the message:** "Nothing was sent" means the kit wasn't touched; "N write(s) had already gone out" means
the kit may have changed (for example Gate C may already have rewritten slot 2) — look at the steps before you
run it again. Only one job runs on a device at a time; `/ops/module` and a bench job can't hold the kit at the
same moment.

## 5a. What VWBD found on the real kit (Sep 28 – Oct 7, 2026)

Paris ran these at VWBD from a local workshop server, not staging. Everything here is about **Kingston test**, the
kit registered as **bench-kit**, unless it says otherwise. The Sep 28 runs were made from another computer and matched
to the same controller on Oct 2 by an id digest (`docs/bench-commissioning-proof.json`,
`docs/vwbd-commissioning-transfer-2026-10-02.json`). PHYSICAL = seen on the lights by the person at VWBD; CLOUD =
Hubspace HTTP result or readback; SCREEN = on-screen preview only. Cloud or screen evidence never counts as physical.

**What the kit does**
- **60 entries, 30 pucks.** Every slot holds 60 entries (CLOUD, Gate A, Sep 28 and Oct 2:
  `docs/bench-capability-check/gate-a-result.json`, `docs/vwbd-commissioning-transfer-2026-10-02.json`); the kit
  has 30 pucks (confirmed by the person at VWBD, `docs/vwbd-commissioning-transfer-2026-10-02.json`).
- **The map.** Entries 0–29 drive pucks 1–30 counting from the controller end; entries 30–59 drive nothing
  (PHYSICAL, Sep 28): entry 0 lit only puck 1, entry 1 only puck 2, entry 29 only puck 30, and 30–59 left all 30
  dark (`docs/bench-capability-check/address-probe-*.json`); a `/ops/module` puck-15 marker and a 30-puck
  red → purple → blue gradient looked right (`docs/custom-pattern-session.md`). Not proven: every middle puck one
  by one, or the panel's 8/7/8/7 routing.
- **T1.** Gate C passed on Sep 28 (PHYSICAL: red → green when Custom 2 was copied into Custom 3;
  `docs/bench-capability-check/gate-c-result.json`, `docs/custom-pattern-session.md`).
- **Motion code 1** (`motionEffect` 1, speed 30, separated red/green/blue blocks, 20 s at 30%, Oct 2): "Blocks
  moved to different heads" (PHYSICAL); header and restore matched (CLOUD) (`docs/vwbd-effect-probe-2026-10-02.json`).
  A bounded pass only: vendor name, direction, period and phase are unknown, earlier rainbow tries looked still or
  flashed slightly (`docs/custom-pattern-session.md`), and `effects.enabled` stays empty.
- **No Gate B (T2) or unplugged-readback proof** exists for either shop module (`docs/bench-commissioning-proof.json`;
  for String light 1, Paris's package, `CONFIGURATION-NOTES.txt`).

**Where the map lives** (decision D54)
- Per kit, as `framebuffer_mapping` {`frame_length`, `indices`, `proven_on`} in the ops overlay — never in git. It
  only applies to the kit whose Hubspace id is `proven_on`, and only while the frame length and the 30-puck module
  map still match (`conductor/config.py`, `conductor/module/session.py`).
- A 60-entry kit without its own map gets no per-puck painting; `/ops/module` says why.
- No probe saves a map by itself. **Apply map** on an address probe's result card records it, from that kit's own
  two answered probes (decision D58; step 4 below). It replaces the offline `save_overlay_patch` edit with the server
  stopped (`VWBD-HANDOFF.md`).

**The commissioning jobs VWBD added** (cards on `/ops/bench`; `conductor/bench/jobs.py`; decision D55)
- **Gate C — bounded replay** (`gate_c_safe`). Replays a source slot (default Custom 3, only read) into Custom 1 or
  2 (default 1), never Custom 3, at a set brightness (default 30%). Holds the BEFORE look 4–60 s (default 4) and the
  replay 4–120 s (default 8), then restores the frame, the slot selection and the active look as separate commands
  and reads them back — anything not exactly as before is a FAIL. Same question as Gate C; a Yes enables Apply
  flags for `t1_frames` (section 6).
- **Address probe — bounded** (`address_probe_safe`). Custom 2 by default; refuses Custom 3. Lights `markers`
  (entry 0 red, 1 green, 29 blue — a 60-entry kit with 30 pucks only) or a range (`start`, `count`, one colour),
  everything else off, for 4–120 s (default 60) at 30% by default, then restores exactly. Needs that kit's own T1
  (Gate C applied), and the slot's pattern must have no effect running. It never saves a map itself. A markers run
  asks "Counting from the controller, was head 1 red, head 2 green and head 30 blue, with every other head dark?" A
  range wholly past the 30 heads (e.g. `start=30 count=30`) asks whether every head stayed dark (nothing lit). Both
  are Yes/No, and those two answers are what Apply map reads. Any other range asks how many heads lit and where.
- **Address probe — original** (`address_probe`): **retired** (D55). It defaulted to Custom 3 and left its pattern
  in the slot, so the bench no longer offers it. Use the bounded probe above.
- **Custom effect test** (`effect_probe`). One code (`motionEffect` or `brightnessEffect`, 1–255, speed 0–100) on
  a rainbow or separated blocks, 2–20 s at 30%; needs T1; Custom 3 by default. Afterwards it writes the slot's
  original pattern and header back, but the lights end on **that slot** at the previous brightness, not on the look
  that was showing before. It never enables or names a code.

**Commissioning another kit** (order from Paris's Oct 7 package, `INTEGRATE.txt` §8 and `CHANGELOG.txt`)
1. Fresh full backups of all three slots, and an observer at the lights who is ready before anything is sent.
2. That kit's own `gate_c_safe`, answered Yes from the lights (PHYSICAL), then Apply flags.
3. Its own bounded address probes, each answered from the lights: `address_probe_safe` with `pattern=markers`,
   then with `start=30 count=30` (pattern `range`). Count heads from the controller end.
4. **Apply map** on either result card. The card says what it will record, or why not. It shows only when all of
   these hold (D58):
   - Gate C is applied on this very kit;
   - this kit's newest markers run is answered **Yes**;
   - its newest 30–59 run is answered **Yes**;
   - both runs saw the same frame length, and the kit still reports it.

   It records exactly the plain map (entries 0–29 → heads 1–30, 30–59 unused), for this kit only, in the ops overlay
   (never git). It sends nothing to the lights. Any No, or markers anywhere else, means no map: tell Alon, and don't
   edit one in by hand. Changed your mind? Answering No on either run takes the map back.
5. Only then any paired or gradient test.

Never copy Kingston's flags or map to another controller. A whole-strand colour response or a 30-puck count is not
a map. (The one exception is the same physical controller registered again: compare the SHA-256 digest of its id
(UTF-8, whitespace stripped, no newline) with `device_id_sha256` in `docs/bench-commissioning-proof.json`, never
printing the id, and take a fresh Gate A first — `VWBD-HANDOFF.md`.)

Where the second shop module stands: **String light 1** (only in VWBD's private overlay, as `shop-string-1`) has no
physical Gate C pass and no map. Its latest `gate_c_safe` (Oct 6 evening, run `20261007T010607-gate_c_safe-30ed44`)
matched in the cloud and restored exactly, with no physical answer yet; an earlier one got "No—it did something
else… whitish pinkish" (Paris's package, `VALIDATION-STATUS.json`). The Studio's paired per-head gradient refuses
until each kit has its own T1 proof and its own recorded map (`conductor/module/scene_gradient.py`; decision D56),
so it has never been sent to real lights — only the on-screen preview has shown it (SCREEN).

## 6. Apply flags (after Gates B and C)

When Gate B, Gate C or `gate_c_safe` passes and you've answered its question **Yes**, the result card enables
**Apply flags** (`t2_select`, `t1_frames`, `readback_device_level`). Tapping it records what the kit proved in the
ops overlay, together with **which kit** proved it (its Hubspace id, `flags_proven_on`). It stays disabled — with
the reason shown — for simulated runs and until the matching answer is Yes.

- **Changed your mind?** Tapping **No** on that question after applying puts the flags back to what they were
  before (the card says "Undone…"), so `/ops/module` locks per-puck painting again. Tap Yes and Apply to redo it.
- **Swapped kits?** Flags only count for the kit that proved them. If a different kit is registered as
  `bench-kit`, its old flags don't carry over (nor does its map: `framebuffer_mapping.proven_on`), and Apply flags
  on a run from the old kit is refused — run the gates again on the new kit, in the order in 5a.

## 7. Getting results back to the build session

The build session can't reach staging. For every job: **Copy anonymized JSON** on the result card, and paste it
into the session as a message. Ids, MACs, network names, emails, names and locations are replaced with
placeholders, so it's safe to paste. Send the Gate A profile download and your photos/videos the same way
(say which job each belongs to). The **Recent runs** list at the bottom reopens any earlier result.

From a terminal instead: `python -m conductor.bench --server https://<staging URL> runs` lists results, and
`python -m conductor.bench --server https://<staging URL> export <run id>` prints the same anonymized JSON.
(Without `--server`, or `CONDUCTOR_URL` in the environment, the commands talk to `http://localhost:8000`.)

## 8. From a terminal (optional)

With `CONDUCTOR_BASIC_AUTH=user:password` in the environment (or `--user NAME`, which asks for the password):

- `python -m conductor.bench --server <staging URL> list` — jobs, and whether the kit runs SIMULATED
- `python -m conductor.bench --server <staging URL> run gate_b --device bench-kit` — follows the steps live,
  asks the questions and posts your answers
- `--param k=v` sets a job option, e.g. `run latency_trials --param n=10 --param spacing_s=5` (n ≤ 100,
  spacing ≤ 30 s); the unplugged test needs `run gate_b_unplugged --param confirmed=1`, and the head-count test
  `run head_count_boot --param confirmed=1`; `run rate_ramp --param writes_per_step=50` is the full 04 ramp
- `python -m conductor.bench --server <staging URL> stop <run id>` — stops a run that is going (Ctrl-C only
  stops following it)
- `python -m conductor.bench run gate_c --local` — tries any job on the simulator on your laptop, no server

## 9. `/ops/module` — hands-on single-module testing (after the P0b pull requests)

For you, or VWBD's crew on Prototype 01, once `/ops/module` exists.

- **Target a device.** Open `/ops/module`, pick the device. Opening it takes the device's lock (the show engine
  and bench jobs leave it alone); it's released when you leave or after 10 minutes idle.
- **Module map.** The 30 pucks drawn where they sit on the panel, in wire order, with what each is showing.
- **Paint + Apply.** Tap pucks to colour them, then Apply writes the frame into a slot and selects it. Per-puck
  writes (T1: paint, index sweep, single head) are only offered once Gate C has passed on that kit and, on a 60-entry
  kit, once its own map is recorded (5a) — so map a new 60-entry kit with the bench address probes, not the sweep.
- **Whole-module controls.** Power, white temperature, brightness, RGB, presets and speed for the whole strand.
- **Prototype 01 test cards** (one tap each): white 2700 / 3000 / 4000 K; HD orange, amber, peach and warm
  ivory; recipe compare (RGB-only vs RGB + warm white, side by side); thirds split for the Mylar zones; hue ramp;
  index sweep (one puck at a time, for mapping); single head.
- **Slots and effects.** Select, read or rewrite slots 1–3; start benched effect codes only.
- **Calibration.** Cable path, start corner and orientation; saved to the overlay.
- **Hold / photo mode.** Freezes the current look so nothing changes while you photograph.
- **Telemetry strip.** Every write with its time, HTTP result and whether readback matched.

**VWBD's crew get a tester login, not the ops password.** From `/ops`, create an enrollment link for role
**tester**, granting only the Prototype 01 device, and send it to their lead. Opened once on their phone, it
signs that phone in to `/ops/module` for that device only — no accounts, no bench, no other devices. Revoke it
from `/ops` when Prototype 01 is done.

## 10. End of session

1. Run **Restore**.
2. Copy anything you haven't sent yet.
3. Leave the Railway variables as they are between sessions (staging stops writing on its own after
   `HUBSPACE_LIVE_UNTIL`). Merging pull requests is fine again now.

**Never connect the show accounts (canopy and vignettes) to staging.** They live only on the venue box.
