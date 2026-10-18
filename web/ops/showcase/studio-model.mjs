// The Studio's scenes as the show runs them (D68): GET /api/canopy/scenes (the live looks: approved or published)
// laid over demo-model's six (their order, tile style and chosen chip), so the palette chips, the tiles, the name,
// mood and description agree with the illustration (GET /api/canopy/design, also the live looks) and with the iPad.
// Only when the read fails do the Studio's own handoff copies stand in, and the caption says so.

export const LIVE_CAPTION = 'The colours this scene is designed from, as the show runs it now.';
export const FALLBACK_CAPTION =
  'The approved colours from this page’s own copy: the show’s live looks couldn’t be read, so a published change isn’t shown here.';
export const REFRESH_MS = 15000; // a slow re-read while the page is visible (a publish from the Lab or another screen)

const HEX = /^#[0-9a-f]{6}$/i;
const text = (v, max) => (typeof v === 'string' && v.trim() ? v.slice(0, max) : null);

/** The scenes the Studio shows: `fallback` (demo-model's SCENES) with each scene's name, mood, description, palette
 * and brightness from `answer` (GET /api/canopy/scenes) when it has that scene in a usable shape.
 * → {scenes, live: bool, generation}. `live` is false when the answer is missing or names none of the scenes. */
export function liveScenes(fallback, answer) {
  const list = answer && Array.isArray(answer.scenes) ? answer.scenes : null;
  const byId = new Map((list || []).filter((s) => s && typeof s.id === 'string').map((s) => [s.id, s]));
  let used = 0;
  const scenes = fallback.map((scene) => {
    const s = byId.get(scene.id);
    if (!s) return scene;
    const palette = Array.isArray(s.palette)
      ? s.palette.filter((c) => c && HEX.test(String(c.hex))).map((c) => ({name: text(c.name, 60) || String(c.hex).toUpperCase(), hex: String(c.hex).toUpperCase()}))
      : [];
    if (palette.length < 2) return scene;
    used += 1;
    const brightness = Number.isInteger(s.brightness) && s.brightness >= 1 && s.brightness <= 100 ? s.brightness : scene.brightness;
    const defaultColor = Math.min(scene.defaultColor || 0, palette.length - 1);
    return {
      ...scene,
      name: text(s.name, 40) || scene.name,
      mood: text(s.mood, 60) || scene.mood,
      description: text(s.description, 200) || scene.description,
      palette,
      brightness,
      defaultColor,
      source: s.source === 'published' ? 'published' : 'approved',
    };
  });
  const live = used > 0;
  return {scenes: live ? scenes : fallback, live, generation: live && Number.isInteger(answer.generation) ? answer.generation : null};
}

/** Whether the Studio's cached designs and scenes are stale against a newer answer (another publish since). */
export function scenesChanged(before, after) {
  if (!after || !after.live) return false;
  if (!before || !before.live) return true;
  return before.generation !== after.generation;
}

function rgb(hex) {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
}

/** The palette left to right (the fallback picture when the show's design can't be had), from the scene the Studio
 * shows — the live palette once it is read, never a stale copy. Lowcountry Calm (style calm) mirrors, as
 * demo-model's sceneColorAt does. → {source: 'palette', head, spill}. */
export function palettePainters(scene, width) {
  const colors = (scene && scene.palette ? scene.palette : []).map((c) => rgb(c.hex));
  const at = (position) => {
    if (!colors.length) return {r: 0, g: 0, b: 0};
    let p = Math.max(0, Math.min(1, position));
    if (scene.style === 'calm') p = 1 - Math.abs(p * 2 - 1);
    const x = p * (colors.length - 1);
    const a = Math.floor(x);
    const b = Math.min(a + 1, colors.length - 1);
    const t = x - a;
    const out = colors[a].map((v, c) => Math.round(v * (1 - t) + colors[b][c] * t));
    return {r: out[0], g: out[1], b: out[2]};
  };
  return {source: 'palette', head: (h) => at(h.x / width), spill: (p) => at(p)};
}
