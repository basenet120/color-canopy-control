# Sidestep research: simplest pixel-control options (agent pass, 2026-09-19)

## Ranked shortlist for this job

1. **WLED v16 on wired-Ethernet ESP32** (QuinLED Dig-Quad $39.50 / Dig-Octa $65+ / Athom ETH ~$30) — control UI + machine API are the same built-in HTML web server on the controller; Python triggers scenes with one HTTP call `POST /json/state {"ps":5}` (250 preset slots, playlists); boot-preset = automatic show recovery; native 2D matrix + ledmap.json; DDP/E1.31/Art-Net in; 512px/pin @60fps (Dig-Quad ~2k px). WLED v16.0.0 shipped 2026-05-03. Failure mode: use Ethernet variant; HW watchdog reboot + boot preset ≈ seconds, no operator.
2. **FPP 10.1.2 on Pi/Kulp K8-Pi ($83-165)** — show-hardened (10.1.2 released Sep 17 2026); full web UI, playlists, scheduler, REST; **Pixel Overlay HTTP API: `PUT /api/overlays/model/{m}/data` accepts raw RGB bytes row-major or an encoded image (`?fmt=image&scale=fit`)** — Python or even browser fetch() paints whole 20×10 frames over plain HTTP at 20-40fps (verified in master source, PixelOverlay.cpp). Plays video files onto matrix models; HDMI "virtual matrix" output.
3. **Pixelblaze V3** ($35 Crowd Supply in stock, ships 3 days; direct shop sold out; expander $19) — THE "built-in web visualizer" product: live browser pattern editor, built-in 2D/3D mapper, 2,500 WS2812/unit; WebSocket API :81 + maintained python lib `pixelblaze-client` 1.1.8 (2026-07-22); Firestorm console for fleets. WiFi-only (no Ethernet) — the watch-item for retail RF.

## Other options (table summary)

- Falcon F16v5 $280 in stock — receiver of E1.31/DDP; built-in test patterns but real looks need a feed/sequence; overkill alone.
- Advatek PixLite A4-S Mk3 $649 — pro; SHOWTime standalone SD playback + 25 triggers; pricey for this scale.
- Minleon NDB-PRO (dealer, ~$500 unverified street) — Art-Net/sACN bridge; 15 canned standalone effects; permanent-install oriented; 800px/unit.
- xLights + xSchedule — free; REST `GET /xScheduleCommand?Command=PressButton&Parameters=X`; needs always-on PC + controller anyway; ~a day to sequence 6 looks.
- ENTTEC ELM 2026 — Pro tier $250 (16U): 2D pixel mapper with **NDI/Spout + screen-capture + live-video input** and HTTP/OSC remote = the turnkey paid "play HTML/video onto pixels" PC option. Free tier ~340px, no remote API.
- Madrix 5 (~$300-400 entry, unverified), Resolume Arena €799, TouchDesigner Commercial $600 (Web Render TOP = embedded Chromium → DMX Out CHOP sACN — the most flexible HTML→pixels bridge), Chromatik/LX (free non-commercial, JVM desktop), LedFx (free, audio-reactive bonus mode on WLED, not a show controller).
- Twinkly Pro: 6-port ETH controller = 1,500px, camera auto-mapping magic, **accepts Art-Net/sACN input (fw ≥2.6)** — as an sACN node it's an expensive WLED; native effects app/cloud-tied.
- Hue/Nanoleaf: Entertainment API caps ~10 lights/area — out at 200 points.

## "HDMI/HTML as protocol" — concrete toolchains

1. **Browser/Python → FPP Pixel Overlay HTTP** (recommended; no capture layer at all).
2. **Python `sacn` lib → any E1.31 device** — threaded sender, 30fps default; 2,000px = 12 universes ≈ 360 pkts/s = noise on wired LAN; DDP even simpler (10-byte UDP header, port 4048, ~20 lines of Python, no lib). This is exactly how LedFx drives WLED fleets; Hyperion→WLED DDP is a documented consumer chain. (Legacy 490-LED cap applies only to old UDP protocol 0, not DDP.)
3. **HTML page → ELM Pro $250** — screen-capture/NDI → 2D map → sACN out + HTTP/OSC scene switching.
4. **HTML inside TouchDesigner** — Web Render TOP → DMX Out CHOP ($600 commercial).
5. **Video-file route, zero live PC** — render the 6 looks as tiny MP4s; FPP playlist plays them onto the overlay model; touchscreen calls playlist REST.

## Flags
- At 200px the problem is small: one $35 Pixelblaze or $10 ESP32+WLED covers it. Real decisions: Ethernet-vs-WiFi + where the 6 looks get authored (WLED presets vs FPP sequences vs live Python frames).
- Unverified: Athom price (~$25-35), Madrix entry price, Minleon street price, Resolume USD/sACN-out, LedFx current version.
- Release recency verified: WLED May 2026, Pixelblaze client Jul 2026, FPP Sep 17 2026 — all actively maintained.
