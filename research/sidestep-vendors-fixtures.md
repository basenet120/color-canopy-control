# Sidestep research: Environmental vendors, RGBW fixtures, Astera (agent pass, 2026-09-19)

## Vendor disambiguation
- **EnvironmentalLights.com** ("Environmental Lights", San Diego): B2B LED engineering supplier, sales-engineer model, strong exhibit/event spec support (LDI, City Theatrical Eluxtra partnership). Nearly all pricing login-gated (sales 888-880-1880). Sells Advatek PixLite Mk3 line (A4-S/E4-S/E16-S/T8-S + receivers), ENTTEC (OCTO MK3, Pixelator Mini, S-Play), DiGidot C1. Their addressable RGB+white products are STRIP (WS2814 RGBWW 2700K 4-in-1 5V; Eluxtra 5-in-1 RGB+2400K/6500K 16-bit) — **their discrete-point PixelPro bullets/domes are RGB-only; no RGBW node/festoon in catalog**. DMX-decoder alternative: DMXtune 25-ch RDM decoder (5× RGBWW fixtures per box) → 200 points = 40 decoders ≈ $6-10k just in decoders (only sane zoned).
- **environmentalled.com** ("Environmental LED"): small holiday-lighting dealer, public prices + live stock, same-day ship claim. Minleon RGB+ dealer.

## Live Minleon stock at Environmental LED (Sep 19 2026)
- RGB+ Triklit 42mm set/20 @ 12" spacing: **$129/set, 18 sets in stock** (=360 bulbs) — 12" pitch matches our grid!
- RGB+ Pebble set/25: $75, 38 sets
- **4-port NDB: $219, 5 in stock** (Art-Net/sACN/DDP in) — they do NOT carry NDB+16/NDB-PRO
- NEC effects controller $149; T-ways $12, boosters $17, extenders $22
- NDB+16 elsewhere: $139 SOLD OUT (LED Warehouse); In-Stock call-for-price at Reinders
- NDB-PRO (minleonusa.com, dealer): 8 outputs, 16 universes, ArtNet4/sACN/DDP, native SK6812 + Minleon RGBW+ strings support. Minleon USA phone (717) 763-1055
- **Minleon RGBW exists**: TrikLits G42 "RGB or RGBW" diodes, custom sets/spacing, dealer-quote + MOQ, lead time unknown; T50 RGBW festoon = Minleon AUSTRALIA only

## RGBW chip/controller reality
- Chips with true white(s): SK6812-RGBW (incl 3000K variants), WS2814 (12/24V), TM1814, UCS8904 (16-bit), WS2805 (RGB+dual white), FW1906 (RGB+CCT)
- Advatek PixLite Mk3: first-class RGBW (E4-S: 768 RGBW px/output, 3072 total, 24 universes) — E4-S ~$450-620, US warehouse stock thin
- WLED ESP32: first-class all six chips
- Falcon F16V5 / Kulp: 3-ch native, RGBW via raw-data workaround (fine when Python sends DDP/sACN)
- **In-stock discrete RGBW points (US, this month): Amazon SK6812-RGBW 12mm bullets 50ct xConnect (~$30-45 unverified) and RGBW-3000K "pebble seed" strings 50/100/200ct 12V xConnect** — pitch 10-13.5cm (needs splicing to 12" or coiled slack); C9 covers from Gilbert Engineering to read as bulbs. Amazon QC → buy 25% spares.

## Astera NYX Bulb (pro wireless-DMX route)
- FP5 NYX: Titan RGB+Mint+Amber engine, 10W, E26/E27 mains (also DC/USB), IP44, CRMX (LumenRadio) + Bluetooth app. Purchase $115.20 ea; 8-bulb kit w/ PowerStation $1,242
- ART7 AsteraBox CRMX transmitter ~$508-549; 1 DMX universe per box (Art-Net input claim conflicting) → safe chain: Python sACN → Ethernet-DMX node → 2-3 ART7
- **Rental (Lensrentals list): $24/bulb/14-day → 200 bulbs ≈ $4,800 + transmitters + insurance/shipping ≈ realistically $5.5-9k for 2 weeks.** No single US house verified to stock 200 (typical 8-32; multi-house or broker needed). ~2kW total on E26 festoon circuits.

## Route BOMs (200 points + spares)
- **A. Minleon Triklit RGB + 4-port NDB (all in stock TODAY, 12" pitch native): ≈ $2,330** — but RGB-only (warm ivory = mixed RGB; weakest warm-white)
- **B. Minleon RGBW G42 TrikLits + NDB-PRO: dealer-quote (717) 763-1055, MOQ + lead-time risk vs Oct 13** — true RGBW in the same bulb form
- **C. SK6812-RGBW bullets/pebbles + PixLite E4-S (or WLED/Kulp): ≈ $1,050-1,350** — cheapest true-RGBW; pitch + Amazon-QC caveats
- **D. Environmental Lights spec-house: no RGBW discrete point exists; strip-segment or 40-decoder analog routes only ($6-10k decoders); account/call required**
- **E. Astera NYX rental: ≈ $5.5-9k / 2wk rental (or $23k purchase)** — film-grade warm whites, zero data wiring, screw into any E26
