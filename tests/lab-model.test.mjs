// The Design Lab's page model (web/ops/showcase/lab-model.mjs, docs/design-lab/PLAN.md §5.2). Run with
// `node --test tests/*.test.mjs`. Fixtures are shaped exactly like PLAN §4's JSON (PreviewJSON, TodayJSON,
// CatalogJSON's approved SceneDraft, StateJSON, the 422 / 409 / 501 bodies).
import test from "node:test";
import assert from "node:assert/strict";
import { CANOPY, designPaint } from "../web/ops/showcase/demo-model.mjs";
import { UNLIT as KIOSK_UNLIT } from "../web/kiosk/canopy-preview.mjs";
import * as M from "../web/ops/showcase/lab-model.mjs";

const GRID = [0, 1, 2].flatMap((r) => [0, 1, 2].map((c) => `r${r}c${c}`));
const hex = (r, g, b) => "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("").toUpperCase();
const rgb = (a) => ({ r: a[0], g: a[1], b: a[2] });
const UNLIT_RGB = rgb([0xd3, 0xd2, 0xd8]);

// A DesignJSON (routes/design.py mode_design): heads listed per module in LATTICE order, which is not the Studio's
// CANOPY wire order, so the lattice is the CANOPY order reversed within each module here, on purpose.
function design(extra = {}) {
  const heads = GRID.flatMap((pos) =>
    CANOPY.heads
      .filter((h) => h.module === pos)
      .reverse()
      .map((h) => ({ module: pos, x: h.x, y: h.y, rgb: [Math.round(h.x), Math.round(h.y), 7] })),
  );
  const modules = GRID.map((pos, i) => ({ module: pos, rgb: [20 * i, 200 - 10 * i, 99] }));
  return { mode: "golden-hour", label: "Golden Hour", ladder: ["canvas", "slot", "stepped", "modules", "strand"], design: "canvas", heads, modules, sample_at: "centre", ...extra };
}
const latticeOf = (d, pos) => d.heads.filter((h) => h.module === pos);

// TodayJSON in lit_state's module shape: r0c0 lit with 30 head colours (lattice order), r0c1 one colour, rest dark.
function today(d, extra = {}) {
  const colors = latticeOf(d, "r0c0").map((_, k) => hex(k * 8, 100, 200 - k));
  return {
    title: "What this simulator shows",
    tier: "modules",
    label: "Module level (one colour per strand)",
    feature: "Colour and dimming: each Hubspace strand set to a single colour …",
    lit: {
      level: 95,
      modules: GRID.map((pos) =>
        pos === "r0c0" ? { pos, state: "lit", color: null, colors } : pos === "r0c1" ? { pos, state: "lit", color: "#FF8FBF", colors: null } : { pos, state: "dark", color: null, colors: null },
      ),
    },
    heads: null,
    positions: { driven: ["r0c0", "r0c1"], left_out: GRID.slice(2) },
    note: "",
    ...extra,
  };
}

function preview(extra = {}) {
  const d = design();
  return {
    scene_id: "golden-hour",
    label: "Golden Hour",
    approved: false,
    design: d,
    brightness: 95,
    tiers: [
      { tier: "canvas", label: "Painted (all 270 lights)", draws: "heads", feature: "Painted…" },
      { tier: "slot", label: "Painted in the app", draws: "heads", feature: "Slot…", note: "hand-painted in the Hubspace app to match; only after it is recorded" },
      { tier: "stepped", label: "Module level, stepped", draws: "modules", feature: "Still…", moving_feature: "Moving…" },
      { tier: "modules", label: "Module level (one colour per strand)", draws: "modules", feature: "Modules…" },
      { tier: "strand", label: "Strand (one colour)", draws: "solid", solid: [255, 155, 196], feature: "Strand…" },
    ],
    today: today(d),
    show_box_today: today(d, { title: "Show box today (Modules 1–2, module level)" }),
    warnings: [],
    copy: { modules: { chosen: "approved", why: "", sentence: "…" }, stepped: null },
    changes: {},
    t1_lock: { locked: false, simulated: true, slot: "custom-3", positions: [], sentence: "In the simulator …" },
    stepped: null,
    rings: null,
    elapsed_ms: 140,
    ...extra,
  };
}

// A SceneDraft as CatalogJSON's `approved` carries it (seed_draft(...).model_dump(mode="json")).
function golden(extra = {}) {
  return {
    scene_id: "golden-hour",
    name: "Golden Hour",
    mood: "Linger in the glow",
    description: "Soft gold through peach, sunset pink, rose and lavender.",
    palette: [
      { name: "Soft gold", hex: "#FFDB9B" },
      { name: "Peach", hex: "#FFB69D" },
      { name: "Sunset pink", hex: "#FF9BC4" },
      { name: "Rose", hex: "#E779B0" },
      { name: "Lavender", hex: "#B59AE2" },
    ],
    layout: { kind: "radial", transform: "linear", invert: false, cycles: 1, centre_in: [0.0, 60.0], centres_in: null, radius_in: "auto", metric: "canopy" },
    sample_at: "centre",
    brightness: 95,
    solid: 2,
    stepped: { direction: "forward", steps_per_cycle: 6, step_s: 6.0, run_s: 90.0 },
    copy: { modules: "auto", stepped: "auto" },
    ...extra,
  };
}

// ---- the painter --------------------------------------------------------------------------------------------------------

test("viewPainter's canvas and module views are demo-model's designPaint of the server's design, head for head", () => {
  const p = preview();
  for (const [view, level] of [["canvas", "heads"], ["modules", "modules"]]) {
    const painter = M.viewPainter(p, view);
    const ref = designPaint(p.design, level);
    assert.ok(!painter.error, view);
    for (const h of CANOPY.heads) assert.deepEqual(painter.head(h), ref.head(h));
    assert.equal(painter.level, 95);
    assert.deepEqual(painter.spill(0.5), ref.spill(0.5));
  }
});

test("brightness is a level beside the colours, never multiplied in (D59)", () => {
  const dim = M.viewPainter(preview({ brightness: 10 }), "canvas");
  const full = M.viewPainter(preview({ brightness: 100 }), "canvas");
  for (const h of CANOPY.heads.slice(0, 40)) assert.deepEqual(dim.head(h), full.head(h));
  assert.equal(dim.level, 10);
});

test("viewPainter paints today's dark modules UNLIT and lit heads by their lattice index", () => {
  const p = preview();
  assert.equal(M.UNLIT, KIOSK_UNLIT);
  const painter = M.viewPainter(p, "today");
  assert.ok(!painter.error);
  assert.equal(painter.level, 95);
  const lattice = latticeOf(p.design, "r0c0");
  const colors = p.today.lit.modules[0].colors;
  for (const h of CANOPY.heads) {
    const c = painter.head(h);
    if (h.module === "r0c0") {
      const k = lattice.findIndex((x) => x.x === h.x && x.y === h.y);
      assert.ok(k >= 0);
      assert.equal(hex(c.r, c.g, c.b), colors[k], `head ${h.index} of r0c0`);
    } else if (h.module === "r0c1") assert.deepEqual(c, rgb([0xff, 0x8f, 0xbf]));
    else assert.deepEqual(c, UNLIT_RGB, `${h.module} is dark`);
  }
  // the lattice order differs from the Studio's wire order on purpose: the k-th CANOPY head is not colors[k]
  const first = CANOPY.heads.find((h) => h.module === "r0c0" && h.index === 0);
  assert.notEqual(hex(...Object.values(painter.head(first))), colors[0]);
});

test("today: an unknown or off module and a lit module with no colour for a head make no colour claim", () => {
  const p = preview();
  p.today.lit.modules[0] = { pos: "r0c0", state: "unknown", color: null, colors: null };
  p.today.lit.modules[1] = { pos: "r0c1", state: "lit", color: "not-a-colour", colors: null };
  p.today.lit.modules[2] = { pos: "r0c2", state: "off", color: null, colors: null };
  const painter = M.viewPainter(p, "today");
  for (const h of CANOPY.heads.filter((x) => ["r0c0", "r0c1", "r0c2"].includes(x.module))) assert.deepEqual(painter.head(h), UNLIT_RGB);
});

test("the show box view lights only Modules 1–2 and is captioned with its own title", () => {
  const painter = M.viewPainter(preview(), "showBox");
  const lit = new Set(CANOPY.heads.filter((h) => JSON.stringify(painter.head(h)) !== JSON.stringify(UNLIT_RGB)).map((h) => h.module));
  assert.deepEqual([...lit].sort(), ["r0c0", "r0c1"]);
  assert.equal(painter.caption, "Show box today (Modules 1–2, module level) · Module level (one colour per strand)");
});

test("viewPainter({design: null}) returns {error}, never a palette fallback", () => {
  for (const view of ["canvas", "modules"]) {
    assert.ok(M.viewPainter({ design: null }, view).error, view);
    assert.ok(M.viewPainter(preview({ design: { error: "can't be drawn" } }), view).error, view);
  }
  assert.ok(M.viewPainter(null, "canvas").error);
  assert.ok(M.viewPainter(preview({ design: design({ heads: null }) }), "canvas").error, "no heads, no painted view");
  // a design that misses heads can't paint every light: an error, not a guess
  const d = design();
  d.heads = d.heads.slice(1);
  assert.ok(M.viewPainter(preview({ design: d }), "canvas").error);
  // today with no lit picture says why (the engine isn't running here)
  const p = preview({ today: { title: "What the lights can show today", tier: null, note: "The canopy engine isn't running here." } });
  assert.equal(M.viewPainter(p, "today").error, "The canopy engine isn't running here.");
  const unlit = M.unlitPainter("why");
  assert.deepEqual(unlit.head(CANOPY.heads[0]), UNLIT_RGB);
});

test("the strand view paints every head the strand tier's solid", () => {
  const painter = M.viewPainter(preview(), "strand");
  for (const h of CANOPY.heads) assert.deepEqual(painter.head(h), rgb([255, 155, 196]));
  const p = preview();
  p.tiers = p.tiers.filter((t) => t.tier !== "strand");
  assert.ok(M.viewPainter(p, "strand").error);
});

test("availableViews: painted only when the ladder has a painted rung; stepped scenes add no tab", () => {
  assert.deepEqual(M.availableViews(preview()), ["canvas", "modules", "strand", "today", "showBox"]);
  const calm = preview({ design: design({ ladder: ["modules", "strand"], design: "sample" }), show_box_today: undefined });
  assert.deepEqual(M.availableViews(calm), ["modules", "strand", "today"]);
  const tides = preview({ design: design({ ladder: ["stepped", "modules", "strand"] }) });
  assert.ok(!M.availableViews(tides).includes("canvas"));
  assert.deepEqual(M.availableViews(null), []);
});

test("pickView keeps the stage's view when the scene has it, else the level the show tries first", () => {
  const calm = preview({ design: design({ ladder: ["modules", "strand"] }) });
  assert.equal(M.pickView(calm, "canvas"), "modules");
  assert.equal(M.pickView(preview(), "canvas"), "canvas");
  assert.equal(M.pickView(preview(), "strand"), "strand");
  assert.equal(M.pickView(preview({ design: design({ ladder: ["stepped", "modules", "strand"] }) }), "nope"), "modules");
});

test("tierCaption", () => {
  assert.equal(M.tierCaption("canvas", preview()), "Painted · all 270 lights");
  assert.equal(M.tierCaption("modules", preview()), "Module level · one colour per strand");
  assert.equal(M.tierCaption("strand", preview()), "Strand · one colour on every strand");
  assert.equal(M.tierCaption("today", preview()), "What this simulator shows · Module level (one colour per strand)");
  assert.equal(M.viewLabel("today", preview()), "What this simulator shows");
  assert.equal(M.viewLabel("today", null), "What the lights can show today");
});

// ---- colour reality -----------------------------------------------------------------------------------------------------

const APPROVED_PALETTES = {
  "lowcountry-calm": ["#F7F2E7", "#D1EAE3"],
  "rainbow-row-sunset": ["#FF9E96", "#FFD2E0", "#C4B3EC"],
  "garden-gathering": ["#8FBF9D", "#E6EED1", "#F7F2E7"],
  "coastal-tides": ["#659BD5", "#79CED4", "#B2E8D2", "#E9F3E7"],
  "golden-hour": ["#FFDB9B", "#FFB69D", "#FF9BC4", "#E779B0", "#B59AE2"],
  "goodnight-glow": ["#8B8BD1", "#BEA9E6", "#C4D6F3"],
};

test("colourHint flags HD orange and none of the six palettes", () => {
  assert.equal(M.colourHint("#F96302"), "check on the real strand");
  assert.equal(M.colourHint("#f96302"), "check on the real strand");
  assert.equal(M.colourHint("#FFD000"), M.HINT, "saturated yellow");
  assert.equal(M.colourHint("#9B30E0"), M.HINT, "saturated purple");
  for (const [scene, hexes] of Object.entries(APPROVED_PALETTES)) for (const x of hexes) assert.equal(M.colourHint(x), null, `${scene} ${x}`);
  // the nearest approved colours sit just under the saturation line
  assert.equal(Math.round(M.hsv("#FFDB9B").s * 100) / 100, 0.39); // Soft gold
  assert.equal(Math.round(M.hsv("#B59AE2").s * 100) / 100, 0.32); // Lavender
  // the rule's edges: saturation, value and the hue bands
  assert.equal(M.colourHint("#202000"), null, "too dark");
  assert.equal(M.colourHint("#3060F0"), null, "blue isn't weak");
  assert.equal(M.colourHint("#E0204A"), null, "red isn't in the band");
  assert.equal(M.colourHint("nope"), null);
});

// ---- drafts -------------------------------------------------------------------------------------------------------------

test("draftEquals ignores hex case and number formatting, fills defaults, and copy auto equals only auto", () => {
  const a = golden();
  const b = golden({ palette: a.palette.map((c) => ({ ...c, hex: c.hex.toLowerCase() })) });
  assert.ok(M.draftEquals(a, b), "an <input type=color> gives lower case");
  assert.ok(M.draftEquals(a, golden({ brightness: "95" })));
  assert.ok(M.draftEquals(a, golden({ layout: { ...a.layout, centre_in: ["0", "60"] } })));
  assert.ok(M.draftEquals(golden({ layout: { kind: "linear", angle_deg: 45 } }), golden({ layout: { kind: "linear", angle_deg: 45.0, transform: "linear", invert: false, cycles: 1 } })));
  const noCopy = golden();
  delete noCopy.copy;
  assert.ok(M.draftEquals(a, noCopy), "a missing copy is auto/auto");
  assert.ok(!M.draftEquals(a, golden({ copy: { modules: "approved", stepped: "auto" } })));
  assert.ok(!M.draftEquals(a, golden({ brightness: 94 })));
  assert.ok(!M.draftEquals(a, golden({ layout: { ...a.layout, centre_in: [0, 62.5] } })));
  assert.ok(!M.draftEquals(a, golden({ palette: [...a.palette].reverse() })));
  // normalising never touches the caller's draft
  const input = golden({ palette: [{ name: "x", hex: "#abcdef" }, ...a.palette.slice(1)] });
  M.normaliseDraft(input);
  assert.equal(input.palette[0].hex, "#abcdef");
  assert.equal(M.normaliseDraft(input).palette[0].hex, "#ABCDEF");
});

test("draftState shows in try-on over draft over approved", () => {
  const a = golden();
  assert.equal(M.draftState(a, golden(), null), "approved");
  assert.equal(M.draftState(a, golden({ brightness: 90 }), null), "draft");
  assert.equal(M.draftState(a, golden({ brightness: 90 }), { scene_id: "golden-hour" }), "in try-on");
  assert.equal(M.draftState(a, golden(), { scene_id: "golden-hour" }), "in try-on");
  assert.equal(M.draftState(a, golden({ brightness: 90 }), { scene_id: "coastal-tides" }), "draft");
});

test("changesApprovedColour names a rename or a rehex at that index", () => {
  const a = golden();
  assert.ok(!M.changesApprovedColour(a, golden(), 2));
  assert.ok(M.changesApprovedColour(a, M.setColour(a, 2, { hex: "#ff8fbf" }), 2));
  assert.ok(M.changesApprovedColour(a, M.setColour(a, 2, { name: "Hot pink" }), 2));
  assert.ok(!M.changesApprovedColour(a, M.setColour(a, 2, { hex: "#ff9bc4" }), 2), "case only");
  assert.ok(!M.changesApprovedColour(a, M.addColour(a), 5), "an added colour isn't an approved one");
});

test("editing returns new drafts; the solid follows its colour; the palette stays within 2–6", () => {
  const a = golden();
  const moved = M.moveColour(a, 2, -1);
  assert.equal(moved.palette[1].name, "Sunset pink");
  assert.equal(moved.solid, 1, "the solid moved with Sunset pink");
  assert.equal(a.palette[2].name, "Sunset pink", "the input is untouched");
  assert.equal(M.moveColour(a, 0, -1).palette[0].name, "Soft gold", "can't move past the top");
  const removed = M.removeColour(a, 0);
  assert.equal(removed.palette.length, 4);
  assert.equal(removed.solid, 1, "Sunset pink is now at 1");
  assert.equal(M.removeColour(a, 2).solid, 0, "removing the solid falls back to the first colour");
  const two = { ...a, palette: a.palette.slice(0, 2), solid: 0 };
  assert.equal(M.removeColour(two, 0).palette.length, 2, "never below two");
  const six = M.addColour(a);
  assert.equal(six.palette.length, 6);
  assert.equal(six.palette[5].hex, "#B59AE2");
  assert.ok(!/\d/.test(six.palette[5].name), "an added name has no number (kiosk copy counts in words)");
  assert.equal(M.addColour(six).palette.length, 6, "never above six");
  assert.equal(M.setColour(a, 1, { hex: "#abcdef" }).palette[1].hex, "#ABCDEF");
  assert.equal(M.setLayoutKind(a, "linear").layout.angle_deg, 45);
  assert.equal(M.setField(a, "brightness", 90).brightness, 90);
  assert.equal(a.brightness, 95);
});

// ---- layout controls ----------------------------------------------------------------------------------------------------

test("controlsFor shows angle for linear and centre for radial (centre.step === 2.5)", () => {
  const lin = M.controlsFor({ kind: "linear", angle_deg: 78 });
  assert.ok(lin.angle && !lin.centre && !lin.start && !lin.metric);
  assert.deepEqual([lin.angle.min, lin.angle.max], [0, 359]);
  const rad = M.controlsFor(golden().layout);
  assert.ok(rad.centre && !rad.angle && rad.metric && rad.radius === "auto");
  assert.equal(rad.centre.step, 2.5);
  assert.equal(rad.centre.x.step, 2.5);
  assert.equal(rad.centre.numberStep, 0.5);
  assert.deepEqual([rad.centre.x.max, rad.centre.y.max], [225, 120]);
  // every approved centre (and each module centre, edge middle and corner) is on the 2.5 grid
  for (const v of [112.5, 187.5, 60, 20, 0, 120, 225, 37.5]) assert.equal((v / rad.centre.step) % 1, 0, String(v));
  const sw = M.controlsFor({ kind: "sweep", centre_in: [0, 60], start_deg: 0 });
  assert.ok(sw.centre && sw.start && !sw.angle && !sw.metric);
  for (const c of [lin, rad, sw]) {
    assert.ok(c.transform && c.invert);
    assert.match(c.locked.cycles, /locked, D63/);
    assert.match(c.locked.sample_at, /locked, D63/);
  }
  assert.equal(M.snapCentre(113, 225), 112.5);
  assert.equal(M.snapCentre(999, 120), 120);
});

test("withLayoutKind linear defaults to 45 and sweep never sits on a module centre", () => {
  const radial = { kind: "radial", centre_in: [112.5, 60], invert: true, transform: "mirror", cycles: 1, radius_in: "auto", metric: "inches" };
  const lin = M.withLayoutKind(radial, "linear");
  assert.deepEqual(lin, { kind: "linear", transform: "mirror", invert: true, cycles: 1, angle_deg: 45 });
  const sweep = M.withLayoutKind(radial, "sweep");
  assert.deepEqual(sweep.centre_in, [0, 60]);
  assert.ok(!M.onModuleCentre(sweep.centre_in));
  assert.ok(M.onModuleCentre(radial.centre_in), "112.5, 60 is r1c1's centre");
  assert.equal(sweep.invert, true);
  assert.equal(M.withLayoutKind(lin, "radial").centre_in.length, 2);
  assert.deepEqual(M.withLayoutKind(radial, "radial"), radial, "same kind: unchanged");
  assert.deepEqual(M.withLayoutKind(golden().layout, "radial"), golden().layout);
  assert.deepEqual(M.withLayoutKind({ kind: "sweep", centre_in: [0, 60] }, "radial").centre_in, [0, 60], "radial keeps the centre it had");
  assert.equal(M.MODULE_CENTRES.length, 9);
});

// ---- server answers -----------------------------------------------------------------------------------------------------

const PROBLEMS = [
  { path: "mode 'golden-hour' → techniques → modules → feature", field: "copy.modules", message: "the T3 sentence …" },
  { path: "body → draft → palette → 2 → hex", field: "palette.2.hex", message: "colours are #RRGGBB" },
  { path: "body → draft → palette → 2 → name", field: "palette.2.name", message: "a colour name can't say 'one'" },
  { path: "body → draft → layout → angle_deg", field: "layout.angle_deg", message: "angle_deg must be …" },
  { path: "x", field: "draft", message: "Something general." },
];

test("problemsByField and the editor slots", () => {
  const by = M.problemsByField(PROBLEMS);
  assert.deepEqual(by["palette.2.hex"], ["colours are #RRGGBB"]);
  assert.deepEqual(by["copy.modules"], ["the T3 sentence …"]);
  const slots = M.problemSlots(PROBLEMS);
  assert.deepEqual(slots["palette.2"], ["colours are #RRGGBB", "a colour name can't say 'one'"]);
  assert.deepEqual(slots.layout, ["angle_deg must be …"]);
  assert.deepEqual(slots.copy, ["the T3 sentence …"]);
  assert.deepEqual(slots.draft, ["Something general."]);
  assert.equal(M.fieldSlot("sample_at"), "layout");
  assert.equal(M.fieldSlot("stepped.run_s"), "stepped");
  assert.equal(M.fieldSlot("scene_id"), "draft");
  assert.equal(M.fieldSlot(undefined), "draft");
});

test("alertFor the store error, status 0 and 5xx", () => {
  const state = { store: { ok: false, error: "while parsing a block mapping", set_aside: "looks.corrupt-20261017T120000Z.yaml", read_only: false, versions: 0, unreadable: 0 }, current: null, engine: {}, trial: null };
  assert.equal(M.alertFor(state), "Saved versions couldn't be read (while parsing a block mapping); the file was set aside as looks.corrupt-20261017T120000Z.yaml. The show doesn't use this file.");
  assert.match(M.alertFor({ store: { ...state.store, read_only: true, error: "Permission denied" } }), /won't save until the file is moved by hand/);
  assert.equal(M.alertFor({ store: { ok: true, error: null, read_only: false, unreadable: 0 } }), null);
  assert.match(M.alertFor({ store: { ok: true, error: null, read_only: false, unreadable: 2 } }), /2 saved version/);
  assert.equal(M.alertFor({ name: "ApiError", status: 0, message: "Can't reach the Conductor." }), "Can't reach the Conductor — showing the last valid draft.");
  assert.match(M.alertFor({ status: 500, body: null, message: "The Conductor had a problem (HTTP 500). Try again." }), /HTTP 500/);
  assert.match(M.alertFor({ status: 503, body: { detail: "busy" } }), /HTTP 503\): busy/);
  assert.match(M.alertFor({ status: 501, body: { detail: "Not built yet: S1: seed_draft.", not_built: true } }), /^Not built yet: S1: seed_draft\. .*isn't available/);
  assert.ok(M.isNotBuilt({ status: 501, body: { not_built: true } }));
  const lock = { status: 409, body: { detail: "Golden Hour: changing this needs a slot repaint at commissioning.", field: "palette" } };
  assert.equal(M.alertFor(lock), lock.body.detail);
  assert.equal(M.alertFor({ status: 409, body: { detail: "The Lab's store couldn't be set aside: x", read_only: true } }), "The Lab's store couldn't be set aside: x");
  // a 422 shows at its fields; only the `draft`-field problems come to the alert
  assert.equal(M.alertFor({ status: 422, body: { detail: "The draft has 4 problem(s).", problems: PROBLEMS.slice(0, 4) } }), null);
  assert.equal(M.alertFor({ status: 422, body: { detail: "…", problems: PROBLEMS } }), "Something general.");
  assert.equal(M.alertFor({ name: "AbortError" }), null);
  assert.equal(M.alertFor(null), null);
});

test("nextPreviewAt: latest wins, at least 150 ms apart", () => {
  assert.equal(M.nextPreviewAt(null, 1000), 1000);
  assert.equal(M.nextPreviewAt(1000, 1050), 1150);
  assert.equal(M.nextPreviewAt(1000, 1200), 1200);
  assert.equal(M.nextPreviewAt(1000, 1100, 300), 1300);
});

test("the read-only lines: copy, brightness, layout words, warnings, stepped", () => {
  assert.equal(M.copyLine({ chosen: "approved", why: "", sentence: "x" }), "Approved wording");
  assert.equal(M.copyLine({ chosen: "generated", why: "the layout changed", sentence: "x" }), "Generated: the layout changed");
  assert.equal(M.brightnessLine(95), "Level 95% — a device setting; colours stay full strength");
  assert.equal(M.layoutWords(preview({ changes: { layout: { words_to: "column by column" } } })), "column by column");
  assert.equal(M.layoutWords(preview({ layout_words: "from one edge" })), "from one edge");
  assert.equal(M.layoutWords(preview()), "as approved");
  assert.deepEqual(M.warningLines(preview({ warnings: [{ rule: "two-d", field: "layout", message: "Reads as stripes at module level (D63)" }] })), ["Reads as stripes at module level (D63)"]);
  assert.equal(M.steppedSummary(golden().stepped), "Forward · 6 steps · 6 s a step · runs 90 s");
  assert.equal(M.steppedSummary(null), "");
});

// ---- versions -----------------------------------------------------------------------------------------------------------

const VERSIONS = [
  { id: "v0003", name: "Client Oct 17 v3 – warmer sunset", note: "warmer sunset", created_at: 1792270800.0, parent: "v0002", scenes: ["golden-hour"], needs_d59: true, stale: false, problems: [], older_config: false },
  { id: "v0002", name: "Client Oct 17 v2", note: "", created_at: 1792267200.0, parent: null, scenes: ["coastal-tides"], needs_d59: false, stale: true, problems: [{ path: "p", field: "palette", message: "m" }], older_config: true },
  { id: "v0001", unreadable: true, error: "bad field" },
];

test("version flags, the compare set and labels", () => {
  assert.deepEqual(M.versionFlags(VERSIONS[0]), ["Changes an approved colour"]);
  assert.deepEqual(M.versionFlags(VERSIONS[1]), ["Stale", "Older config"]);
  assert.deepEqual(M.versionFlags(VERSIONS[2]), ["Unreadable"]);
  assert.deepEqual(M.compareSet(VERSIONS), ["approved", "v0003", "v0002"], "Approved first, then newest first; unreadable left out");
  assert.deepEqual(M.compareSet(VERSIONS, { unsaved: true }), ["working", "approved", "v0003", "v0002"]);
  assert.equal(M.entryLabel("approved", VERSIONS), "Approved");
  assert.equal(M.entryLabel("v0003", VERSIONS), "Client Oct 17 v3 – warmer sunset");
  assert.equal(M.entryLabel("working", VERSIONS, { unsaved: true }), "Working draft (unsaved)");
  assert.equal(M.entryLabel("working", VERSIONS), "Working draft");
  assert.match(M.versionDate(1792270800.0), /^[A-Z][a-z]{2} \d{1,2}, \d{2}:\d{2}$/);
});

test("unsaved and drafted scenes, the export label and the replace question", () => {
  const approved = { "golden-hour": golden(), "coastal-tides": golden({ scene_id: "coastal-tides" }) };
  const base = JSON.parse(JSON.stringify(approved));
  const working = { ...base, "golden-hour": golden({ brightness: 90 }) };
  const order = ["golden-hour", "coastal-tides"];
  assert.deepEqual(M.unsavedScenes(working, base, order), ["golden-hour"]);
  assert.deepEqual(M.draftedScenes(working, approved, order), ["golden-hour"]);
  assert.deepEqual(M.unsavedScenes({ ...working, "golden-hour": golden({ palette: golden().palette.map((c) => ({ ...c, hex: c.hex.toLowerCase() })) }) }, base, order), []);
  assert.equal(M.exportLabel({ selected: "v0003", unsaved: false, drafted: true }), "Export");
  assert.equal(M.exportLabel({ selected: "v0003", unsaved: true, drafted: true }), "Save & export");
  assert.equal(M.exportLabel({ selected: null, unsaved: false, drafted: true }), "Save & export");
  assert.equal(M.exportLabel({ selected: null, unsaved: false, drafted: false }), null);
  assert.equal(M.replaceQuestion(["Golden Hour"]), "Replace your unsaved changes to Golden Hour?");
  assert.equal(M.replaceQuestion(["Golden Hour", "Coastal Tides", "Goodnight Glow"]), "Replace your unsaved changes to Golden Hour, Coastal Tides and Goodnight Glow?");
  assert.equal(M.replaceQuestion([]), null);
});

test("draftStoreKey and staleDraftKeys", () => {
  assert.equal(M.draftStoreKey("3f9a1c0e2b7d", "golden-hour"), "lab:3f9a1c0e2b7d:golden-hour");
  const keys = ["lab:3f9a1c0e2b7d:golden-hour", "lab:000000000000:golden-hour", "lab:000000000000:coastal-tides", "lab-ui", "studio-favorites", "lab:3f9a1c0e2b7d:coastal-tides"];
  assert.deepEqual(M.staleDraftKeys(keys, "3f9a1c0e2b7d"), ["lab:000000000000:golden-hour", "lab:000000000000:coastal-tides"]);
  assert.deepEqual(M.staleDraftKeys(keys, "000000000000"), ["lab:3f9a1c0e2b7d:golden-hour", "lab:3f9a1c0e2b7d:coastal-tides"]);
  assert.deepEqual(M.staleDraftKeys(null, "x"), []);
  assert.deepEqual(M.readStoredDraft(JSON.stringify(golden()), "golden-hour"), golden());
  assert.equal(M.readStoredDraft(JSON.stringify(golden()), "coastal-tides"), null, "another scene's draft");
  assert.equal(M.readStoredDraft("{not json", "golden-hour"), null);
  assert.equal(M.readStoredDraft(null, "golden-hour"), null);
});

test("undo stack caps at 50", () => {
  let stack = [];
  for (let i = 1; i <= 60; i += 1) stack = M.undoPush(stack, golden({ brightness: i }));
  assert.equal(stack.length, 50);
  assert.equal(stack[0].brightness, 11, "the oldest are dropped");
  const { stack: rest, state } = M.undoPop(stack);
  assert.equal(state.brightness, 60);
  assert.equal(rest.length, 49);
  assert.equal(M.undoPush(rest, golden({ brightness: 59 })).length, 49, "a repeat of the top isn't pushed");
  assert.deepEqual(M.undoPop([]), { stack: [], state: null });
  const d = golden();
  const pushed = M.undoPush([], d);
  d.brightness = 1;
  assert.equal(pushed[0].brightness, 95, "the stack keeps a copy");
});

// ---- Present ------------------------------------------------------------------------------------------------------------

const SCENE_IDS = ["lowcountry-calm", "rainbow-row-sunset", "garden-gathering", "coastal-tides", "golden-hour", "goodnight-glow"];

test("presentStep wraps scenes and the compare set", () => {
  const compare = M.compareSet(VERSIONS);
  let s = { scenes: SCENE_IDS, compare, scene: "goodnight-glow", version: "approved" };
  s = M.presentStep(s, "ArrowRight");
  assert.equal(s.scene, "lowcountry-calm", "wraps forward");
  s = M.presentStep(s, "ArrowLeft");
  assert.equal(s.scene, "goodnight-glow", "wraps back");
  s = M.presentStep(s, "ArrowDown");
  assert.equal(s.version, "v0003", "down is the next version, newest first");
  s = M.presentStep(M.presentStep(s, "ArrowDown"), "ArrowDown");
  assert.equal(s.version, "approved", "wraps to Approved");
  s = M.presentStep(s, "ArrowUp");
  assert.equal(s.version, "v0002", "up from Approved wraps to the oldest");
  assert.equal(M.presentStep(s, "left").scene, "lowcountry-calm", "a left swipe brings the next scene");
  assert.equal(M.presentStep(s, "right").scene, "golden-hour");
  assert.equal(M.presentStep(s, "up").version, "approved", "an up swipe brings the next version");
  assert.equal(M.presentStep(s, "down").version, "v0003");
  assert.equal(M.presentStep(s, "Enter"), s, "anything else changes nothing");
  assert.deepEqual(M.presentStep({ ...s, scene: "unknown" }, "ArrowRight").scene, "rainbow-row-sunset");
});

test("swipeOf needs 60 px within 600 ms, along the dominant axis", () => {
  assert.equal(M.swipeOf(-80, 10, 200), "left");
  assert.equal(M.swipeOf(80, -10, 200), "right");
  assert.equal(M.swipeOf(5, -90, 300), "up");
  assert.equal(M.swipeOf(5, 90, 300), "down");
  assert.equal(M.swipeOf(-59, 0, 200), null, "too short");
  assert.equal(M.swipeOf(-200, 0, 601), null, "too slow");
  assert.equal(M.swipeOf(-60, 0, 600), "left", "the edges count");
  assert.equal(M.swipeOf(70, 69, 100), "right");
  assert.equal(M.swipeOf(NaN, 0, 100), null);
});

test("presentDraft: the working draft, a version's draft, or approved where the version doesn't draft the scene", () => {
  const approved = { "golden-hour": golden(), "coastal-tides": golden({ scene_id: "coastal-tides" }) };
  const working = { ...approved, "golden-hour": golden({ brightness: 80 }) };
  const versionDrafts = { v0003: { "golden-hour": golden({ brightness: 70 }) } };
  const ctx = { approved, working, versionDrafts };
  assert.equal(M.presentDraft("working", "golden-hour", ctx).brightness, 80);
  assert.equal(M.presentDraft("approved", "golden-hour", ctx).brightness, 95);
  assert.equal(M.presentDraft("v0003", "golden-hour", ctx).brightness, 70);
  assert.equal(M.presentDraft("v0003", "coastal-tides", ctx).scene_id, "coastal-tides");
  assert.equal(M.presentDraft("v0009", "golden-hour", ctx), null, "not loaded yet");
  assert.equal(M.previewKey(golden()), M.previewKey(golden({ palette: golden().palette.map((c) => ({ ...c, hex: c.hex.toLowerCase() })) })));
});
