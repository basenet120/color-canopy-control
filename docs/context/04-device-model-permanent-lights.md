# 04 — Device model: the canopy strand (Hampton Bay SQHDK03-15PSL)

Evidence and sources: `research/permanent-lights/` (start with `_synthesis.md`, then `api-model.md`). Everything marked **UNVERIFIED** must be settled by the bench gates at the bottom before the show depends on it.

## Hardware
- A Home Depot–branded **Nanoleaf Permanent Outdoor Lights** kit (maker Nanogrid Ltd; Nanoleaf warranty doc, UPC prefix and Nanoleaf staff answering its Home Depot Q&A) running Hubspace firmware. Starter kit SQHDK03-15PSL (HD #330863181); expansion string SQHDE03-5PSL (10 pucks, 16.4 ft, HD #330863255, $29.97).
- **One kit = one Hubspace cloud device.** Inline controller: 2.4 GHz Wi-Fi + BLE, buttons power / brightness − / + / "M" next-scene, microphone for Party Mode, puck-count detection at power-up.
- 30 pucks on 3 strings × 16.4 ft × 10 pucks, **fixed 19.7″ (50 cm) spacing**. Up to 60 pucks / 6 strings per controller (still one device). Separate kits stay separate devices.
- Pucks: **hexagonal** with two screw ears (~35 × 32 mm body, ~2″ with ears — measure before VWBD cuts panel openings; the deck calls them octagonal). Each puck: 1 RGB LED + 3 warm-white + 3 cool-white LEDs; tunable white 2200–6500 K; CRI 80; 50 lm/puck in white. Saturated colors are much dimmer than whites; reviewers say orange, yellow and purple render weakly — matters for Rainbow Row Sunset and Golden Hour.
- Power supply 36 V DC / 1 A (36 W), polarized plug, **indoor/dry use only** per the HD manual. Manual forbids covered/recessed installs and enclosed spaces above 40 °C → the plenum above each panel needs ventilation.
- Cutting/splicing is physically possible (2 splicers included) but official guidance conflicts and cutting correlates with the "length detection failed / only the first 12 pucks light" failure → **loop the ~22 ft of slack per module above the panel; never cut.**
- 3-year warranty is **void in commercial use**; controllers and PSUs are not sold separately → buy spare kits. $99 Special Buy through Nov 3 2026 (regular $119).
- Reliability themes (4.1★, 114 reviews): controller/PSU failures, only first ~12 pucks lighting, single pucks stuck white, white flashes, schedule misfires, Wi-Fi dropouts. Reset: hold power ~10 s until three red flashes.

## Cloud profile (from sibling Hubspace addressable strings — SQHDK03's own profile is UNVERIFIED until a kit is dumped)

| attr | functionClass / instance | values |
|---|---|---|
| 1 | `power` | on / off |
| 2 | `brightness` | 1–100 (whole strand) |
| 3 | `color-temperature` | 2700–6500 step 100 on the sibling string (hardware goes to 2200 — check SQHDK's API range) |
| 4 | `color-rgb` | `{"color-rgb": {"r","g","b"}}` 0–255 (whole strand) |
| 5 | `color-mode` | the sibling string exposes white=0, color=1, sequence=2, **individual=4**; other devices add mixed=3, night-light=5, music-sync=6, circadian-rhythm=10 — read the list from each device |
| 6 | `color-sequence` / `preset` | firmware presets, e.g. christmas, rainbow, snowfall, mistletoe, prism, cross-faded, effervescent, fade-3/7, jump-3/7, custom (full list per device) |
| 11 | `speed` / `color-sequence` | −10…+10 (−10 slowest); **resets after power-off** |
| 12 | `color-individual` / `custom` | selects a slot: `custom-1` → 351, `custom-2` → 352, `custom-3` → 353; value hints carry geometry, e.g. `"20-segment"`, `"no-white"` |
| 31 | `toggle` / `party-mode` | on/off — keep off |
| 100 | `restore-values` | restored / not-restored / partial-restore |
| 300 | `color-sequence` / `custom` | named on-device programs: nightlight (2700 K 5%), dinner-party (2800 K 62%), chill (3200 K 40%), sleep (40→5% over 30 min), wake-up, focus, clarity… |
| 351–353 | `color-sequence-v2` / `custom-1..3` | **one object per saved pattern holding every puck's color + on-device animation parameters** |

### The per-puck pattern object (`color-sequence-v2`)
```json
{"color-sequence-v2": {
  "id": 0, "version": 1, "headerFlags": 128, "sequenceFlags": 0,
  "brightnessDepth": 100, "brightnessEffect": 0, "brightnessSpeed": 50,
  "motionEffect": 0, "motionSpeed": 50,
  "frameBuffer": {"flags": 0, "framebuffer": [
    {"r": 255, "g": 110, "b": 15, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0}
  ]}}}
```
- `framebuffer` index = position along the wire (index 0 presumably nearest the controller — verify). **Length = segment count** (20 on the Novolink string, 40 on the RGBIC tape; expected 30 — or a fixed 60 — on SQHDK03).
- Per-entry `cct` / `whiteBrightness` should drive the white LEDs on this RGBW product (the sibling dumps with `no-white` were RGB-only) — UNVERIFIED.
- `motionEffect` / `brightnessEffect` codes are unknown (0 = static; 6 observed elsewhere) — enumerate on the bench; keep them in config, never hard-code.

## Write shapes
**Full frame + select + mode + power in ONE PUT per module** (verified offline by running aioafero 9.3.0's own code with the network stubbed; ~3.4 KB for 30 entries):
```
PUT https://api2.afero.net/v1/accounts/{accountId}/metadevices/{metadeviceId}/state
Host: semantics2.afero.net      Content-Type: application/json; charset=utf-8
{"metadeviceId": "...", "values": [
  {"functionClass": "color-sequence-v2", "functionInstance": "custom-1", "value": {"color-sequence-v2": {…header…, "frameBuffer": {…}}}, "lastUpdateTime": <epoch ms>},
  {"functionClass": "color-individual", "functionInstance": "custom", "value": "custom-1", "lastUpdateTime": <ms>},
  {"functionClass": "color-mode", "functionInstance": null, "value": "individual", "lastUpdateTime": <ms>},
  {"functionClass": "power", "functionInstance": null, "value": "on", "lastUpdateTime": <ms>}]}
```
**Slot select only** (show-time, < 1 KB): the `color-individual` + `color-mode` rows (+ `power`).
**Whole-strand looks** use the same explicit-rows path. aioafero's `set_state` / `set_rgb` / `set_effect` show the wire encodings (color-temperature as a string, `{"color-rgb": {r,g,b}}`, programs as preset `custom` + color-sequence/custom `<name>`) but return nothing and silently adjust or drop values — see `07` §4.

### Gotchas (each one is a real bug someone hit)
- aioafero 9.3.0 has **no API for frames or slot select** (PR #61 is stale — assume it never lands). Build the rows by hand and send them through `bridge.request("put", …)` per `07` §4. `bridge.lights.update(id, states=[...])` also emits the right PUT, but its return value is ambiguous (`None` = HTTP 400 *or* exhausted retries *or* unknown device; other errors come back as response objects or exceptions).
- `lastUpdateTime` must be **epoch milliseconds** — seconds return HTTP 200 and are silently dropped (aioafero #65). `update()` stamps it for you; the `bridge.request` path doesn't.
- For re-asserts send the **full state vector** (defined in `07` §5) — `set_state`/`set_effect` skip fields that match aioafero's cache.
- Copy the header (`id`, `version`, `headerFlags`, `sequenceFlags`, `frameBuffer.flags`) and the framebuffer length **from the device's own readback**; change only colors and bench-verified effect fields.
- HTTP 2xx ≠ lights changed, and REST readback only proves the cloud stored the value (device-level application is UNVERIFIED — `07` §5). aioafero's Conclave path stores attr-351 pushes raw, so never trust the push cache for frames.
- aioafero's default session is `limit_per_host=3` → 9 module PUTs go out in 3 waves. Pass your own session (settings in `07` §4).
- aioafero does **not** split addressable strings into per-zone clones (only a `…-light-party-mode` switch clone); writes go to the parent metadevice id.
- A raw Afero attribute write exists (`POST /v1/accounts/{aid}/devices/{deviceId}/actions {"type":"attribute_write","attrId":351,"data":"<hex LE>"}`) — document it, don't build it; use only if the semantic PUT is rejected and only after a bench proof.

## Canopy control model
- 9 metadevices on one dedicated canopy account (vignettes on their own accounts). No group/scene/sync call exists, so **every mode compiles to exactly one PUT per module**: a burst of 9 concurrent PUTs over a warm session with a fresh token, then readback, then re-assert any module that differs (≤ 27 requests per change).
- **Store and select**: the three painted looks (Rainbow Row Sunset, Garden Gathering, Golden Hour) are written into slots custom-1..3 at commissioning; at show time the kiosk only selects slots. The reconciler hashes slot contents via REST and rewrites (or alerts) on drift — this also undoes any staff edits made in the app.
- All animation runs on each controller with no ongoing cloud traffic — but on its **own free-running clock**. Nothing phase-syncs the 9 controllers.
- **Fallback ladder per mode** (chosen by config/capabilities, not code): **T1** frames written by API + select → **T2** slots hand-painted in the Hubspace app (≈15–20 min/module) and selected by API → **T3** whole-strand CCT/RGB/preset, including module-level composition (each module = one coarse pixel). Kiosk copy follows the tier actually in use.

| Mode | Primary technique | Hubspace feature shown | Calls/change | Fallback |
|---|---|---|---|---|
| Lowcountry Calm | whole-strand white ~2700 K, 55–65% (or `dinner-party`/`chill` program if listed) | tunable white + dimming | 9 | RGB-mixed ivory |
| Rainbow Row Sunset | canvas gradient HD orange #F96302 → peach → warm white, stored in custom-1 | per-puck paint + saved patterns | 9 selects | T2 hand-paint → module rows orange/peach/white |
| Garden Gathering | brighter warm "pathway" meandering over a dim amber base, custom-2 | per-puck color + brightness | 9 selects | T2 → centre-row modules as pathway |
| Holiday at Home | firmware holiday preset (twinkle-type that looks fine unsynced), slow speed, ~70% | built-in holiday presets + speed | 9 | other preset / painted warm + red/green accents |
| Golden Hour | amber band per module in custom-3 with slow on-device motion, all 9 started in one tight burst, re-triggered to re-align; **or** a Conductor-stepped sweep (≥ 2 s/step, bounded) | custom pattern + animated effect + speed | 9 (stepped: 3–9 per step) | static amber + slow brightness effect ("amber glow", not "moving") → last resort supplemental light, never credited to Hubspace |
| The Goodnight Glow | whole-strand warmest CCT at 8–12% (optionally a 3-step dim-down) | dimming + warm white (+ nightlight/sleep program if listed) | 9 (+18) | RGB deep amber, low |

Golden Hour only reads as one wash if on-device motion runs the same direction in every module: **ask VWBD to route every module's cable column-wise, starting at the module's canvas-left edge (B modules included), so the index rises with canvas X.** Software can't reverse on-device motion.

## Bench gates — one kit, now (don't wait for Prototype 01)
1. **Gate A — read-only profile dump**: `fetch_metadevice` with expansions; commit the anonymized JSON as the SimDriver fixture. Record segment hint + framebuffer length, slot count, color-mode values (is `individual` present?), CCT range, preset + program lists, speed range, restore-values, party-mode.
2. App-paint an index-coded pattern (puck 1 red, 2 green, 3 blue, 4 white) into slot 1 → read back → confirm order and which end is index 0. Tabulate effect fields while switching effects/speeds in the app.
3. **Gate B — slot select** by API (color-individual + color-mode): visible switch ≤ 2 s, readback matches → proves T2. Then switch the kit off at the wall, PUT another select and GET: if the GET shows the new value, readback can't detect device-level misses.
4. **Gate C — replay write**: PUT the exact object read from slot 1 into slot 2 + select → HTTP 200, readback unchanged, lights match → proves T1.
5. Synthetic hue ramp (hue = 12°·i) → photograph for the index map; film all 30 changing in one frame; 20 trials of PUT-to-light latency.
6. Edge cases: 29/31/60-entry frames; per-puck white (cct 2700, whiteBrightness 100, colorBrightness 0); rewrite the active slot without reselecting; cut vs fade; order of rows within one PUT; a combined PUT starting from power-off; a PUT with lastUpdateTime = now − 10 min (does a lagging clock get writes dropped?).
7. Whole-strand + presets via API: CCT floor/ceiling, 1–10% dimming smoothness, HD orange, presets at speed −10/0/+10, programs.
8. On-device proof: start an animation, cut the WAN for 10–30 min — it must keep running.
9. Motion enumeration: sweep motionEffect/brightnessEffect codes; find a directional scroll, its slowest period, whether re-select restarts phase.
10. Power: pull mains in each mode × restore setting. Boot with strings 2–3 unplugged and dump the hints and all three framebuffer lengths — whether the cloud profile reflects the detected puck count at all is UNVERIFIED (the segment hint may be a per-model constant); if it doesn't, the 12-puck fault can only be caught visually.
11. Single-device rate: 50 frame writes at 5 s / 2 s / 1 s spacing; compare readback to sent to catch silent drops; stop at first 429/403.
12. With 2–3 kits: start skew (9-way-style parallel PUTs, 120 fps video, 20 trials) and drift at 10/30/60 min; chaining test (60 pucks on one controller: 60 independent entries or 30 repeated?).
13. On Prototype 01 with Mylar: color recipes (RGB-only vs RGB + warm-white blend) for amber, HD orange, peach, ivory at show brightness; 4 h thermal run; calibration in A and B orientation.
