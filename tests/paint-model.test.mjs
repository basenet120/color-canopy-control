// The Paint tab's pure model (web/kiosk/paint-model.mjs): run with `node --test tests/*.test.mjs`.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { GRID, layout } from "../web/kiosk/canopy-preview.mjs";
import {
  BRUSHES,
  MODES,
  PALETTE,
  distToSegment,
  dotsInStroke,
  emptyPainting,
  fillAll,
  fillPanel,
  isBlank,
  lightSpacing,
  litCount,
  needsDarkInk,
  normalizeHex,
  paintBody,
  paintDots,
  paintPalette,
  paintRefusal,
  paintSummary,
  paintingFromLit,
  panelAt,
} from "../web/kiosk/paint-model.mjs";

// The canopy as GET /api/kiosk/panel serves it (conductor/canopy_view.py): 3 × 3 panels of 75 × 40 in, 30 heads each
// in lattice order (rows of 8 and 7, staggered by 5 in), 10 in apart.
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
  view: { mirror_x: false, rotate_deg: 0, ...view },
  modules: GRID.map((pos) => module(Number(pos[1]), Number(pos[3]))),
});

test("the palette is bold presets plus warm white; three modes and three brushes", () => {
  assert.ok(PALETTE.length >= 12);
  for (const p of PALETTE) assert.match(p.hex, /^#[0-9A-F]{6}$/);
  assert.ok(PALETTE.some((p) => p.hex === "#FFE4BC" && /warm white/i.test(p.name)));
  assert.equal(new Set(PALETTE.map((p) => p.hex)).size, PALETTE.length);
  assert.deepEqual(MODES.map((m) => m.id), ["whole", "modules", "lights"]);
  assert.deepEqual(BRUSHES.map((b) => b.id), ["small", "medium", "large"]);
  const r = BRUSHES.map((b) => b.radius);
  assert.ok(r[0] < r[1] && r[1] < r[2]);
  assert.ok(r[0] >= 0.625, "the small brush always reaches the nearest light (the lattice's covering radius)");
});

test("colours are normalised and a light swatch gets dark ink", () => {
  assert.equal(normalizeHex("#ffe4bc"), "#FFE4BC");
  assert.equal(normalizeHex(" #abc "), "#AABBCC");
  assert.equal(normalizeHex("red"), null);
  assert.equal(normalizeHex(null), null);
  assert.equal(needsDarkInk("#FFE4BC"), true);
  assert.equal(needsDarkInk("#FFE500"), true);
  assert.equal(needsDarkInk("#2962FF"), false);
  assert.equal(needsDarkInk("#FF1E1E"), false);
});

test("an empty painting has every panel's lights, all off", () => {
  const p = emptyPainting(canopy());
  assert.deepEqual(Object.keys(p), GRID);
  for (const colors of Object.values(p)) assert.deepEqual(colors, Array(30).fill(null));
  assert.equal(isBlank(p), true);
  assert.equal(litCount(p), 0);
  assert.equal(paintSummary(p), "Your painting: all 270 lights off");
});

test("the painting starts from what the canopy shows: lit and changing panels only, per light when known", () => {
  const c = canopy();
  const per = Array.from({ length: 30 }, (_, k) => (k % 2 ? "#00ff00" : "#FF0000"));
  const lit = {
    modules: [
      { pos: "r0c0", state: "lit", color: "#123456", colors: null },
      { pos: "r0c1", state: "changing", color: null, colors: per },
      { pos: "r0c2", state: "off", color: "#FFFFFF", colors: null },
      { pos: "r1c0", state: "unknown", color: "#FFFFFF", colors: null },
      { pos: "r1c1", state: "lit", color: "#ABCDEF", colors: null, headLevels: Array.from({ length: 30 }, (_, k) => (k < 10 ? 0 : 80)) },
      { pos: "r1c2", state: "lit", color: "#000000", colors: null },
      { pos: "r2c0", state: "lit", color: "nope", colors: null },
    ],
  };
  const p = paintingFromLit(c, lit);
  assert.deepEqual(p.r0c0, Array(30).fill("#123456"));
  assert.equal(p.r0c1[0], "#FF0000");
  assert.equal(p.r0c1[1], "#00FF00");
  assert.deepEqual(p.r0c2, Array(30).fill(null));
  assert.deepEqual(p.r1c0, Array(30).fill(null));
  assert.deepEqual(p.r1c1.slice(0, 10), Array(10).fill(null)); // a light the step turns off starts off
  assert.deepEqual(p.r1c1.slice(10), Array(20).fill("#ABCDEF"));
  assert.deepEqual(p.r1c2, Array(30).fill(null)); // black is off
  assert.deepEqual(p.r2c0, Array(30).fill(null));
  assert.deepEqual(p.r2c2, Array(30).fill(null)); // a panel `lit` doesn't name
  assert.deepEqual(paintingFromLit(c, null), emptyPainting(c));
});

test("distance to a segment, and to a point", () => {
  assert.equal(distToSegment(5, 3, 0, 0, 10, 0), 3);
  assert.equal(distToSegment(-4, 3, 0, 0, 10, 0), 5); // beyond the start: the distance to the end point
  assert.equal(distToSegment(3, 4, 0, 0, 0, 0), 5);
});

test("the light spacing is the lattice's, whatever the box", () => {
  const d = layout(canopy());
  const s = lightSpacing(d.dots);
  assert.ok(Math.abs(s - 10) < 1e-9, s); // 10 in along a row
  const big = layout(canopy(), { width: 900, height: 480 }); // 4×
  assert.ok(Math.abs(lightSpacing(big.dots) - 4 * s) < 1e-6);
  assert.equal(lightSpacing([]), 1);
});

test("a brush stroke paints every light within its radius of the whole path, so a fast stroke leaves no gap", () => {
  const d = layout(canopy());
  const s = lightSpacing(d.dots);
  const r = BRUSHES[0].radius * s;
  // one tap on a light paints it
  const head = d.dots[0];
  assert.ok(dotsInStroke(d.dots, { x: head.cx, y: head.cy }, { x: head.cx, y: head.cy }, r).includes(0));
  // a single long jump across the top row (no samples between) paints every light of that row it passes
  const y = d.dots[0].cy;
  const a = { x: d.dots[0].cx, y };
  const b = { x: 222.5, y };
  const hit = new Set(dotsInStroke(d.dots, a, b, r));
  const row = d.dots.map((dot, i) => ({ dot, i })).filter(({ dot }) => Math.abs(dot.cy - y) < 1e-9);
  assert.equal(row.length, 24);
  for (const { i } of row) assert.ok(hit.has(i), `light ${i} skipped`);
  // and nothing beyond the radius
  for (const i of hit) assert.ok(distToSegment(d.dots[i].cx, d.dots[i].cy, a.x, a.y, b.x, b.y) <= r + 1e-9);
  // a bigger brush paints more
  const big = dotsInStroke(d.dots, a, b, BRUSHES[2].radius * s);
  assert.ok(big.length > hit.size);
});

test("anywhere among a panel's lights, the small brush reaches at least one", () => {
  const d = layout(canopy());
  const r = BRUSHES[0].radius * lightSpacing(d.dots);
  for (let col = 0; col < 3; col += 1) {
    for (let x = col * 75 + 2.5; x <= col * 75 + 72.5; x += 1.7) {
      for (let y = 5; y <= 115; y += 1.3) {
        assert.ok(dotsInStroke(d.dots, { x, y }, { x, y }, r).length > 0, `${x},${y}`);
      }
    }
  }
});

test("a panel tap finds its panel, in any view, with a little slop outside", () => {
  const d = layout(canopy());
  assert.equal(panelAt(d.panels, 10, 10), "r0c0");
  assert.equal(panelAt(d.panels, 112, 60), "r1c1");
  assert.equal(panelAt(d.panels, 224, 119), "r2c2");
  assert.equal(panelAt(d.panels, -3, 10), null);
  assert.equal(panelAt(d.panels, -3, 10, 5), "r0c0");
  // mirrored, the top-left of the drawing is the canopy's top right
  const m = layout(canopy({ mirror_x: true }));
  assert.equal(panelAt(m.panels, 10, 10), "r0c2");
  // turned a quarter, every dot still sits in its own panel
  const t = layout(canopy({ rotate_deg: 90 }));
  for (const dot of t.dots) assert.equal(panelAt(t.panels, dot.cx, dot.cy), dot.pos);
});

test("painting dots, panels and the whole canopy reports only what changed", () => {
  const c = canopy();
  const d = layout(c);
  const p = emptyPainting(c);
  const changed = paintDots(p, d.dots, [0, 1, 1], "#FF0000");
  assert.deepEqual(changed, [0, 1]);
  assert.equal(p[d.dots[0].pos][d.dots[0].k], "#FF0000");
  assert.deepEqual(paintDots(p, d.dots, [0], "#FF0000"), []);
  const panel = fillPanel(p, d.dots, "r1c1", "#00FF00");
  assert.equal(panel.length, 30);
  assert.deepEqual(p.r1c1, Array(30).fill("#00FF00"));
  assert.equal(litCount(p), 32);
  assert.equal(paintSummary(p), "Your painting: 32 of 270 lights painted");
  assert.equal(fillAll(p, d.dots, "#0000FF").length, 270);
  assert.equal(fillAll(p, d.dots, null).length, 270); // the eraser
  assert.equal(isBlank(p), true);
});

test("the request body is one entry per panel, each as long as its heads, in lattice order", () => {
  const c = canopy();
  const d = layout(c);
  const p = emptyPainting(c);
  fillPanel(p, d.dots, "r0c0", "#ff8800");
  p.r2c2 = ["#00FF00"]; // short: padded with off
  delete p.r1c1; // missing: all off
  const body = paintBody(p, c, "modules");
  assert.equal(body.kind, "modules");
  assert.deepEqual(body.modules.map((m) => m.pos), c.modules.map((m) => m.pos));
  for (const m of body.modules) assert.equal(m.colors.length, 30);
  assert.deepEqual(body.modules[0].colors, Array(30).fill("#FF8800"));
  assert.deepEqual(body.modules.find((m) => m.pos === "r1c1").colors, Array(30).fill(null));
  assert.deepEqual(body.modules.find((m) => m.pos === "r2c2").colors, ["#00FF00", ...Array(29).fill(null)]);
  for (const kind of ["whole", "modules", "lights"]) assert.equal(paintBody(p, c, kind).kind, kind);
  assert.equal(paintBody(p, c, "sparkle").kind, "lights");
  // it round-trips as JSON exactly as conductor/looks/paint.py parse_painting reads it
  const again = JSON.parse(JSON.stringify(body));
  assert.deepEqual(again, body);
  for (const m of again.modules) for (const x of m.colors) assert.ok(x === null || /^#[0-9A-F]{6}$/.test(x));
});

test("the painting's own palette (Now showing's tint) is its distinct colours in canopy order", () => {
  const c = canopy();
  const d = layout(c);
  const p = emptyPainting(c);
  assert.deepEqual(paintPalette(p), []);
  fillPanel(p, d.dots, "r0c0", "#FF0000");
  fillPanel(p, d.dots, "r2c2", "#0000FF");
  fillPanel(p, d.dots, "r1c1", "#FF0000");
  assert.deepEqual(paintPalette(p), [{ hex: "#FF0000" }, { hex: "#0000FF" }]);
  for (const [i, pos] of GRID.entries()) fillPanel(p, d.dots, pos, `#0000${String(i).padStart(2, "0")}`);
  assert.equal(paintPalette(p, 6).length, 6);
});

test("a 422 refusal shows the server's calm detail; anything else is left to the scene toasts", () => {
  const err = (status, body) => ({ status, body });
  assert.deepEqual(paintRefusal(err(422, { detail: "Paint something first: every light is off." })), {
    text: "Paint something first: every light is off.",
    retryAfterS: 0,
  });
  assert.equal(paintRefusal(err(429, { detail: "Wait", retry_after_s: 5 })), null);
  assert.equal(paintRefusal(err(422, { detail: [{ loc: ["body"] }] })), null); // a validation dump is never shown
  assert.equal(paintRefusal(err(422, "text")), null);
  assert.equal(paintRefusal(null), null);
});

test("the paint model writes no Hubspace copy and makes no request of its own", () => {
  const js = readFileSync(new URL("../web/kiosk/paint-model.mjs", import.meta.url), "utf8").replace(/(^|\s)\/\/.*$/gm, "");
  assert.doesNotMatch(js, /['"`][^'"`\n]*Hubspace[^'"`\n]*['"`]/);
  assert.doesNotMatch(js, /\bfetch\(|document\.|window\./);
});

test("kiosk.js wires the Paint tab: a tablist, pointer capture per pointer, and one versioned paint-model import", () => {
  const html = readFileSync(new URL("../web/kiosk/index.html", import.meta.url), "utf8");
  const js = readFileSync(new URL("../web/kiosk/kiosk.js", import.meta.url), "utf8");
  const css = readFileSync(new URL("../web/kiosk/kiosk.css", import.meta.url), "utf8");
  assert.match(html, /role="tablist"/);
  assert.equal((html.match(/role="tab"/g) || []).length, 2);
  assert.match(html, /id="paint-body" role="tabpanel"/);
  const [v] = html.match(/kiosk\.js\?v=([\w.-]+)/).slice(1);
  assert.ok(js.includes(`from "/kiosk/paint-model.mjs?v=${v}"`));
  assert.match(js, /paint\.pointers\.set\(e\.pointerId/);
  assert.match(js, /post\("\/api\/kiosk\/paint", body\)/);
  assert.match(css, /\.paint-stage \{[^}]*touch-action: none/);
});
