# Color Canopy guest panel: design spec (Hubspace look)

The iPad on the stand at the Home Depot × Hearst "Whole Home" activation. Guests choose one of six scenes, set brightness, and turn the canopy on or off. The panel should look like a Hubspace **device control sheet** (dark header with the device name and the master switch, then a light rounded sheet holding the controls) and use the Hubspace home screen's section headers, chips and cards. It must stay readable and easy to use with fingers at arm's length.

`REF/` = the Hubspace reference set (App Store / Play screenshots, Home Depot product and support pages, press reviews) gathered for this design. Those images are third-party screenshots and are **not** committed; the paths below name them so the choices can be traced. The values taken from them are written out in this spec.

Data contract (unchanged, from `conductor/routes/guest.py`):
- `GET /api/kiosk/panel` returns `scenes[] {id, name, mood, description, palette[{name,hex}], frame[30][r,g,b], brightness_draft}`, `brightness_range [10,100]` and `state {power, brightness, scene, look, phase: ready|changing|resting|paused|cooldown, message, feature, cooldown_remaining_s}`. Each scene also carries `solid` (the one-colour fallback) and `state.look` is `"gradient"`, `"solid"` or null, so the preview draws what is actually lit.
- Writes: `POST /api/kiosk/scene|brightness|power`. A refusal returns `{detail, retry_after_s?}`, and `detail` is shown verbatim in a toast.

Units: CSS px. iPad Safari renders 1 CSS px = 1 pt.

---

## 1. Design tokens

### 1.1 Colour

| Token | Value | Use | Source (REF/…) |
|---|---|---|---|
| `--brand` | `#FA4616` | Hubspace orange: hub glyph badge, selected-card ring accent, primary pill fill (large bold text only), section-header rule tint | `site/hubspace-logo.svg` (exact fill), `appstore/icon-itunes-artwork-1024.png` |
| `--brand-text` | `#C9370E` | Orange **text** on the light sheet: section headers, "Now showing" (4.63:1 on `#F2F1F7`). This is the accessibility-darkened version of the app's red-orange header text. | derived; app uses ≈`#FA4516` in `lights/pcworld_permanent_2024/pcworld_app_scenes.png` ("BACKYARD HUBSPACE") |
| `--brand-press` | `#D63A0E` | Pressed fill for brand pills, and the fill of any brand pill whose text is under 18.66px bold (white on it is 4.7:1) | derived |
| `--rule-salmon` | `#F0CDC7` | 1px rule after a section header | `lights/gearbrain_hubspace2_app.png` (rule after "BACKYARD") |
| `--switch-on` | `#FE9F2B` | Master power switch track when ON | `lights/pcworld_permanent_2024/pcworld_app_scenes.png` (header switch), `appstore/iphoneX-03-Controls.png` (#FE9F29) |
| `--switch-off-dark` | `#5A5A5F` | Master switch track when OFF (it sits on the dark header) | assumption; dark-mode off track from `lights/crop_techradar_darkmode_home_phone.jpg` |
| `--switch-knob` | `#FFFFFF` | Switch knob | all toggle refs |
| `--header-base` | `#323232` | Dark header base | `support/crop-eco05-string-light-phone-2025.png` |
| `--sheet` | `#F2F1F7` | Bottom-sheet and page background | `lights/pcworld_permanent_2024/pcworld_app_scenes.png` (third panel) |
| `--card` | `#FFFFFF` | Scene cards, Now-showing card, feature card | `appstore/iphoneX-07-Rooms.png` (CREATE A ROOM card) |
| `--grabber` | `#C3C3C5` | Sheet grab handle | `play/screenshot-03.jpg`, `appstore/iphoneX-05-Groups.png` |
| `--hairline` | `#DCDBE0` | Dividers, bottom-bar top rule | `lights/hd_stringlight_hb10035/hb10035_c3.jpg` |
| `--text` | `#000000` | Primary text | `appstore/iphoneX-06-Scheduler.png` |
| `--text-2` | `#58575D` | Secondary text: mood, help copy, feature sentence (6.37:1 on sheet, 7.15:1 on white) | `lights/pcworld_permanent_2024/pcworld_app_scenes.png` (brightness help text) |
| `--icon` | `#38373F` | Line icons (sun, list) | `lights/pcworld_permanent_2024/pcworld_app_scenes.png` |
| `--ring-selected` | `#6E6D72` | Ring around the selected mode disc. Here it is the outer ring of the selected scene card (2px, with a gap). | `lights/pcworld_permanent_2024/pcworld_app_scenes.png` (preset disc ring) |
| `--slider-fill-a` | `#FFF6D6` | Start of the brightness fill gradient (cream) | `appstore/iphoneX-05-Groups.png` |
| `--slider-fill-b` | `#FFE051` | End of the brightness fill gradient (yellow) | `lights/pcworld_permanent_2024/pcworld_app_scenes.png` (#FFE051), `lights/ecoactions_holiday_05.png` (#FFDF51) |
| `--slider-rest` | `#A0A0A0` | Unfilled part of the brightness track | `appstore/iphoneX-05-Groups.png` |
| `--chip-grey` | `#E6E5EA` | Inactive chip, disabled pill | `lights/pcworld_permanent_2024/pcworld_app_scenes.png` ("Lights" chip) |
| `--chip-ok` | `#2E7D32` | "On" status chip fill (white text 5.13:1). The app's `#4CB050` with white text is only 2.75:1, so it is darkened here. | derived from `appstore/iphoneX-06-Scheduler.png` ("Turn On" chip) |
| `--toast` | `rgba(50,50,50,0.94)` | Toast background | assumption (dark header tone) |
| `--scrim` | `rgba(0,0,0,0.55)` | Behind the debug drawer and the paused card | `lights/pcworld_permanent_2024/pcworld_app_scehduling.png` (#6F6F71 scrim) |
| `--selected-row` | `#DCE8F6` | Selected row in the debug drawer | `lights/pcworld_permanent_2024/pcworld_app_scehduling.png` |

Light theme only. The panel ignores `prefers-color-scheme` on purpose: a kiosk must look the same on every iPad, and the light sheet is the reference look. Every page still sets `body { background: var(--header-base) }` so overscroll never shows white above the header.

**Header surface.** The app uses a blurred room photo here. The panel gets the same feel without a photo:
```
background:
  linear-gradient(rgba(0,0,0,.38), rgba(0,0,0,.38)),
  radial-gradient(60% 140% at 18% 30%, <scene palette[0]> 0%, transparent 70%),
  radial-gradient(55% 140% at 82% 70%, <scene palette[last]> 0%, transparent 70%),
  #323232;
filter: none; /* the radial falloff provides the blur */
```
Cap each palette blob at `opacity .30` (draw the blobs on a pseudo-element). This keeps white title text ≥ 4.5:1 even for the palest scene (Lowcountry Calm `#F7F2E7`). **Verify** by sampling the pixel behind the title glyphs. When power is off or the scene is unknown, the header uses plain `#323232`. When the scene changes, the blobs crossfade over 600 ms.

### 1.2 Typography

**Typeface: Quicksand** (SIL OFL 1.1, Google Fonts). Rendering it beside crops of the app matches its glyphs (`lights/_preview/font_vs_app.png`). Vendor `Quicksand[wght].woff2` (variable, 300–700, Latin subset, about 40 KB) under `web/vendor/fonts/` with its `OFL.txt`. Load with `font-display: block` and a 1 s preload, so guests never see the fallback flash.
`font-family: "Quicksand", ui-rounded, "SF Pro Rounded", system-ui, -apple-system, sans-serif;` (iPadOS falls back to SF Pro Rounded, the closest system face.)
Numerals: `font-variant-numeric: tabular-nums` on the brightness value and countdowns.
The status bar, the Home Depot web pages and Helvetica Neue are **not** used.

| Role | Size / line-height | Weight | Tracking | Colour | App source |
|---|---|---|---|---|---|
| Header title ("The Color Canopy") | 40/48 (landscape 1180), 34/42 (1024), 40/48 (portrait) | 500 | 0 | #FFF, `text-shadow: 0 1px 3px rgba(0,0,0,.35)` | `appstore/iphoneX-03-Controls.png` "Ceiling Light" |
| Header subtitle / message | 18/24 | 500 | .01em | `rgba(255,255,255,.88)` | — (assumption) |
| Section header ("CHOOSE A SCENE", "NOW SHOWING") | 15/20 | 700 | .2em, uppercase | `--brand-text` | `lights/gearbrain_hubspace2_app.png` "BACKYARD" |
| Scene card name | 21/26 (1180), 19/24 (1024) | 600 | 0 | `--text` | room tile names in `appstore/iphoneX-07-Rooms.png` |
| Scene card mood | 16/21 (1180), 15/20 (1024) | 500 | .01em | `--text-2` | preset row description in `pcworld_app_scenes.png` |
| Now-showing name | 28/34 | 700 | 0 | `--text` | — |
| Now-showing description | 17/24 | 500 | .01em | `--text-2` | — |
| Control label ("Brightness:") | 20/26 | 500 | .01em | `--text` | `pcworld_app_scenes.png` "Brightness:" |
| Control value ("75") | 26/26 | 700 | 0 | `--text` | "100%" bold |
| Value unit ("%") | 16 | 700 | 0 | `--text` | small % sign in `iphoneX-05-Groups.png` |
| Help text | 15/21 | 500 | .01em | `--text-2` | "The brightness may vary over time…" |
| Feature kicker ("HUBSPACE FEATURE") | 13/16 | 700 | .18em, uppercase | `--brand-text` | section-header style |
| Feature sentence | 17/23 | 500 | .01em | `--text` | — |
| Chip label | 16/20 | 600 | .01em | per chip | `iphoneX-06-Scheduler.png` chips |
| Toast | 18/24 | 600 | .01em | #FFF | — |
| Attract headline | 56/64 | 500 | 0 | #FFF | — |
| Attract prompt | 24/30 | 600 | .02em | #FFF | — |

Base tracking for all Quicksand text is +0.01em, because the app runs slightly loose (`lights/_preview/font_vs_app.png`).

### 1.3 Radii, shadows, spacing

| Token | Value | Basis |
|---|---|---|
| `--r-sheet` | 24px (top corners only) | sheet in `pcworld_app_scenes.png` (≈ 24pt) |
| `--r-card` | 16px | app cards are about 8pt on phone; doubled for the iPad scale (`iphoneX-07-Rooms.png`) |
| `--r-pill` | 999px | chips, primary buttons, slider track |
| `--r-tile` | 3px | snake tiles (`hb10035_c3.jpg`) |
| `--sh-card` | `0 1px 2px rgba(0,0,0,.06), 0 4px 12px rgba(0,0,0,.06)` | soft shadow under the CREATE A ROOM card |
| `--sh-card-press` | `0 1px 2px rgba(0,0,0,.08)` | pressed state |
| `--sh-knob` | `0 2px 6px rgba(0,0,0,.22), 0 0 0 .5px rgba(0,0,0,.08)` | slider and switch knobs |
| `--sh-toast` | `0 8px 24px rgba(0,0,0,.25)` | — |
| Spacing scale | 4, 8, 12, 16, 20, 24, 32, 40, 48 | — |
| Sheet side padding | 32 (1180), 24 (1024), 32 (820 portrait) | — |
| Column gap | 32 (1180), 24 (1024) | — |
| Card grid gap | 16 | 15pt room-tile gap in `iphoneX-07-Rooms.png` |

### 1.4 Icons

Inline SVG, monoline, 24×24 viewBox, `stroke: currentColor`, `stroke-width: 2`, round caps and joins, colour `--icon`. This matches the thin line icons in `pcworld_app_scenes.png` (☰ sun, list) and `iphoneX-05-Groups.png`. The set:
- **sun**: circle r=4 plus 8 rays. Used for Brightness.
- **list** (3 dots + 3 lines): used for Scene, mirroring "☰ Preset:".
- **hub glyph**: three arcs over the open-bottom pentagon house with a square window. Trace it from `appstore/icon-itunes-artwork-1024.png`: stroke 3.4% of size, white, on a `--brand` rounded square with radius 22% of size.
- **check** (2.5px stroke): used on the selected card.
- **spinner**: a 270° arc of 2.5px stroke rotating at 900 ms per turn, linear. Only shown during `changing`.

No emoji. No icon font. No Schedule or Settings icons: they would promise features the guest panel doesn't offer.

### 1.5 Logo usage
- **Bottom bar, right:** the official "powered by HUBSPACE™" vector (`site/hubspace-logo.svg`, grey `#53565A` and orange `#FA4616` arcs, 300×129 viewBox), 40px tall, unmodified. Clear space ≥ 16px all round. Never recolour or retype the wordmark (its A is a house drawing).
- **Attract and resting screens:** the white 2025 lockup (`site/hubspace-logo-2025-white.png`, 858×145) at 36px tall on the dark surface.
- **Hub glyph badge** (§1.4) at 32px beside the feature line. This echoes the centre FAB and the app icon.
- **Favicon / apple-touch-icon:** generate from `appstore/icon-itunes-artwork-1024.png` (180px and 32px).
- No Home Depot or Hearst marks are on the guest panel itself.

---

## 2. Components

### 2.1 Header (the device-sheet header)
- Height: `env(safe-area-inset-top) + 112px` (1180 and 820 portrait); `+ 100px` (1024).
- Content row, vertically centred in the 112px: title at left (padding-left = sheet side padding). The **master switch** sits at the right edge (padding-right = side padding) on the title's baseline row, as in `appstore/iphoneX-03-Controls.png`.
- Subtitle line directly under the title, 4px gap: `state.message` if non-empty. Otherwise, by phase: "Choose a scene below." (ready), "Changing the lights…" with the 18px spinner before it (changing), "Lights are off. Tap a scene or the switch to turn them on." (power false). Text only, no chips.
- There is no X close button (`appstore/iphoneX-03-Controls.png` has one). Guests can't leave the panel.
- To the left of the switch, 12px gap: the label "On" or "Off", 18/24 600 white. The app has no label; it is added for guests and screen readers.

### 2.2 Master power switch (iOS UISwitch style, scaled 1.5×)
- Visual track 76×46, radius 23. Knob 40×40 white circle, inset 3px, `--sh-knob`.
- ON: track `--switch-on`, knob at right. OFF: track `--switch-off-dark`, knob at left.
- Hit area: 96×72 transparent box centred on the track. Together with the "On"/"Off" label the hit area extends left to cover the label (min 140×72).
- `role="switch"`, `aria-checked`, `aria-label="Canopy lights"`.
- Tap toggles it optimistically (the knob slides 200 ms, `cubic-bezier(.3,.7,.4,1)`), then POSTs `/api/kiosk/power`. If the request is refused, it snaps back and shows a toast with `detail`.
- Pressed: the knob widens to 48px (iOS stretch) while the finger is down.
- When power is unknown (`null`): track `--switch-off-dark` at 60% opacity, knob centred, label "—". It is still tappable and sends `on: true`.

### 2.3 Sheet
- Background `--sheet`, `border-radius: 24px 24px 0 0`. It overlaps the header by 0, starting directly below it; the header colour shows behind the rounded corners.
- Grabber: 40×5, radius 3, `--grabber`, centred, 8px from the sheet top. Decorative only, `aria-hidden`, not draggable.
- Content starts 28px below the sheet top.

### 2.4 Section header
- The text (§1.2 role), then a 1px `--rule-salmon` rule that fills the remaining width. The rule is vertically centred on the cap height, with a 16px gap after the text. Copied from "BACKYARD" in `lights/gearbrain_hubspace2_app.png`.
- An optional right-side slot holds the cooldown text (§3).
- Height 24, margin-bottom 16.

### 2.5 Scene card (Hubspace card plus the custom-pattern preview)
Six cards in fixed order from `config/scenes.yaml`.
- `<button>`, white `--card`, radius 16, `--sh-card`, padding 16, `overflow: hidden`.
- **Palette strip** at the top: full card width minus padding, 28px tall, radius 999. It is 30 equal segments from `scene.frame` (`rgb(r,g,b)`), with no gaps. It shows the scene's colours in order, not a picture of the canopy: the canopy's own picture is Now showing (§2.6). The outer pill has `box-shadow: inset 0 0 0 1px rgba(0,0,0,.06)` so ivory scenes don't vanish on white. This mirrors the striped track of the custom-mode brightness slider in `hb10035_c3.jpg`.
- 14px gap, then **name** (2 lines max, clamp), 4px gap, then **mood** (1 line, ellipsis).
- 12px gap, then **palette swatches**: palette circles 22px, 6px apart, each with `box-shadow: inset 0 0 0 1px rgba(0,0,0,.08)`. They mirror the colour preset circles in `iphoneX-03-Controls.png`. They are not interactive and are `aria-hidden`; the card's label carries the names.
- Card min-height: see §4. Content is top-aligned and swatches sit at the bottom (`margin-top: auto`).
- **Selected** (`state.scene === id`): the mode-disc ring treatment, i.e. `outline: 2px solid var(--ring-selected); outline-offset: 3px;`. The top-right corner gets a 28px `--brand` circle with a white check (14px). `aria-pressed="true"`.
- **Pending** (just tapped, phase `changing`, `state.scene` not yet equal): the same ring in `--brand` with a 1.2 s ease-in-out opacity pulse (1 → .45 → 1). The spinner replaces the check.
- **Pressed**: `transform: scale(.97)`, `--sh-card-press`, 90 ms. Released: back to normal over 160 ms.
- **Disabled** (cooldown for other cards, paused, resting): opacity .45, no press transform, and `aria-disabled="true"`. Disabled cards still accept taps and answer with an explanatory toast; they are never silent.
- Accessible name: "{name}. {mood}. Colors: {palette names joined}."

### 2.6 Now-showing card
- White card, radius 16, padding 24, `--sh-card`.
- Section header "NOW SHOWING", with the list icon to its left at 20px, `--icon`.
- Scene name (28/700), then description (17/24 `--text-2`, up to 3 lines).
- **The canopy of 270 lights** (`svg#canopy-preview`, `role="img"`; drawn by `web/kiosk/canopy-preview.mjs`): the 3 × 3 panels as nine faint outlines (`--hairline`), each with its 30 lights as dots where they really hang (rows of 8 and 7, staggered, 10 in apart). The drawing is in canopy inches from `panel.canopy`, so it is the ceiling, never the cable: each panel's heads come in lattice order, whatever its wiring.
- **The fins (D74):** with the canopy's fin map the drawing is 237 × 132 in: the 225 × 120 canopy plus the 6 in apron all round, its outline dashed in `--hairline`. The 650 fins (VWBD drawing A-20-1, `config/fins.json`) are each a 5 in stroke along its plan axis, over smaller head dots (1.6 in instead of 3.6 in). A fin's colour is the weighted mix, w = exp(−(d / 6)²), of its four nearest heads' colours from `state.lit`; its opacity is the level's × its drop's (14″ 0.45 … 24.5″ 1) × the light reaching it (√ of the lit weight, at most 1). The picture is laid out only when the geometry or view changes (a new poll of the same canopy compares its key first), and a fin is repainted only when a head's paint changes.
- **What each panel shows** comes from `state.lit` (conductor/canopy_view.py), which says per panel what may truthfully be claimed. The page adds nothing of its own:

  | `state.lit.modules[].state` | When | Dots | Fins (D74) |
  |---|---|---|---|
  | `lit` | the lights read back the look | the look's colours (`colors` per head, or one `color`) | the mix of the lit heads' colours |
  | `changing` | a change is on its way, or a retry | the target colours, with a soft 1.2 s opacity pulse (1 → .45 → 1) | the mix, with the same 1.2 s pulse (a fin is changing when at least half its lit weight is) |
  | `dark` | the panel is left out, or not registered | unlit `#D3D2D8` | unlit `#D3D2D8` at half its drop's opacity |
  | `off` | the guest turned the canopy off and the panel reads back off (or it isn't in the look) | unlit `#D3D2D8` (the name reads "Lights are off") | unlit `#D3D2D8` at half its drop's opacity |
  | `unknown` | a test page holds it, it is stuck, the canopy is resting (on or off), a power-off hasn't read back yet, or its look isn't known | an outline only (`#A0A0A0`), no fill: no colour claim | a dashed `#A0A0A0` line, no colour claim, once 10% or more of its weight is on unknown heads |

  Colours are full strength; the level is drawn as opacity, `dim_floor + (1 − dim_floor) × level / 100` (floor 0.35, so 10% never reads as off). Dots and fins fade to a new colour over 600 ms; with reduced motion the fins snap to the current frame (no fade) and don't pulse.
- **Motion** (`state.lit.motion`, phase-synced to the server's clock: `server_now` plus the local time since it was read): a `ripple` switches ring j at `t0 + j × stagger_s` and shows each ring's previous colours until then (a page with no previous picture shows those panels as unknown); `steps` shows frame `floor((now − t0) / step_s)` (nine colours in grid order, null = dark) and after `ends_at` holds the frame showing then (the last one sent). Any other kind shows the still picture. The page animates only while a motion has a frame to come.
- **Orientation**: the geometry is the reflected-ceiling view (looking up, origin at the canopy's top-left, as config/canopy.yaml lists it). `canopy.view` (`kiosk.preview` in config/conductor.yaml, set at install) turns it to match what a guest at the stand sees overhead: `mirror_x` flips left and right first, then `rotate_deg` turns it clockwise by quarter turns. The default is unmirrored and unturned; a quarter-turned view letterboxes into the card.
- **Accessible name**: "Preview: 2 of 9 panels lit" (plus ", N changing" and ", N not shown" when they apply), or "Preview: the canopy is off".
- **Fallback, the serpentine of 30 lights** (when `state.lit` is null: no look yet, or an older server), copied from `hb10035_c3.jpg` and `pcworld_app_scenes-2.png`: 3 rows of 10 square tiles. Tile size = (card inner width − 2×cap) / 10, max 30px, with 2px gaps. Rows run L→R, R→L, L→R. The ends connect through half-round U-turn caps of the same tile thickness: at the right between rows 1→2, at the left between rows 2→3. Head order: tile i of the snake = `frame[i]`. No numbers. The tile corner radius is 3; caps are full semicircles.
  - When power is off: every tile is `#D3D2D8` (the unattached-puck grey from `pcworld_app_scenes-2.png`), and the name line reads "Lights are off".
  - When the scene is unknown: tiles `#D3D2D8`, name "No scene yet", description "Choose a scene to light the canopy."

### 2.7 Brightness control (Hubspace slider)
- Label row: sun icon 24, 12px gap, "Brightness:" (label role), 10px gap, the value (e.g. "75", value role) followed by "%" (unit role). Copied from `iphoneX-05-Groups.png`.
- Help text, 8px below: "The brightness selected will apply to all the lights." This adapts the app's "The brightness and white temperature selected will apply to all the bulbs." and is true for both targets.
- Slider, 20px below:
  - Hit area: full width, 72px tall.
  - Visual track: 36px tall, radius 18, centred vertically.
  - Fill: from the left edge to the knob centre, `linear-gradient(90deg, var(--slider-fill-a), var(--slider-fill-b))`. The rest of the track is `--slider-rest`.
  - Knob: white horizontal pill 64×44, radius 22, `--sh-knob`, centred on the value.
  - The value maps the range 10–100 across the track. Below 10 is impossible.
  - Pressed knob: scale 1.08. The label value updates live while dragging.
- Pointer handling: `pointerdown` anywhere in the hit area jumps the value to that x and starts the drag. Capture the pointer. Send `POST /api/kiosk/brightness` **once, on `pointerup`**, with the final value (D69: every guest input shares the engine's cooldown, so values sent while dragging held the guest's real level back by a whole cooldown); nothing is sent when the release changes nothing. The UI never waits for the reply to move the knob.
- After release, the knob shows the guest's value for 3 s, then follows `state.next.brightness` while the server still holds the guest's level (queued, or waiting out the cooldown: "Your brightness is next. It reaches the lights in a moment."), else `state.brightness`. The switch follows `state.next.power` the same way.
- Keyboard and AT: `role="slider"`, `aria-valuemin=10`, `aria-valuemax=100`, `aria-valuenow`, `aria-valuetext="75 percent"`. Arrow keys step by 5.
- Disabled (paused or resting): opacity .45, with the knob still visible at the current value.
- No −/+ buttons. The 72px hit area and jump-to-tap make the slider usable on its own, and the app has none.

### 2.8 Hubspace feature line (bottom bar)
- Bottom bar: full sheet width, 1px `--hairline` top rule, height `88px + env(safe-area-inset-bottom)`, padding 0 side-padding. This is where the app puts Schedule/Settings (`iphoneX-03-Controls.png`, `pcworld_app_scenes.png`).
- Left (flex 1): hub glyph badge 32px, 16px gap, then two lines: kicker "HUBSPACE FEATURE" and the sentence `state.feature`. When `feature` is null: kicker "HUBSPACE", sentence "Choose a scene to see the Hubspace feature at work."
- The sentence is exactly the server's text. The front end never writes its own feature claims (contract: kiosk copy must be true). Clamp to 2 lines; never truncate mid-sentence on 1180 and 1024 (the sentence is short by contract).
- Right: the "powered by HUBSPACE™" SVG, 40px tall.
- `aria-live="polite"` on the sentence.

### 2.9 Status chip (cooldown countdown)
- Pill height 36, padding 0 14px, radius 999, label in chip role.
- Cooldown: fill `--chip-grey`, text `--text`, "New scene in 6 s". The countdown is derived from `cooldown_remaining_s` and `server_now` against the local clock.
- It sits in the right slot of the "CHOOSE A SCENE" section header.

### 2.10 Toast
- Bottom-centre, 24px above the bottom bar. Max width 560, padding 16px 24px, radius 999 when one line (radius 20 for two lines), `--toast`, `--sh-toast`, toast text role. Optional leading 20px icon (spinner or info "i" circle).
- Enters by sliding 12px up with a fade over 180 ms. Holds 3.5 s, or `max(3.5, retry_after_s)` with a live countdown when `retry_after_s` is present. Exits with a fade over 180 ms. A new toast replaces the old one.
- Content: `detail` verbatim from a refusal, or a network fallback: "Hang on, we're reconnecting." Never an HTTP code.
- `role="status"`, `aria-live="polite"`. Tapping the toast dismisses it.

### 2.11 Paused card (phase `paused`)
- `--scrim` over the sheet only. The header stays visible with the switch disabled.
- A centred white card (radius 24, padding 32, max-width 520) with the hub glyph badge (48px), the title "Paused for a moment" (24/700) and the body `state.message`, or "Our team is adjusting the canopy. It will be back shortly." (17/24 `--text-2`).
- Taps on the scrim do nothing, with no toast. It disappears when the phase changes; fade 240 ms.

### 2.12 Resting screen (phase `resting`)
- Full screen `#2A2A2E` (dark-mode charcoal, `lights/crop_hd_video_darkmode_stringlight_list.png`). A slow radial glow uses the last scene's palette: 40% opacity, scale 1→1.06 over 8 s, alternating.
- Centre: white 2025 lockup (36px), 32px gap, "The Color Canopy is resting." (40/48 500 white), then `state.message` or "Come back soon." (20/28 500 `rgba(255,255,255,.85)`).
- There are no controls. Taps do nothing.

### 2.13 Attract (idle) state
- Trigger: 90 s with no `pointerdown`, while phase is `ready` or `cooldown`. It never triggers during `changing`, `paused` or `resting`.
- Visual only: it never sends a command, and the lights keep whatever is showing.
- Full screen, covering the header and sheet. Background: the header surface (§1.1) at full screen, cycling its palette blobs through the six scenes every 6 s with a 1.5 s crossfade.
- Content, centred:
  - Hub glyph badge 72px.
  - "The Color Canopy" (attract headline).
  - The mood of the scene currently being cycled (24/30 500, 85% white).
  - A row of the six preview strips: each 120×16, gap 12, the cycled one at full opacity and the others at 40%.
  - "Tap anywhere to light the canopy" (attract prompt) in a pill with `rgba(255,255,255,.16)` fill, 1px `rgba(255,255,255,.35)` border, padding 16px 32px. The pill breathes with an opacity pulse of 1 → .7 over 2.4 s.
  - The white lockup at 36px, bottom-right (32px inset), mirroring the HUBSPACE wordmark at bottom right of the app home photo (`lights/gearbrain_hubspace2_app.png`).
- The first touch only dismisses the attract screen (fade 240 ms). That touch is swallowed and never reaches a control underneath.

### 2.14 Connection lost
The panel reads `/api/kiosk/panel` the moment the server pushes a change on the live socket (topic `canopy`, used only as a nudge; D69) and every 2 s regardless; each read times out after 6 s. If it fails 3 polls in a row: show a toast that stays up until reconnected, "Hang on, we're reconnecting." Controls are disabled at opacity .45. Recovery dismisses it silently.

---

## 3. States, mapped to UI

| `state.phase` / condition | Header subtitle | Scene cards | Brightness | Switch | Extra |
|---|---|---|---|---|---|
| `ready` | message or "Choose a scene below." | enabled | enabled | enabled | — |
| `changing` | spinner + message or "Changing the lights…" | target card pending; others enabled but a tap gives the toast "One moment, the lights are changing." | enabled | enabled | — |
| `cooldown` | message or "Choose a scene below." | others disabled (.45); the selected card stays full opacity | enabled | enabled | chip "New scene in N s"; a tap on a disabled card gives the toast "You can choose a new scene in N s." |
| `paused` | message | disabled | disabled | disabled | paused card (§2.11) |
| `resting` | — | — | — | — | resting screen (§2.12) |
| `power === false` | "Lights are off. Tap a scene or the switch to turn them on." | enabled; a scene turns the lights on | enabled | OFF | canopy dots (or snake) grey, header plain #323232 |
| refusal 429/423/409/503 | — | — | — | — | toast with `detail` (+ countdown when `retry_after_s` is set) |
| idle 90 s | — | — | — | — | attract (§2.13) |

All text comes from the server where the server provides it. The front-end fallbacks above are the only copy the client writes.

---

## 4. Layouts

All layouts use one `.app` grid filling `100dvh`, `overflow: hidden`, with no page scroll. The header is fixed height; the sheet fills the rest.

### 4.1 Landscape 1180×820 (primary; iPad 10th gen / Air 11")
```
┌──────────────────────────────────────────────────────────────── 1180 ┐
│ HEADER 112 (+safe top)                                               │
│ 32 ▸ The Color Canopy (40/500)                     On ( ●━━ ) ◂ 32   │
│      Choose a scene below.                                           │
├──╮ SHEET r24 ─────────────────── grabber ───────────────────────╭─────┤
│  CHOOSE A SCENE ───────────────── [New scene in 6 s] │ NOW SHOWING   │
│  ┌────────┐ ┌────────┐ ┌────────┐                    │ ┌───────────┐ │
│  │ card   │ │ card   │ │ card   │   left col 724     │ │ name      │ │
│  └────────┘ └────────┘ └────────┘                    │ │ desc      │ │
│  ┌────────┐ ┌────────┐ ┌────────┐                    │ │ snake 3x10│ │
│  │ card   │ │ card   │ │ card   │                    │ └───────────┘ │
│  └────────┘ └────────┘ └────────┘                    │ ☼ Brightness: │
│                                                      │ ═════●═══     │
├──────────────────────────────────────────────────────┴───────────────┤
│ [hub] HUBSPACE FEATURE / sentence              powered by HUBSPACE   │ 88 (+safe bottom)
└──────────────────────────────────────────────────────────────────────┘
```
- Sheet padding: 28 top, 32 sides, 24 above the bottom bar.
- Columns: `grid-template-columns: 1fr 360px; gap: 32px`. The left column is 724 wide.
- Scene grid: `repeat(3, 1fr)`, gap 16, so cards are ≈230.7 wide. 2 rows; each row = (available height − 40 section header − 16 gap) / 2 ≈ 240.
- Right column, stacked with a 24 gap: the Now-showing card (the canopy picture 312 px wide, about 166 px tall; the serpentine fallback's tiles (360−48−2·cap)/10 ≈ 27px), then the brightness block. The brightness block has no card and sits directly on the sheet like the app; the slider spans the full 360.
- Header switch centre at x = 1180 − 32 − 38.

### 4.2 Landscape 1024×768 (iPad 9th gen and older)
- Header 100 (+safe top). Title 34/42.
- Sheet sides 24, column gap 24, right column 320. The left column is 632, so cards are ≈200 wide; card name 19/24, mood 15/20, swatches 20px.
- Card row height ≈ (768 − 100 − 88 − 28 − 24 − 40 − 16 − 20 safe) / 2 ≈ 226.
- Now-showing description is clamped to 2 lines. Snake tiles ≈ 24px.
- Bottom bar 80 (+safe). Lockup 36px tall.

### 4.3 Portrait 820×1180 (supported)
Single column, ordered like the app sheet: choices first, then brightness, then the pattern preview.
```
HEADER 112 (+safe) — title left, switch right
SHEET
  CHOOSE A SCENE ─────────── [chip]
  3 × 2 grid, gap 16, cards ≈ 241 × 220
  (24)
  ☼ Brightness: 75%  · help text
  ═════════●══════  (full width 756)
  (24)
  NOW SHOWING card — name + description on the left (flex 1), the canopy picture (or the serpentine fallback) on the right, width ≈ 340
BOTTOM BAR 88 (+safe) — feature left, lockup right
```
- Sides 32. Vertical budget: 28 + 40 + 456 + 24 + 150 + 24 + ≈190 = 912 ≤ 1180 − 112 − 88 − 20.

### 4.4 Rules shared by all layouts
- Breakpoints: `@media (orientation: portrait)` selects §4.3. In landscape, `@media (max-width: 1100px)` selects §4.2. The default is §4.1. Below 1000 px wide in landscape, scale with §4.2 values (the content fits down to 960).
- Never scroll during normal use. If the content overflows (e.g. very large Dynamic Type), only the sheet scrolls (`overflow-y: auto`) and the header and bottom bar stay put.
- Rotating re-lays out instantly with no animation.

---

## 5. Touch rules (iPad on a stand)
- **Target sizes:** every interactive element is at least 64×64. The switch hit area is 96×72 (140×72 including its label), the slider hit area is full width × 72, and scene cards are ≥ 200×220. Targets are at least 16px apart.
- **No hover:** nothing relies on `:hover`. Wrap all hover rules in `@media (hover: hover)` (debug view only).
- **Pressed states:** use `:active` plus a `.is-pressed` class set on `pointerdown` and cleared on `pointerup`/`pointercancel`/`pointerleave`, because iOS fires `:active` late without a touch listener. Add `document.addEventListener('touchstart', ()=>{}, {passive:true})`.
- **Activation:** buttons activate on `click`, which iOS sends immediately thanks to `touch-action: manipulation`. If the finger moves more than 10px between down and up, the press is cancelled.
- CSS on `html, body`:
  ```
  -webkit-user-select: none; user-select: none;
  -webkit-touch-callout: none;
  -webkit-tap-highlight-color: transparent;
  -webkit-text-size-adjust: 100%;
  touch-action: manipulation;
  overscroll-behavior: none;
  position: fixed; inset: 0; overflow: hidden;
  ```
  The slider uses `touch-action: none`. Images and SVGs get `-webkit-user-drag: none; pointer-events: none` (except controls).
- **Viewport:** `<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover">`. Also call `preventDefault` on `gesturestart`/`gesturechange` (iOS ignores `user-scalable=no` in some modes). Block double-tap zoom with `touch-action: manipulation` plus a `dblclick` preventDefault.
- **Web app:** `<meta name="apple-mobile-web-app-capable" content="yes">`, `apple-mobile-web-app-status-bar-style=black-translucent`, and an `apple-touch-icon` (§1.5). Ops should run the panel in Guided Access.
- **Safe areas:** the header adds `env(safe-area-inset-top)`, the bottom bar adds `env(safe-area-inset-bottom)`, and side paddings use `max(<padding>, env(safe-area-inset-left/right))`.
- **Context menu:** prevent `contextmenu` and long-press (`-webkit-touch-callout: none` plus no `title` attributes).
- **Multi-touch:** only the first pointer drives the slider; extra pointers are ignored.
- **Debounce:** ignore a second tap on the same scene card within 600 ms. Rapid taps on different cards send one request each; the server's cooldown answers with a toast.

## 6. Motion
- Durations: press 90 ms, release 160 ms, switch 200 ms, toast 180 ms, state fades 240 ms, header palette crossfade 600 ms, attract crossfade 1.5 s.
- Easing: `cubic-bezier(.3,.7,.4,1)` for movement, `ease-out` for fades.
- Nothing bounces, nothing spins except the spinner, and nothing loops except the pending pulse, a changing panel's pulse in Now showing (its dots and fins), the attract and resting glow, and the attract prompt breathing.
- `@media (prefers-reduced-motion: reduce)`: no transforms, scale or slide. Fades are capped at 120 ms. The pending pulse becomes a static `--brand` ring. A changing panel doesn't pulse, and a ripple or stepped run shows the current frame only, with no fades between frames. The spinner becomes three static dots (`•••`). Attract and resting use no glow animation; scenes swap with a 300 ms crossfade every 8 s.

## 7. Accessibility
- Text contrast is ≥ 4.5:1 everywhere (checked):
  - black on sheet 18.7
  - `--text-2` on sheet 6.37 and on white 7.15
  - `--brand-text` on sheet 4.63
  - white on `#323232` 12.8, and ≥ 4.5 on the tinted header (verify per §1.1)
  - white on `--chip-ok` 5.13
  - white on `--brand-press` 4.7
  - `--text` on `--chip-grey` > 15
  - white on the toast > 12
- Raw `#FA4616` is used only for non-text graphics (badge, check circle, ring). It is never used for text smaller than 18.66px bold.
- Non-text contrast ≥ 3:1: switch-on `#FE9F2B` on `#323232` is 6.2. The slider knob is delimited by its shadow and the `#A0A0A0` remainder, and the value is always shown as text.
- Semantics: cards are `<button aria-pressed>` with full names. The canopy picture is `role="img"` with a summary name (§2.6); the serpentine fallback is decorative. The switch is `role=switch`, the slider `role=slider`. The feature line and toast are `aria-live=polite`. The header title is `<h1>`, section headers `<h2>`.
- Colour is never the only signal: the selected card has a ring plus a check, and power has the switch position plus the "On"/"Off" text.
- Text uses rem with root 16px; layouts tolerate 120% text before the sheet scrolls.
- `lang="en"`, US spelling ("Color"), Title Case labels, sentence-case help copy ending in periods (the app's tone).

---

## 8. Ops debug mode (`?debug=1`, never shown to guests)
- Activation: `?debug=1` on the panel URL. The panel itself is unchanged, plus a 56×56 round button fixed at the bottom-left inside the bottom bar: `#323232`, white "bug" glyph, 70% opacity. It opens the drawer. Without `?debug=1` none of this exists in the DOM.
- **Drawer:** slides from the right, 420 wide (100% in portrait below 900), over `--scrim`. Styled like the app's "Select Product" sheet (`pcworld_app_scehduling.png`):
  - Header band `#DDDCE2`, 64 tall, X at left (64×64), centred title "Debug".
  - Body `--sheet`, scrollable.
  - Sections, each a white card radius 12:
    - **Target:** `target`, `target_label`, `simulated` (chip: "Simulator" grey / "LIVE" `--brand-press` white text).
    - **State:** the raw `state` JSON, monospace `ui-monospace, SFMono-Regular, Menlo, monospace` 13/18.
    - **Capabilities.**
    - **Commands:** the last 50 from `GET /api/kiosk/debug` as rows `at · action · outcome · latency_ms`, with errors in `--brand-text`. A tapped row expands to its params and error and gets the `--selected-row` fill.
    - **Controls:** "Show attract now", "Simulate toast".
  - If `/api/kiosk/debug` returns 403: the card reads "Sign in as ops to see command history."
- **iPad-frame preview** (a tab in the drawer header or a separate `?debug=1&frame=1` view):
  - Page bg `#1B1B1B`.
  - A centred device frame: bezel `#0E0E0E`, radius 36, bezel 18px, 1px `#3A3A3A` outer edge. It holds an `<iframe src="/kiosk/">` (no debug flag) at exactly 1180×820, 1024×768 or 820×1180, scaled with `transform: scale()` to fit the window with 32px margin.
  - Segmented control above it (the app's 1/2/3 pattern control, `hb10035_c3.jpg`): selected `#4EAD51` white text, others `#E6E6E8`, 44 tall, radius 8. Labels: "1180×820", "1024×768", "820×1180".
  - A toggle "Show safe areas" overlays 24px top / 20px bottom bands at 20% `--brand`.
- The debug UI may use hover styles inside `@media (hover:hover)`.

---

## 9. Copy (client-side strings only)
- Title: "The Color Canopy"
- Section headers: "CHOOSE A SCENE", "NOW SHOWING"
- "Choose a scene below." · "Changing the lights…" · "Lights are off. Tap a scene or the switch to turn them on."
- "Brightness:" · "The brightness selected will apply to all the lights."
- "HUBSPACE FEATURE" · "Choose a scene to see the Hubspace feature at work."
- "New scene in {n} s" · "You can choose a new scene in {n} s." · "One moment, the lights are changing."
- "Paused for a moment" · "Our team is adjusting the canopy. It will be back shortly."
- "The Color Canopy is resting." · "Come back soon."
- "Tap anywhere to light the canopy"
- "Hang on, we're reconnecting."
- "No scene yet" · "Choose a scene to light the canopy." · "Lights are off"

---

## 10. Reference map

| Decision | Reference image (REF/…) |
|---|---|
| Overall structure: dark header with name + master switch over a light rounded sheet | `lights/pcworld_permanent_2024/pcworld_app_scenes.png` (panel 3), `appstore/iphoneX-03-Controls.png`, `support/crop-eco05-string-light-phone-2025.png` |
| Title left, switch right, large white title with soft shadow | `appstore/iphoneX-03-Controls.png`, `appstore/iphoneX-05-Groups.png` |
| Switch geometry and orange ON `#FE9F2B` | `lights/pcworld_permanent_2024/pcworld_app_scenes.png`, `appstore/iphoneX-03-Controls.png` |
| Sheet `#F2F1F7`, 24 radius, grabber | `lights/pcworld_permanent_2024/pcworld_app_scenes.png`, `play/screenshot-03.jpg` |
| Section header: uppercase, tracked, orange, trailing salmon rule | `lights/gearbrain_hubspace2_app.png`, `appstore/iphoneX-01-Hero-L.png` |
| Scene cards: white cards, 16px gap grid, soft shadow, two-column room grid scaled to 3 columns | `appstore/iphoneX-07-Rooms.png` |
| Card name + mood as title + one-line description | `lights/pcworld_permanent_2024/pcworld_app_scenes.png` (Select Preset sheet) |
| Selected = grey ring with gap | `lights/pcworld_permanent_2024/pcworld_app_scenes.png` (preset disc), `appstore/iphoneX-05-Groups.png` |
| Palette circles | `appstore/iphoneX-03-Controls.png` (Color Presets) |
| Striped preview strip | `lights/hd_stringlight_hb10035/hb10035_c3.jpg` (striped brightness track) |
| Serpentine 3×10 snake preview, grey empty tiles `#D3D2D8` | `lights/hd_stringlight_hb10035/hb10035_c3.jpg`, `lights/pcworld_permanent_2024/pcworld_app_scenes-2.png` |
| Brightness label row (icon, label, bold value, small %) | `appstore/iphoneX-05-Groups.png`, `lights/pcworld_permanent_2024/pcworld_app_scenes.png` |
| Brightness help text pattern | `lights/pcworld_permanent_2024/pcworld_app_scenes.png`, `lights/hd_stringlight_hb10035/hb10035_40.jpg` |
| Thick slider, cream→yellow fill, grey remainder, white pill knob | `appstore/iphoneX-05-Groups.png`, `lights/ecoactions_holiday_05.png` |
| Bottom bar with hairline (feature line sits where Schedule/Settings sit) | `appstore/iphoneX-03-Controls.png`, `lights/pcworld_permanent_2024/pcworld_app_scenes.png` |
| Typeface Quicksand, weights, loose tracking | `lights/_preview/font_vs_app.png`, `play/crop-font-sample-tap-add-event.png`, `lights/gearbrain_hubspace2_new_features.png` |
| Brand orange `#FA4616`, logo grey `#53565A` | `site/hubspace-logo.svg`, `appstore/icon-itunes-artwork-1024.png` |
| "powered by HUBSPACE" lockup in the bottom bar | `site/hubspace-logo.svg` (render `site/hubspace-logo-render.png`) |
| White lockup on dark (attract/resting) | `site/hubspace-logo-2025-white.png` |
| Hub glyph badge (echoes centre FAB and icon) | `appstore/icon-itunes-artwork-1024.png`, `appstore/iphoneX-07-Rooms.png` (FAB) |
| Wordmark bottom-right on attract | `lights/gearbrain_hubspace2_app.png` (photo header) |
| Chips (pill, grey/green) | `appstore/iphoneX-06-Scheduler.png`, `lights/pcworld_permanent_2024/pcworld_app_scenes.png` |
| Scrim + centred white card (paused) | `lights/pcworld_permanent_2024/pcworld_app_scehduling.png` (New Event dialog) |
| Resting charcoal `#2A2A2E` | `lights/crop_hd_video_darkmode_stringlight_list.png`, `support/crop-video-still-phone-2026.png` |
| Debug drawer: grey header band, X left, centred title, selected row blue | `lights/pcworld_permanent_2024/pcworld_app_scehduling.png` (Select Product) |
| Frame-size segmented control | `lights/hd_stringlight_hb10035/hb10035_c3.jpg` (1/2/3 pattern control) |
| Line icon style | `lights/pcworld_permanent_2024/pcworld_app_scenes.png`, `appstore/iphoneX-05-Groups.png` |

---

## 11. Couldn't match, and assumptions
1. **No iPad Hubspace UI exists.** No official source has iPad screenshots, so the iPad layout (two columns in landscape, a 3×2 card grid) is an extrapolation of the phone device sheet. Everything is scaled to about 1.3–1.5× the phone metrics for arm's-length use.
2. **Quicksand is identified by eye.** It is a visual glyph match, not confirmed from the app binary. Older iOS builds used SF Pro. Vendoring a webfont departs from the repo convention "system font stacks" (CLAUDE.md). This needs Alon's OK; the fallback stack (SF Pro Rounded / system-ui) is acceptable if it is declined.
3. **Brand orange as text** (`#FA4616`) fails 4.5:1 on light backgrounds. Text uses `--brand-text #C9370E` (and white-on-orange uses `--brand-press #D63A0E`), which is slightly deeper than the app. The app's green "Turn On" chip (`#4CB050`, white text 2.75:1) is darkened to `#2E7D32` for the same reason.
4. **No blurred room photo.** The header uses the scene palette as soft colour blobs on `#323232` instead. This is truthful to what is lit and needs no photo licensing.
5. **Things the app doesn't have:** the scene cards (the app's closest is the Select Preset list plus mode discs), the "On"/"Off" switch label, toasts, the attract and resting screens, the paused card and the feature line. All are designed from app primitives.
6. **No X, Schedule or Settings.** Guests can't close the panel or open features that aren't offered; showing those controls would be untrue kiosk copy.
7. **Light theme only.** Dark mode exists in the app (2026), but it is fixed off for kiosk consistency.
8. **Colours are sampled from JPEG/PNG marketing art** (±2–5 per channel). The exact values are `#FA4616` and `#53565A` (official SVG).
9. **Logo use** in an event kiosk is assumed to be cleared by the Home Depot activation. This is unverified; confirm with Home Depot/Hearst brand.
10. **Idle timeout of 90 s and the 250 ms brightness send interval** are assumptions to tune on site. Brightness pacing is also bounded server-side by coalescing.
11. **The tinted-header contrast** must be measured in the build (§1.1) for the palest scene. If it is under 4.5:1, lower the blob opacity.

---

## 12. As built (web/kiosk/, October 2026)

Where the build differs from the spec above, and why:
1. **Brightness is sent once, on release** (D69; before that 1 request/s while dragging, and spec §2.7 said 250 ms). Every guest input shares the engine's cooldown, so values sent mid-drag held the guest's real level back by a whole cooldown; a change inside the cooldown now waits for its end on the server and goes out then.
2. **Attract after 120 s idle** (spec §2.13 said 90 s): the build brief asked for about 2 minutes. `IDLE_MS` in `web/kiosk/panel-model.mjs`.
3. **Quicksand is vendored as one variable file renamed "Canopy Rounded"**: `web/static/fonts/canopy-rounded/CanopyRounded-VF.woff2` (wght 400–700, about 52 KB) with `OFL.txt`. It was built with fontTools varLib from the unmodified Quicksand 3.006 Regular and Bold as its two masters (the official variable file wasn't available offline), so 500 and 600 render as real Medium and SemiBold; 400 and 700 are the original glyphs. Because Quicksand has the Reserved Font Name, the OFL requires a modified version to be renamed, hence "Canopy Rounded" in the font and in the CSS. Swapping in the official `Quicksand[wght].woff2` later (under its own name) is a drop-in change. Vendoring a web font departs from the "system font stacks" convention and needs Alon's OK (§11.2); deleting the `@font-face` rule falls back to SF Pro Rounded / system-ui.
4. **Scene card preview** is a 30-segment hard-stop strip of fixed height (72 px; 60 px at 1024 landscape; radius 12) instead of a 28 px pill, so the colours read from arm's length on the 3 × 2 grid. Every card is the same: names take up to two lines, moods one line with an ellipsis, and the swatches sit on the card bottom (`margin-top: auto`), so swatch rows line up. The selected/pending badge sits on the top-right corner above the ring, with a sheet-coloured gap where it crosses it.
5. **Now-showing preview follows `state.look`**: when the strands took the one-colour fallback, every tile shows the scene's `solid` colour (the kiosk copy must be true, and the picture is copy too). On the canopy engine target `look` is null and Now showing draws the server's `state.lit` instead (item 9).
6. **Brand files** in `web/kiosk/brand/`: the "powered by HUBSPACE" SVG, the white 2025 lockup, a traced hub glyph and icons made from the app icon. Logo use at the event is assumed to be cleared by the activation (§11.9) and still needs confirming with Home Depot / Hearst brand.
7. **Debug** (`?debug=1`, ops viewers only): drawer with target, live updates (polling every 2 s, latency), capabilities, raw state, the last 50 commands, internals and lease holders, and controls (show attract, simulate a toast, iPad frame preview at `?debug=1&frame=1`).
8. **Landscape right column**: the Now-showing card takes the spare height (the picture centred in it), so the brightness slider lines up with the bottom of the card grid. The header switch is centred on the title line. The 10% floor still shows a sliver of fill left of the knob. The SIMULATOR badge (sim only, `simulated: true`) sits in the top-left corner, clear of the title and the switch.
9. **Now showing draws the canopy's 270 lights** (§2.6) from the server's `state.lit` instead of the 30-light serpentine, which stays as the fallback (no look yet). The serpentine walked one strand's wire, which is not what hangs overhead: with nine strands in a 3 × 3 grid, a 2-D design, a partial canopy and per-panel tiers, only the server knows what each panel truthfully shows. The picture is capped at 210 px tall in landscape so a quarter-turned view letterboxes instead of making the sheet scroll. The scene cards' strips stay, as palette strips (§2.5).
10. **The fins (D74) are SVG strokes, not canvas 2D** (preset-options plan §8.4 said canvas): 650 `line.fin` over the 270 `circle.dot`. SVG keeps the dots' 600 ms fades and the shared changing pulse, the per-node paint dedup, D69's no-layout-per-frame structure and the e2e selectors, and the card thumbnails can reuse the same layout; the cost is 650 + 270 nodes in the card. The picture draws frosted fins only (clear vs frosted Mylar waits for Prototype 01's photos). The portrait drawing grows from 225 : 120 to 237 : 132 and still fits at 820 × 1180 without the sheet scrolling (checked in Chromium; the iPad's WebKit is unverified).
