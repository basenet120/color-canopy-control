// The Now-showing picture of the canopy: 270 dots on its 3 × 3 panels (docs/design/guest-panel.md §2.6). Pure: no
// DOM, no network. kiosk.js draws what these functions return; tests/canopy-preview.test.mjs checks them.
//
// Honesty (CLAUDE.md "kiosk copy must be true"; a picture is copy too): every colour comes from the server's
// `state.lit` (conductor/canopy_view.py), which says per panel what may truthfully be claimed: `lit` and `changing`
// carry the colours the lights were sent (full strength; the level is drawn as opacity), `off` and `dark` are unlit
// grey, and `unknown` (a test page holds the panel, it is stuck, or its look isn't known) is an outline with no fill,
// so the picture never claims a colour nobody can vouch for. Geometry is `panel.canopy`: canopy inches, origin top
// left, each panel's heads in lattice order (never its wiring). `view` turns it to match what a guest at the stand
// sees overhead: `mirror_x` flips left and right, then `rotate_deg` turns it clockwise (kiosk.preview, set at install).

export const UNLIT = "#D3D2D8"; // an unlit puck (the app's unattached-puck grey)
export const DIM_FLOOR = 0.35; // the opacity of the dimmest level, so 1% never reads as off
export const DOT_R_IN = 3.6; // a head's radius in canopy inches (heads are 10 in apart, rows staggered by 5 in)
const LIT = new Set(["lit", "changing"]);
const UNLIT_STATES = new Set(["off", "dark"]);

/** The nine positions in grid order, row by row (the order of `motion.frames[i]`). */
export const GRID = [0, 1, 2].flatMap((r) => [0, 1, 2].map((c) => `r${r}c${c}`));

/** `(x, y)` in canopy inches → the view's own coordinates, and the view's size. */
function viewTransform(canopy) {
  const W = Number(canopy.width_in);
  const H = Number(canopy.height_in);
  const view = canopy.view || {};
  const mirror = view.mirror_x === true;
  const deg = [0, 90, 180, 270].includes(Number(view.rotate_deg)) ? Number(view.rotate_deg) : 0;
  const turned = deg === 90 || deg === 270;
  const point = (x0, y) => {
    const x = mirror ? W - x0 : x0;
    if (deg === 90) return [H - y, x];
    if (deg === 180) return [W - x, H - y];
    if (deg === 270) return [y, W - x];
    return [x, y];
  };
  return { point, width: turned ? H : W, height: turned ? W : H };
}

/**
 * The drawing: `{width, height, dots: [{pos, k, cx, cy, r}] × 270, panels: [{pos, x, y, w, h}] × 9, key}` in a box
 * `box.width × box.height` (default: canopy inches), scaled to fit and centred. `k` is the head's lattice index in its
 * panel (the index into `lit.modules[].colors`). `key` changes only when the geometry or view does.
 */
export function layout(canopy, box = null) {
  const t = viewTransform(canopy);
  const bw = box ? Number(box.width) : t.width;
  const bh = box ? Number(box.height) : t.height;
  const s = Math.min(bw / t.width, bh / t.height);
  const ox = (bw - s * t.width) / 2;
  const oy = (bh - s * t.height) / 2;
  const at = (x, y) => {
    const [u, v] = t.point(x, y);
    return [ox + s * u, oy + s * v];
  };
  const mw = Number(canopy.module_w_in);
  const mh = Number(canopy.module_h_in);
  const dots = [];
  const panels = [];
  for (const m of canopy.modules || []) {
    const x0 = m.col * mw;
    const y0 = m.row * mh;
    const [ax, ay] = at(x0, y0);
    const [bx, by] = at(x0 + mw, y0 + mh);
    panels.push({ pos: m.pos, x: Math.min(ax, bx), y: Math.min(ay, by), w: Math.abs(bx - ax), h: Math.abs(by - ay) });
    (m.heads || []).forEach(([x, y], k) => {
      const [cx, cy] = at(x, y);
      dots.push({ pos: m.pos, k, cx, cy, r: DOT_R_IN * s });
    });
  }
  return { width: bw, height: bh, dots, panels, key: geometryKey(canopy, box) };
}

/** A string that changes only when the geometry, the view or the box does (rebuild the dots only then). */
export function geometryKey(canopy, box = null) {
  return JSON.stringify([canopy, box ? [box.width, box.height] : null]);
}

/**
 * How head `k` of a panel is painted: `{kind, fill, pulse}`. kind is "lit" | "changing" | "unlit" | "unknown"; fill
 * is "#RRGGBB" or null (unknown: an outline only, no colour claim). A lit or changing panel without a colour for
 * that head is unknown too. `pulse` is true for a changing panel unless reduced motion asks for stillness.
 */
export function paintFor(litModule, k, { reduced = false } = {}) {
  const state = litModule && litModule.state;
  if (UNLIT_STATES.has(state)) return { kind: "unlit", fill: UNLIT, pulse: false };
  if (LIT.has(state)) {
    const c = Array.isArray(litModule.colors) ? litModule.colors[k] : litModule.color;
    if (typeof c === "string" && /^#[0-9a-f]{6}$/i.test(c)) {
      return { kind: state, fill: c.toUpperCase(), pulse: state === "changing" && !reduced };
    }
  }
  return { kind: "unknown", fill: null, pulse: false };
}

/** The lit dots' opacity for a brightness level: `floor` at 0, 1 at 100 (1 when the level isn't known). */
export function levelOpacity(level, floor = DIM_FLOOR) {
  const n = Number(level);
  if (level === null || level === undefined || !Number.isFinite(n)) return 1;
  const f = Math.min(1, Math.max(0, Number(floor)));
  const v = f + ((1 - f) * Math.min(100, Math.max(0, n))) / 100;
  return Math.round(v * 1000) / 1000;
}

/** A panel's dots' opacity: its own `level` when a breathing run's frame gives it one (D75), else the canopy's. */
export function moduleOpacity(module, lit, floor = DIM_FLOOR) {
  const own = module && typeof module.level === "number" ? module.level : undefined;
  return levelOpacity(own !== undefined ? own : lit && lit.level, floor);
}

const byPos = (modules) => new Map((modules || []).map((m) => [m.pos, m]));

/** True while `motion` still has a frame to come at `now` (server seconds): the page animates only then. */
export function motionActive(motion, now) {
  if (!motion || typeof motion !== "object") return false;
  if (motion.kind === "ripple") {
    const rings = Array.isArray(motion.rings) ? motion.rings.length : 0;
    return rings > 1 && now < Number(motion.t0) + (rings - 1) * Number(motion.stagger_s);
  }
  if (motion.kind === "steps") return now < Number(motion.ends_at);
  return false;
}

/**
 * The nine panels to draw at `now` (server seconds, the page's estimate): `[{pos, state, color, colors}] × 9`.
 *
 * - ripple `{t0, stagger_s, rings: [[pos]]}`: ring j switches from the previous picture to `lit` at t0 + j·stagger_s
 *   (a panel without a previous picture reads unknown until its ring lands; a panel in no ring shows `lit` at once).
 * - steps `{t0, step_s, frames: [[hex | null] × 9 in grid order], ends_at, levels?}`: frame floor((now − t0)/step_s),
 *   clamped to the frames there are; after ends_at the frame showing then holds (the last one sent, so a run cut short
 *   holds where it stopped). A null entry is a dark panel; an off, dark or unknown panel keeps its own state. With
 *   `levels` (D75: [[level | null] × 9] alongside frames, a breath's brightness per panel) a lit panel also gets that
 *   frame's `level`, drawn as its opacity (`moduleOpacity`); its colour stays full strength.
 * - anything else, or no motion: `lit` as it is (the still frame).
 */
export function motionColors(motion, prevLit, lit, now) {
  const modules = (lit && lit.modules) || [];
  if (!motion || typeof motion !== "object") return modules;
  if (motion.kind === "ripple" && Array.isArray(motion.rings)) {
    const prev = byPos(prevLit && prevLit.modules);
    const ringOf = new Map();
    motion.rings.forEach((ring, j) => (ring || []).forEach((pos) => ringOf.set(pos, j)));
    return modules.map((m) => {
      if (!ringOf.has(m.pos)) return m;
      const at = Number(motion.t0) + ringOf.get(m.pos) * Number(motion.stagger_s);
      if (now >= at) return m;
      return prev.get(m.pos) || { pos: m.pos, state: "unknown", color: null, colors: null };
    });
  }
  if (motion.kind === "steps" && Array.isArray(motion.frames) && motion.frames.length) {
    const step = Number(motion.step_s);
    const last = motion.frames.length - 1;
    const t = Math.min(now, Number(motion.ends_at) - 1e-6); // time stops at ends_at
    const raw = step > 0 ? Math.floor((t - Number(motion.t0)) / step) : last;
    const index = Math.min(last, Math.max(0, Number.isFinite(raw) ? raw : last));
    const frame = motion.frames[index] || [];
    const levels = Array.isArray(motion.levels) ? motion.levels[index] || [] : null;
    return modules.map((m) => {
      if (!LIT.has(m.state)) return m;
      const i = GRID.indexOf(m.pos);
      const hex = i >= 0 ? frame[i] : null;
      if (typeof hex !== "string") return { pos: m.pos, state: "dark", color: null, colors: null };
      const item = { pos: m.pos, state: m.state, color: hex, colors: null };
      if (levels && typeof levels[i] === "number" && Number.isFinite(levels[i])) item.level = levels[i];
      return item;
    });
  }
  return modules;
}

/** The picture's accessible name, e.g. "Preview: 2 of 9 panels lit". */
export function ariaSummary(lit) {
  const modules = (lit && lit.modules) || [];
  const n = modules.length;
  if (!n) return "Preview of the canopy";
  if (modules.every((m) => m.state === "off")) return "Preview: the canopy is off";
  const count = (states) => modules.filter((m) => states.includes(m.state)).length;
  const on = count(["lit", "changing"]);
  const changing = count(["changing"]);
  const unknown = count(["unknown"]);
  let text = `Preview: ${on} of ${n} panels lit`;
  if (changing) text += `, ${changing} changing`;
  if (unknown) text += `, ${unknown} not shown`;
  return text;
}
