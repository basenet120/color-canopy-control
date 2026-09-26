# Bench runbook — Prototype 01 kit

For Alon at the bench, on a phone. One kit, one table, Wi-Fi. About an hour the first time.
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

- **Terminal (preferred):** `python -m conductor.auth login` against the staging URL. It asks for the email,
  the password (never shown) and the code if one is needed. It talks to the running server; nothing is stored on
  your laptop.
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

On `/ops/bench`: pick **bench-kit**, then tap **Run** on each card, top to bottom. Each card says what to look for.
Steps appear live under the job. When it finishes you get a verdict and, for most jobs, questions with big
**Yes / No** buttons — answer them from what you saw. After each job, tap **Copy anonymized JSON** and paste it
into the build session (see section 7).

1. **Gate A — read-only profile dump.** Nothing changes on the lights.
   Pass = 3 slots with a 30-entry frame (or a fixed 60). Fail = "T3 only" (no per-puck path).
   Tap **Download profile (sqhdk03.json)** as well and send that file to the build session — it becomes the
   simulator's model of the real kit.
2. **Index order.** First, in the Hubspace app, paint slot 1: puck 1 red, 2 green, 3 blue, 4 white (the rest
   anything dim), and save. Then Run. It reads slot 1 (nothing written) and says whether the red puck is
   index 0 or the last entry. Answer whether the red puck is the one nearest the controller.
   While you're in the app: switch slot 1 through each effect and a few speeds, run Index order after each, and
   copy the JSON each time — that builds the effect-code table.
3. **Gate B — select a slot.** Film it. Pass = the strand switches to slot 2 within about 2 s.
   Answer "Did the strand switch…?". Yes proves T2 (store-and-select).
4. **Gate B — unplugged.** Switch the kit off at the wall (unplug the power supply), wait 10 s, tick
   **I've switched the kit off**, Run. It selects another slot and reads it back. Answer whether the kit was off
   the whole time, then **plug it back in**. The result says whether readback shows what the cloud stored or
   what the device did.
5. **Gate C — replay slot 1 into slot 2.** Film it. Pass = HTTP 200, slot 2 reads back identical to slot 1, and
   the lights show exactly slot 1's pattern. Answer "Do the lights now match…?". Yes proves T1 (frames by API).
6. **Hue ramp.** Every puck a different colour, running through the rainbow. **Take a clear photo of the whole
   strand** (it becomes the index map) and answer the question.
7. **Latency trials.** 20 selects, one every 3 s. Film the whole run with the clock in frame. The result gives
   send→ack and send→readback times; PUT-to-light comes from the video (the flash marks each send).
8. **Whole-strand looks.** Warmest and coolest white, 10% and 1%, HD orange, and a preset at slowest / middle /
   fastest. Each holds about 2 s. Film it and answer one question per look.
9. **Restore.** Puts the kit back to calm warm white 2700 K at 50%. Run it at the end of every session.

A job that says **ERROR** didn't run (device busy, not connected, not allowed). Nothing was changed; read the
message, fix that, and run it again. Only one job runs on a device at a time; `/ops/module` and a bench job
can't hold the kit at the same moment.

## 6. Apply flags (after Gates B and C)

When Gate B or C passes and you've answered its question **Yes**, the result card enables **Apply flags**
(`t2_select`, `t1_frames`, `readback_device_level`). Tapping it records what the kit proved in the ops overlay.
It stays disabled — with the reason shown — for simulated runs and until the matching answer is Yes.

## 7. Getting results back to the build session

The build session can't reach staging. For every job: **Copy anonymized JSON** on the result card, and paste it
into the session as a message. Ids, MACs, network names, emails, names and locations are replaced with
placeholders, so it's safe to paste. Send the Gate A profile download and your photos/videos the same way
(say which job each belongs to). The **Recent runs** list at the bottom reopens any earlier result.

From a terminal instead: `python -m conductor.bench runs` lists results, and
`python -m conductor.bench export <run id>` prints the same anonymized JSON.

## 8. From a terminal (optional)

With `CONDUCTOR_BASIC_AUTH=user:password` in the environment (or `--user NAME`, which asks for the password):

- `python -m conductor.bench --server <staging URL> list` — jobs, and whether the kit runs SIMULATED
- `python -m conductor.bench --server <staging URL> run gate_b --device bench-kit` — follows the steps live,
  asks the questions and posts your answers
- `--param k=v` sets a job option, e.g. `run latency_trials --param n=10 --param spacing_s=5`
- `python -m conductor.bench run gate_c --local` — tries any job on the simulator on your laptop, no server

## 9. `/ops/module` — hands-on single-module testing (after the P0b pull requests)

For you, or VWBD's crew on Prototype 01, once `/ops/module` exists.

- **Target a device.** Open `/ops/module`, pick the device. Opening it takes the device's lock (the show engine
  and bench jobs leave it alone); it's released when you leave or after 10 minutes idle.
- **Module map.** The 30 pucks drawn where they sit on the panel, in wire order, with what each is showing.
- **Paint + Apply.** Tap pucks to colour them, then Apply writes the frame into a slot and selects it. Per-puck
  writes (T1) are only offered once Gate C has passed on that kit.
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
