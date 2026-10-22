// The Paint tab's pure model (Oct 21): the guest's painting, its brush and panel hit-tests, and the request it sends
// (POST /api/kiosk/paint, conductor/looks/paint.py). Pure: no DOM, no network. kiosk.js draws and wires it;
// tests/paint-model.test.mjs checks it.
//
// A painting is `{pos: [hex | null] × that panel's lights}`, each panel's lights in its lattice order (the order of
// `canopy.modules[i].heads`, the `k` of a layout() dot): "#RRGGBB" for a painted light, null for a light left off.
// Geometry comes from canopy-preview.mjs layout() (the view a guest at the stand sees), so hit-tests work in the
// drawing's own units, whatever the box, mirror or rotation.
//
// Honesty (CLAUDE.md "kiosk copy must be true"): the painting is the guest's own drawing, not a picture of the
// ceiling; nothing here claims what the canopy shows. A 200 from the server means "accepted", so the confirmation
// says the painting is on its way, never that it is lit.

/** The bold presets, in the order the palette shows them. */
export const PALETTE = [
  { name: "Red", hex: "#FF1E1E" },
  { name: "Orange", hex: "#FF6A00" },
  { name: "Amber", hex: "#FFB000" },
  { name: "Yellow", hex: "#FFE500" },
  { name: "Lime", hex: "#9BE000" },
  { name: "Green", hex: "#00C853" },
  { name: "Teal", hex: "#00B3A1" },
  { name: "Cyan", hex: "#00D4FF" },
  { name: "Blue", hex: "#2962FF" },
  { name: "Violet", hex: "#7C3AED" },
  { name: "Magenta", hex: "#E400D0" },
  { name: "Pink", hex: "#FF5FA2" },
  { name: "Warm white", hex: "#FFE4BC" },
];

/** The paint modes: the segmented control's order, each with the `kind` the server takes. */
export const MODES = [
  { id: "whole", label: "Whole canopy", hint: "Tap the canopy to fill every light." },
  { id: "modules", label: "Panels", hint: "Tap a panel to fill its 30 lights." },
  { id: "lights", label: "Draw", hint: "Drag a finger to paint light by light." },
];

/** Brush radius in light spacings (a diameter of about 1.3, 2 and 3.5 lights). Small always reaches the nearest light:
 * on the canopy's lattice (10 in along a row, rows 10 in apart and staggered by 5 in) no point is further than
 * 6.25 in, 0.625 spacings, from a light. */
export const BRUSHES = [
  { id: "small", label: "Small", radius: 0.65 },
  { id: "medium", label: "Medium", radius: 1.1 },
  { id: "large", label: "Large", radius: 1.8 },
];

export const PAINT_SENT = "Sent. Your painting is on its way to the canopy.";
export const PAINT_BLANK = "Paint a few lights first: every light is off.";
export const PAINT_SHOWING = {
  id: "paint",
  name: "Your painting",
  description: "The painting sent from this iPad.",
};

const HEX = /^#[0-9a-f]{6}$/i;

/** "#rgb" / "#rrggbb" (any case) → "#RRGGBB"; anything else → null. */
export function normalizeHex(value) {
  if (typeof value !== "string") return null;
  const v = value.trim();
  if (HEX.test(v)) return v.toUpperCase();
  if (/^#[0-9a-f]{3}$/i.test(v)) return `#${[...v.slice(1)].map((c) => c + c).join("")}`.toUpperCase();
  return null;
}

/** True when a light check mark needs dark ink to show on `hex` (relative luminance above ~0.6). */
export function needsDarkInk(hex) {
  const h = normalizeHex(hex);
  if (!h) return false;
  const lin = (i) => {
    const c = parseInt(h.slice(1 + 2 * i, 3 + 2 * i), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(0) + 0.7152 * lin(1) + 0.0722 * lin(2) > 0.6;
}

/** Every light off: `{pos: [null × that panel's lights]}`. */
export function emptyPainting(canopy) {
  const out = {};
  for (const m of (canopy && canopy.modules) || []) out[m.pos] = (m.heads || []).map(() => null);
  return out;
}

/**
 * The painting the canopy shows now, from `state.lit` (canopy_view): a lit or changing panel's lights take their
 * colours (`colors[k]`, else the panel's `color`), a light a per-light step turns off (`headLevels[k] === 0`) stays
 * off, and off, dark or unknown panels start off. A colour that isn't "#RRGGBB" is off. No `lit`: all off.
 */
export function paintingFromLit(canopy, lit) {
  const out = emptyPainting(canopy);
  const byPos = new Map(((lit && lit.modules) || []).map((m) => [m.pos, m]));
  for (const [pos, colors] of Object.entries(out)) {
    const m = byPos.get(pos);
    if (!m || (m.state !== "lit" && m.state !== "changing")) continue;
    for (let k = 0; k < colors.length; k += 1) {
      if (Array.isArray(m.headLevels) && m.headLevels[k] === 0) continue;
      const c = Array.isArray(m.colors) ? m.colors[k] : m.color;
      const hex = normalizeHex(c);
      colors[k] = hex === "#000000" ? null : hex;
    }
  }
  return out;
}

/** How many lights are painted (not off). */
export function litCount(painting) {
  let n = 0;
  for (const colors of Object.values(painting || {})) for (const c of colors) if (c !== null) n += 1;
  return n;
}

/** True when every light is off (the server refuses that painting). */
export const isBlank = (painting) => litCount(painting) === 0;

/** The usual distance between neighbouring lights in the drawing's units: the median nearest-neighbour distance. */
export function lightSpacing(dots) {
  const near = [];
  for (let i = 0; i < dots.length; i += 1) {
    let best = Infinity;
    for (let j = 0; j < dots.length; j += 1) {
      if (i === j) continue;
      const d = Math.hypot(dots[i].cx - dots[j].cx, dots[i].cy - dots[j].cy);
      if (d > 0 && d < best) best = d;
    }
    if (Number.isFinite(best)) near.push(best);
  }
  if (!near.length) return 1;
  near.sort((a, b) => a - b);
  return near[Math.floor(near.length / 2)];
}

/** The distance from (px, py) to the segment a → b (a point when a = b). */
export function distToSegment(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const len2 = dx * dx + dy * dy;
  const t = len2 > 0 ? Math.min(1, Math.max(0, ((px - ax) * dx + (py - ay) * dy) / len2)) : 0;
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/**
 * The dots a brush of `radius` touches moving from `a` to `b` (`{x, y}` in the drawing's units): every dot within
 * `radius` of the whole segment, so a fast stroke between two pointer moves leaves no gap. Indices into `dots`.
 */
export function dotsInStroke(dots, a, b, radius) {
  const out = [];
  const r = Math.max(0, Number(radius) || 0);
  const lo = { x: Math.min(a.x, b.x) - r, y: Math.min(a.y, b.y) - r };
  const hi = { x: Math.max(a.x, b.x) + r, y: Math.max(a.y, b.y) + r };
  dots.forEach((d, i) => {
    if (d.cx < lo.x || d.cx > hi.x || d.cy < lo.y || d.cy > hi.y) return;
    if (distToSegment(d.cx, d.cy, a.x, a.y, b.x, b.y) <= r) out.push(i);
  });
  return out;
}

/** The panel under (x, y): the one whose box holds it, else the nearest within `slop`, else null. */
export function panelAt(panels, x, y, slop = 0) {
  let best = null;
  let bestD = Infinity;
  for (const p of panels || []) {
    const dx = Math.max(p.x - x, 0, x - (p.x + p.w));
    const dy = Math.max(p.y - y, 0, y - (p.y + p.h));
    const d = Math.hypot(dx, dy);
    if (d === 0) return p.pos;
    if (d <= slop && d < bestD) {
      best = p.pos;
      bestD = d;
    }
  }
  return best;
}

/** Paint dots `indices` (of `dots`, layout() order) `color`; returns the indices whose colour changed. Mutates. */
export function paintDots(painting, dots, indices, color) {
  const changed = [];
  for (const i of indices) {
    const d = dots[i];
    const colors = d && painting[d.pos];
    if (!colors || d.k >= colors.length || colors[d.k] === color) continue;
    colors[d.k] = color;
    changed.push(i);
  }
  return changed;
}

/** Every light of panel `pos` to `color`; the indices (of `dots`) that changed. Mutates. */
export function fillPanel(painting, dots, pos, color) {
  const idx = [];
  dots.forEach((d, i) => {
    if (d.pos === pos) idx.push(i);
  });
  return paintDots(painting, dots, idx, color);
}

/** Every light to `color`; the indices (of `dots`) that changed. Mutates. */
export function fillAll(painting, dots, color) {
  return paintDots(
    painting,
    dots,
    dots.map((_, i) => i),
    color,
  );
}

/**
 * The request body for POST /api/kiosk/paint: `{kind, modules: [{pos, colors}]}`, one entry per canopy panel in
 * payload order, each exactly as long as its heads (a panel the painting lacks, or a short one, is padded with off).
 */
export function paintBody(painting, canopy, kind) {
  const ids = MODES.map((m) => m.id);
  return {
    kind: ids.includes(kind) ? kind : "lights",
    modules: ((canopy && canopy.modules) || []).map((m) => {
      const have = (painting && painting[m.pos]) || [];
      return { pos: m.pos, colors: (m.heads || []).map((_, k) => normalizeHex(have[k]) || null) };
    }),
  };
}

/** Up to `max` distinct colours of a painting in canopy order, as palette entries `{hex}` (Now Showing's tint). */
export function paintPalette(painting, max = 6) {
  const seen = [];
  for (const colors of Object.values(painting || {})) {
    for (const c of colors) {
      if (c !== null && !seen.includes(c)) seen.push(c);
      if (seen.length >= max) return seen.map((hex) => ({ hex }));
    }
  }
  return seen.map((hex) => ({ hex }));
}

/** A refused painting the scene toasts don't cover: a 422's calm `detail` (a painting the server can't take). */
export function paintRefusal(err) {
  const body = err && err.body;
  if (!err || err.status !== 422 || !body || typeof body !== "object" || typeof body.detail !== "string") return null;
  return body.detail ? { text: body.detail, retryAfterS: 0 } : null;
}

/** The mockup's accessible name, e.g. "Your painting: 42 of 270 lights painted". */
export function paintSummary(painting) {
  const total = Object.values(painting || {}).reduce((n, colors) => n + colors.length, 0);
  const n = litCount(painting);
  if (!total) return "Your painting";
  if (!n) return `Your painting: all ${total} lights off`;
  return `Your painting: ${n} of ${total} lights painted`;
}
