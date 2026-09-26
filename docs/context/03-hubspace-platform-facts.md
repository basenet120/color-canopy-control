# 03 — Hubspace / Afero platform facts

Condensed from the Sep 19 2026 research pass. Full digests with sources: `research/*.md` (start with `aioafero-source.md`, `app-reverse-eng.md`, `performance.md`, `risk-legal.md`).

## Architecture
- Hubspace is a white-label deployment of **Afero**'s IoT platform (Android package `io.afero.partner.hubspace`). Every device is **100% cloud-controlled**: no local API, no Matter, no usable BLE control path after setup. Bulb firmware is eFuse-locked (ESP32, Leedarson-built) — no custom firmware.
- **No sanctioned API.** The developer portal is gone and Afero hasn't answered community maintainers. Our path is the community library `aioafero` (MIT, github.com/Expl0dingBanana/aioafero), which impersonates the Android app's OAuth client — the same approach the 381-star Home Assistant integration has used publicly for 4+ years with no known enforcement.

## Auth
- Keycloak OAuth2 authorization-code + PKCE at `accounts.hubspaceconnect.com`, realm `thd`, client_id `hubspace_android`, redirect `hubspace-app://loginredirect`.
- Access tokens live **~118 s**; everything runs on long-lived refresh tokens (scope includes `offline_access`). aioafero ≥ 9.0 requires a refresh token for the bridge (no password login in the bridge).
- A password login from a new IP can trigger an **email OTP** challenge (`OTPRequired`). Mint refresh tokens deliberately, persist them, keep MFA **off** on show accounts.
- "Sign out of all other devices" in the Hubspace app kills bridge sessions within ~2 minutes.

## REST
- Account id: `GET https://api2.afero.net/v1/users/me`
- Discovery: `GET /v1/accounts/{aid}/metadevices?expansions=state,capabilities,semantics` (sent to api2.afero.net with `Host: semantics2.afero.net`)
- State: `GET|PUT /v1/accounts/{aid}/metadevices/{metadeviceId}/state`, body `{"metadeviceId": id, "values": [{"functionClass", "functionInstance", "value", "lastUpdateTime"}, …]}`
- **One metadevice per PUT**; several attributes of the same device can ride in one PUT. No public multi-device or scene call (a legacy Afero rules engine exists — `POST /v1/accounts/{aid}/rules/{ruleId}/actions {"type":"execute_actions"}` — but whether Hubspace tokens may use it is unverified).
- **Writes are asynchronous**: a 2xx means the cloud accepted the value, not that the device changed. Confirmation comes from Conclave push events (or a camera).
- The shipping app also uses a `/v2/` semantics path; aioafero still uses `/v1/` successfully.

## Realtime: Conclave
- `POST https://api2.afero.net/v1/accounts/{aid}/conclaveAccess {"user": true, "softHub": false}` → host (e.g. `conclave-stream1.afero.net`), port 443, single-use socket token, channelId.
- Raw TLS socket (not WebSocket), newline-delimited JSON, optional zlib. Login frame → welcome; ~60 s heartbeats (bare newlines). Events: `attr_change`, `status_change`, `invalidate`. **Subscribe-only** — writes still go over REST. Mint a fresh token on every reconnect.
- Implemented in aioafero 9.3.0 (Sep 15 2026) as opt-in `enable_conclave=True`; very new — soak it before trusting it for show ops.

## aioafero library (pin it)
- Python ≥ 3.12. Three breaking majors in 2026 (7.0, 8.0, 9.0) — **pin the exact version** and wrap it behind our own adapter.
- Stock limits our bridge must override or wrap: `TCPConnector(limit_per_host=3)`; retries only on 429/503/504, 3 tries at 0.25/0.5/0.75 s; **500/502 and connection errors are not retried**; 403 raises immediately and 3 consecutive 403s pause a device's polling; no client-side throttle; no group/scene entities.
- State polling defaults to 30 s (GET per device, all concurrent). With Conclave on, a slow REST true-up (the HA integration uses 900 s) is the pattern.
- Addressable string lights are **not** split into per-zone clones (only main/trim fixtures are; strings get just a `…-light-party-mode` switch clone). Per-segment colors live inside `color-sequence-v2` framebuffers — see `04-device-model-permanent-lights.md`.
- `Emkraan/homeassistant-hubspace` vendors a patched aioafero whose patch list is a catalog of real failure modes (token refresh deadlock, 500/502 retry gaps, silent stale-poll loop, stale poll overwriting optimistic state). Mine it; don't adopt it blindly.

## Rate limits and reliability
- **Unpublished.** Community polling floor ~2 s ("Afero rate-limits aggressively"); throttling has shown up as 429s, 403s, and **silently degraded responses** (fields missing) — validate response shapes, not just status codes.
- Official Afero attribute guidance: no more than ~1 write/second per device.
- Healthy single command: ~1–2 s tap-to-light reported. No public p95 data.
- Documented outage: Jul 27 2026, ~43 min of HTTP 500 + timeouts. No status page. Cloud runs on Google Cloud.
- Largest known public deployment: 143 devices. Our 9 canopy strands + ~15–25 vignette devices is comfortably inside known territory.

## Device facts relevant to vignettes
- EcoSmart A19 RGB (12A19060WRGBWH1): `color-rgb {r,g,b}` 0–255, brightness 1–100, `color-mode` white/color/sequence, `color-temperature`, **no transition/fade parameter**, on-bulb effects (fade-3/7, jump-3/7, named presets, speed −10…+10), `restore-values` power-restore setting.
- Defiant Wi-Fi deadbolts: `lock-control` lock/unlock only (no PIN management through aioafero).
- Defiant outdoor plug HPPA52CWB: two independently switchable outlets.
- Hampton Bay Hubspace string lights: per-segment custom colors (3 saved pattern slots, `color-individual` + `color-sequence-v2`), preset themes, party mode; keep running their last preset if the cloud drops.
- **There is no Hubspace humidifier** — the bedroom humidifier rides a Hubspace smart plug (on/off only).

## Provisioning
- App-only, one device at a time via QR + BLE; ~2–5 min each. **Scanning a device's QR from another account transfers it** — guard the QR cards.
- Devices store the Wi-Fi SSID/password permanently: freeze the show network's SSID/PSK/WPA2 settings **before** provisioning anything; re-onboarding is per device.

## Legal posture (not legal advice)
- The Hubspace app terms license "personal, noncommercial use"; Home Depot is the counterparty, so a short **written authorization from Home Depot** for the activation window converts ToS ambiguity into licensed use. Never bypass an actual technical block (rotating IPs/accounts after a ban).
- Words-only "works with Hubspace®" is nominative use; the Hubspace logo in our UI needs brand sign-off.
