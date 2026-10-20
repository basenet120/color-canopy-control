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
//
// The fins (D74): when the payload carries them, the drawing grows by the 6 in apron on every side (its outline) and
// each of the 650 fins is a short stroke along its plan axis over smaller head dots. A fin claims only what its four
// nearest heads claim, mixed with weights exp(−(d / 6)²) (`finNeighbours`, `finPaint`): lit and changing heads give
// their colours at full strength (the level, the fin's drop and the light reaching it are opacity only); a fin is
// changing, and pulses with its dots, when at least half its lit weight is changing; a fin with no lit head is unlit
// grey; and a fin with 10% or more of its weight on unknown heads claims nothing (the kiosk dashes it).

export const UNLIT = "#D3D2D8"; // an unlit puck (the app's unattached-puck grey)
export const DIM_FLOOR = 0.35; // the opacity of the dimmest level, so 1% never reads as off
export const DOT_R_IN = 3.6; // a head's radius in canopy inches (heads are 10 in apart, rows staggered by 5 in)
export const FIN_DOT_R_IN = 1.6; // a head's radius when the fins are drawn, so the strokes around it stay visible
export const FIN_STROKE_IN = 1.2; // a fin stroke's width in canopy inches
export const FIN_SIGMA_IN = 6; // a head's weight on a fin: exp(−(d / FIN_SIGMA_IN)²), the server's fins-field form
export const FIN_NEAR = 4; // the heads each fin mixes
export const FIN_KNOWN_SHARE = 0.9; // the weight share on known heads a fin needs before it claims a colour
export const FIN_LIGHT_FULL = 1; // the lit weight at which a fin reads at full light (less fades it by its root)
export const FIN_ALPHA_MIN = 0.45; // the shallowest fin's opacity (14 in); the deepest (24.5 in) is 1
export const FIN_UNLIT_ALPHA = 0.5; // an unlit fin is drawn at half its drop's opacity
const LIT = new Set(["lit", "changing"]);
const UNLIT_STATES = new Set(["off", "dark"]);
const round3 = (v) => Math.round(v * 1000) / 1000;

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

/** The heads in layout() order (panels in payload order, then lattice order): [[x, y]] in canopy inches. */
function headPoints(canopy) {
  const out = [];
  for (const m of canopy.modules || []) for (const [x, y] of m.heads || []) out.push([Number(x), Number(y)]);
  return out;
}

const NEIGHBOURS = new WeakMap(); // canopy object → {near, alpha}
let lastNeighbours = { key: null, value: null }; // the geometry last measured, for a new object of the same geometry

/**
 * Each fin's `FIN_NEAR` nearest heads and its drop's opacity: `{near: [[{i, w}] × 4] per fin, alpha: [number] per
 * fin}`. `i` is the head's index in layout() order (ties go to the lower index), `w` = exp(−(d / FIN_SIGMA_IN)²) with
 * d in canopy inches, nearest first. alpha runs from FIN_ALPHA_MIN at the shallowest drop to 1 at the deepest (1 for
 * all when every fin drops the same). It depends only on the fins and the heads, never the view or a box, so it is
 * measured once per geometry: a new poll of the same canopy, or many thumbnails, reuse it. Don't mutate the result.
 */
export function finNeighbours(canopy) {
  const known = NEIGHBOURS.get(canopy);
  if (known) return known;
  const fins = Array.isArray(canopy.fins) ? canopy.fins : [];
  const key = JSON.stringify([fins, (canopy.modules || []).map((m) => m.heads)]);
  if (lastNeighbours.key === key) {
    NEIGHBOURS.set(canopy, lastNeighbours.value);
    return lastNeighbours.value;
  }
  const heads = headPoints(canopy);
  const drops = fins.map((f) => Number(f.drop_in));
  const lo = drops.length ? Math.min(...drops) : 0;
  const hi = drops.length ? Math.max(...drops) : 0;
  const near = [];
  const alpha = [];
  fins.forEach((f, j) => {
    const fx = Number(f.x);
    const fy = Number(f.y);
    const best = []; // [{i, d}] nearest first; a later head never displaces an equally near earlier one
    heads.forEach(([x, y], i) => {
      const d = Math.hypot(x - fx, y - fy);
      if (best.length === FIN_NEAR && d >= best[best.length - 1].d) return;
      let at = best.length;
      while (at > 0 && best[at - 1].d > d) at -= 1;
      best.splice(at, 0, { i, d });
      if (best.length > FIN_NEAR) best.pop();
    });
    near.push(best.map(({ i, d }) => ({ i, w: Math.exp(-((d / FIN_SIGMA_IN) ** 2)) })));
    alpha.push(hi > lo ? round3(FIN_ALPHA_MIN + ((1 - FIN_ALPHA_MIN) * (drops[j] - lo)) / (hi - lo)) : 1);
  });
  const value = { near, alpha };
  lastNeighbours = { key, value };
  NEIGHBOURS.set(canopy, value);
  return value;
}

/**
 * The drawing: `{width, height, apron, outline: {x, y, w, h}, dots: [{pos, k, cx, cy, r}] × 270, panels: [{pos, x, y,
 * w, h}] × 9, fins: [{x1, y1, x2, y2, drop, alpha, near}], strokeWidth, key}` in a box `box.width × box.height`
 * (default: canopy inches plus the apron on every side), scaled to fit and centred. `k` is the head's lattice index in
 * its panel (the index into `lit.modules[].colors`). With `apron_in` the drawing is the canopy plus that margin all
 * round (`outline`); without it, everything is as before the fins. A fin is a stroke `fin_width_in` long along its
 * plan axis (angle_deg: 0 = +x, 90 = +y, y down), turned with the view; `near` is finNeighbours(canopy)'s entry.
 * `key` changes only when the geometry or view does.
 */
export function layout(canopy, box = null) {
  const t = viewTransform(canopy);
  const a = Math.max(0, Number(canopy.apron_in) || 0);
  const fw = t.width + 2 * a;
  const fh = t.height + 2 * a;
  const bw = box ? Number(box.width) : fw;
  const bh = box ? Number(box.height) : fh;
  const s = Math.min(bw / fw, bh / fh);
  const ox = (bw - s * fw) / 2 + a * s;
  const oy = (bh - s * fh) / 2 + a * s;
  const at = (x, y) => {
    const [u, v] = t.point(x, y);
    return [ox + s * u, oy + s * v];
  };
  const fins = Array.isArray(canopy.fins) ? canopy.fins : [];
  const r = (fins.length ? FIN_DOT_R_IN : DOT_R_IN) * s;
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
      dots.push({ pos: m.pos, k, cx, cy, r });
    });
  }
  const W = Number(canopy.width_in);
  const H = Number(canopy.height_in);
  const [lx, ly] = at(-a, -a);
  const [hx, hy] = at(W + a, H + a);
  const outline = { x: Math.min(lx, hx), y: Math.min(ly, hy), w: Math.abs(hx - lx), h: Math.abs(hy - ly) };
  const half = (Number(canopy.fin_width_in) || 0) / 2;
  const measured = fins.length ? finNeighbours(canopy) : { near: [], alpha: [] };
  const strokes = fins.map((f, j) => {
    const th = (Number(f.angle_deg) * Math.PI) / 180;
    const dx = half * Math.cos(th);
    const dy = half * Math.sin(th);
    const [x1, y1] = at(Number(f.x) - dx, Number(f.y) - dy);
    const [x2, y2] = at(Number(f.x) + dx, Number(f.y) + dy);
    return { x1, y1, x2, y2, drop: Number(f.drop_in), alpha: measured.alpha[j], near: measured.near[j] };
  });
  return {
    width: bw,
    height: bh,
    apron: a,
    outline,
    dots,
    panels,
    fins: strokes,
    strokeWidth: FIN_STROKE_IN * s,
    key: geometryKey(canopy, box),
  };
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
    if (Array.isArray(litModule.headLevels) && litModule.headLevels[k] === 0) {
      return { kind: "unlit", fill: UNLIT, pulse: false }; // D80: a light this step turns off
    }
    const c = Array.isArray(litModule.colors) ? litModule.colors[k] : litModule.color;
    if (typeof c === "string" && /^#[0-9a-f]{6}$/i.test(c)) {
      return { kind: state, fill: c.toUpperCase(), pulse: state === "changing" && !reduced };
    }
  }
  return { kind: "unknown", fill: null, pulse: false };
}

/**
 * How a fin is painted from its heads' paints: `{kind, stroke, opacity, pulse}`. `paints[i]` is paintFor's result for
 * head i in layout() order, optionally with its own `opacity` (else `opacity`, the level's). The fin mixes its
 * `near` heads by weight:
 * - 10% or more of the weight on unknown heads (or a missing paint): "unknown", no stroke colour, no claim;
 * - no lit or changing head: "unlit", UNLIT at half its drop's opacity;
 * - else the lit and changing heads' colours mixed at full strength (channels rounded), "changing" when at least half
 *   that lit weight is changing (it pulses unless `reduced`), at opacity = their opacity × the drop's alpha × the
 *   light reaching it (√(lit weight / FIN_LIGHT_FULL), at most 1).
 */
export function finPaint(fin, paints, { opacity = 1, reduced = false } = {}) {
  let total = 0;
  let unknownW = 0;
  let litW = 0;
  let changingW = 0;
  let opacityW = 0;
  const rgb = [0, 0, 0];
  for (const { i, w } of fin.near || []) {
    total += w;
    const p = paints[i];
    if (!p || p.kind === "unknown") {
      unknownW += w;
      continue;
    }
    if (p.kind !== "lit" && p.kind !== "changing") continue;
    litW += w;
    if (p.kind === "changing") changingW += w;
    const hex = String(p.fill);
    for (let c = 0; c < 3; c += 1) rgb[c] += w * parseInt(hex.slice(1 + 2 * c, 3 + 2 * c), 16);
    opacityW += w * (p.opacity ?? opacity);
  }
  if (!(total > 0) || unknownW >= (1 - FIN_KNOWN_SHARE) * total - 1e-9) {
    return { kind: "unknown", stroke: null, opacity: 1, pulse: false };
  }
  if (litW === 0) return { kind: "unlit", stroke: UNLIT, opacity: round3(fin.alpha * FIN_UNLIT_ALPHA), pulse: false };
  const stroke = `#${rgb.map((v) => Math.round(v / litW).toString(16).padStart(2, "0")).join("")}`.toUpperCase();
  const kind = 2 * changingW >= litW ? "changing" : "lit";
  const level = (opacityW / litW) * fin.alpha * Math.min(1, Math.sqrt(litW / FIN_LIGHT_FULL));
  return { kind, stroke, opacity: round3(level), pulse: kind === "changing" && !reduced };
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

/** Dot k's opacity: in a per-light run (D80) its own level times the panel's, else the panel's (`moduleOpacity`). */
export function headOpacity(module, k, lit, floor = DIM_FLOOR) {
  const own = module && Array.isArray(module.headLevels) ? module.headLevels[k] : undefined;
  if (typeof own !== "number") return moduleOpacity(module, lit, floor);
  const panel = module && typeof module.level === "number" ? module.level : lit && typeof lit.level === "number" ? lit.level : 100;
  return levelOpacity((own * panel) / 100, floor);
}

/** One per-light step's heads (D80): "RGBL" per head → {colors: ["#RRGGBB"], levels: [0–100]}. */
export function decodeHeads(text) {
  const colors = [];
  const levels = [];
  if (typeof text !== "string") return null;
  for (let i = 0; i + 4 <= text.length; i += 4) {
    const ch = (j) => parseInt(text[i + j], 16) * 17;
    colors.push("#" + [ch(0), ch(1), ch(2)].map((v) => v.toString(16).padStart(2, "0")).join("").toUpperCase());
    levels.push(Math.round((parseInt(text[i + 3], 16) * 100) / 15));
  }
  return { colors, levels };
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
    let raw = step > 0 ? Math.floor((t - Number(motion.t0)) / step) : last;
    if (Array.isArray(motion.times) && motion.times.length === motion.frames.length) {
      // the rate test's uneven steps (D80): the last frame that has started
      raw = 0;
      for (let j = 0; j < motion.times.length; j += 1) if (Number(motion.times[j]) <= t) raw = j;
    }
    const index = Math.min(last, Math.max(0, Number.isFinite(raw) ? raw : last));
    const frame = motion.frames[index] || [];
    const levels = Array.isArray(motion.levels) ? motion.levels[index] || [] : null;
    // D80: a per-light run gives each lit panel its lights' own colours and levels
    const heads =
      Array.isArray(motion.heads) && Array.isArray(motion.head_steps) ? motion.heads[motion.head_steps[index]] || null : null;
    return modules.map((m) => {
      if (!LIT.has(m.state)) return m;
      const i = GRID.indexOf(m.pos);
      const hex = i >= 0 ? frame[i] : null;
      if (typeof hex !== "string") return { pos: m.pos, state: "dark", color: null, colors: null };
      const item = { pos: m.pos, state: m.state, color: hex, colors: null };
      if (levels && typeof levels[i] === "number" && Number.isFinite(levels[i])) item.level = levels[i];
      const own = heads ? decodeHeads(heads[i]) : null;
      if (own) {
        item.colors = own.colors;
        item.headLevels = own.levels;
      }
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
