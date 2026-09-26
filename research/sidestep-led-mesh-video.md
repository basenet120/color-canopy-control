# Sidestep research: LED mesh / video fabric for 3×6m canopy (agent pass, 2026-09-19)

Baseline: 200-bulb grid ≈ 10×20 "pixels". Every route below is 10-150× that resolution. Sweet spot for slow washes at 8-15ft: P25-P50 mesh (P50 → 60×120 = 7,200 px). Fine-pitch soft modules (P2.5-P5) = heavy/hot/expensive overkill.

## Route A — China direct
- **A1 SPI pixel mesh (WS2811/GS8208, 12V):** $100-250/m² verified (Onvision P40 $248/m² MOQ 1m²; P25/P50 ~$100/m² tier MOQ 10m²; P50 = 53W/m², IP65, production 15 days, DDP offered). 18.6m² landed ≈ **$3.5-8k**. Control = Art-Net/sACN mapping (ELM/MADRIX) or SD sequences — NOT plug-HDMI.
- **A2 NovaStar scan mesh:** $420-720/m² verified (SightLED P31-P50; production 20-30 working days = ALREADY TOO LATE unless ≤15-day confirmed). Landed ≈ **$9.5-16.5k**. Control = mini-PC HDMI → NovaStar TB40 ($158-269) / VX600 ($960-1,500 US stock) → **fullscreen browser = the show**.
- **Budget wildcard:** led-cloth.com "LED Vision Curtain" 3×6m special **$649** (P9cm ≈ 2,178 px, fabric, feather-light, horizontal-trivial; vendor controller, not HDMI; demand NFPA 701 report).
- **Timeline:** commit + deposit by ~Sep 23, absolute stop Sep 26, only with written ≤15-day production + DDP express air. Tariff reality: ~40% effective duty on LED display goods — insist duty-inclusive DDP in writing, Alibaba Trade Assurance.
- **Compliance risk:** CE/RoHS/FCC self-declarations, usually no UL/ETL; overhead AC-powered scan mesh = weakest fire-marshal story. Cleanest: 12V SPI class fed by US-bought UL-listed Mean Well PSUs at deck level. Mesh curtains are engineered for VERTICAL hang — horizontal canopy = off-label, support every 0.5-1m + safety cables.

## Route B — US rental / semi-pro buy
- Rental 18.6m² × 2wk ≈ **$15-30k** incl processor/rigging/labor (2-week static hold priced like concert days). Ships with processor = plug-HDMI YES. Local Charleston labor: Rayne Events, Fox AV, Sifi, Plus Event Rentals.
- **Twinkly Lightwall** 8.2×8.9ft, 1,120px, fabric-backed, **$1,199.99 Sweetwater in stock** → 3 units ≈ 3,360 px for ~$3.6k, horizontal-friendly. Control app/xLights; Art-Net support coarse (not per-pixel streaming); consumer Wi-Fi reliability. Order by ~Oct 6-8.

## Route C — US-stock pixel-matrix build (VWBD-friendly)
- WiredWatts 12V bullets $0.38/px; HolidayCoro PixNode Net 8×4ft ($48-50, 2"/3" spacing) — naturally horizontal-safe; Falcon F16v5 $280 or Kulp K16A-B full build $725.
- @3" pitch: 40×80 = 3,200 px ≈ **$2.6-3k** + 15-25 labor hrs. @2": 60×120 = 7,200 px ≈ **$4.6-5.3k** + up to 40 hrs. Zero customs risk; order by Sep 26-Oct 3.
- Control: FPP playing rendered loops (export HTML visualizer to MP4) or ELM Pro (~$240, 16 universes ≈ ≤2,720 px) for live browser capture. Looks DIY up close — mitigate with black net + scrim.

## Bottom line
| Route | Landed | Px | Commit | HDMI-browser |
|---|---|---|---|---|
| China SPI mesh P25-P50 | $3.5-8k | 7,200-28,800 | Sep 23-26 | No (capture/loops) |
| China NovaStar mesh | $9.5-16.5k | 7,200-18,400 | Sep 23-26 | **Yes** |
| led-cloth vision cloth | $1-2.5k | ~2,200 | ~Oct 1-3 | No |
| US rental | $15-30k/2wk | any | ~Oct 6 | **Yes** |
| 3× Twinkly Lightwall | ~$3.6k | 3,360 | ~Oct 6-8 | No |
| US pixel-net build | $2.6-5.3k + labor | 3,200-7,200 | Sep 26-Oct 3 | No (FPP loops/ELM) |
