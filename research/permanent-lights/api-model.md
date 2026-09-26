# api-model (research pass 2026-09-26)

**HEADLINE:** On Hubspace addressable strings, all per-head colors live in one attribute per custom slot. The functionClass is color-sequence-v2, instances custom-1..3, attributes 351-353. Its value is {"color-sequence-v2":{...header..., "frameBuffer":{"flags":0,"framebuffer":[N x {r,g,b,cct,colorBrightness,whiteBrightness}]}}}. That means one /state PUT per module can carry the whole 30-head frame plus the slot select (color-individual=custom-N), color-mode=individual and power. aioafero 9.3.0 is still the latest on PyPI and has no method for this: you build the states by hand and send them with bridge.lights.update(id, states=[...]). I ran it offline and it sends exactly one PUT. Two things are unproven until Prototype 01: (1) that the cloud accepts a third-party write of this object (all public evidence is read-side), and (2) that the SQHDK03 profile exposes the same function classes with a 30-entry frame rather than 60.

## Findings
- [HIGH] **Per-head colors are stored as one object attribute per custom slot: functionClass 'color-sequence-v2', functionInstance custom-1 / custom-2 / custom-3 (custom-4 on some fixtures).**
  - Function definition: type 'object', schedulable false, a single value named 'color-sequence-v2' with deviceValues {type:'attribute', key:'351'|'352'|'353'|'354', format:'color-sequence-v2'}. The state value has the same shape in three independent dumps: {"color-sequence-v2":{"id":0,"version":1,"headerFlags":128,"sequenceFlags":0,"brightnessDepth":100,"brightnessEffect":0,"brightnessSpeed":50,"motionEffect":0,"motionSpeed":50,"frameBuffer":{"flags":0,"framebuffer":[{"r":0-255,"g":0-255,"b":0-255,"cct":0,"colorBrightness":100,"whiteBrightness":0}, ...]}}}. Encoding: a JSON list where the index is the position along the string. There is no count field inside the object; the list length equals the segment count. Observed lengths: 20 entries on Hampton Bay 'String Lights' (default_image novolink-20-segment-holiday-string-lights-icon), 40 on Ecosmart AL-TP-RGBICTW-3 (color-cct-ic-tape-light-40-segment-icon), 2 on the Leedarson 'Smart BT Speaker Flush Mount Light'. The RGBIC custom-1 frame is hand-painted: red, with 6 scattered segments in a blue gradient at indices 1, 2, 4, 20, 27 and 36. That shows arbitrary per-segment values, not just two-stop gradients.
  - src: github.com/Expl0dingBanana/aioafero tests/v1/device_dumps/light-with-speed.json and light-with-speaker.json; github.com/tpural/aioafero (branch include-more-effects, PR #61) tests/v1/device_dumps/light-rgbic.json; local clones in /private/tmp/claude-503/-Users-ns38-Documents-CODING-HUBSPACE-PROJECT/cd9cac9c-9277-4e5e-9f7f-c70810256dbf/scratchpad/clones
- [HIGH] **A stored slot is displayed with two small category writes: color-individual (instance 'custom', attribute 12) = 'custom-N', and color-mode (attribute 5) = 'individual'.**
  - The color-individual category maps each name to a device value: custom-1->351, custom-2->352, custom-3->353. Attribute 12 therefore holds a pointer to the slot attribute. The category 'hints' carry the geometry: ['20-segment'] on Novolink, ['40-segment','no-white'] on the RGBIC tape, ['2-section-gradient','no-white'] on Leedarson (which has 4 slots). color-mode enum across all dumps: white=0, color=1, sequence=2, mixed=3, individual=4, night-light=5, music-sync=6, circadian-rhythm=10. The RGBIC dump was captured while showing a painted frame, and its state then was color-mode 'individual' with color-individual 'custom-1'.
  - src: same dumps (functions[].values[].deviceValues/hints); PR #61 light-rgbic.json
- [HIGH] **On the client side, one /state PUT per module can carry the full frame plus slot select, mode and power. I verified this by running aioafero 9.3.0 offline.**
  - The metadevice state PUT body is {metadeviceId, values:[...]} with any number of functionClass/functionInstance/value rows. I ran aioafero's own LightController.update(device_id, states=[...]) against the Novolink dump, with the HTTP transport stubbed so nothing left the machine. It produced exactly 1 request: PUT https://api2.afero.net/v1/accounts/{aid}/metadevices/{metadeviceId}/state with headers host=semantics2.afero.net and content-type 'application/json; charset=utf-8'. The body had 4 values (color-sequence-v2/custom-1 object, color-individual/custom, color-mode/null, power/null), each stamped with lastUpdateTime in epoch ms. Body size is about 3.4 KB for 30 entries and 6.0 KB for 60. The code accepted the echoed object values without error. This proves the client side only.
  - src: aioafero src/aioafero/v1/controllers/base.py (update, update_afero_api, get_afero_states_from_list); harness /private/tmp/claude-503/-Users-ns38-Documents-CODING-HUBSPACE-PROJECT/cd9cac9c-9277-4e5e-9f7f-c70810256dbf/scratchpad/offline_put_harness.py
- [MEDIUM] **No public evidence shows the cloud accepting a third-party WRITE of color-sequence-v2; only the read shape is proven. The case for writes working is strong but needs a bench test.**
  - No public repo writes color-sequence-v2: GitHub code search for the functionClass and field names only finds the two identical aioafero/HA test dumps. In HA issue #148 (2025-05-12) the aioafero maintainer said zones are driven through color-sequence-v2's three slots, that HA can't display this, and that it would have to be a manual state set. He posted the read shape but did not show a successful write. In HA #224 (Feb 2026) a user said per-segment control worked but published no code. Evidence that writes should work: (1) aioafero already writes other object formats using the same {<format>:{...}} wrapper, in the exact shape the GET returns (color-rgb; security-sensor-config-v2 is written as {'security-sensor-config-v2':{chirpMode,triggerType,bypassType}}, identical to its read). (2) The Hubspace app edits these slots remotely; the current app binary targets /v2/accounts/.../metadevices/.../state.
  - src: github.com/jdeath/Hubspace-Homeassistant/issues/148 and /issues/224; aioafero models/features.py (ColorFeature, SecuritySensorConfigFeature) and tests/v1/controllers/test_security_system_sensor.py; github.com/wesen/2026-09-05--inspect-hubspace sources/hubspace-dart/metadata/selected-pool-entries.txt
- [HIGH] **aioafero 9.3.0 has no method for per-segment frames or slot selection. You must build the states yourself and send them with bridge.lights.update(id, states=[...]) (which returns the response) or bridge.send_service_request(id, states) (which returns None).**
  - LightController.set_state only handles on, temperature, brightness, color_mode, color, effect, numbers and channel. process_effects() reads only the color-sequence functions (instances preset and custom), and initialize_elem/update_elem ignore color-individual and color-sequence-v2 rows. States passed by hand skip the cache diff (dataclass_to_afero only diffs when obj_in is used) and are sent as given, with fresh ms timestamps. send_service_request raises DeviceNotFound for an untracked id and discards the result. LightController.update returns the aiohttp response, or None on HTTP 400 ('Invalid update'), exhausted retries, or an unknown device. A 403 propagates as HTTPForbidden. Open PR #61 (tpural, opened 2026-02-15, changes requested 2026-02-21, mergeable=false/dirty) would only add set_effect('custom-N') to select a slot (color-individual plus color-mode individual). It never writes frames and is written against the pre-8.0 feature API.
  - src: aioafero src/aioafero/v1/controllers/light.py, controllers/base.py, v1/__init__.py lines 964-975; github.com/Expl0dingBanana/aioafero/pull/61
- [HIGH] **aioafero does not split string lights into per-segment or per-zone Light clones; the segments live inside the framebuffer. This corrects the brief's statement that multizone string lights become per-zone clones.**
  - get_split_instances() splits only when a light has more than one non-primary brightness instance (e.g. main/trim) or has toggles. Running light_callback on the Novolink dump returns one extra resource, a Switch clone '{id}-light-party-mode', with remove_original=False. bridge.lights therefore keeps the parent metadevice id, and writes go to that id. Per-zone clones apply to main/trim fixtures such as 'Recessed Light With Accent Ring', not to addressable strings.
  - src: aioafero controllers/light.py get_split_instances/light_callback; offline harness output
- [MEDIUM] **Built-in animations run on the controller after a single write. Custom-slot patterns also carry on-device motion and brightness effect parameters. Nothing suggests the 9 modules can be synchronized with each other.**
  - A preset is one category write to attribute 6. Values: christmas 10000, rainbow 10002, under-the-sea 10010, red-shift 10012, rainbow-road 10017, mistletoe 10018, prism 10023, rainbow-rudolf 10025, penguin-disco 10027, rainbow-roll 10036, cross-faded 10037, hanukkah 10039, snowfall 10042, halloween 10045, effervescent 10046, fade-7 302, fade-3 303, jump-7 304, jump-3 305, custom 300. Speed is attribute 11, range -10..+10. The manual says the controller's Next Scene key cycles scenes without the app. Slot headers include motionEffect, motionSpeed, brightnessEffect, brightnessSpeed and brightnessDepth; the Leedarson dump shows motionEffect 6 with motionSpeed 49. PCWorld describes per-pattern effects as animated variations in motion and brightness. Together this points to firmware-side animation of custom frames; this is an inference, and the enum values are unknown. No profile field suggests multi-device phase sync, so each of the 9 modules would start animating when it receives its own command. HA #205 reports that the app's preset speed resets to normal after power-off, so re-send speed together with power-on.
  - src: light-with-speed.json; light-with-speaker.json; https://images.thdstatic.com/catalog/pdfImages/55/55b7c72a-79f1-4dbb-bd4d-f51b0d61538f.pdf; https://www.pcworld.com/article/2504896/hampton-bay-permanent-string-lights-review.html; github.com/jdeath/Hubspace-Homeassistant/issues/205
- [HIGH] **SQHDK03-15PSL is one Wi-Fi controller driving 30 addressable RGBWIC pucks (3 sections of 16.4 ft, 10 pucks each). It expands to 60 pucks / 100 ft and detects string length automatically.**
  - From the Use & Care guide (Model SQHDK03-15PSL item #1012 049 433; extension SQHDE03-5PSL item #1012 049 434, one 16.4 ft string): package is 3 strings totalling 50 ft and 30 pucks, controller, power adaptor, 9.8 ft extension cable, 2 splicing tools, 70 screws, 16 cable clips. Max 60 pucks / 6 strings per controller. Output 36 V DC 1 A, 36 W. CCT 2200-6500 K. 50 lm per bulb. Wires can be cut between pucks and re-joined with the male/female splicers. Controller keys: power, brightness -/+, Next Scene. Party Mode (app only) enables the controller's microphone. Troubleshooting lists 'Length detection failed': power off, check connections, restart. From PCWorld (Michael Brown, 2024-10-31, $99 then): 19.7 in puck spacing; red, green, blue, white and warm-white diodes; up to three programmable patterns with per-puck color, white CCT, brightness, gradient and effects; power-cycle after adding or removing lights; separate 100 ft runs cannot be programmed as one run. A search snippet lists the kit at $119 at Home Depot today.
  - src: https://images.thdstatic.com/catalog/pdfImages/55/55b7c72a-79f1-4dbb-bd4d-f51b0d61538f.pdf ; https://www.pcworld.com/article/2504896/hampton-bay-permanent-string-lights-review.html ; https://manuals.plus/hampton-bay/sqhdk03-15psl-permanent-mount-smart-color-changing-string-lights-manual
- [MEDIUM] **No public API dump of the SQHDK03 permanent-light profile exists. Its segment hint (30 or 60), any 'no-white' hint, slot count, CCT range and preset list are all unverified.**
  - GitHub code and issue searches for SQHDK, permanent, puck, 30-segment, 60-segment and RGBWIC found nothing Hubspace-related, and neither aioafero nor the HA integration has a dump. Inferences: PCWorld's 'three patterns' matches 3 slots (custom-1..3). Automatic length detection suggests either a fixed '60-segment' frame with an unused tail, or a frame whose length depends on the detected count; this has to be read from the device. The SQHDK has real white diodes, while the dumps carrying 'no-white' are RGB-only products, so per-entry cct/whiteBrightness may be honored. Unverified.
  - src: gh search code/issues run 2026-09-26; PCWorld review; SQHDK03 manual
- [HIGH] **aioafero 9.3.0 is still the latest on PyPI (uploaded 2026-09-15T20:57Z). Nothing relevant to string or permanent lights has changed since.**
  - PyPI JSON: latest 9.3.0; earlier 9.2.1 (09-13), 9.2.0 (09-12), 9.1.0 (09-03). GitHub main HEAD is 58864da1 (2026-09-15, Conclave client #99). Open PRs: #106 (ruff bump) and #61 (stale). 9.x changelog: 9.3.0 opt-in Conclave push, 9.2.1 SplitDeviceId, 9.2.0 dehumidifier, 9.1.0 portable-AC eco-mode. HA integration v8.3.0 (2026-09-15) pins aioafero==9.3.0. Inference from semantics.coerce_rest_state_value: with enable_conclave=True, a pushed attribute-351 change is stored as the raw pushed value, because there is no color-sequence-v2 decoder. Read frames back over REST (fetch_device_states / fetch_metadevice), not from the Conclave cache.
  - src: https://pypi.org/pypi/aioafero/json ; gh api repos/Expl0dingBanana/aioafero/commits ; aioafero CHANGELOG.rst ; Hubspace-Homeassistant custom_components/hubspace/manifest.json ; aioafero src/aioafero/v1/conclave/semantics.py
- [HIGH] **Gotchas for hand-built PUTs: timestamps must be ms, HTTP 200 can hide a dropped write, retries are limited, and the default session allows only 3 connections.**
  - aioafero issue #65: lastUpdateTime sent in seconds got HTTP 200 but the device never changed. Use epoch ms, which aioafero's manual path does. update_afero_api treats 400 as 'Invalid update' and returns False. It retries only on 429/503/504, with MAX_RETRIES=3 and 0.25*n s backoff, and raises on 403. AferoBridgeV1.open() creates a session with TCPConnector(limit_per_host=3) unless you pass your own, so 9 parallel module PUTs would go out in 3 waves. Because a 2xx does not mean the lights changed, confirm by reading back the slot and mode rows, or with a camera.
  - src: github.com/Expl0dingBanana/aioafero/issues/65 ; aioafero controllers/base.py lines 452-489 ; v1/__init__.py lines 567-571 and 927-962
- [LOW] **The Afero platform has a lower-level raw attribute write, and the current Hubspace app binary still references it. Whether a Hubspace token may use it, and how attribute 351 is laid out in bytes, are both unverified.**
  - Afero docs: POST /v1/accounts/{accountId}/devices/{deviceId}/actions with {"type":"attribute_write","attrId":N,"data":"<hex, little endian>"}. It is asynchronous and returns a requestId. The Afero SDK also has a batch form, POST .../devices/{deviceId}/requests with [{"type":"attribute_write","attrId":N,"value":"..."}]. The current Hubspace Flutter binary contains both '/v2/accounts/'+'/metadevices/'+'/state' and '/v1/accounts/'+'/devices/'+'/requests' plus 'postDeviceRequests'. GET .../devices/{deviceId}?expansions=attributes returns raw attribute hex; pairing it with the semantic GET would reveal the frame's byte layout. Here deviceId is the 16-hex physical id (AferoDevice.device_id), not the metadevice UUID. Afero caps MCU BYTES attributes at 255 B (non-Potenco) or 1536 B (Potenco); that this bounds frame size is an inference.
  - src: github.com/aferodeveloper/afero-devdocs docs/API-DeviceEndpoints.md ; github.com/aferodeveloper/AferoJavaSDK afero-sdk-core/.../models/WriteRequest.java and afero-sdk-client-retrofit2/.../AferoClientAPI.java ; github.com/wesen/2026-09-05--inspect-hubspace sources/hubspace-dart/metadata/selected-pool-entries.txt ; https://afero-docs.readthedocs.io/en/latest/AttrDef/
- [HIGH] **Attribute map of the Hubspace addressable-string profile (Hampton Bay/Novolink 'String Lights', manufacturerName 'Hampton Bay', device_class 'light', typeId metadevice.device).**
  - Key 1 power (on/off). 2 brightness 1-100. 3 color-temperature 2700-6500 step 100. 4 color-rgb {r,g,b}. 5 color-mode (individual/sequence/white/color). 6 color-sequence/preset (29 values; hints WinterHolidays, FunPatterns, MoreHolidays). 11 speed/color-sequence -10..+10. 12 color-individual/custom. 31 toggle/party-mode. 100 restore-values (restored / not-restored / partial-restore). 300 color-sequence/custom (9 named programs). 351/352/353 color-sequence-v2/custom-1..3. The RGBIC tape adds 51 power-off-timer 0-1440, 204 warm-dimming, schedule-pause, vacation-mode, and color-modes music-sync and circadian-rhythm. Leedarson adds 354 custom-4, night-light (hint no-brightness) and a speaker-power toggle.
  - src: light-with-speed.json ; light-rgbic.json (PR #61) ; light-with-speaker.json ; tests/v1/data/raw_hs_data.json for typeId
- [MEDIUM] **The named programs in attribute 300 ('custom' sequences) are compact on-device programs. Several map directly onto the Lowcountry modes as genuine app features, if the SQHDK profile carries them.**
  - Decoded; the layout is my inference: a 3-byte header [id, flags, 0] followed by 6-byte steps. CCT steps are [kelvin LE16, %, 0, duration LE16]; RGB steps are [r, g, b, %, duration LE16]. Results: nightlight 2700K 5%; dinner-party 2800K 62%; chill 3200K 40%; moonlight 5000K 10%; focus 4400K 85%; getting-ready 4300K 91%; clarity 5000K 100%; sleep 2700K 40% -> 5% (duration field 1800); wake-up 5% -> 100% (1800); rainbow red/green/blue at 20 each; christmas red/green at 10 each. aioafero set_effect('<name>') sends the PUT color-sequence preset='custom' plus custom='<name>' (verified offline). The semantic API only accepts the listed names for these categories.
  - src: deviceValues in light-with-speed.json and light-a21.json ; offline harness
- [HIGH] **Whole-strand modes are fully supported by aioafero today; only per-head frames need hand-built states. Note that set_state skips fields that match its cached state.**
  - LightController.set_state(on, temperature, brightness, color_mode, color, effect, numbers={('speed','color-sequence'): n}) covers white CCT, single RGB color, presets and speed. It drops fields equal to the cached state. Offline, set_effect('rainbow-roll') on the dump sent only color-sequence/preset, because the cache already had color-mode 'sequence' and power 'on'. For kiosk re-asserts after app or button interference, send the full states list with update(states=...).
  - src: aioafero controllers/light.py; controllers/base.py dataclass_to_afero; offline harness run
- [MEDIUM] **Design implications for the 3x3 canopy. These are inferences.**
  - (a) 3 slots is fewer than 6 modes, so write the frame and select the slot on every mode change. This is idempotent and also undoes edits made from the app or staff. (b) Framebuffer index follows wire order, so the software must map index to physical head through VWBD's routing within the 8/7/8/7 stagger. B modules (A rotated 180 degrees) run their index order in reverse, so static frames must be mirrored per module. Any on-device motion effect follows wire order, so it runs in opposite directions on A and B modules and zig-zags across rows. (c) A canopy-wide 'moving' amber wash cannot be phase-locked across 9 controllers. Options are slow per-module motion that is not synced, or cloud-stepped frames at 9 PUTs per step (rate-limit risk). (d) Warm ivory and lamp-glow modes should use the white diodes (per-entry cct/whiteBrightness, or whole-strand CCT 2200-3000K) rather than RGB-mixed ivory, provided the SQHDK profile honors per-entry white.
  - src: inference from the dumps, the SQHDK manual and the canopy brief
- [HIGH] **Corrections to the Sep 19 research set.**
  - (1) The '20-segment' Novolink string has 3 custom slots (custom-1..3), not 4. custom-4 appears only on the Leedarson 2-section-gradient flush mount. (2) aioafero does not split string lights into per-zone clones (see above). (3) The typeId conflict flagged in _gaps.md is resolved. Raw API typeIds are 'metadevice.device', 'metadevice.home' and 'metadevice.room' (tests/v1/data/raw_hs_data.json). aioafero's ResourceTypes.HOME/ROOM values 'metadata.home'/'metadata.room' are unused constants, and discovery filters on 'metadevice.device'.
  - src: dumps; /Users/ns38/Documents/CODING/HUBSPACE PROJECT/research/hardware.md lines 43 and 86 ; aioafero src/aioafero/v1/models/resource.py ; tests/v1/data/raw_hs_data.json

## Numbers
- aioafero latest on PyPI = **9.3.0, uploaded 2026-09-15T20:57:03Z (previous: 9.2.1 on 09-13, 9.2.0 on 09-12, 9.1.0 on 09-03)** (https://pypi.org/pypi/aioafero/json)
- aioafero GitHub main HEAD = **58864da1, 2026-09-15 (Conclave client #99); no later commits** (gh api repos/Expl0dingBanana/aioafero/commits)
- Hubspace-Homeassistant pin = **v8.3.0 (2026-09-15) requires aioafero==9.3.0** (Hubspace-Homeassistant custom_components/hubspace/manifest.json)
- Framebuffer length (= segments) = **20 (Hampton Bay/Novolink 'String Lights'), 40 (Ecosmart AL-TP-RGBICTW-3), 2 (Leedarson speaker flush mount); SQHDK03 unknown (30 or 60 expected)** (light-with-speed.json, light-rgbic.json (PR #61), light-with-speaker.json)
- Custom pattern slots per device = **3 (custom-1..3) on string and RGBIC tape; 4 on Leedarson; PCWorld says 3 on SQHDK** (dumps; PCWorld review)
- Slot attribute keys = **custom-1=351, custom-2=352, custom-3=353, custom-4=354 (format color-sequence-v2)** (light-with-speed.json / light-with-speaker.json functions)
- Selector / mode / effect attribute keys = **color-individual=12, color-mode=5, color-sequence preset=6, speed=11, color-sequence custom=300, party-mode toggle=31, power=1, brightness=2, CCT=3, RGB=4, restore-values=100** (light-with-speed.json)
- color-mode enum = **white 0, color 1, sequence 2, mixed 3, individual 4, night-light 5, music-sync 6, circadian-rhythm 10** (all aioafero light dumps + light-rgbic.json)
- Effect speed range = **-10..+10 step 1 (attribute 11); HA users report -10 = slowest; app resets speed after power-off** (light-with-speed.json; HA issue #205)
- Slot header defaults observed = **id 0, version 1, headerFlags 128, sequenceFlags 0, brightnessDepth 100, brightnessEffect 0, brightnessSpeed 50, motionEffect 0 (6 observed), motionSpeed 50 (49 observed), frameBuffer.flags 0** (light-with-speed.json, light-with-speaker.json, light-rgbic.json)
- Framebuffer entry fields = **r,g,b 0-255; cct 0 in all RGB samples; colorBrightness 100; whiteBrightness 0** (same dumps)
- HTTP requests per module per full-frame scene change = **1 (verified offline with aioafero 9.3.0 code, 4 values in one PUT)** (scratchpad/offline_put_harness.py run)
- PUT body size = **3,374 bytes for 30 entries; 5,984 bytes for 60 (pretty JSON default separators)** (local computation from the payload shape)
- SQHDK03 kit contents / limits = **30 pucks on 3 x 16.4 ft strings (10/section); max 60 pucks / 6 strings / 100 ft per controller** (SQHDK03 Use & Care guide (images.thdstatic.com ...55b7c72a...pdf))
- SQHDK03 electrical / optical = **36 V DC 1 A output, 36 W, CCT 2200-6500 K, 50 lm per bulb** (SQHDK03 Use & Care guide)
- SQHDK03 stock puck spacing = **19.7 in (canopy design wants 10 in pitch)** (PCWorld review 2024-10-31)
- SQHDK03 price = **$119 at Home Depot (search snippet, 2026-09); $99 at PCWorld review time; extension kit $29.97** (WebSearch snippet for homedepot.com/p/330863181; PCWorld)
- aioafero retry / concurrency defaults = **MAX_RETRIES=3 on 429/503/504 with 0.25*n s sleep; TCPConnector(limit_per_host=3) when open() creates the session** (aioafero v1/v1_const.py; v1/__init__.py lines 567-571, 939-962)
- Afero MCU BYTES attribute size cap = **255 bytes (non-Potenco) / 1536 bytes (Potenco)** (https://afero-docs.readthedocs.io/en/latest/AttrDef/)
- PR #61 status = **opened 2026-02-15, CHANGES_REQUESTED 2026-02-21, mergeable=false (dirty), last update 2026-06-06** (gh api repos/Expl0dingBanana/aioafero/pulls/61)

## Payloads / code
### Exact PUT: one module, 30 heads, 30 distinct colors (frame write + slot select + mode + power in ONE request). Cloud acceptance still needs a bench test.
source: Built from the read shapes in aioafero tests/v1/device_dumps/light-with-speed.json and PR #61 light-rgbic.json, plus aioafero controllers/base.py update_afero_api (URL/Host/body). Saved at /private/tmp/claude-503/-Users-ns38-Documents-CODING-HUBSPACE-PROJECT/cd9cac9c-9277-4e5e-9f7f-c70810256dbf/scratchpad/put_30_heads_example.txt
```
PUT https://api2.afero.net/v1/accounts/{accountId}/metadevices/{metadeviceId}/state
Host: semantics2.afero.net
Authorization: Bearer {access_token}
Content-Type: application/json; charset=utf-8

{
  "metadeviceId": "{metadeviceId}",
  "values": [
    {
      "functionClass": "color-sequence-v2",
      "functionInstance": "custom-1",
      "value": {
        "color-sequence-v2": {
          "id": 0, "version": 1, "headerFlags": 128, "sequenceFlags": 0,
          "brightnessDepth": 100, "brightnessEffect": 0, "brightnessSpeed": 50,
          "motionEffect": 0, "motionSpeed": 50,
          "frameBuffer": {"flags": 0, "framebuffer": [
          {"r": 255, "g": 110, "b": 15, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 114, "b": 21, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 118, "b": 26, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 122, "b": 32, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 127, "b": 38, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 131, "b": 43, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 135, "b": 49, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 139, "b": 55, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 143, "b": 61, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 147, "b": 66, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 151, "b": 72, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 155, "b": 78, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 158, "b": 84, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 162, "b": 91, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 166, "b": 97, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 169, "b": 103, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 173, "b": 109, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 177, "b": 116, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 180, "b": 122, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 184, "b": 128, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 187, "b": 133, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 190, "b": 137, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 193, "b": 141, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 196, "b": 145, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 199, "b": 149, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 203, "b": 153, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 206, "b": 158, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 209, "b": 162, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 212, "b": 166, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
          {"r": 255, "g": 215, "b": 170, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0}
          ]}
        }
      },
      "lastUpdateTime": 1790440660560
    },
    {"functionClass": "color-individual", "functionInstance": "custom", "value": "custom-1", "lastUpdateTime": 1790440660560},
    {"functionClass": "color-mode", "functionInstance": null, "value": "individual", "lastUpdateTime": 1790440660560},
    {"functionClass": "power", "functionInstance": null, "value": "on", "lastUpdateTime": 1790440660560}
  ]
}
# lastUpdateTime MUST be epoch milliseconds (seconds are accepted with 200 and silently dropped, aioafero #65).
# Framebuffer length must equal the device's segment hint (20/40/... ; read SQHDK03's from the device first).
```
### aioafero 9.3.0: send it (no first-class method exists; hand-built states, one PUT)
source: aioafero src/aioafero/v1/controllers/base.py (update), v1/__init__.py (open, fetch_device_states, send_service_request); pattern verified offline via scratchpad/offline_put_harness.py
```
import asyncio, aiohttp
from aioafero.v1 import AferoBridgeV1

HEADER_DEFAULT = {"id": 0, "version": 1, "headerFlags": 128, "sequenceFlags": 0,
                  "brightnessDepth": 100, "brightnessEffect": 0, "brightnessSpeed": 50,
                  "motionEffect": 0, "motionSpeed": 50}

def entries(colors):  # len(colors) == segment count from the color-individual hint
    return [{"r": r, "g": g, "b": b, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0}
            for (r, g, b) in colors]

async def show_frame(bridge, light_id, colors, slot="custom-1", header=None, **effect):
    hdr = dict(header or HEADER_DEFAULT)   # better: copy the header read back from the device
    hdr.update(effect)                     # e.g. motionEffect=?, motionSpeed=? (codes TBD on bench)
    hdr["frameBuffer"] = {"flags": 0, "framebuffer": entries(colors)}
    states = [
        {"functionClass": "color-sequence-v2", "functionInstance": slot,
         "value": {"color-sequence-v2": hdr}},
        {"functionClass": "color-individual", "functionInstance": "custom", "value": slot},
        {"functionClass": "color-mode", "functionInstance": None, "value": "individual"},
        {"functionClass": "power", "functionInstance": None, "value": "on"},
    ]
    # One PUT. Returns aiohttp response, or None on HTTP 400 / exhausted retries.
    # (bridge.send_service_request(light_id, states) does the same but returns None.)
    return await bridge.lights.update(light_id, states=states)

async def main(username, refresh_token, frames):  # frames: {metadevice_id: [(r,g,b)*N]}
    session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit_per_host=10))
    bridge = await AferoBridgeV1.open(username, refresh_token, session)
    try:
        await asyncio.gather(*(show_frame(bridge, mid, cols) for mid, cols in frames.items()))
        # verify: rows come back as AferoState objects
        for mid in frames:
            st = {(s.functionClass, s.functionInstance): s.value
                  for s in await bridge.fetch_device_states(mid)}
            assert st[("color-mode", None)] == "individual"
    finally:
        await bridge.close(); await session.close()
```
### What aioafero actually emitted for that call (offline run, transport stubbed)
source: /private/tmp/claude-503/-Users-ns38-Documents-CODING-HUBSPACE-PROJECT/cd9cac9c-9277-4e5e-9f7f-c70810256dbf/scratchpad/offline_put_harness.py (runs aioafero main@58864da1 source against tests/v1/device_dumps/light-with-speed.json)
```
method: put
url: https://api2.afero.net/v1/accounts/ACCOUNT-ID/metadevices/a2d36de5-8b91-411a-907a-ecb665422d00/state
headers: {'host': 'semantics2.afero.net', 'content-type': 'application/json; charset=utf-8'}
body: {"metadeviceId": "a2d36de5-...", "values": [ {color-sequence-v2/custom-1 {...20 entries...}, lastUpdateTime: 1790440660560},
        {color-individual/custom = custom-1}, {color-mode/null = individual}, {power/null = on} ]}
== number of HTTP requests for the whole scene: 1

light_callback(split) on the same device -> only '<id>-light-party-mode' (switch), remove_original: False

set_effect('rainbow-roll')  -> PUT values [color-sequence/preset = rainbow-roll]   (mode/power skipped: cache already matched)
set_effect('dinner-party')  -> PUT values [color-sequence/preset = custom, color-sequence/custom = dinner-party]
set_state(numbers={('speed','color-sequence'): 5}) -> PUT values [speed/color-sequence = 5]

run: python3 /private/tmp/claude-503/-Users-ns38-Documents-CODING-HUBSPACE-PROJECT/cd9cac9c-9277-4e5e-9f7f-c70810256dbf/scratchpad/offline_put_harness.py
```
### Read-side definitions to key off at runtime (verbatim shapes from dumps)
source: aioafero tests/v1/device_dumps/light-with-speed.json, light-with-speaker.json; PR #61 light-rgbic.json
```
// color-individual (the slot selector + geometry hint)
{"functionClass":"color-individual","functionInstance":"custom","type":"category","schedulable":true,
 "values":[{"name":"custom-1","hints":["20-segment"],"deviceValues":[{"type":"attribute","key":"12","value":"351"}]},
           {"name":"custom-2","hints":["20-segment"],"deviceValues":[{"type":"attribute","key":"12","value":"352"}]},
           {"name":"custom-3","hints":["20-segment"],"deviceValues":[{"type":"attribute","key":"12","value":"353"}]}]}
// RGBIC tape: hints ["40-segment","no-white"]; Leedarson: ["2-section-gradient","no-white"] (+custom-4 -> 354)

// one slot (object attribute)
{"functionClass":"color-sequence-v2","functionInstance":"custom-1","type":"object","schedulable":false,
 "values":[{"name":"color-sequence-v2","deviceValues":[{"type":"attribute","key":"351","format":"color-sequence-v2"}]}]}

// color-mode (attr 5): individual=4, sequence=2, white=0, color=1 (+ mixed=3, night-light=5, music-sync=6, circadian-rhythm=10 on some)
// segment count at runtime = int(hint.split('-')[0]) or len(state['color-sequence-v2']['frameBuffer']['framebuffer'])
```
### Cheap slot-switch PUT (frames already stored in custom-1..3)
source: PR #61 diff (features.EffectFeature.api_value for custom segments) + dumps
```
{"metadeviceId":"{id}","values":[
  {"functionClass":"color-individual","functionInstance":"custom","value":"custom-2","lastUpdateTime":<ms>},
  {"functionClass":"color-mode","functionInstance":null,"value":"individual","lastUpdateTime":<ms>}]}
// Equivalent to what open PR #61's set_effect('custom-2') would emit.
```
### Device-side preset / named program / white modes (first-class aioafero, one PUT each)
source: aioafero src/aioafero/v1/controllers/light.py; offline harness
```
await bridge.lights.set_effect(light_id, "rainbow-roll")       # attr 6 preset (device-animated)
await bridge.lights.set_effect(light_id, "nightlight")         # preset=custom + custom=nightlight (2700K 5%, decoded)
await bridge.lights.set_state(light_id, on=True, numbers={("speed", "color-sequence"): -6})
await bridge.lights.set_state(light_id, on=True, temperature=2700, brightness=40)   # whole-strand white
await bridge.lights.set_rgb(light_id, 255, 150, 60)                                 # whole-strand color
# NB: set_state/set_effect skip fields equal to the cached state; for re-asserts send full states via update(states=...)
```
### Raw Afero attribute write (platform API; NOT verified with Hubspace tokens; byte layout of attr 351 unknown)
source: github.com/aferodeveloper/afero-devdocs docs/API-DeviceEndpoints.md; github.com/aferodeveloper/AferoJavaSDK WriteRequest.java + AferoClientAPI.java; wesen/2026-09-05--inspect-hubspace selected-pool-entries.txt ('/v1/accounts/','/devices/','/requests','postDeviceRequests')
```
POST https://api2.afero.net/v1/accounts/{accountId}/devices/{deviceId16hex}/actions
{"type": "attribute_write", "attrId": 351, "data": "<hex, little endian>"}
-> {"type":"attribute_write","requestId":14,"timestampMs":...,"sender":"ClientApi",...}   (async)

POST https://api2.afero.net/v1/accounts/{accountId}/devices/{deviceId16hex}/requests
[{"type": "attribute_write", "attrId": 12, "value": "..."}, ...]      (SDK WriteRequest batch)

GET  https://api2.afero.net/v1/accounts/{accountId}/devices/{deviceId16hex}?expansions=attributes   (raw hex per attrId; read-only)
```

## Bench tests
- Profile dump before any write (read-only, Prototype 01 kit): after onboarding the SQHDK03 in the Hubspace app, run `await bridge.fetch_metadevice(id)` (expansions=state,capabilities,semantics) and save the JSON. Record: every functionClass/functionInstance; the color-individual category names, hints and device values (look for 'N-segment' and 'no-white'); the length of framebuffer[] in each custom-N state; the color-mode values (look for 'individual'); the color-temperature range (expect 2200-6500); the preset list; any functionClass with length/count/segment/pixel in its name. Pass = color-sequence-v2 slots exist and framebuffer length equals 30 (or a documented 60).
- Replay test, which proves write acceptance with a known-good object: in the Hubspace app, paint custom pattern 1 with clearly different colors on the first 5 pucks. GET the state. PUT that exact color-sequence-v2 object into custom-2 with aioafero `bridge.lights.update(id, states=[slot object, color-individual=custom-2, color-mode=individual])`. Pass = the pucks show the pattern within about 2 s, GET returns custom-2 unchanged, and the HTTP status is 200 (not 400).
- Synthetic 30-color index map: PUT a frame where index i gets a unique hue (e.g. hue = 12*i degrees) and photograph the module. Record the physical head for each index along VWBD's wiring through the 8/7/8/7 stagger, and for both A and B orientations. This gives the index->(row, col) table the kiosk needs.
- Wrong-length handling: PUT framebuffers of 29, 31 and 60 entries. For each, record HTTP status (400?), the GET-back length (padded or truncated?) and what the lights show. Also PUT a frame whose entries carry cct=2700, whiteBrightness=100, colorBrightness=0 to check whether per-puck white diodes are used.
- Effect enumeration: in the app's custom-pattern editor, select each available effect and several speeds in turn. After each change, GET custom-1 and tabulate motionEffect, brightnessEffect, motionSpeed, brightnessSpeed and brightnessDepth. Then replay one effect through the API and unplug the router's WAN. Pass = the animation continues with no internet, which proves it runs on-device.
- Live-slot semantics: with custom-1 active, PUT only a new custom-1 frame (no reselect) and see whether the lights update. Then test value order within the PUT (select before write vs write before select). Also check whether the change is an instant cut or a fade.
- 9-module timing: fire 9 parallel full-frame PUTs, using your own aiohttp session with limit_per_host >= 9, 50 times. Film at 60 fps and measure time from send to first visible change, plus the spread from first to last module. Separately, send one frame every 2 s for 5 minutes and compare GET-back frames with what was sent, to detect silent throttling.
- Power and persistence: unplug and replug the controller, and turn it off and on through the API. Check whether it returns to color-mode individual with the same slot and frame, whether motionSpeed and speed persist (HA #205 says preset speed resets), and how restore-values reports.
- Cut-to-pitch recalibration: after VWBD shortens the wire between pucks to get the 10 in pitch, power-cycle as the manual and PCWorld advise. Re-run the profile dump and confirm the segment count and hint did not change, and that 'length detection' passes.
- Orientation check: on one A module and one B module, run the same on-device motion effect and confirm whether it travels in opposite physical directions. Then decide whether to keep A/B/A orientation or wire all modules the same way for a coherent 'wash'.
- Optional raw-encoding probe, read-only: GET /v1/accounts/{aid}/devices/{deviceId}?expansions=attributes. Pair the hex of attributes 351-353 with their decoded JSON to learn the byte layout and size. Do not use attribute_write on the prototype until the semantic PUT path is proven.

## Open questions
- Does the SQHDK03-15PSL cloud profile use color-individual + color-sequence-v2 at all? If so, is its hint '30-segment', '60-segment' or something else, is it flagged 'no-white', and does the frame length follow the auto-detected puck count?
- Does the Afero semantics layer accept a color-sequence-v2 object written by a third-party client over /v1 (the current app uses /v2/accounts/.../metadevices/.../state)? No public capture or code shows a successful write.
- What do the motionEffect and brightnessEffect codes mean (0 = static; 6 was observed on a 2-section gradient), and what do headerFlags=128, sequenceFlags, frameBuffer.flags, id, version and brightnessDepth do?
- Does a wrong-length framebuffer get rejected with 400, padded or truncated, or silently dropped with a 200?
- Does rewriting the active slot re-render immediately, or only after color-individual is re-selected? Is there any transition or fade between frames?
- Is there any way to phase-sync effects across the 9 controllers (for example a shared start time)? No profile field suggests one.
- What are Afero's real rate limits for about 9 PUTs of ~3.4 KB per mode change, alongside 4 vignettes on the same account? Is throttling keyed per account or per IP?
- Do hubspace_android Keycloak tokens authorize the raw /v1/accounts/{aid}/devices/{deviceId}/actions and /requests endpoints (strings present in the current app binary), and what is the byte layout of attribute 351?
- Will PR #61 or any successor land in aioafero before Oct 20? It is currently stale with changes requested. The plan should assume it will not.
- Does the SQHDK profile include the attribute-300 named CCT programs (nightlight, dinner-party, chill, sleep...)? Kiosk signage could then point to them as genuine app features.

## Goldmines
- https://github.com/Expl0dingBanana/aioafero/blob/main/tests/v1/device_dumps/light-with-speed.json
- https://github.com/Expl0dingBanana/aioafero/blob/main/tests/v1/device_dumps/light-with-speaker.json
- https://github.com/tpural/aioafero/blob/include-more-effects/tests/v1/device_dumps/light-rgbic.json
- https://github.com/Expl0dingBanana/aioafero/pull/61
- https://github.com/jdeath/Hubspace-Homeassistant/issues/148
- https://github.com/jdeath/Hubspace-Homeassistant/issues/224
- https://github.com/jdeath/Hubspace-Homeassistant/issues/205
- https://github.com/Expl0dingBanana/aioafero/issues/65
- https://github.com/Expl0dingBanana/aioafero/blob/main/src/aioafero/v1/controllers/base.py
- https://github.com/Expl0dingBanana/aioafero/blob/main/src/aioafero/v1/controllers/light.py
- https://github.com/Expl0dingBanana/aioafero/blob/main/src/aioafero/v1/conclave/semantics.py
- https://github.com/Expl0dingBanana/aioafero/blob/main/docs/mitm/daily_use.rst
- https://pypi.org/pypi/aioafero/json
- https://images.thdstatic.com/catalog/pdfImages/55/55b7c72a-79f1-4dbb-bd4d-f51b0d61538f.pdf
- https://www.pcworld.com/article/2504896/hampton-bay-permanent-string-lights-review.html
- https://github.com/aferodeveloper/afero-devdocs/blob/main/docs/API-DeviceEndpoints.md
- https://github.com/aferodeveloper/AferoJavaSDK/blob/master/afero-sdk-core/src/main/java/io/afero/sdk/client/afero/models/WriteRequest.java
- https://github.com/wesen/2026-09-05--inspect-hubspace/blob/main/sources/hubspace-dart/metadata/selected-pool-entries.txt
- https://afero-docs.readthedocs.io/en/latest/AttrDef/
- /private/tmp/claude-503/-Users-ns38-Documents-CODING-HUBSPACE-PROJECT/cd9cac9c-9277-4e5e-9f7f-c70810256dbf/scratchpad/offline_put_harness.py
- /private/tmp/claude-503/-Users-ns38-Documents-CODING-HUBSPACE-PROJECT/cd9cac9c-9277-4e5e-9f7f-c70810256dbf/scratchpad/put_30_heads_example.txt