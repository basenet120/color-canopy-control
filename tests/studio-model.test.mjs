// The Studio's live scenes (web/ops/showcase/studio-model.mjs, D68 / PR B review UI-5): GET /api/canopy/scenes laid
// over demo-model's SCENES, so the chips, tiles and copy are the show's live looks, and SCENES only stands in when the
// read fails. Run with `node --test tests/*.test.mjs`.
import test from "node:test";
import assert from "node:assert/strict";
import { SCENES } from "../web/ops/showcase/demo-model.mjs";
import * as S from "../web/ops/showcase/studio-model.mjs";

// shaped like GET /api/canopy/scenes after a publish of Coastal Tides (colour 1 #3060C0, renamed)
const ANSWER = {
  generation: 3,
  live_id: "p0002",
  scenes: SCENES.map((s) => ({
    id: s.id,
    name: s.id === "coastal-tides" ? "Deep Coastal Tides" : s.name,
    mood: s.mood,
    description: s.id === "coastal-tides" ? "Deeper blues along the water." : s.description,
    brightness: s.id === "coastal-tides" ? 70 : s.brightness,
    default_solid: 0,
    palette: s.id === "coastal-tides" ? [{ name: "Ocean blue", hex: "#3060c0" }, ...s.palette.slice(1)] : s.palette,
    source: s.id === "coastal-tides" ? "published" : "approved",
  })),
};

test("the live answer replaces each scene's palette, name, mood, description and brightness; the order and style stay", () => {
  const out = S.liveScenes(SCENES, ANSWER);
  assert.equal(out.live, true);
  assert.equal(out.generation, 3);
  assert.deepEqual(out.scenes.map((s) => s.id), SCENES.map((s) => s.id));
  const tides = out.scenes.find((s) => s.id === "coastal-tides");
  const old = SCENES.find((s) => s.id === "coastal-tides");
  assert.equal(tides.palette[0].hex, "#3060C0");
  assert.notEqual(old.palette[0].hex, "#3060C0"); // the handoff copy is never edited
  assert.equal(tides.name, "Deep Coastal Tides");
  assert.equal(tides.description, "Deeper blues along the water.");
  assert.equal(tides.brightness, 70);
  assert.equal(tides.style, old.style);
  assert.equal(tides.defaultColor, old.defaultColor);
  assert.equal(tides.source, "published");
});

test("a failed or unusable read falls back to SCENES and says it isn't live", () => {
  for (const bad of [null, {}, { scenes: "x" }, { scenes: [{ id: "coastal-tides", palette: [{ hex: "nope" }] }] }]) {
    const out = S.liveScenes(SCENES, bad);
    assert.equal(out.live, false);
    assert.equal(out.scenes, SCENES);
  }
  assert.match(S.FALLBACK_CAPTION, /couldn’t be read/);
  assert.match(S.LIVE_CAPTION, /as the show runs it now/);
});

test("a newer generation makes the Studio drop its cached designs; the same one doesn't", () => {
  const a = S.liveScenes(SCENES, ANSWER);
  const same = S.liveScenes(SCENES, { ...ANSWER });
  const next = S.liveScenes(SCENES, { ...ANSWER, generation: 4 });
  assert.equal(S.scenesChanged(a, same), false);
  assert.equal(S.scenesChanged(a, next), true);
  assert.equal(S.scenesChanged({ live: false }, a), true);
  assert.equal(S.scenesChanged(a, { live: false }), false);
});

test("the palette fallback picture paints the scene the Studio shows (the live palette), left to right", () => {
  const tides = S.liveScenes(SCENES, ANSWER).scenes.find((s) => s.id === "coastal-tides");
  const p = S.palettePainters(tides, 225);
  assert.equal(p.source, "palette");
  assert.deepEqual(p.head({ x: 0 }), { r: 0x30, g: 0x60, b: 0xc0 });
  assert.deepEqual(p.spill(1), { r: 0xe9, g: 0xf3, b: 0xe7 });
  const calm = SCENES.find((s) => s.id === "lowcountry-calm");
  const c = S.palettePainters(calm, 225);
  assert.deepEqual(c.spill(0), c.spill(1)); // Calm mirrors, as demo-model's sceneColorAt does
});
