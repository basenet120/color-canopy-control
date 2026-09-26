# 01 — Project brief

## The activation
- **What:** Home Depot × Hearst (House Beautiful "Whole Home" 2026) immersive retail experience showing connected living through **Hubspace**, Home Depot's smart-home platform.
- **Where:** 284 Meeting Street, Charleston SC (near Ansonborough / Marion Square). 34′ × 37′ planning area.
- **Dates (all 2026):**
  - Load-in / install: Oct 20–23 — the canopy sculpture must be installed by **Fri Oct 23**
  - Home tours (public): Oct 23–25 (Oct 25 is a Sunday — Hubspace phone support is closed Sundays)
  - Hospitality evening: Oct 29
  - Strike: Nov 1
- **Who:**
  - Clients: The Home Depot (Hubspace brand team) and Hearst / House Beautiful
  - Creative/production agency: For The Stories
  - Scenic fabrication, rigging, install: **VWBD** (Venture Works Build Design)
  - Control system + software (this repo): Alon, tech lead

## Spaces
1. **The Color Canopy** (hero) — overhead sculptural light installation with a guest touchscreen kiosk. See `02-color-canopy-design.md`.
2. **Traditional Entryway** — Feather River door + Defiant Hubspace smart deadbolt ("A smarter welcome").
3. **Holiday Living Room** — table/floor lamps, lit trees/garland, warm perimeter wash ("Set the holiday mood").
4. **Bedroom + Comfort** — bedside lamps, perimeter uplighting, humidifier, possible ceiling fan ("Find your evening glow").
5. **Glow Garden** — outdoor seating, string lights, pathway lights, fountain on an outdoor smart plug ("Bring the garden to life").

Each vignette gets one staff-operated control tablet; the canopy gets a guest-facing kiosk.

## What Home Depot's product team told us (product call, Sep 2026)
- Hubspace devices can be grouped; no per-account device limit other than what the Wi-Fi can handle.
- **Use separate Hubspace accounts per vignette** so a guest playing with one room can't change another.
- Bring our own network (mesh / multiple APs); the building's Wi-Fi is weak. All devices on one 2.4 GHz network is fine.
- **No official API** for buttons/touchscreens. They like the idea of dimmers.
- Fountain via the outdoor smart plug.

## Scope decisions already made (by Alon)
- Control surfaces: one device at each of the 4 vignettes + a special canopy display (5 surfaces) + an ops dashboard for the tech team.
- v1 stays tethered to features the stock Hubspace app already offers (on/off, brightness, color, white temperature, built-in effects/presets). Fancier interactions are phase 2.
- Built as a show tool now, architected to be productized later (config-driven, white-label-able).
- Every bulb/fixture a guest can see or touch is genuine Hubspace hardware; kiosk copy must accurately describe the Hubspace functionality being demonstrated.

## Budget scope
- VWBD's production allowance covers canopy fabrication, install and strike only. Technology, software and testing are separate scope and budget.

## Source documents (in the local `Ref Docs/` folder — kept out of git; everything the build needs is summarized in these context docs)
- `Color Canopy.pdf` — VWBD's current canopy design deck (supersedes the 200-bulb concept)
- `Home_Depot_House_Beautiful_Sept14_Updated.pdf` — agency concept deck (spaces, Lowcountry Modes, signage)
- `Hubspace_Interactive_Canopy_Tech_Roadmap.pdf` — VWBD's earlier technical roadmap (acceptance targets, validation ladder, handover rules) — written for the 200-bulb design but its principles still apply
- `Hearst x Home Depot Hubspace tech qq & requirments.md` — notes from the Hubspace product call

Our own briefs (Canopy Control Brief, Pixel Canopy Sidesteps, and the Sep 21 question docs for VWBD and Hearst/Home Depot) are also kept locally; their substance is folded into these docs and `research/`.
