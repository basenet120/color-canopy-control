// The Now-showing picture of the canopy (web/kiosk/canopy-preview.mjs): run with `node --test tests/*.test.mjs`.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  DIM_FLOOR,
  DOT_R_IN,
  FIN_DOT_R_IN,
  GRID,
  UNLIT,
  ariaSummary,
  finNeighbours,
  finPaint,
  geometryKey,
  layout,
  levelOpacity,
  motionActive,
  motionColors,
  paintFor,
} from "../web/kiosk/canopy-preview.mjs";

// The canopy as GET /api/kiosk/panel serves it (conductor/canopy_view.py): 225 × 120 in, 3 × 3 panels of 75 × 40 in,
// 30 heads each in lattice order (rows of 8 and 7, staggered by 5 in).
function module(row, col) {
  const heads = [];
  for (let r = 0; r < 4; r += 1) {
    const n = r % 2 === 0 ? 8 : 7;
    const x0 = r % 2 === 0 ? 2.5 : 7.5;
    for (let i = 0; i < n; i += 1) heads.push([col * 75 + x0 + 10 * i, row * 40 + 5 + 10 * r]);
  }
  return { pos: `r${row}c${col}`, row, col, heads };
}
const canopy = (view = {}) => ({
  width_in: 225,
  height_in: 120,
  module_w_in: 75,
  module_h_in: 40,
  view: { mirror_x: false, rotate_deg: 0, dim_floor: 0.35, ...view },
  modules: GRID.map((pos) => module(Number(pos[1]), Number(pos[3]))),
});
const flat = (state, color = null) => GRID.map((pos) => ({ pos, state, color, colors: null }));
// The fins as the panel serves them with the canopy's fin map (D74): a 6 in apron, 5 in fins.
const withFins = (c, fins, { apron = 6, width = 5 } = {}) => ({
  ...c,
  apron_in: apron,
  outline: { x: [-apron, 225 + apron], y: [-apron, 120 + apron] },
  fin_width_in: width,
  fins,
});
// The drawing's own 650 fins (config/fins.json, VWBD A-20-1 extracted): centres reach 6 in into the apron.
const DRAWN = JSON.parse(readFileSync(new URL("../config/fins.json", import.meta.url), "utf8")).fins;
const litOf = (modules, extra = {}) => ({ level: 70, modules, motion: null, ...extra });
const near = (a, b) => Math.abs(a - b) < 1e-9;

test("the canopy is drawn as 270 dots on 9 panels, in canopy inches", () => {
  const d = layout(canopy());
  assert.equal(d.dots.length, 270);
  assert.equal(d.panels.length, 9);
  assert.equal(d.width, 225);
  assert.equal(d.height, 120);
  for (const pos of GRID) assert.equal(d.dots.filter((x) => x.pos === pos).length, 30, pos);
  assert.deepEqual(
    d.dots.filter((x) => x.pos === "r0c0").map((x) => x.k),
    Array.from({ length: 30 }, (_, k) => k),
  );
  const middle = d.panels.find((p) => p.pos === "r1c1");
  assert.deepEqual([middle.x, middle.y, middle.w, middle.h], [75, 40, 75, 40]);
  // every dot sits inside its own panel
  for (const dot of d.dots) {
    const p = d.panels.find((x) => x.pos === dot.pos);
    assert.ok(dot.cx > p.x && dot.cx < p.x + p.w && dot.cy > p.y && dot.cy < p.y + p.h, dot);
  }
});

test("the view: (2.5, 5) is top left unmirrored and top right mirrored; a quarter turn turns the box", () => {
  const first = (c) => layout(c).dots[0];
  const plain = first(canopy());
  assert.deepEqual([plain.cx, plain.cy], [2.5, 5]);
  const mirrored = first(canopy({ mirror_x: true }));
  assert.deepEqual([mirrored.cx, mirrored.cy], [222.5, 5]);
  const turned = layout(canopy({ rotate_deg: 90 }));
  assert.deepEqual([turned.width, turned.height], [120, 225]);
  assert.deepEqual([turned.dots[0].cx, turned.dots[0].cy], [115, 2.5]); // clockwise: top left → top right
  const half = layout(canopy({ rotate_deg: 180 })).dots[0];
  assert.deepEqual([half.cx, half.cy], [222.5, 115]);
  const back = layout(canopy({ rotate_deg: 270 })).dots[0];
  assert.deepEqual([back.cx, back.cy], [5, 222.5]);
  // a box scales to fit and centres
  const boxed = layout(canopy(), { width: 450, height: 400 });
  assert.ok(near(boxed.dots[0].cx, 5) && near(boxed.dots[0].cy, 80 + 10));
  // the geometry key changes only with the geometry or view
  assert.equal(geometryKey(canopy()), geometryKey(canopy()));
  assert.notEqual(geometryKey(canopy()), geometryKey(canopy({ mirror_x: true })));
});

test("paints: lit and changing carry their colour, dark and off are unlit grey, unknown has no fill", () => {
  const colors = Array.from({ length: 30 }, (_, k) => `#0000${k.toString(16).padStart(2, "0")}`);
  assert.deepEqual(paintFor({ state: "lit", color: "#ffd99b", colors: null }, 7), {
    kind: "lit",
    fill: "#FFD99B",
    pulse: false,
  });
  assert.equal(paintFor({ state: "lit", color: null, colors }, 7).fill, "#000007"); // per head, lattice order
  assert.deepEqual(paintFor({ state: "changing", color: "#6FB5D5" }, 0), {
    kind: "changing",
    fill: "#6FB5D5",
    pulse: true,
  });
  for (const state of ["dark", "off"]) {
    assert.deepEqual(paintFor({ state, color: null, colors: null }, 0), { kind: "unlit", fill: UNLIT, pulse: false });
  }
  assert.equal(UNLIT, "#D3D2D8");
  for (const m of [
    { state: "unknown", color: null, colors: null },
    { state: "unknown", color: "#FF0000" }, // never a colour claim, whatever came with it
    { state: "lit", color: null, colors: null }, // lit without a colour: unknown, not black
    { state: "changing", color: "red" },
    { state: "bogus", color: "#FF0000" },
    undefined,
  ]) {
    assert.deepEqual(paintFor(m, 0), { kind: "unknown", fill: null, pulse: false }, JSON.stringify(m));
  }
});

test("the level is opacity with a floor, so the dimmest level never reads as off", () => {
  assert.equal(DIM_FLOOR, 0.35);
  assert.equal(levelOpacity(10), 0.415);
  assert.equal(levelOpacity(100), 1);
  assert.equal(levelOpacity(0), 0.35);
  assert.equal(levelOpacity(50, 0.5), 0.75);
  assert.equal(levelOpacity(null), 1); // unknown level: drawn full
  assert.equal(levelOpacity(150), 1);
});

test("a ripple switches ring by ring at t0 + j × stagger", () => {
  const prev = litOf(flat("lit", "#111111"));
  const lit = litOf(flat("changing", "#222222"));
  const motion = { kind: "ripple", t0: 1000, stagger_s: 1.2, rings: [["r1c1"], ["r0c1", "r1c0", "r1c2", "r2c1"], ["r0c0", "r0c2", "r2c0", "r2c2"]] };
  const colourAt = (now, pos) => motionColors(motion, prev, lit, now).find((m) => m.pos === pos).color;
  assert.equal(colourAt(1000, "r1c1"), "#222222");
  assert.equal(colourAt(1000, "r0c1"), "#111111");
  assert.equal(colourAt(1001.19, "r0c1"), "#111111");
  assert.equal(colourAt(1001.21, "r0c1"), "#222222");
  assert.equal(colourAt(1002.39, "r2c2"), "#111111");
  assert.equal(colourAt(1002.41, "r2c2"), "#222222");
  assert.equal(motionActive(motion, 1002.39), true);
  assert.equal(motionActive(motion, 1002.41), false);
  // a page that has no previous picture claims nothing for a ring still to land
  const fresh = motionColors(motion, null, lit, 1000.5).find((m) => m.pos === "r0c0");
  assert.deepEqual(fresh, { pos: "r0c0", state: "unknown", color: null, colors: null });
  // a panel in no ring shows the new look at once
  const two = { ...motion, rings: [["r1c1"], ["r0c0"]] };
  assert.equal(motionColors(two, prev, lit, 1000).find((m) => m.pos === "r2c2").color, "#222222");
});

test("a stepped run shows frame floor((now − t0) / step_s) and holds the frame showing at ends_at", () => {
  const frames = ["#000001", "#000002", "#000003", "#000004"].map((c) => GRID.map((pos) => (pos === "r2c2" ? null : c)));
  const motion = { kind: "steps", t0: 500, step_s: 4, frames, ends_at: 516 };
  const lit = litOf(flat("lit", "#ABCDEF"));
  const at = (now) => motionColors(motion, null, lit, now);
  assert.equal(at(509.9)[0].color, "#000003"); // index 2
  assert.equal(at(500)[0].color, "#000001");
  assert.equal(at(499)[0].color, "#000001"); // a clock a little behind the server's clamps to the first
  assert.equal(at(515.9)[0].color, "#000004");
  assert.equal(at(530)[0].color, "#000004"); // holds after ends_at
  assert.deepEqual(at(509.9)[8], { pos: "r2c2", state: "dark", color: null, colors: null }); // null = dark
  // a run cut short holds the frame that was showing when it ended, not one never sent
  const cut = { ...motion, ends_at: 509 };
  assert.equal(motionColors(cut, null, lit, 520)[0].color, "#000003");
  assert.equal(motionColors({ ...motion, ends_at: 508 }, null, lit, 520)[0].color, "#000002"); // ends on a boundary
  assert.equal(motionActive(motion, 515.9), true);
  assert.equal(motionActive(motion, 516), false);
  // a panel that is off, dark or unknown keeps its own state, with no colour from the run
  const own = [
    { pos: "r0c0", state: "unknown", color: null, colors: null },
    { pos: "r0c1", state: "dark", color: null, colors: null },
    { pos: "r0c2", state: "off", color: null, colors: null },
  ];
  const mixed = litOf([...own, ...flat("lit", "#ABCDEF").slice(3)]);
  assert.deepEqual(motionColors(motion, null, mixed, 509.9).slice(0, 3), own);
});

test("no motion, or a kind this page doesn't know, is the still frame", () => {
  const lit = litOf(flat("lit", "#ABCDEF"));
  assert.equal(motionColors(null, null, lit, 0), lit.modules);
  assert.equal(motionColors({ kind: "sparkle", t0: 0 }, null, lit, 0), lit.modules);
  assert.equal(motionActive({ kind: "sparkle", t0: 0, ends_at: 1e12 }, 0), false);
  assert.equal(motionActive(null, 0), false);
});

test("reduced motion: no pulse, and the current frame only", () => {
  assert.equal(paintFor({ state: "changing", color: "#6FB5D5" }, 0, { reduced: true }).pulse, false);
  assert.equal(paintFor({ state: "changing", color: "#6FB5D5" }, 0, { reduced: true }).fill, "#6FB5D5");
  const css = readFileSync(new URL("../web/kiosk/kiosk.css", import.meta.url), "utf8");
  const reduced = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
  assert.match(reduced, /\.canopy-preview \.dot \{[^}]*animation: none !important/);
  assert.match(css, /\.canopy-preview \.dot \{ transition: fill 600ms/);
  // the fins (D74): they fade and pulse like the dots, an unknown one is dashed, and reduced motion snaps them
  assert.match(css, /\.canopy-preview \.fin \{[^}]*transition: stroke 600ms/);
  assert.match(css, /\.canopy-preview \.fin\.changing \{[^}]*animation: dot-pulse 1\.2s/);
  assert.match(css, /\.canopy-preview \.fin\.is-unknown \{[^}]*stroke: var\(--slider-rest\)[^}]*stroke-dasharray/);
  assert.match(reduced, /\.canopy-preview \.fin \{[^}]*transition: opacity 120ms[^}]*animation: none !important/);
  assert.doesNotMatch(css, /\.fin\.is-unlit[^{]*\{[^}]*dasharray/);
  assert.equal(finPaint({ alpha: 1, near: [{ i: 0, w: 1 }] }, [{ kind: "changing", fill: "#6FB5D5" }], { reduced: true }).pulse, false);
});

test("the accessible summary counts the panels lit", () => {
  const mods = [...flat("lit", "#ABCDEF").slice(0, 2), ...flat("dark").slice(2)];
  assert.equal(ariaSummary({ modules: mods }), "Preview: 2 of 9 panels lit");
  assert.equal(ariaSummary(litOf(flat("off"))), "Preview: the canopy is off");
  const busy = [...flat("changing", "#ABCDEF").slice(0, 7), ...flat("unknown").slice(7)];
  assert.equal(ariaSummary({ modules: busy }), "Preview: 7 of 9 panels lit, 7 changing, 2 not shown");
  assert.equal(ariaSummary(null), "Preview of the canopy");
});

test("the picture writes no Hubspace copy of its own", () => {
  const js = readFileSync(new URL("../web/kiosk/canopy-preview.mjs", import.meta.url), "utf8").replace(/(^|\s)\/\/.*$/gm, "");
  assert.doesNotMatch(js, /Hubspace/);
  assert.doesNotMatch(js, /https?:\/\//);
});

// ---- the fins (D74) ---------------------------------------------------------------------------------------------
const close = (a, b, eps = 1e-3) => Math.abs(a - b) <= eps;
const fin = (x, y, angle_deg = 0, drop_in = 14) => ({ x, y, angle_deg, drop_in });

test("with an apron the drawing grows by it on every side and the canopy keeps its place", () => {
  const c = withFins(canopy(), [fin(10, 20)]);
  const d = layout(c);
  assert.deepEqual([d.width, d.height, d.apron], [237, 132, 6]);
  assert.deepEqual([d.dots[0].cx, d.dots[0].cy], [8.5, 11]);
  assert.deepEqual(d.outline, { x: 0, y: 0, w: 237, h: 132 });
  const middle = d.panels.find((p) => p.pos === "r1c1");
  assert.deepEqual([middle.x, middle.y, middle.w, middle.h], [81, 46, 75, 40]);
  assert.equal(d.dots[0].r, FIN_DOT_R_IN);
  const boxed = layout(c, { width: 474, height: 400 });
  assert.deepEqual(boxed.outline, { x: 0, y: 68, w: 474, h: 264 });
  assert.equal(boxed.strokeWidth, 2.4);
  // without the fins, everything is as it was
  const plain = layout(canopy());
  assert.equal(plain.dots[0].r, DOT_R_IN);
  assert.equal(DOT_R_IN, 3.6);
  assert.deepEqual([plain.apron, plain.fins], [0, []]);
  assert.deepEqual(plain.outline, { x: 0, y: 0, w: 225, h: 120 });
});

test("fins are strokes along their axis, turned with the view", () => {
  const ends = (d) => [d.fins[0].x1, d.fins[0].y1, d.fins[0].x2, d.fins[0].y2];
  const one = (view, apron, f = fin(10, 20)) => layout(withFins(canopy(view), [f], { apron }));
  assert.deepEqual(ends(one({}, 0)), [7.5, 20, 12.5, 20]);
  assert.deepEqual(ends(one({}, 6)), [13.5, 26, 18.5, 26]);
  assert.deepEqual(ends(one({ mirror_x: true }, 6)), [223.5, 26, 218.5, 26]);
  const turned = one({ rotate_deg: 90 }, 6);
  assert.deepEqual([turned.width, turned.height], [132, 237]);
  assert.deepEqual(ends(turned), [106, 13.5, 106, 18.5]);
  // 45° runs down and to the right (y down); mirrored, down and to the left
  const diag = ends(one({}, 0, fin(10, 20, 45)));
  [8.232, 18.232, 11.768, 21.768].forEach((v, i) => assert.ok(close(diag[i], v), `${diag}`));
  const back = ends(one({ mirror_x: true }, 6, fin(10, 20, 45)));
  [222.768, 24.232, 219.232, 27.768].forEach((v, i) => assert.ok(close(back[i], v), `${back}`));
  // every one of the drawing's fins stays inside the drawing (the canopy and its apron), in every view
  for (const view of [{}, { mirror_x: true }, { rotate_deg: 90 }, { rotate_deg: 270, mirror_x: true }]) {
    const d = layout(withFins(canopy(view), DRAWN));
    for (const f of d.fins) {
      for (const [x, y] of [[f.x1, f.y1], [f.x2, f.y2]]) {
        assert.ok(x >= 0 && x <= d.width && y >= 0 && y <= d.height, JSON.stringify({ view, f }));
      }
    }
  }
});

test("each fin weighs its four nearest heads, once per geometry", () => {
  const c = withFins(canopy(), [fin(2.5, 5, 0, 24.5), fin(7.5, 5, 0, 14), ...DRAWN]);
  const n = finNeighbours(c);
  assert.equal(n.near[0].length, 4);
  assert.deepEqual(n.near[0][0], { i: 0, w: 1 });
  for (const row of n.near) for (let j = 1; j < row.length; j += 1) assert.ok(row[j].w <= row[j - 1].w);
  assert.equal(n.near[1][0].i, 0); // 5 in from heads 0 and 1: the tie goes to the lower index
  assert.equal(n.near[1][1].i, 1);
  assert.equal(n.near[1][0].w, n.near[1][1].w);
  assert.ok(close(n.near[1][0].w, Math.exp(-((5 / 6) ** 2)), 1e-12)); // exp(−(d / 6)²)
  assert.equal(n.alpha[0], 1); // the deepest drop
  assert.equal(n.alpha[1], 0.45); // the shallowest
  assert.equal(finNeighbours(c), finNeighbours(c));
  assert.equal(finNeighbours(structuredClone(c)), finNeighbours(c)); // a new poll of the same geometry
  assert.equal(layout(c, { width: 100, height: 60 }).fins[3].near, finNeighbours(c).near[3]);
  assert.equal(layout(withFins(canopy({ mirror_x: true }), c.fins)).fins[3].near, finNeighbours(c).near[3]);
  // one drop everywhere: every fin at full opacity
  assert.deepEqual(finNeighbours(withFins(canopy(), [fin(2.5, 5), fin(50, 50)])).alpha, [1, 1]);
});

test("finPaint mixes lit heads at full strength and claims nothing it can't vouch for", () => {
  const two = { alpha: 1, near: [{ i: 0, w: 1 }, { i: 1, w: 1 }] };
  const lit = (fill, extra = {}) => ({ kind: "lit", fill, pulse: false, ...extra });
  const unknown = { kind: "unknown", fill: null, pulse: false };
  const unlit = { kind: "unlit", fill: UNLIT, pulse: false };
  assert.deepEqual(finPaint(two, [lit("#FF0000"), lit("#0000FF")], { opacity: 0.805 }), {
    kind: "lit",
    stroke: "#800080",
    opacity: 0.805,
    pulse: false,
  });
  const none = { kind: "unknown", stroke: null, opacity: 1, pulse: false };
  assert.deepEqual(finPaint(two, [lit("#FF0000"), unknown]), none);
  assert.deepEqual(finPaint(two, [lit("#FF0000")]), none); // a missing paint is unknown too
  const weighed = (a, b) => ({ alpha: 1, near: [{ i: 0, w: a }, { i: 1, w: b }] });
  const most = finPaint(weighed(0.95, 0.05), [lit("#FF0000"), unknown], { opacity: 0.805 });
  assert.deepEqual([most.kind, most.stroke, most.opacity], ["lit", "#FF0000", 0.785]);
  assert.equal(finPaint(weighed(0.9, 0.1), [lit("#FF0000"), unknown]).kind, "unknown"); // exactly 10%
  assert.deepEqual(finPaint({ ...two, alpha: 0.45 }, [unlit, unlit]), {
    kind: "unlit",
    stroke: "#D3D2D8",
    opacity: 0.225,
    pulse: false,
  });
  const half = finPaint(two, [lit("#FF0000"), unlit]);
  assert.deepEqual([half.kind, half.stroke, half.opacity], ["lit", "#FF0000", 1]);
  const changing = { kind: "changing", fill: "#00FF00", pulse: true };
  const faint = { alpha: 1, near: [{ i: 0, w: 0.25 }] };
  assert.deepEqual(finPaint(faint, [changing]), { kind: "changing", stroke: "#00FF00", opacity: 0.5, pulse: true });
  assert.equal(finPaint(faint, [changing], { reduced: true }).pulse, false);
  assert.equal(finPaint(two, [changing, lit("#00FF00")]).kind, "changing"); // a tie isn't confirmed yet
  assert.equal(finPaint(weighed(0.4, 0.6), [changing, lit("#00FF00")]).kind, "lit");
  // a per-head opacity (a panel's own level) passes through as data
  assert.equal(finPaint(two, [lit("#FF0000", { opacity: 1 }), lit("#FF0000", { opacity: 0.5 })]).opacity, 0.75);
  const u = finPaint(two, [unknown, unknown]);
  const g = finPaint(two, [unlit, unlit]);
  assert.notEqual(u.kind, g.kind);
  assert.notEqual(u.stroke, g.stroke);
  assert.deepEqual(finPaint({ alpha: 1, near: [] }, []), none);
});

test("kiosk.js lays out only when the geometry changes, and paints fins only when a dot changed (D69)", () => {
  const raw = readFileSync(new URL("../web/kiosk/kiosk.js", import.meta.url), "utf8");
  const js = raw.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|\s)\/\/.*$/gm, "$1");
  const slice = (from, to) => js.slice(js.indexOf(from), js.indexOf(to));
  const build = slice("function buildCanopy", "function paintCanopy");
  const order = [
    "if (canopy === canopyView.canopy) return;",
    "geometryKey(canopy)",
    "if (key === canopyView.key) return;",
    "layout(canopy)",
  ].map((needle) => build.indexOf(needle));
  assert.ok(order.every((i) => i >= 0), JSON.stringify(order));
  assert.deepEqual([...order].sort((a, b) => a - b), order);
  const paint = slice("function paintCanopy", "function tickCanopy");
  const at = (needle) => paint.indexOf(needle);
  assert.ok(at("paints[i] = p") >= 0 && at("paints[i] = p") < at("if (paint === dot.paint) return;"));
  assert.ok(at("if (dirty)") >= 0 && at("if (dirty)") < at("finPaint("));
  const tick = slice("function tickCanopy", "function stopCanopy");
  for (const part of [paint, tick]) {
    assert.ok(part.length > 0);
    assert.doesNotMatch(part, /\blayout\(/);
    assert.doesNotMatch(part, /geometryKey\(/);
  }
  // the key: the same geometry gives the same key, a fin that moves gives a new one
  const c = withFins(canopy(), [fin(10, 20), fin(30, 40, 90, 24.5)]);
  assert.equal(geometryKey(c), geometryKey(structuredClone(c)));
  const moved = structuredClone(c);
  moved.fins[1].x += 0.5;
  assert.notEqual(geometryKey(moved), geometryKey(c));
});
