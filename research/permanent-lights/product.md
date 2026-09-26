# product (research pass 2026-09-26)

**HEADLINE:** SQHDK03-15PSL is a Home Depot-branded version of Nanoleaf's Permanent Outdoor Lights (maker Nanogrid Ltd, Hong Kong), running on Hubspace. Each kit is ONE Wi-Fi (2.4 GHz) + Bluetooth cloud device: an inline 4-button controller with a party-mode microphone drives 30 individually addressable RGB + cool-white + warm-white pucks. The pucks sit on three 16.4 ft three-wire strings at a fixed 19.7 in (50 cm) spacing, and the power supply is 36 V / 1 A (36 W) and rated for indoor/dry use only. So 9 modules = 9 cloud devices is real. Per-puck 'paint' is a genuine app feature (3 saved custom patterns per device), and preset or animated scenes run on the controller itself. Still unproven on this model, and to be tested on Prototype 01: (a) repainting all 30 pucks with one API call (the other Hubspace segmented lights do use a single per-pattern 'color-sequence-v2' framebuffer object, which is the reason to expect it), and (b) syncing an animation across the 9 controllers (no mechanism is known). Also note the warranty is void in commercial use, and the head spacing is 19.7 in, not the design's 10 in.

## Findings
- [HIGH] **Product IDs: starter kit = HD internet #330863181, store SKU 1012049433, UPC 4897105339489. Expansion SQHDE03-5PSL = #330863255, store SKU 1012049434, UPC 4897105339496.**
  - The Use & Care cover lists 'Item #1012 049 433 Model #SQHDK03-15PSL' and 'Item #1012 049 434 Model #SQHDE03-5PSL'. The HD page data in the archived snapshot (Dec 9 2025, __APOLLO_STATE__) gives modelNumber SQHDK03-15PSL, storeSkuNumber 1012049433, upc 4897105339489, isSuperSku true, parentId 331807837. HD Canada sells the same model as SKU 1001926337, apparently mislabeled there as an 'Expansion Set'. The HD title calls the heads 'Mini'.
  - src: https://images.thdstatic.com/catalog/pdfImages/55/55b7c72a-79f1-4dbb-bd4d-f51b0d61538f.pdf ; https://web.archive.org/web/20251209173728/https://www.homedepot.com/p/Hampton-Bay-30-LED-50-ft-Plug-in-Mini-Integrated-LED-Novelty-Permanent-Outdoor-String-Light-Starter-Kit-Powered-by-Hubspace-SQHDK03-15PSL/330863181 ; https://web.archive.org/web/20250328094357/https://www.homedepot.com/p/Hampton-Bay-10-LED-16-4-ft-Plug-in-Mini-Integrated-LED-Novelty-Permanent-Outdoor-String-Light-Expansion-Set-Powered-by-Hubspace-SQHDE03-5PSL/330863255 ; https://www.homedepot.ca/product/hampton-bay-30-led-50-ft-plug-in-mini-integrated-led-outdoor-string-light-expansion-set-powered-by-hubspace/1001926337
- [HIGH] **The maker is Nanoleaf (NANOGRID LIMITED, Hong Kong; FCC grantee code 2AEWY), not Novolink, Seasonal Specialties or Leedarson.**
  - Five independent signals. (1) The 'Warranty' PDF linked from the HD page for both SKUs is literally the 'Nanoleaf Hardware Limited Warranty' (2-year, Nanoleaf legal text, made 2018). (2) The UPC company prefix 4897105 is Nanoleaf's: UPC 4897105330066 is the Nanoleaf Lines 60 Degrees Smarter Kit NL59-K-0003LW-9PK, registered in Hong Kong. (3) In Dec 2024, HD Q&A answers posted by the 'Hubspace' account sent customers to support@nanoleaf.me and helpdesk.nanoleaf.me and used Nanoleaf's abbreviation 'POL'. (4) The model scheme matches Nanoleaf Permanent Outdoor Lights: N7401K03-15POL (49 ft kit) and N7401E00-5POL (5 m expansion) versus SQHDK03-15PSL and SQHDE03-5PSL. (5) The specs are identical: 30 pucks per 15 m kit, 10 pucks per 5 m string, 50 lm per puck, 2200-6500 K, 4-button inline controller with microphone, and the '147 ft / 45 m' and '90 pucks' figures. Nanoleaf also has an FCC filing with an 'SQ' model prefix (2AEWY-SQFX01). The Use & Care PDF's metadata title is 'HD版本洗墙灯 QSG-240614-0621' ('HD-version wall-washer light quick-start guide'), made 2024-06-20, so the manual was written on the factory side. The HD controller housing (Power, −, +, 'M' button, mic hole) differs from Nanoleaf's retail controller (Power, '>', +/−), so the firmware and radio are HD/Hubspace-specific.
  - src: https://images.thdstatic.com/catalog/pdfImages/41/4183613e-3f9e-4c1f-8c62-a345a012a088.pdf ; https://www.upcitemdb.com/upc/4897105330066 ; https://www.upcitemdb.com/upc/4897105339489 ; https://web.archive.org/web/20250117235327/https://www.homedepot.com/p/Hampton-Bay-30-LED-50-ft-Plug-in-Mini-Integrated-LED-Novelty-Permanent-Outdoor-String-Light-Starter-Kit-Powered-by-Hubspace-SQHDK03-15PSL/330863181 (Q&A 2024-12-22/24) ; https://nanoleaf.me/en-US/products/essentials/outdoor/multicolor-permanent-outdoor-lights/matter-multicolor-permanent-outdoor-lights-smk-15m/ ; https://web.archive.org/web/20230804023609/https://fccid.io/2AEWY-SQFX01
- [HIGH] **Heads and spacing: 30 pucks per kit, as 3 strings × 16.4 ft (5 m) × 10 pucks, spaced 19.7 in (50 cm) apart.**
  - The HD spec table gives Number of Bulbs 30, 'Space Between Bulbs (in.) 19.7' and Lighted Length 50 ft. The manual says '50FT of string lights, made of three 16.4-FT sections'. An official Hubspace Q&A answer (Jan 2025) says each string is 5 m with 10 pucks 50 cm apart, and the wire from power supply to controller is 1 m. A 9.8 ft (3 m) extension cable is included. One reviewer counted about 11 ft of cable before the first light. Inference: about 25 cm of lead at each string end, since 5 m − 9 × 0.5 m = 0.5 m.
  - src: Wayback HD snapshot 20251209173728 (specificationGroup) ; Use & Care PDF p.4, p.6 ; Wayback HD snapshot 20250117235327 Q&A 2025-01-08 ; https://www.pcworld.com/article/2504896/hampton-bay-permanent-string-lights-review.html
- [MEDIUM] **Heads are hexagonal white pucks with two screw ears, not octagonal as the VWBD deck says. Roughly 35 × 32 mm body, about 2 in wide including the ears (not yet measured on this kit).**
  - HD product images and the manual drawings show hexagonal pucks with two screw tabs on a flat white 3-conductor cable, and a reviewer calls them 'hexagon pucks'. Another HD reviewer: 'Puck lights are appro. 2 inches wide.' A retailer listing for the Nanoleaf version gives 35 × 32 mm per puck. Behind the diffuser is a ring of small white LEDs around a centre RGB LED. Pucks ship with adhesive and take 2 screws each.
  - src: https://images.thdstatic.com/productImages/f3842275-a22d-45b5-a382-55d915376d3d/svn/white-hampton-bay-smart-outdoor-lighting-sqhdk03-15psl-e1_1000.jpg ; HD review 2024-12-21 and 2024-12-03 in the Wayback snapshots ; https://lightsdaddy.com/products/nanoleaf-matter-permanent-outdoor-lights-smart-rgbcw-outdoor-string-lights-for-daily-accent-lighting-ip67-waterproof-compatible-with-alexa-google-home-multiple-scene-modes-49ft-15m
- [HIGH] **Colour: RGB plus separate cool-white and warm-white LEDs in every puck ('RGBWIC'), with tunable white 2200-6500 K and CRI 80. Each puck has its own control chip on a 3-wire bus.**
  - PCWorld says the pucks have 'discrete red, blue, green, white, and warm-white diodes'. Nanoleaf's FAQ for the identical product: each puck has 3 warm-white LEDs, 3 cool-white LEDs and 1 RGB LED. HD: 'dedicated white LEDs', tunable white, CRI 80, Actual Color Temperature 2600 K (probably the default). The Use & Care gives CCT 2200-6500 K. An HD Oct 2025 blog says 'separate white and RGB chips'. The splice instructions separate 3 wires; inference: power, data and ground for a single-data-line addressable bus.
  - src: https://www.pcworld.com/article/2504896/hampton-bay-permanent-string-lights-review.html ; https://nanoleaf.box.com/v/guide-permanent-outdoor-lights (FAQ #3) ; Use & Care PDF p.12, p.14 ; https://ecoactions.homedepot.com/blog/hubspace-smart-holiday-lights/
- [HIGH] **Output and power: 50 lm per puck in white; the power supply outputs 36 V DC at 1 A (36 W); input 110/120 V AC through a polarized plug with a built-in fuse.**
  - Use & Care specs: 'Output 36V DC 1A, Power 36W, CCT 2200K-6500K, Luminous flux 50lm/bulb'. HD spec: Voltage 110/120 V, Lumens 50, Dimmable. Safety text: polarized plug; 'Do not replace the attachment plug. It contains a safety device (fuse)'. The HD spec in Sep 2025 also listed 'ETL Listed, FCC Listed' and a kit weight of 3.8 lb. Brightness in colour modes is not published. Maximum white output is about 1,500 lm per kit.
  - src: Use & Care PDF p.4, p.14 ; Wayback HD snapshots 20250909160408 and 20251209173728
- [HIGH] **Each puck can be coloured individually in the Hubspace app ('paint'), and up to 3 custom patterns can be saved per device.**
  - HD bullets: '30 addressable LED pucks' and 'Individual puck control: "paint" where each color goes on your lights with the Hubspace app'. PCWorld: up to three patterns, each puck individually addressable for colour, white temperature, brightness, gradient and animated motion/brightness effects. PCWorld also notes you must deselect pucks after programming them, or new choices apply to all of them. HD Q&A (Jan 2025): a scene you create can be saved. The unit of control is one puck: 30 per kit, 60 per controller.
  - src: Wayback HD snapshot 20251209173728 (descriptiveAttributes) ; https://www.pcworld.com/article/2504896/hampton-bay-permanent-string-lights-review.html ; Wayback expansion snapshot 20250328094357 Q&A 2025-01-19
- [MEDIUM] **Inference, not yet seen on this model: all 30 puck colours of a saved pattern probably live in ONE cloud object, so one write could repaint a whole module.**
  - Every other segmented Hubspace light dumped publicly stores its 3 saved patterns in one object attribute per slot: functionClass 'color-sequence-v2', instances custom-1/custom-2/custom-3. Each object holds a header (motionEffect, motionSpeed, brightnessEffect, brightnessSpeed, brightnessDepth, headerFlags 128, version 1) and a framebuffer array with one {r,g,b,cct,colorBrightness,whiteBrightness} entry per segment. A saved slot is activated with color-mode='individual' plus color-individual(custom)='custom-N'. Framebuffer length equals the segment count: 40 on EcoSmart tape AL-TP-RGBICTW-3, 20 on the Hampton Bay/Novolink 100-light string HB-10035SC-BK, 2 on a Leedarson two-zone flush mount. If SQHDK03 follows this schema, one PUT of about 30 entries repaints a module. Caveats: no public dump of SQHDK03 exists; aioafero v9.3.0 cannot write framebuffers; its PR #61 for selecting saved slots is still unmerged; and a GitHub issue reports the Hubspace app uses certificate pinning, which blocks capturing its traffic through a proxy.
  - src: github.com/tpural/aioafero@178999d tests/v1/device_dumps/light-rgbic.json ; github.com/Expl0dingBanana/aioafero@58864da tests/v1/device_dumps/light-with-speed.json and light-with-speaker.json ; https://github.com/Expl0dingBanana/aioafero/pull/61 ; https://github.com/jdeath/Hubspace-Homeassistant/issues/224
- [MEDIUM] **Preset and animated scenes and party mode run on the controller itself; the cloud only sends which one to play. There is no known way to sync effects across controllers.**
  - The HD controller has a physical 'Next Scene' (M) button that cycles scenes with no app involved. 'When party mode is turned ON, microphone will be enabled': audio reacts on the device, though PCWorld found the response weakly tied to the music. Nanoleaf's manual for the same product: controller presets are 'a mix of static color Scenes, animated Scenes and Rhythm Scenes', usable without the app. In the other dumps, effects on saved patterns are header fields (a Leedarson dump shows motionEffect 6 and motionSpeed 49 set by the app), so they most likely also render on the device. Preset speed on other Hubspace string lights is a number from −10 to +10. Inference: each of the 9 controllers keeps its own animation clock. A 'moving amber wash' across the canopy will not stay phase-locked between modules unless it is stepped from the cloud or the modules share controllers.
  - src: Use & Care PDF p.13 ; https://nanoleaf.box.com/v/guide-permanent-outdoor-lights (pages 'Controlling your Permanent Outdoor Lights' and FAQ #7) ; https://www.pcworld.com/article/2504896/hampton-bay-permanent-string-lights-review.html ; https://github.com/jdeath/Hubspace-Homeassistant/issues/205
- [HIGH] **The controller is an inline box on 2.4 GHz Wi-Fi (no 5 GHz) with Bluetooth for setup and fallback, 4 buttons, a microphone, and puck-count detection at power-up.**
  - Chain: plug → power supply → 1 m cable → controller → optional 9.8 ft extension → string 1 → string 2 → string 3 → end cap. Buttons: Power, Brightness −, Brightness +, M/Next Scene, plus a microphone. The controller and power supply have screw holes. Troubleshooting 'Product can't be paired' says to use a 2.4 GHz network, and a Hubspace answer (Aug 2025) says 5 GHz is not supported. HD: 'Products must be in range for Bluetooth connection'; PCWorld: 'You can also fall back to Bluetooth control'. Troubleshooting 'Length detection failed' means power off, secure connections, restart. Reviewers and Hubspace say to unplug and replug after adding, removing or splicing strings. Works with Alexa and Google; not Matter, Apple Home or IFTTT.
  - src: Use & Care PDF p.11-14 ; https://images.thdstatic.com/productImages/a5667cc6-f78d-4bdd-9c72-bab8805b890c/svn/white-hampton-bay-smart-outdoor-lighting-sqhdk03-15psl-1d_1000.jpg ; Wayback HD snapshot 20250909160408 Q&A 2025-08-02 ; https://www.pcworld.com/article/2504896/hampton-bay-permanent-string-lights-review.html
- [HIGH] **Power supply: indoor/dry only per the HD manual. Weather ratings for the Hampton Bay version conflict across sources.**
  - The HD manual says: 'Do not put the power supply in outdoor areas, regardless of weather conditions' and 'Do not put the power supply in wet location'. It prints no IP rating, and HD's spec only says 'Waterproof'. PCWorld claims the power supply and controller are IP65 and the strings IP67. Nanoleaf's version is rated controller IP67, pucks IP67, connectors IP65, power supply IP67, −15 to +40 °C. Treat the Hampton Bay power supply as indoor/dry and ventilated.
  - src: Use & Care PDF p.3 ; https://www.pcworld.com/article/2504896/hampton-bay-permanent-string-lights-review.html ; https://support.nanoleaf.me/hc/en-us/articles/33663498061204-FAQ-Product-What-is-the-IP-Rating ; https://support.nanoleaf.me/hc/en-us/articles/32781834830996-FAQ-Product-Indoor-Vs-Outdoor
- [HIGH] **Up to 60 pucks (6 strings, about 100 ft) can daisy-chain on ONE controller, and everything on it is one Hubspace device. Separate kits stay separate devices.**
  - Manual step 8: 'Max 60 pucks can be connected; 30 pucks per 50 ft strings provided'. Step 9: 'Max. 6 light strings'. Box: 'CONNECT UP TO (2) 50 FT SETS, OR 100 FT PER CONTROLLER'. A Hubspace answer (Dec 2025) says 60 is the 'confirmed and tested maximum' and the 90-puck figure came from early internal testing. Older Hubspace answers (Dec 2024, Sep/Dec 2025) cite 147 ft (45 m) per controller and say extension cables can be chained; those are Nanoleaf's native limits (90 pucks, 147 ft).
  - src: Use & Care PDF p.11, p.13 ; box image https://images.thdstatic.com/productImages/d50c5ee9-8aee-435c-a335-609d3efe5179/svn/white-hampton-bay-smart-outdoor-lighting-sqhdk03-15psl-66_1000.jpg ; Wayback HD snapshots 20251209173728 and 20250117235327 (Q&A)
- [MEDIUM] **Only two Hubspace SKUs exist in this family: the starter kit and the SQHDE03-5PSL expansion (10 pucks, 16.4 ft, $29.97). No other lengths, no non-Mini version, and no separately listed extension cable were found.**
  - Expansion: 10 pucks, 16.4 ft, 0.7 lb, 3-year warranty, 'Starter Kit Required', HD price $29.97 (Mar 2025; PCWorld also says $29.97). Hubspace says extension cables come 'only in standard lengths', and no separate HD SKU for them was found. Nanoleaf-branded equivalents use the Nanoleaf app and Matter, not Hubspace, so they would not satisfy the 'Hubspace app-controllable' requirement: N7401K03-15POL (49 ft), N7401K03-30POL (98 ft), KIT_N7401K03-45POL (147 ft), N7401E00-5POL (expansion). Their kit adds 1.2 m and 3 m extension cables and cable covers.
  - src: Wayback expansion snapshot 20250328094357 ; https://nanoleaf.me/en-US/products/essentials/outdoor/multicolor-permanent-outdoor-lights/matter-multicolor-permanent-outdoor-lights-exp-5m/ ; https://nanoleaf.box.com/v/guide-permanent-outdoor-lights (What's in the box)
- [HIGH] **Strings can be cut and spliced with the 2 included screw-lock splicers. The cable is 22 AWG, 3 conductors, flame-rated VW-1. Official guidance on cutting is contradictory.**
  - The manual shows cutting midway between pucks, stripping 3 wires (separate 1 in, strip 0.5 in), then fitting the male splicer to shorten or the female splicer to extend; 'qualified professional' only. Cable marking in the drawing: UR AWM 2468, VW-1, 80 °C, 300 V, 22 AWG. A Hubspace answer (Jun 2025) says the strings are 'not designed to be cut'. Nanoleaf: never splice the power supply or controller cabling. Reviewers spliced successfully but had to power-cycle so the controller re-detects length. An HD answer (Dec 2025) calls pucks 'modular' and replaceable through support, but individual pucks are not sold.
  - src: Use & Care PDF p.11-12 ; Wayback HD snapshot 20250909160408 Q&A 2025-06-07 ; https://nanoleaf.box.com/v/guide-permanent-outdoor-lights (FAQ #1)
- [HIGH] **Manual restrictions that bear directly on the canopy build.**
  - The manual forbids: using the chain 'when covered or recessed into a surface'; hanging objects from the cord, wire or lights; covering it with any material; 'enclosed locations that limit the flow of air and/or trap heat' above 40 °C; mounting other than 'the mounting means provided'; and installing below 0 °C (the adhesive won't hold). Loose cords within reach must be secured. Inference for VWBD: surface-mount the heads rather than recessing them, hang the Mylar ribbons from the panel rather than the light cable, and ventilate the space above the panel that hides the extra cable. Otherwise the ETL listing terms are at risk.
  - src: Use & Care PDF p.3, p.7
- [HIGH] **In the box: 3 strings, controller, power supply, 9.8 ft extension, 2 splicers, 70 screws, 16 cable clips with screws, 6 double-sided tape pads, 2 end caps.**
  - Tools: Phillips H2, ladder, and a wire stripper and side cutter if splicing. The manual estimates 2 hours with 2 people for 50 ft; a reviewer took about 1 hour. Two screws per puck. One reviewer found missing parts in re-taped (returned) boxes, so check every box.
  - src: Use & Care PDF p.5-6, p.9 ; Wayback HD snapshot 20251209173728 reviews
- [MEDIUM] **Price per kit has ranged $80-$119 at HD. It was $119 in late 2025; a third-party tracker shows $99.00 on Sep 24 2026.**
  - PCWorld, Oct 31 2024: $99. HD page on Jan 17 2025: $99.99. HD page on Sep 9 and Dec 9 2025: $119. An undated search snippet showed $79.97, down from $119. upcitemdb shows $99.00 at Home Depot, updated Sep 24 2026. Postmates (delivery markup) shows $104.99. HD has no per-order quantity limit and 90-day returns. Nine kits come to about $891-$1,071 before spares and tax.
  - src: https://www.pcworld.com/article/2504896/hampton-bay-permanent-string-lights-review.html ; Wayback HD snapshots 20250117235327, 20250909160408, 20251209173728 (pricing, storeId 8119) ; https://www.upcitemdb.com/upc/4897105339489
- [MEDIUM] **Availability: sold online and in stores and not discontinued as of Dec 2025. Live stock at the Charleston stores could not be checked because homedepot.com blocks automated lookups.**
  - HD page Dec 2025: discontinued false, buyable true, sold both online and in store; Q&A traffic peaks Nov-Dec, so the item is seasonally merchandised. Nearest HD stores to 284 Meeting St: #1118 East Charleston (2008 Magwood Dr, 29414), #1171 Mt Pleasant (2213 N Hwy 17, 29466), #1103 Charleston/North Charleston (7554 Northwood Blvd, 29406). Check in-store counts in the HD app or by phone. For 9-12 kits, order online (ship to store or venue) rather than relying on one store.
  - src: Wayback HD snapshot 20251209173728 (availabilityType, info.quantityLimit) ; https://www.homedepot.com/l/East-Charleston/SC/Charleston/29414/1118 ; https://www.homedepot.com/l/1171 ; https://homedepot.com/l/Charleston/SC/Charleston/29406/1103
- [HIGH] **Warranty: 3-year limited per the manual and HD spec, but it is void in any commercial or business use, so it will not cover this activation. HD also links a 2-year Nanoleaf warranty.**
  - Use & Care: 3 years from purchase, replace or refund at the store with receipt, and 'This warranty is voided if the product is ever used in a commercial or business environment.' HD spec: 3 Year Limited Warranty. The HD 'Warranty' link, however, is Nanoleaf's 2-year hardware warranty. Plan on buying spares rather than making warranty claims.
  - src: Use & Care PDF p.5 ; https://images.thdstatic.com/catalog/pdfImages/41/4183613e-3f9e-4c1f-8c62-a345a012a088.pdf
- [MEDIUM] **No FCC ID was found for the Hampton Bay controller. The manual prints only the standard FCC Part 15 Class B statement, naming The Home Depot as responsible party.**
  - The manual gives no FCC or IC ID; its FCC section names 'Responsible Party: The Home Depot, 2455 Paces Ferry Road Atlanta'. Nanoleaf's grantee code is 2AEWY (NANOGRID LIMITED, Kowloon HK). Leedarson's is 2AB2Q; Leedarson makes the Hubspace A19 bulbs and 2.4 GHz Wi-Fi/Bluetooth modules (for example 2AB2Q-LA02301), but nothing ties it to this controller. fccid.io, fcc.report and apps.fcc.gov all block automated access. The FCC ID has to be read off the controller label.
  - src: Use & Care PDF p.4 ; https://web.archive.org/web/20210422035129/https://fccid.io/2AB2Q-LA02301 ; https://web.archive.org/web/20230804023609/https://fccid.io/2AEWY-SQFX01
- [MEDIUM] **Reliability is the main review risk: controllers failing, puck-count detection dropping to 12, single pucks stuck white, saved colours reverting.**
  - HD rating fell from 4.47 (30 reviews, Jan 2025) to 4.21 (38, Sep 2025) to 4.15 (71, Dec 2025); some early reviews are HD-incentivized. Reported failures: 'only worked for 3 days… controller went bad and only 12 lights started lighting up' (1 star, Dec 2025); lights failed or turned intermittent after 2 months even with a new controller; the app suddenly showed only 12 pucks (Mar 2025, with a second user agreeing); 1-2 pucks stay lit white when off (Dec 2024, Dec 2025); scheduled turn-on came back in a different colour; holiday presets vanished until the device was re-added. Nanoleaf's own version has similar complaints about controllers failing within 6 months. The count of 12 appears in independent reports, possibly a fallback when detection fails (inference).
  - src: Wayback HD snapshots 20250117235327, 20250909160408, 20251209173728 (reviews and Q&A) ; https://support.nanoleaf.me/hc/en-us/community/posts/44425459990292-Customer-Service-Support
- [MEDIUM] **Colour quality: whites and primaries are strong, but yellow, orange and purple are weak. That matters for RAINBOW ROW SUNSET and GOLDEN HOUR.**
  - A detailed 3-star reviewer (Jan 2025): red, green and blue look good, pastels are very good, and there is a comprehensive range of whites; but it 'struggles a bit with yellow, orange, and especially PURPLE', with purple a washed-out lavender. Others say it is blinding at full brightness and dims enough for indoor use. The app connects quickly. The reviewer counted a large number of static and animated presets (blinking, moving, fading). Inference: build amber and peach looks from the 2200-2700 K white LEDs, possibly blended with RGB, and verify on the bench.
  - src: Wayback HD snapshot 20251209173728 (review 2025-01-23 and 2024-12-22)
- [LOW] **Power-loss and schedule behaviour is uncertain; the kiosk software should re-send the mode after power events.**
  - Some reviews say the lights do not return to their previous setting. HD Q&A (Mar 2025): scheduled turn-on came back in a different colour. Nanoleaf's version has an optional 'Power Loss Recovery' toggle in the Nanoleaf app. Other Hubspace lights expose a 'restore-values' setting (partial-restore / not-restored / restored); whether this model has it is unknown.
  - src: Wayback HD snapshot 20250909160408 Q&A 2025-03-23 ; https://support.nanoleaf.me/hc/en-us/articles/36390229280148-FAQ-Features-Power-Loss-Recovery ; aioafero tests/v1/device_dumps/light-rgbic.json (restore-values)
- [MEDIUM] **Canopy fit (inference from the geometry): the fixed 19.7 in spacing leaves about 21-23 ft of extra cable per module, and the pixel order runs away from the controller.**
  - 30 heads at 10 in spacing in 8/7/8/7 rows means 29 gaps of about 10-11 in, against 19.7 in of cable per gap: roughly 8.5-9.7 in extra per gap, 250-280 in per module. Each module also has 2 bulky screw-lock connectors between strings, the controller, a 1 m power-supply lead, and optionally the 9.8 ft extension. Pixel index increases away from the controller, so 'B' modules (rotated 180°) need a reversed index map in software. Nine kits give exactly 270 heads and at most 324 W across 9 power supplies. Of the 6 Lowcountry Modes, 3 could be stored as genuine app-painted patterns in each module's 3 slots. The rest can be white mode (CALM around 2700 K, GOODNIGHT at 2200 K and about 10% brightness) or presets (HOLIDAY), so the kiosk would only switch modes.
  - src: Derived from HD spec (19.7 in), the VWBD brief, and PCWorld (3 patterns)

## Numbers
- Pucks per starter kit = **30 (3 strings × 10)** (Use & Care PDF p.6; HD spec 'Number of Bulbs 30')
- Head-to-head cable spacing = **19.7 in (50 cm)** (HD spec 'Space Between Bulbs (in.) 19.7' (Wayback 20251209173728); Hubspace Q&A 2025-01-08)
- String length = **16.4 ft (5 m) per string; 50 ft (49.2 ft) per kit** (Use & Care PDF p.4, p.6)
- Power supply to controller cable = **1 m** (Hubspace Q&A 2025-01-08 (Wayback 20250117235327))
- Included extension cable = **9.8 ft (3 m) × 1** (Use & Care PDF p.6)
- Cable before first light (as measured by one reviewer) = **about 11 ft** (HD review 2025-01-23)
- White output = **50 lm per puck (about 1,500 lm per kit)** (Use & Care PDF p.14; HD spec Lumens 50)
- White temperature range = **2200-6500 K** (Use & Care PDF p.14)
- Colour rendering index = **80** (HD spec (Wayback 20251209173728))
- LEDs per puck (Nanoleaf version) = **3 warm white + 3 cool white + 1 RGB** (Nanoleaf POL manual FAQ #3 (nanoleaf.box.com/v/guide-permanent-outdoor-lights))
- Power supply output = **36 V DC, 1 A (36 W)** (Use & Care PDF p.14)
- Input = **110/120 V AC; polarized plug with fuse** (HD spec Voltage; Use & Care PDF p.3-4)
- Max per controller (official Hampton Bay) = **60 pucks / 6 strings / about 100 ft** (Use & Care PDF p.11, p.13; box text; Hubspace Q&A 2025-12-07)
- Max per controller (Nanoleaf native / older Hubspace answers) = **147 ft (45 m), 90 pucks** (Hubspace Q&A 2024-12-22; shine.lighting Nanoleaf article; Nanoleaf help center article 33028886014612)
- Saved custom 'paint' patterns per device = **3** (PCWorld review (Oct 31 2024))
- Preset speed range on other Hubspace string lights = **-10 to +10 (step 1)** (aioafero tests/v1/device_dumps/light-with-speed.json; jdeath/Hubspace-Homeassistant#205)
- Framebuffer length on other Hubspace segmented lights = **40 (EcoSmart AL-TP-RGBICTW-3), 20 (HB-10035SC-BK Novolink), 2 (Leedarson flush mount)** (tpural/aioafero light-rgbic.json; Expl0dingBanana/aioafero light-with-speed.json and light-with-speaker.json)
- Kit weight = **3.8 lb (expansion 0.7 lb)** (HD spec Jan/Sep 2025; expansion spec Mar 2025)
- Kit price history = **$99 (Oct 2024) → $99.99 (Jan 17 2025) → $119 (Sep 9 and Dec 9 2025) → $99.00 (Sep 24 2026, third-party tracker); promo $79.97 (undated)** (PCWorld; Wayback HD snapshots; upcitemdb.com/upc/4897105339489; search snippet)
- Expansion SQHDE03-5PSL price = **$29.97** (Wayback 20250328094357; PCWorld)
- HD rating trend = **4.47/30 (Jan 2025) → 4.21/38 (Sep 2025) → 4.15/71 (Dec 2025); 92 Q&A threads** (Wayback HD snapshots (reviews.ratingsReviews))
- Warranty = **3-year limited, void in commercial use (manual); HD also links Nanoleaf's 2-year warranty** (Use & Care PDF p.5; thdstatic warranty PDF 4183613e)
- Hardware in the box = **70 screws, 16 cable clips with screws, 6 tape pads, 2 end caps, 2 splicers** (Use & Care PDF p.6)
- Cable = **22 AWG, 3 conductors, UL AWM 2468, VW-1, 80 °C, 300 V** (Use & Care PDF p.11 (wire marking drawing))
- Install estimate = **2 h, 2 people per 50 ft** (Use & Care PDF p.5)
- Operating limits = **Do not install below 0 °C; no enclosed spaces above 40 °C (Nanoleaf version: -15 to +40 °C)** (Use & Care PDF p.3, p.7; Nanoleaf help center 32781834830996)
- Canopy totals (inference) = **9 kits = 270 heads; up to 324 W across 9 power supplies; about 21-23 ft extra cable per module** (Derived from specs above)
- Charleston-area HD stores = **#1118 East Charleston (2008 Magwood Dr); #1171 Mt Pleasant (2213 N Hwy 17); #1103 Charleston (7554 Northwood Blvd)** (homedepot.com/l store pages (search results))

## Payloads / code
### Afero state write request (the form aioafero uses for every device write)
source: github.com/Expl0dingBanana/aioafero@58864da src/aioafero/v1/controllers/base.py (payload = {"metadeviceId": ..., "values": states}); src/aioafero/v1/v1_const.py (API_HOST api2.afero.net, API_DATA_HOST semantics2.afero.net, API_DEVICE_STATE_ENDPOINT /v1/accounts/{}/metadevices/{}/state)
```
PUT https://api2.afero.net/v1/accounts/{accountId}/metadevices/{metadeviceId}/state
Host: semantics2.afero.net
Content-Type: application/json; charset=utf-8

{"metadeviceId": "<metadeviceId>",
 "values": [
   {"functionClass": "<class>", "functionInstance": null, "value": <value>, "lastUpdateTime": <epoch_ms>}
 ]}
```
### Saved-pattern 'color-sequence-v2' state from a 40-segment EcoSmart RGBIC tape (trimmed to 2 of 40 entries); expected shape for SQHDK03, unverified
source: github.com/tpural/aioafero@178999d (PR #61 branch include-more-effects) tests/v1/device_dumps/light-rgbic.json
```
{"functionClass": "color-sequence-v2", "functionInstance": "custom-1", "lastUpdateTime": 0,
 "value": {"color-sequence-v2": {
   "id": 0, "version": 1, "headerFlags": 128, "sequenceFlags": 0,
   "brightnessDepth": 100, "brightnessSpeed": 50, "brightnessEffect": 0,
   "motionSpeed": 50, "motionEffect": 0,
   "frameBuffer": {"flags": 0, "framebuffer": [
     {"r": 255, "g": 0, "b": 0, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
     {"r": 0, "g": 73, "b": 255, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0}
     /* ... one entry per segment (40 here) */ ]}}}}

Function definition: {"functionClass":"color-sequence-v2","functionInstance":"custom-1","type":"object","schedulable":false,"values":[{"name":"color-sequence-v2","deviceValues":[{"type":"attribute","key":"351","format":"color-sequence-v2"}]}]}  (custom-2 uses key 352, custom-3 uses 353 on this device)
```
### Activate a saved pattern slot (the approach in aioafero PR #61, not yet merged)
source: https://github.com/Expl0dingBanana/aioafero/pull/61 (src/aioafero/v1/models/features.py EffectFeature.api_value; controllers/light.py set_effect)
```
values = [
  {"functionClass": "power", "functionInstance": null, "value": "on"},
  {"functionClass": "color-mode", "functionInstance": null, "value": "individual"},
  {"functionClass": "color-individual", "functionInstance": "custom", "value": "custom-1"}
]
# color-mode values seen on other devices: individual | sequence | color | white | music-sync | circadian-rhythm
# color-individual(custom) values: custom-1 | custom-2 | custom-3
```
### Preset effect plus speed (runs on the device); preset names differ per device
source: aioafero features.py EffectFeature.api_value; tests/v1/device_dumps/light-with-speed.json
```
values = [
  {"functionClass": "color-mode", "functionInstance": null, "value": "sequence"},
  {"functionClass": "color-sequence", "functionInstance": "preset", "value": "christmas"},
  {"functionClass": "speed", "functionInstance": "color-sequence", "value": -3}   # range -10..10
]
# Preset enum on HB-10035SC-BK includes: hanukkah, cross-faded, rainbow-roll, st-patricks-day, prism, snowfall, mistletoe, ... ; the SQHDK03 list is unknown until dumped
```
### INFERENCE, untested: repaint all 30 pucks of one module with a single PUT
source: Assembled from the dumps above and aioafero base.py. Must be proven on Prototype 01.
```
{"metadeviceId": "<module_id>", "values": [
  {"functionClass": "color-sequence-v2", "functionInstance": "custom-1", "lastUpdateTime": <ms>,
   "value": {"color-sequence-v2": {"id":0,"version":1,"headerFlags":128,"sequenceFlags":0,
     "brightnessDepth":100,"brightnessSpeed":50,"brightnessEffect":0,"motionSpeed":50,"motionEffect":0,
     "frameBuffer": {"flags":0, "framebuffer": [ /* exactly N entries, N = puck count read back from the device (30) */
        {"r":255,"g":120,"b":40,"cct":0,"colorBrightness":90,"whiteBrightness":0}, ... ]}}}},
  {"functionClass": "color-mode", "functionInstance": null, "value": "individual", "lastUpdateTime": <ms>},
  {"functionClass": "color-individual", "functionInstance": "custom", "value": "custom-1", "lastUpdateTime": <ms>}
]}
# First GET the device and copy the header and framebuffer length the app wrote after painting; only change the colour entries.
```
### Dump the SQHDK03 function list and states (the evidence the software team needs)
source: aioafero README.rst (quick start); jdeath/Hubspace-Homeassistant custom_components/hubspace/button.py (the 'Generate Debug' dump uses fetch_discovery_data, get_afero_device, anonymize_devices)
```
# python -m asyncio   (pip install aioafero==9.3.0)
import json, aiohttp
from aioafero import v1, get_afero_device, anonymize_devices
session = aiohttp.ClientSession()
auth = v1.AferoAuth.for_login(session, EMAIL, PASSWORD)
try:
    tok = await auth.login()
except v1.OTPRequired:
    tok = await auth.submit_otp(input('email code: ').strip())
bridge = v1.AferoBridgeV1(EMAIL, tok.refresh_token, session=session)
await bridge.initialize()
raw = await bridge.fetch_discovery_data()
json.dump(raw, open('_dump_raw.json','w'), indent=2)
json.dump(anonymize_devices([get_afero_device(d) for d in raw]), open('_dump_hs_devices.json','w'), indent=2)
await bridge.close(); await session.close()
```
### Read HD product data despite homedepot.com 403s (Wayback id_ snapshot plus Apollo state)
source: https://web.archive.org/cdx/search/cdx (method used in this research)
```
U='https://www.homedepot.com/p/Hampton-Bay-30-LED-50-ft-Plug-in-Mini-Integrated-LED-Novelty-Permanent-Outdoor-String-Light-Starter-Kit-Powered-by-Hubspace-SQHDK03-15PSL/330863181'
curl -s "https://web.archive.org/cdx/search/cdx?url=$U&output=json&fl=timestamp,statuscode,length"
curl -s -L "https://web.archive.org/web/20251209173728id_/$U" -o hd.html   # older snapshots may be gzip: gunzip -c
python3 - <<'EOF'
import json; s=open('hd.html',encoding='utf-8',errors='ignore').read()
i=s.find('window.__APOLLO_STATE__=')+len('window.__APOLLO_STATE__=')
obj,_=json.JSONDecoder().raw_decode(s[i:])
p=obj['base-catalog-330863181']  # identifiers, specificationGroup, details, pricing(...), reviews
print(p['identifiers']['upc'], [k for k in p if k.startswith('pricing')])
EOF
```
### Nanoleaf (OEM) help-center API and manual for the equivalent product
source: support.nanoleaf.me (Zendesk public API); https://nanoleaf.box.com/v/guide-permanent-outdoor-lights
```
curl -s 'https://support.nanoleaf.me/api/v2/help_center/articles/search.json?query=permanent%20outdoor%20lights&per_page=100'
curl -s 'https://support.nanoleaf.me/api/v2/help_center/en-us/articles/33663498061204.json'   # IP ratings
curl -L -o POL_manual.pdf 'https://nanoleaf.box.com/index.php?rm=box_download_shared_file&vanity_name=guide-permanent-outdoor-lights&file_id=f_2329056723884'   # image-only PDF, 9 pages
```

## Bench tests
- Unbox Prototype 01 kit: photograph every label. Record the FCC ID/IC number and model on the controller, and input/output, Class 2 and IP markings on the power supply. Caliper the puck (L × W × H with and without ears, screw-hole spacing and diameter). Measure every lead: power supply to controller, controller output, the 9.8 ft extension, and the lead from each string's connector to its first puck. Measure the connector barrel diameter the panel must clear. Count all hardware against the manual.
- Onboard to Hubspace on a dedicated 2.4 GHz SSID configured like the venue's network. Time the setup, note the Bluetooth step, and confirm the app shows ONE device with exactly 30 pucks. Then power-cycle 10 times and confirm it always re-detects 30, never the 12-puck failure pattern.
- Dump the device with aioafero (fetch_discovery_data plus anonymize_devices). Record every functionClass and instance: color-mode values, color-individual, color-sequence-v2 instances and framebuffer length, the preset list, speed range, party-mode/music-sync, restore-values, and the device model string and manufacturer name.
- Paint an asymmetric pattern in the app (puck 1 red, puck 2 green, ..., puck 30 blue) into slot 1, then GET the state. Confirm the framebuffer matches the painting and that index 0 is the puck nearest the controller.
- Single-call write: PUT a modified color-sequence-v2/custom-1 framebuffer plus color-mode individual plus color-individual custom-1. Film against a millisecond clock. Confirm all 30 pucks change together, and measure request-to-light latency (median and p95 over 20 writes).
- Rate test: send 50 alternating frames to one module at 5 s, 2 s and 1 s intervals, then to 9 modules in parallel. Log HTTP codes and look for silent drops or throttling.
- Colour test: compare 2200 K / 2700 K white against RGB amber, orange and peach, and against mixed RGB + white in the framebuffer (whiteBrightness > 0 with RGB). Photograph at equal exposure and measure with a colorimeter or phone meter. Choose recipes for RAINBOW ROW SUNSET and GOLDEN HOUR.
- Effect enum mapping: in the app, apply each available effect and speed to a saved pattern, then read back motionEffect, motionSpeed, brightnessEffect, brightnessSpeed and brightnessDepth to build an enum table.
- Offline test: with an animated effect running, unplug the router's internet uplink for 10 min. Confirm the animation keeps running, then time how long the cloud takes to reconnect after the uplink returns.
- Sync drift: start the same motion effect on 2-3 controllers in one burst of API calls. Film 10 min and measure phase drift. Also test whether a 'B' module (rotated 180°) needs a mirrored pattern or reverse motion.
- Power-loss behaviour: pull the plug in each mode (white, saved pattern, preset, party). Does it come back to the last state? Does a Hubspace schedule reset the colour?
- Chaining (for fewer devices or cross-module sync): put 60 pucks (two kits' strings) on one controller and check detection and framebuffer length. Optionally try 90 pucks (officially unsupported) and log the result and heat.
- Thermal: 4 h at 100% cool white with the extra cable bundled in a mock above-panel space. Log controller, power supply, cable and puck temperatures; the manual forbids enclosed spaces above 40 °C.
- Splicing: on a spare expansion string, cut between pucks, fit the male and female splicers, power-cycle, and confirm detection and addressing still work.
- Controller buttons: check what Next Scene (M) and a double-press do while a saved pattern is active. Decide whether to hide or lock the controller from guests. Test the party-mode microphone at venue noise levels.
- Failure drill: pull one string mid-install and time the recovery (reconnect plus power cycle). Rehearse swapping a string from a spare SQHDE03-5PSL.

## Open questions
- What is the controller's FCC ID and radio module (Nanoleaf/2AEWY design, or a Leedarson/Afero module)? The manual prints none, and the FCC lookup sites block automated access.
- Does SQHDK03 expose 'color-sequence-v2' custom-1..3, and will the cloud accept a whole framebuffer written through the state PUT? Is the framebuffer length fixed at 30, or does it follow the detected puck count?
- What motionEffect and brightnessEffect values does this firmware support, and do animations on saved patterns run fully on the device?
- Is RGB mixed with white per puck (cct and whiteBrightness alongside r/g/b) actually rendered, or does each puck show only colour or only white?
- What are the real IP ratings of the Hampton Bay power supply and controller? The manual says indoor/dry only; PCWorld says IP65; Nanoleaf's version is IP67.
- What is today's HD price ($99 per a third-party tracker on Sep 24 2026 vs $119 in Dec 2025, with a $79.97 promo seen), and what is in-store stock at Charleston #1118, #1171 and #1103 for 9-12 kits plus expansion spares?
- Are Hubspace extension cables sold separately, and is any other length (e.g. 100 ft kit) coming for the 2026 holiday season?
- Who honours the warranty in practice (Hampton Bay 3-year, void for commercial use, vs Nanoleaf 2-year linked on HD), and can replacement controllers or strings be bought?
- Does the Hubspace app offer power-loss recovery ('restore-values') for this model?
- Can Hubspace groups start effects on several controllers in sync, or is cloud-stepped animation the only way to get a coordinated wash across the canopy?
- Is 60 pucks a hard firmware limit on the Hubspace version? (Nanoleaf's version does 90 pucks / 147 ft.)
- Can the heads be flush- or recess-mounted in the VWBD panel without breaching the manual's 'covered or recessed' warning and the ETL listing?
- What are the exact preset scene names on this model, for kiosk signage accuracy?

## Goldmines
- https://images.thdstatic.com/catalog/pdfImages/55/55b7c72a-79f1-4dbb-bd4d-f51b0d61538f.pdf (Hampton Bay Use & Care: specs, controller, 60-puck limit, splicing, warranty void in commercial use)
- https://images.thdstatic.com/catalog/pdfImages/41/4183613e-3f9e-4c1f-8c62-a345a012a088.pdf (HD's 'Warranty' link, which is the Nanoleaf Hardware Limited Warranty and proves the maker)
- https://web.archive.org/web/20251209173728/https://www.homedepot.com/p/Hampton-Bay-30-LED-50-ft-Plug-in-Mini-Integrated-LED-Novelty-Permanent-Outdoor-String-Light-Starter-Kit-Powered-by-Hubspace-SQHDK03-15PSL/330863181 (full HD page data: specs, UPC, price, reviews, Q&A; also snapshots 20250909160408 and 20250117235327)
- https://web.archive.org/web/20250328094357/https://www.homedepot.com/p/Hampton-Bay-10-LED-16-4-ft-Plug-in-Mini-Integrated-LED-Novelty-Permanent-Outdoor-String-Light-Expansion-Set-Powered-by-Hubspace-SQHDE03-5PSL/330863255 (expansion set specs, UPC, price, Q&A)
- https://www.pcworld.com/article/2504896/hampton-bay-permanent-string-lights-review.html (hands-on review: LED makeup, 3 patterns, per-puck effects, Bluetooth fallback, IP claims)
- https://nanoleaf.box.com/v/guide-permanent-outdoor-lights (Nanoleaf's manual for the same product: box contents, controller, FAQ)
- https://support.nanoleaf.me/api/v2/help_center/articles/search.json?query=permanent%20outdoor%20lights (Nanoleaf help-center API: IP ratings, extension limits, buttons, power-loss recovery)
- https://www.upcitemdb.com/upc/4897105330066 (UPC prefix 4897105 = Nanoleaf)
- github.com/tpural/aioafero@178999d tests/v1/device_dumps/light-rgbic.json (full color-sequence-v2 framebuffer example)
- github.com/Expl0dingBanana/aioafero@58864da tests/v1/device_dumps/light-with-speed.json and light-with-speaker.json (20-segment Hampton Bay string and motionEffect examples)
- https://github.com/Expl0dingBanana/aioafero/pull/61 and https://github.com/jdeath/Hubspace-Homeassistant/issues/224 (selecting saved slots; app certificate pinning)
- https://github.com/jdeath/Hubspace-Homeassistant/issues/205 (speed -10..+10 on Hampton Bay string lights)