// The Now-showing picture of the canopy (web/kiosk/canopy-preview.mjs): run with `node --test tests/*.test.mjs`.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  DIM_FLOOR,
  GRID,
  UNLIT,
  ariaSummary,
  geometryKey,
  layout,
  levelOpacity,
  moduleOpacity,
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

test("a breathing run gives each lit panel its frame's level, drawn as opacity", () => {
  // D75: frames carry each panel's level alongside its colour; the colour stays full strength
  const frames = [0, 1, 2].map(() => GRID.map((pos) => (pos === "r2c2" ? null : "#79CED4")));
  const levels = [28, 32, 24].map((lv) => GRID.map((pos) => (pos === "r2c2" ? null : lv)));
  const motion = { kind: "steps", t0: 500, step_s: 4, frames, levels, ends_at: 512 };
  const own = [
    { pos: "r0c0", state: "unknown", color: null, colors: null },
    { pos: "r0c1", state: "dark", color: null, colors: null },
    { pos: "r0c2", state: "off", color: null, colors: null },
  ];
  const lit = litOf([...own, ...flat("lit", "#ABCDEF").slice(3)], { level: 28 });
  const at = (now) => motionColors(motion, null, lit, now);
  assert.deepEqual(at(505).slice(0, 3), own); // off, dark and unknown panels are as they were: no level
  assert.deepEqual(at(505)[3], { pos: "r1c0", state: "lit", color: "#79CED4", colors: null, level: 32 });
  assert.equal(at(509).find((m) => m.pos === "r1c1").level, 24);
  assert.deepEqual(at(505)[8], { pos: "r2c2", state: "dark", color: null, colors: null }); // null frame: dark
  // no levels in the motion: no level key (travel, an older server)
  const travel = { ...motion, levels: undefined };
  assert.equal("level" in motionColors(travel, null, lit, 505)[3], false);
  // the opacity: the panel's own level when it has one, else the canopy's
  assert.equal(moduleOpacity({ level: 24 }, { level: 28 }, 0.35), levelOpacity(24, 0.35));
  assert.equal(moduleOpacity({ pos: "r0c0" }, { level: 28 }, 0.35), levelOpacity(28, 0.35));
  assert.equal(moduleOpacity(undefined, { level: 70 }), levelOpacity(70));
  assert.equal(moduleOpacity(undefined, null), 1);
  // the page paints each dot with its own panel's opacity
  const kiosk = readFileSync(new URL("../web/kiosk/kiosk.js", import.meta.url), "utf8");
  assert.match(kiosk, /moduleOpacity\(byPos\.get\(dot\.pos\)/);
  assert.doesNotMatch(kiosk, /levelOpacity\(lit\.level/);
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
