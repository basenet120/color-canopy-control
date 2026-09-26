# Simulator device profiles

`hampton-bay-string-20seg.json` — a Hampton Bay Hubspace "String Lights" device (Novolink-made, 20 addressable segments, 3 custom pattern slots) taken from element `[2]` of `tests/v1/device_dumps/light-with-speed.json` in [aioafero](https://github.com/Expl0dingBanana/aioafero) at commit `58864da1` (v9.3.0). MIT license: `LICENSE-aioafero.txt`.

It is in aioafero's post-processed `AferoDevice` shape (as produced by its anonymizer), **not** the raw API shape that `fetch_metadevice` returns. It is the closest public sibling of the canopy's SQHDK03 strand until a real SQHDK03 dump exists.

To seed a 30-head canopy module from it, change all three `color-individual` hints (`"20-segment"` → `"30-segment"`), all three `color-sequence-v2` framebuffers (20 → 30 entries) and `default_image` together.

When bench Gate A produces the real SQHDK03 profile, add it here as `sqhdk03.json` (scrubbed: device and metadevice ids, SSID, MACs, geo-coordinates, names) and make it the canopy default.
