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
  // inside the canopy, so the sweep goes all the way round: on an edge only half the palette's circle reaches a strand
  assert.deepEqual(sweep.centre_in, [75, 60]);
  assert.ok(!M.onModuleCentre(sweep.centre_in));
  const [x, y] = sweep.centre_in;
  assert.ok(x > 0 && x < M.CANOPY_IN.width && y > 0 && y < M.CANOPY_IN.height, "not on an edge or a corner");
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

// ---- review fixes (2026-10-16) ------------------------------------------------------------------------------------------

test("today painting the slots' approved design says so in its caption, its badge and its note", () => {
  const p = preview();
  const approvedHeads = { ...p, today: { ...p.today, heads: "approved", note: "The slots hold the approved painted design; …" } };
  assert.equal(
    M.tierCaption("today", approvedHeads),
    `What this simulator shows · Module level (one colour per strand) · ${M.HEADS_APPROVED_CAPTION}`,
  );
  assert.equal(M.stageState("today", approvedHeads, "draft"), "approved design");
  assert.equal(M.BADGE_TEXT[M.stageState("today", approvedHeads, "draft")], "Approved design");
  assert.equal(M.todayNote("today", approvedHeads), "The slots hold the approved painted design; …");
  // the draft's own heads: the caption and the badge are the draft's
  const draftHeads = { ...p, today: { ...p.today, heads: "draft" } };
  assert.equal(M.tierCaption("today", draftHeads), "What this simulator shows · Module level (one colour per strand)");
  assert.equal(M.stageState("today", draftHeads, "draft"), "draft");
  assert.equal(M.stageState("canvas", approvedHeads, "draft"), "draft", "the painted view draws the draft");
  assert.equal(M.todayNote("canvas", approvedHeads), "");
});

test("module positions read in the canopy's own numbering, never as ids", () => {
  assert.equal(M.moduleLabel("r0c0"), "Module 1");
  assert.equal(M.moduleLabel("r1c2"), "Module 6");
  assert.equal(M.moduleList(["r0c0", "r0c1"]), "Modules 1–2");
  assert.equal(M.moduleList(["r0c0", "r0c2", "r1c1"]), "Modules 1, 3 and 5");
  assert.equal(M.moduleList(["r2c2"]), "Module 9");
  assert.equal(M.moduleList([]), "none");
  const line = M.positionsLine({ driven: ["r0c0", "r0c1"], left_out: GRID.slice(2) });
  assert.equal(line, "Lit: Modules 1–2 · dark: Modules 3–9");
  assert.ok(!/r\dc\d/.test(line));
  assert.equal(M.positionsLine({ driven: GRID, left_out: [] }), "Lit: Modules 1–9");
});

test("a Conductor answering from another approved config is noticed", () => {
  assert.equal(M.configChanged("258e9ce39859", "c84d64205b3d"), true);
  assert.equal(M.configChanged("258e9ce39859", "258e9ce39859"), false);
  assert.equal(M.configChanged("258e9ce39859", null), false, "an answer without the header says nothing");
  assert.equal(M.configChanged(null, "c84d64205b3d"), false, "before the catalog loads");
});

test("a refused save's lines say which scene each problem is in", () => {
  const lines = M.saveProblemLines(
    [
      { path: "drafts → coastal-tides → name", field: "name", message: "String should have at least 1 character" },
      { path: "mode 'golden-hour' → description", field: "description", message: "no" },
      { path: "", field: "draft", message: "general" },
    ],
    { "golden-hour": "Golden Hour", "coastal-tides": "Coastal Tides" },
  );
  assert.deepEqual(lines, ["Coastal Tides, name: String should have at least 1 character", "Golden Hour, description: no", "draft: general"]);
});

test("the client-copy notes and the show box's tab label", () => {
  assert.deepEqual(M.copyNotes({ copy_notes: ["The description still names lavender …"] }), ["The description still names lavender …"]);
  assert.deepEqual(M.copyNotes(preview()), []);
  assert.equal(M.viewLabel("showBox", preview()), "Show box today");
});

// ---- the try-on (Slice B: PLAN §4 TrialJSON, §6.4) ------------------------------------------------------------------------

// TrialJSON exactly as PLAN §4 shapes it (the INSTALLED plan's tier; the server's own message and cost).
function trialJSON(extra = {}) {
  return {
    scene_id: "golden-hour",
    label: "Golden Hour",
    version: "v0007",
    draft_name: "Client Oct 17 v3 – warmer sunset",
    started_at: 1792270800.0,
    ends_at: 1792270950.0,
    left_s: 150.0,
    bound_s: 150.0,
    modules: ["canopy-r0c0", "canopy-r0c1"],
    positions: ["r0c0", "r0c1"],
    applies: 1,
    tier: "modules",
    message: "Trying on at module level: one colour per strand; the painted design isn't on the lights.",
    cost: "sends 2 writes now, up to ~8 over the next 10 s",
    next_apply_in_s: 8.0,
    ...extra,
  };
}
const CONFIRM =
  "Try on Client Oct 17 v3 – warmer sunset for Golden Hour at module level on 2 modules (Module 1, Module 2) for up to 150 s. The painted design isn't on the lights.";
const engineOk = { running: true, simulated: true, commissioning: false, paused: false, outage: false };

test("the trial is read from GET /trial (TrialJSON | null) and from start/stop's {trial, state}", () => {
  const t = trialJSON();
  assert.equal(M.trialFromAnswer(t), t);
  assert.equal(M.trialFromAnswer(null), null);
  assert.equal(M.trialFromAnswer({ trial: t, state: { engine: engineOk, trial: t } }), t);
  assert.equal(M.trialFromAnswer({ trial: null, state: { engine: engineOk, trial: null } }), null);
  assert.equal(M.trialFromAnswer({}), null);
  assert.equal(M.trialFromAnswer("null"), null);
});

test("a missing or stubbed trial route reads as 'not on this server', a 503 as no engine", () => {
  assert.equal(M.trialApiFromError({ status: 404, body: { detail: "Not Found" } }), "missing"); // FastAPI: no route
  assert.equal(M.trialApiFromError({ status: 405, body: { detail: "Method Not Allowed" } }), "missing");
  assert.equal(M.trialApiFromError({ status: 501, body: { detail: "Not built yet: B1: build_trial.", not_built: true } }), "missing");
  assert.equal(M.trialApiFromError({ status: 404, body: { detail: "No try-on is running." } }), "none");
  assert.equal(M.trialApiFromError({ status: 503, body: { detail: "The canopy engine isn't running." } }), "no-engine");
  assert.equal(M.trialApiFromError({ status: 0, body: null }), "unreachable");
  assert.equal(M.trialApiFromError({ status: 500, body: { detail: "boom" } }), "error");
});

test("the panel's controls show only when the trial API exists and the engine runs", () => {
  for (const api of ["loading", "missing", "no-engine", "unreachable", "error"]) {
    const a = M.trialAvailability({ api, engine: engineOk });
    assert.equal(a.controls, false, api);
    assert.equal(a.canApply, false, api);
    assert.ok(a.reason, `${api} says why`);
  }
  assert.equal(M.trialAvailability({ api: "missing" }).reason, M.TRIAL_COPY.missing);
  assert.equal(M.trialAvailability({ api: "no-engine" }).reason, M.TRIAL_COPY.noEngine);
  const stopped = M.trialAvailability({ api: "ready", engine: { ...engineOk, running: false } });
  assert.deepEqual(stopped, { controls: false, canApply: false, canStop: false, reason: M.TRIAL_COPY.stopped });
  assert.deepEqual(M.trialAvailability({ api: "ready", engine: engineOk }), { controls: true, canApply: true, canStop: false, reason: "" });
  // a pause, commissioning or an outage: the server refuses a start, so Apply is off with the reason; a running
  // try-on can still be ended
  for (const [flag, reason] of [
    ["commissioning", M.TRIAL_COPY.commissioning],
    ["paused", M.TRIAL_COPY.paused],
    ["outage", M.TRIAL_COPY.outage],
  ]) {
    const a = M.trialAvailability({ api: "ready", engine: { ...engineOk, [flag]: true }, trial: trialJSON() });
    assert.deepEqual(a, { controls: true, canApply: false, canStop: true, reason }, flag);
  }
  // a draft the server refused can't be tried on
  assert.equal(M.trialAvailability({ api: "ready", engine: engineOk, problems: 2 }).canApply, false);
  // before /state answers, the server decides
  assert.equal(M.trialAvailability({ api: "ready", engine: undefined }).canApply, true);
});

test("the scope lines are true to a module-level try-on: painted and stepped scenes say what they lose", () => {
  const painted = M.trialScopeLines({ id: "golden-hour", painted: true, stepped: true });
  assert.ok(painted.includes(M.TRIAL_COPY.painted));
  assert.ok(painted.includes(M.TRIAL_COPY.stepped));
  assert.match(painted[0], /module level only: one colour per strand/);
  const plain = M.trialScopeLines({ id: "lowcountry-calm", painted: false, stepped: false });
  assert.ok(!plain.includes(M.TRIAL_COPY.painted) && !plain.includes(M.TRIAL_COPY.stepped));
  assert.match(M.TRIAL_COPY.painted, /painted design isn’t on the lights/);
});

test("the cost line: one write per module now, up to four per module over the next 10 s", () => {
  assert.equal(M.costLine(2), "sends 2 writes now, up to ~8 over the next 10 s"); // = PLAN §4's example
  assert.equal(M.costLine(9), "sends 9 writes now, up to ~36 over the next 10 s");
  assert.equal(M.costLine(1), "sends 1 write now, up to ~4 over the next 10 s");
  assert.equal(M.EARLY_REASSERT_LIMIT, 3);
  // before the first apply: the running show's driven modules, from the preview's today
  const est = M.trialEstimate(preview());
  assert.deepEqual(est, { positions: ["r0c0", "r0c1"], modules: "Modules 1–2", cost: "sends 2 writes now, up to ~8 over the next 10 s" });
  assert.equal(M.trialEstimate(preview({ today: { title: "x", tier: null, note: "The canopy engine isn't running here." } })), null);
  assert.equal(M.trialEstimate(null), null);
});

test("the time left counts the server's left_s down on this clock, never the wall-clock ends_at", () => {
  const t = trialJSON({ left_s: 150, ends_at: 1 }); // a skewed ends_at changes nothing
  assert.equal(M.trialLeft(t, 10_000, 10_000), 150);
  assert.equal(M.trialLeft(t, 10_000, 40_500), 119.5);
  assert.equal(M.trialLeft(t, 10_000, 500_000), 0);
  assert.equal(M.trialLeft(t, 10_000, 5_000), 150, "a clock that went back never adds time");
  assert.equal(M.trialLeft(null, 0, 0), null);
  assert.equal(M.formatLeft(150), "2:30 left");
  assert.equal(M.formatLeft(119.5), "2:00 left");
  assert.equal(M.formatLeft(59.2), "1:00 left");
  assert.equal(M.formatLeft(45), "45 s left");
  assert.equal(M.formatLeft(0), "ending…");
  assert.equal(M.formatLeft(null), "");
  assert.equal(M.trialBadge(t, 125), "In try-on · Client Oct 17 v3 – warmer sunset · 2:05 left");
  assert.equal(M.trialBadge(trialJSON({ draft_name: null }), 30), "In try-on · Golden Hour · 30 s left");
  assert.equal(M.trialBadge(null, 30), "");
  // "screen only" is said only where no try-on can reach the lights
  assert.equal(M.labEyebrow("missing", null), "DESIGN LAB · SCREEN ONLY");
  assert.equal(M.labEyebrow("no-engine", null), "DESIGN LAB · SCREEN ONLY");
  assert.equal(M.labEyebrow("ready", null), "DESIGN LAB · SCREEN ONLY UNTIL YOU APPLY");
  assert.equal(M.labEyebrow("ready", t), "DESIGN LAB · A TRY-ON IS ON THE LIGHTS");
});

test("the status lines are the server's: its message and the tier the engine actually installed", () => {
  const lines = M.trialStatusLines(trialJSON());
  assert.equal(lines[0], "On the lights now: Client Oct 17 v3 – warmer sunset (Golden Hour), at module level (one colour per strand).");
  assert.equal(lines[1], trialJSON().message);
  assert.equal(lines[2], "Writing to Modules 1–2.");
  assert.equal(lines.length, 3);
  const strand = M.trialStatusLines(
    trialJSON({ tier: "strand", message: "Trying on as one colour on every strand (module level couldn't be compiled: no colour named sea-glass).", applies: 3 }),
  );
  assert.match(strand[0], /as one colour on every strand\.$/);
  assert.match(strand[1], /module level couldn't be compiled/);
  assert.match(strand[3], /Applied 3 times; re-applying never extends the time limit/);
  assert.deepEqual(M.trialStatusLines(null), []);
});

test("Apply ignores a double tap, a tap while one is on its way, and a tap inside the server's gap", () => {
  assert.deepEqual(M.applyReady({ nowMs: 1000 }), { ok: true, waitS: 0, why: null });
  assert.equal(M.applyReady({ busy: true, nowMs: 1000 }).why, "busy");
  assert.equal(M.applyReady({ lastTapMs: 1000, nowMs: 1000 + M.DOUBLE_TAP_MS - 1 }).why, "double-tap");
  assert.equal(M.applyReady({ lastTapMs: 1000, nowMs: 1000 + M.DOUBLE_TAP_MS }).ok, true);
  // accepted at t = 0 with next_apply_in_s 8 (kiosk.cooldown_s): 3 s later wait 5 s; at 8 s it may send
  const gap3 = M.applyReady({ acceptedAtMs: 0, nextApplyInS: 8, nowMs: 3000 });
  assert.equal(gap3.why, "gap");
  assert.equal(gap3.waitS, 5);
  assert.equal(M.applyReady({ acceptedAtMs: 0, nextApplyInS: 8, nowMs: 8000 }).ok, true);
  assert.equal(M.applyLabel({}), "Apply to the lights");
  assert.equal(M.applyLabel({ reapply: true }), "Apply again");
  assert.equal(M.applyLabel({ waitS: 4.2, reapply: true }), "Apply again in 5 s");
  assert.equal(M.applyLabel({ busy: true }), "Sending…");
});

test("a refused start is classified from the §4 bodies: confirm, problems, engine refusal, gap, no engine, missing", () => {
  const confirm = M.startOutcome({
    status: 409,
    body: { detail: CONFIRM, confirm_required: true, confirm: CONFIRM, cost: "sends 2 writes now, up to ~8 over the next 10 s" },
  });
  assert.deepEqual(confirm, { kind: "confirm", sentence: CONFIRM, cost: "sends 2 writes now, up to ~8 over the next 10 s" });
  assert.match(confirm.sentence, /Module 1, Module 2/, "the confirm sentence names the modules it writes to");
  const gap = M.startOutcome({ status: 409, body: { detail: "Wait 5 s between try-ons.", retry_after_s: 5 } });
  assert.deepEqual(gap, { kind: "refused", text: "Wait 5 s between try-ons.", retryAfterS: 5 });
  const commissioning = M.startOutcome({ status: 409, body: { detail: "The canopy is being set up. Try again in a minute.", retry_after_s: 30 } });
  assert.equal(commissioning.kind, "refused");
  assert.equal(commissioning.retryAfterS, 30);
  const paused = M.startOutcome({ status: 409, body: { detail: M.TRIAL_COPY.paused, retry_after_s: null } });
  assert.deepEqual(paused, { kind: "refused", text: M.TRIAL_COPY.paused, retryAfterS: 0 });
  const problems = [{ path: "draft → palette → 1 → hex", field: "palette.1.hex", message: "Not a colour" }];
  assert.deepEqual(M.startOutcome({ status: 422, body: { detail: "The draft has 1 problem(s).", problems } }), {
    kind: "problems",
    problems,
    text: "The draft has 1 problem(s).",
  });
  assert.equal(M.startOutcome({ status: 503, body: { detail: "No canopy engine." } }).kind, "no-engine");
  assert.equal(M.startOutcome({ status: 404, body: { detail: "Not Found" } }).kind, "missing");
  assert.equal(M.startOutcome({ status: 501, body: { detail: "Not built yet: B1.", not_built: true } }).kind, "missing");
  assert.equal(M.startOutcome({ status: 0, body: null, message: "Can't reach" }).kind, "unreachable");
  assert.equal(M.startOutcome({ status: 500, body: { detail: "boom" } }).text, "boom");
});

test("the start body is the normalised working draft, with the confirm sentence once asked", () => {
  const d = golden({ palette: [{ name: "Soft gold", hex: "#ffdb9b" }, ...golden().palette.slice(1)] });
  const body = M.trialStartBody("golden-hour", d);
  assert.deepEqual(Object.keys(body), ["scene_id", "draft"]);
  assert.equal(body.draft.palette[0].hex, "#FFDB9B");
  assert.equal(d.palette[0].hex, "#ffdb9b", "the working draft isn't changed");
  assert.equal(M.trialStartBody("golden-hour", d, CONFIRM).confirm, CONFIRM);
});

test("after an apply, an edit says the lights still show the applied draft", () => {
  const applied = golden({ brightness: 80 });
  const t = trialJSON();
  assert.equal(M.editedSinceApply(t, "golden-hour", golden({ brightness: 80 }), applied), "");
  assert.match(M.editedSinceApply(t, "golden-hour", golden({ brightness: 70 }), applied), /until you apply again/);
  assert.equal(M.editedSinceApply(t, "coastal-tides", golden({ brightness: 70 }), applied), "", "another scene's try-on");
  assert.equal(M.editedSinceApply(null, "golden-hour", golden(), applied), "");
});

test("during a try-on the painted picture and the slots' approved 'today' say they aren't what the lights show", () => {
  const t = trialJSON();
  assert.match(M.trialStageNote("canvas", preview(), t, "golden-hour"), /at module level \(one colour per strand\), not this picture/);
  const slots = preview({ today: { ...today(design()), tier: "canvas", heads: "approved" } });
  assert.match(M.trialStageNote("today", slots, t, "golden-hour"), /not this picture/);
  assert.match(M.trialStageNote("canvas", preview(), trialJSON({ tier: "strand" }), "golden-hour"), /as one colour on every strand/);
  assert.equal(M.trialStageNote("modules", preview(), t, "golden-hour"), "");
  assert.equal(M.trialStageNote("today", preview(), t, "golden-hour"), "", "today drawing modules needs no note");
  assert.equal(M.trialStageNote("canvas", preview(), t, "coastal-tides"), "");
  assert.equal(M.trialStageNote("canvas", preview(), null, "golden-hour"), "");
  // and the badge: the trial's scene is In try-on wherever it shows
  assert.equal(M.draftState(golden(), golden(), t), "in try-on");
  assert.equal(M.stageState("today", slots, "in try-on"), "in try-on");
});

// ---- motion (Slice C, PLAN §7: PreviewJSON.stepped and .rings) --------------------------------------------------------

const STEPPED_LIVE_OFF = "stepped looks are off on live hardware until the stepped rehearsal (Gate R) passes";
// PLAN §7's stepped_preview: six steps, each one colour per position (step 0 = the hold = design.modules)
function steppedJSON(extra = {}) {
  const d = design();
  const steps = Array.from({ length: 6 }, (_, n) => ({
    index: n,
    colours: Object.fromEntries(d.modules.map((m, i) => [m.module, hex(...d.modules[(i + n) % 9].rgb)])),
  }));
  return {
    steps,
    design_step_s: 6.0,
    steps_per_cycle: 6,
    run_s: 90.0,
    direction: "forward",
    moving_feature: "Colour and dimming through the Hubspace cloud: each Hubspace strand set to a single colour and sent its next colour every few seconds, so the sunset's colours move out from one edge.",
    pace: { nine: 7.5, today: 6.0, today_modules: 2 },
    live_sentence: STEPPED_LIVE_OFF,
    ...extra,
  };
}
const RINGS = [["r0c0", "r1c0", "r2c0"], ["r0c1", "r1c1", "r2c1"], ["r0c2", "r1c2", "r2c2"]];
const CATALOG_LIMITS = { palette: [2, 6], stepped: { steps_per_cycle: [3, 12], step_s: [4, 12], run_s_max: 180 } };
const SCENE_GOLDEN = { id: "golden-hour", stepped: true, idle: false, painted: true };

test("stepTimeline: the server's pace, ⌊run_s / step⌋ steps, then the still look holds", () => {
  const st = M.steppedOf(preview({ stepped: steppedJSON() }));
  assert.ok(!st.error);
  const nine = M.stepTimeline(st, "nine");
  assert.deepEqual([nine.stepS, nine.count, nine.cycle, nine.totalS, nine.label], [7.5, 12, 6, 90, "Nine modules (7.5 s)"]);
  const today = M.stepTimeline(st, "today");
  assert.deepEqual([today.stepS, today.count, today.label], [6, 15, "Show box today (two modules, 6 s)"]);
  // step n shows steps[n mod cycle] from n × step; at the run's end the hold (step 0's colours)
  assert.deepEqual(M.stepAt(nine, 0), { n: 0, index: 0, done: false, nextAtS: 7.5 });
  assert.equal(M.stepAt(nine, 7.49).n, 0);
  assert.equal(M.stepAt(nine, 7.5).n, 1);
  assert.equal(M.stepAt(nine, 6 * 7.5).index, 0, "a new cycle starts at step 6");
  assert.equal(M.stepAt(nine, 7 * 7.5 + 1).index, 1);
  assert.deepEqual(M.stepAt(nine, 90), { n: 12, index: 0, done: true, nextAtS: null });
  assert.equal(M.stepAt(nine, 89.99).n, 11);
  // a missing pace falls back to the one the server did send; none at all is an error, never an estimate
  assert.equal(M.stepTimeline(M.steppedOf(preview({ stepped: steppedJSON({ pace: { nine: 7.5 } }) })), "today").pace, "nine");
  assert.match(M.stepTimeline(M.steppedOf(preview({ stepped: steppedJSON({ pace: {} }) })), "nine").error, /no pace/);
  // a run shorter than one step holds still (ProgramRunner.count_for < 1)
  const short = M.stepTimeline(M.steppedOf(preview({ stepped: steppedJSON({ run_s: 7.0 }) })), "nine");
  assert.match(short.error, /shorter than one 7\.5 s step/);
  assert.equal(M.stepAt(short, 3), null);
});

test("the step counter, and the moving sentence only while it plays", () => {
  const st = M.steppedOf(preview({ stepped: steppedJSON() }));
  const tl = M.stepTimeline(st, "nine");
  assert.equal(M.stepCounter(tl, null), "12 steps of 7.5 s (90 s), then the still look holds");
  assert.equal(M.stepCounter(tl, M.stepAt(tl, 16), { state: "playing" }), "Step 3 of 12 · every 7.5 s");
  assert.equal(M.stepCounter(tl, M.stepAt(tl, 16), { state: "paused", fast: true }), `Step 3 of 12 · every 7.5 s · paused · sped up ${M.FAST_FACTOR}×`);
  assert.equal(M.stepCounter(tl, M.stepAt(tl, 95), { state: "done" }), "Run over after 12 steps (90 s): the still look holds");
  assert.equal(M.movingLine(st, "playing", M.stepAt(tl, 16)), steppedJSON().moving_feature);
  for (const state of ["idle", "paused", "done"]) assert.equal(M.movingLine(st, state, M.stepAt(tl, 16)), "", state);
  assert.equal(M.movingLine(st, "playing", M.stepAt(tl, 95)), "", "the run is over: the still look's copy");
  // at the show box's pace, the partial canopy's moving copy (D62) when the server sent it
  const partial = M.steppedOf(preview({ stepped: steppedJSON({ today_moving_feature: "… each of the two Hubspace strands …" }) }));
  assert.equal(M.movingLine(partial, "playing", M.stepAt(tl, 16), "today"), "… each of the two Hubspace strands …");
  assert.equal(M.movingLine(partial, "playing", M.stepAt(tl, 16), "nine"), steppedJSON().moving_feature);
  assert.equal(M.movingLine(st, "playing", M.stepAt(tl, 16), "today"), steppedJSON().moving_feature, "no partial copy: the full one");
  assert.match(M.MOTION_COPY.fast, /faster than the lights would step — not the real pace/);
});

test("steppedOf degrades readably: no stepped (an older server), the catalog's first paint, broken steps", () => {
  assert.equal(M.steppedOf(preview()).error, M.MOTION_COPY.noSteps, "Slice B servers send stepped: null");
  assert.equal(M.steppedOf(preview({ stepped: undefined })).error, M.MOTION_COPY.noSteps);
  assert.equal(M.steppedOf({ fromCatalog: true, design: design() }).error, M.MOTION_COPY.waiting);
  assert.equal(M.steppedOf(null).error, M.MOTION_COPY.waiting);
  assert.equal(M.steppedOf(preview({ stepped: steppedJSON({ steps: [] }) })).error, M.MOTION_COPY.noSteps);
  assert.equal(M.steppedOf(preview({ stepped: steppedJSON({ steps: [{ index: 0 }] }) })).error, M.MOTION_COPY.noSteps);
  // steps arrive in index order whatever the list order
  const shuffled = steppedJSON();
  shuffled.steps = [...shuffled.steps].reverse();
  assert.deepEqual(M.steppedOf(preview({ stepped: shuffled })).steps.map((s) => s.index), [0, 1, 2, 3, 4, 5]);
  const bare = M.steppedOf(preview({ stepped: steppedJSON({ moving_feature: null, live_sentence: null }) }));
  assert.equal(bare.moving, null);
  assert.equal(M.movingLine(bare, "playing", M.stepAt(M.stepTimeline(bare, "nine"), 1)), "");
  assert.equal(M.stepTimeline(M.steppedOf(preview()), "nine").error, M.MOTION_COPY.noSteps);
  assert.equal(M.stepCounter(M.stepTimeline(M.steppedOf(preview()), "nine"), null), M.MOTION_COPY.noSteps);
});

test("viewPainter('stepped') paints the server's step colours module by module; the show box's other modules UNLIT", () => {
  const p = preview({ stepped: steppedJSON() });
  const step = p.stepped.steps[2];
  const all = M.viewPainter(p, "stepped", { step: { colours: step.colours, positions: null } });
  assert.ok(!all.error);
  assert.equal(all.level, 95, "brightness stays a level");
  for (const h of CANOPY.heads) assert.deepEqual(all.head(h), rgb([1, 3, 5].map((i) => parseInt(step.colours[h.module].slice(i, i + 2), 16))));
  assert.match(all.caption, /stepped/);
  const where = M.stepPositions(p, "today");
  assert.deepEqual(where.positions, ["r0c0", "r0c1"]);
  assert.match(where.note, /Modules 1–2 step, the rest stay dark/);
  const box = M.viewPainter(p, "stepped", { step: { colours: step.colours, positions: where.positions } });
  for (const h of CANOPY.heads) {
    if (h.module === "r0c0" || h.module === "r0c1") assert.notDeepEqual(box.head(h), UNLIT_RGB);
    else assert.deepEqual(box.head(h), UNLIT_RGB, h.module);
  }
  assert.deepEqual(M.stepPositions(p, "nine"), { positions: null, note: "" });
  assert.equal(M.stepPositions(preview({ show_box_today: null }), "today").positions, null);
  // a step with a position missing or a bad hex makes no colour claim there
  const holes = M.viewPainter(p, "stepped", { step: { colours: { ...step.colours, r2c2: "red", r1c1: undefined }, positions: null } });
  for (const h of CANOPY.heads.filter((x) => x.module === "r2c2" || x.module === "r1c1")) assert.deepEqual(holes.head(h), UNLIT_RGB);
  assert.ok(M.viewPainter(p, "stepped").error, "no step, no picture");
});

test("rippleSchedule: ring i lands at i × stagger, clamped to 0–1.2 s; screen only", () => {
  const s = M.rippleSchedule(RINGS, 0.6);
  assert.deepEqual(s.rings.map((r) => r.at), [0, 0.6, 1.2]);
  assert.equal(s.spanS, 1.2);
  assert.equal(M.rippleSchedule(RINGS, 5).stagger, M.RIPPLE_STAGGER_MAX_S);
  assert.equal(M.RIPPLE_STAGGER_MAX_S, 1.2, "RIPPLE_SPAN_MAX_S / (RIPPLE_MAX_WAVES − 1)");
  assert.equal(M.rippleSchedule(RINGS, -1).stagger, 0);
  assert.equal(M.rippleSchedule(RINGS, 0.33).stagger, 0.3);
  assert.equal(M.rippleSchedule(null, 0.6), null);
  assert.equal(M.rippleSchedule([[], []], 0.6), null);
  assert.deepEqual([...M.rippleLanded(s, 0)].sort(), RINGS[0].slice().sort());
  assert.equal(M.rippleLanded(s, 0.7).size, 6);
  assert.equal(M.rippleLanded(s, 1.2).size, 9);
  assert.equal(M.rippleLine(s), "Three rings, 0.6 s apart: the last lands 1.2 s after the first.");
  assert.match(M.rippleLine(M.rippleSchedule(RINGS, 0), 0), /0 s apart: every module lands at once, as the canopy does today/);
  assert.equal(M.rippleLine(null), M.MOTION_COPY.noRings);
  // the masked picture: modules not landed yet are UNLIT, the landed ones are the view's own colours
  const p = preview({ rings: RINGS });
  const ref = M.viewPainter(p, "modules");
  const masked = M.viewPainter(p, "modules", { landed: M.rippleLanded(s, 0.1) });
  for (const h of CANOPY.heads) assert.deepEqual(masked.head(h), RINGS[0].includes(h.module) ? ref.head(h) : UNLIT_RGB);
  assert.equal(M.rippleLabel({ ripple_stagger_s: 0 }), "Screen preview only — the canopy's ripple is off (kiosk.ripple_stagger_s: 0) until Alon signs off (D64)");
  assert.equal(M.rippleLabel(null), M.MOTION_COPY.ripple);
  assert.match(M.rippleLabel({ ripple_stagger_s: 0.4 }), /set to kiosk.ripple_stagger_s: 0.4 s \(D64\); the Lab never changes it/);
});

test("the stepped controls: SteppedTechnique's bounds, read-only with the reason where there is no stepped rung", () => {
  const c = M.steppedControls(SCENE_GOLDEN, golden(), CATALOG_LIMITS);
  assert.ok(c.editable);
  assert.equal(c.direction, "forward");
  assert.deepEqual(c.steps, { min: 3, max: 12, step: 1, value: 6 });
  assert.deepEqual(c.step_s, { min: 4, max: 12, step: 0.5, value: 6 });
  assert.deepEqual(c.run_s, { min: 6, max: 180, step: 0.5, value: 90 });
  assert.match(c.runLimit, /180 s at most/);
  const short = M.steppedControls(SCENE_GOLDEN, golden(), { stepped: { ...CATALOG_LIMITS.stepped, run_s_max: 150 } });
  assert.equal(short.run_s.max, 150, "min(180, kiosk.idle_return_s) from the catalog");
  assert.deepEqual(M.steppedControls(SCENE_GOLDEN, golden(), {}).steps, { min: 3, max: 12, step: 1, value: 6 }, "defaults when the catalog says nothing");
  assert.deepEqual(M.steppedControls({ id: "lowcountry-calm", stepped: false, idle: true }, golden({ stepped: null }), CATALOG_LIMITS), { editable: false, reason: M.MOTION_COPY.idle });
  assert.deepEqual(M.steppedControls({ id: "goodnight-glow", stepped: false, idle: false }, golden({ stepped: null }), CATALOG_LIMITS), { editable: false, reason: M.MOTION_COPY.none });
  assert.equal(M.steppedControls(SCENE_GOLDEN, golden({ stepped: null }), CATALOG_LIMITS).reason, M.MOTION_COPY.noDraft);
});

test("setStepped keeps whole steps, half-second times and a run of at least one step; the input is never changed", () => {
  const a = golden();
  const before = JSON.stringify(a);
  assert.equal(M.setStepped(a, { step_s: 6.3 }, CATALOG_LIMITS).stepped.step_s, 6.5);
  assert.equal(M.setStepped(a, { step_s: 2 }, CATALOG_LIMITS).stepped.step_s, 4);
  assert.equal(M.setStepped(a, { step_s: 20 }, CATALOG_LIMITS).stepped.step_s, 12);
  assert.equal(M.setStepped(a, { steps_per_cycle: 2 }, CATALOG_LIMITS).stepped.steps_per_cycle, 3);
  assert.equal(M.setStepped(a, { steps_per_cycle: 7.6 }, CATALOG_LIMITS).stepped.steps_per_cycle, 8);
  assert.equal(M.setStepped(a, { steps_per_cycle: 40 }, CATALOG_LIMITS).stepped.steps_per_cycle, 12);
  assert.equal(M.setStepped(a, { run_s: 500 }, CATALOG_LIMITS).stepped.run_s, 180);
  assert.equal(M.setStepped(a, { run_s: 1 }, CATALOG_LIMITS).stepped.run_s, 6, "never shorter than one step");
  const longStep = M.setStepped(M.setStepped(a, { run_s: 8 }, CATALOG_LIMITS), { step_s: 10 }, CATALOG_LIMITS);
  assert.deepEqual([longStep.stepped.step_s, longStep.stepped.run_s], [10, 10], "a longer step takes the run with it");
  assert.equal(M.setStepped(a, { direction: "reverse" }, CATALOG_LIMITS).stepped.direction, "reverse");
  assert.equal(M.setStepped(a, { direction: "sideways" }, CATALOG_LIMITS).stepped.direction, "forward");
  assert.equal(JSON.stringify(a), before);
  const still = golden({ stepped: null });
  assert.equal(M.setStepped(still, { step_s: 8 }, CATALOG_LIMITS), still, "no stepped rung, nothing to set");
  assert.ok(M.draftEquals(M.setStepped(a, { step_s: 6 }, CATALOG_LIMITS), a));
});

test("the live line, the run line and the effects card: the Lab says where each stands and changes none", () => {
  const st = M.steppedOf(preview({ stepped: steppedJSON() }));
  const live = { stepped_live_enabled: false, stepped_live_sentence: STEPPED_LIVE_OFF, ripple_stagger_s: 0, effects_enabled: false };
  const off = "Stepped looks are off on live hardware until the stepped rehearsal (Gate R) passes. Alon sets motion.stepped.live_enabled after Gate R";
  assert.equal(M.steppedLiveLine(live, M.steppedOf(preview({ stepped: steppedJSON({ live_note: "Alon sets motion.stepped.live_enabled after Gate R" }) }))), off, "the server's STEPPED_LIVE_OFF and note, word for word");
  assert.equal(M.steppedLiveLine({ ...live, stepped_live_note: "Alon sets motion.stepped.live_enabled after Gate R" }, st), off, "the catalog's note when the preview has none");
  assert.equal(M.steppedLiveLine(null), M.MOTION_COPY.live);
  assert.equal(M.steppedLiveLine({ ...live, stepped_live_enabled: true }, M.steppedOf(preview({ stepped: steppedJSON({ live_sentence: null }) }))), M.MOTION_COPY.liveOn, "a server that sent no note");
  assert.match(M.steppedLiveLine({ ...live, stepped_live_enabled: true }, st), /^Stepped looks are off on live hardware/, "the server's own live sentence wins");
  assert.equal(M.rippleLabel({ ripple_stagger_s: 0, ripple_note: "the server's note" }), "the server's note", "the server's ripple note, word for word");
  // a pace the server says holds still (ProgramRunner: past max_step_s) can't be played, and says why
  const held = M.steppedOf(preview({ stepped: steppedJSON({ runs: { nine: { still: "stepping 9 modules … longer than motion.stepped.max_step_s (12 s)" }, today: { still: null } } }) }));
  assert.equal(M.stepTimeline(held, "nine").error, "Stepping 9 modules … longer than motion.stepped.max_step_s (12 s): the still design holds instead.");
  assert.ok(!M.stepTimeline(held, "today").error);
  assert.equal(M.effectsLine(live), "Effects — not enabled: no code has passed the bench (06 #4)");
  assert.notEqual(M.effectsLine({ effects_enabled: true }), M.MOTION_COPY.effects);
  assert.equal(M.steppedRunLine(golden().stepped, st), "A 90 s run — Nine modules (7.5 s): 12 steps · Show box today (two modules, 6 s): 15 steps; then the still look holds.");
  assert.equal(M.steppedRunLine({ ...golden().stepped, run_s: 60 }, st), "", "not until the server answers for this timing");
  assert.equal(M.steppedRunLine(golden().stepped, M.steppedOf(preview())), "");
  assert.equal(M.fmtS(7.5), "7.5");
  assert.equal(M.fmtS(6.0), "6");
  // nothing in the page model offers a control for either switch: no setter names them
  for (const name of Object.keys(M)) assert.ok(!/^(set|toggle|enable).*(live|ripple)/i.test(name), name);
});

// ---- Slice C review fixes ---------------------------------------------------------------------------------------------
const STEPPED_LIVE_ON = "Stepped looks are on for live hardware (motion.stepped.live_enabled).";
const LIVE_ON_PREVIEW = { live_enabled: true, live_sentence: null, live_note: STEPPED_LIVE_ON };

test("with stepped live, the show box's pace note doesn't say stepped is off", () => {
  const on = M.stepPositions(preview({ stepped: steppedJSON(LIVE_ON_PREVIEW) }), "today");
  assert.deepEqual(on.positions, ["r0c0", "r0c1"]);
  assert.match(on.note, /Modules 1–2 step, the rest stay dark/);
  assert.doesNotMatch(on.note, /off/);
  const off = M.stepPositions(preview({ stepped: steppedJSON({ live_enabled: false }) }), "today");
  assert.match(off.note, /\(stepped stays off on the real lights\)/);
});

test("the live line is the server's words when stepped is live, with one final period", () => {
  const live = { stepped_live_enabled: true, stepped_live_note: STEPPED_LIVE_ON };
  const st = M.steppedOf(preview({ stepped: steppedJSON(LIVE_ON_PREVIEW) }));
  const line = M.steppedLiveLine(live, st);
  assert.equal(M.sentence(line), STEPPED_LIVE_ON, "the server's STEPPED_LIVE_ON, word for word");
  // before the preview answers: the catalog's note
  assert.equal(M.sentence(M.steppedLiveLine(live, null)), STEPPED_LIVE_ON);
  // the client's own copy only when the server said nothing
  assert.equal(M.sentence(M.steppedLiveLine({ stepped_live_enabled: true }, null)), `${M.MOTION_COPY.liveOn}.`);
  const offLive = { stepped_live_enabled: false, stepped_live_note: "Alon sets motion.stepped.live_enabled after Gate R" };
  for (const [l, s] of [[live, st], [live, null], [{ stepped_live_enabled: true }, null], [offLive, M.steppedOf(preview({ stepped: steppedJSON() }))], [offLive, null], [null, null]]) {
    assert.doesNotMatch(M.sentence(M.steppedLiveLine(l, s)), /\.\.$/);
    assert.match(M.sentence(M.steppedLiveLine(l, s)), /[^.]\.$/);
  }
});

test("the ripple line says 'as the canopy does today' only at the canopy's own stagger", () => {
  const zero = M.rippleSchedule(RINGS, 0);
  assert.match(M.rippleLine(zero, 0), /every module lands at once, as the canopy does today/);
  const differs = M.rippleLine(zero, 0.8);
  assert.doesNotMatch(differs, /as the canopy does today/);
  assert.match(differs, /the canopy's own ripple is 0\.8 s apart/);
  assert.doesNotMatch(M.rippleLine(zero), /canopy does today/, "no live value: no claim about the canopy");
  const same = M.rippleLine(M.rippleSchedule(RINGS, 0.8), 0.8);
  assert.match(same, /as the canopy lands a new mood today/);
  assert.doesNotMatch(M.rippleLine(M.rippleSchedule(RINGS, 0.6), 0), /today/);
});

test("the D65 export line says where the live gate stands", () => {
  assert.equal(M.steppedSectionClause({ stepped_live_clause: "live on (motion.stepped.live_enabled)" }, {}), "live on (motion.stepped.live_enabled)");
  assert.equal(M.steppedSectionClause({}, { stepped_live_enabled: true }), "live on (motion.stepped.live_enabled)");
  assert.equal(M.steppedSectionClause({}, { stepped_live_enabled: false }), "live off until Gate R");
  assert.equal(M.steppedSectionClause(null, null), "live off until Gate R");
});

test("in A/B the run ends when the longest side's run ends; each side lands on its own hold", () => {
  const long = M.stepTimeline(M.steppedOf(preview({ stepped: steppedJSON({ run_s: 90 }) })), "nine");
  const short = M.stepTimeline(M.steppedOf(preview({ stepped: steppedJSON({ run_s: 30 }) })), "nine");
  assert.deepEqual([long.totalS, short.totalS], [90, 30]);
  const at30 = M.runsAt([long, short], 30);
  assert.equal(at30.done, false, "the 90 s side is still stepping");
  assert.equal(M.stepCounter(short, M.stepAt(short, 30), { state: "playing" }), "Run over after 4 steps (30 s): the still look holds");
  const end = M.runsAt([long, short], 90);
  assert.deepEqual([end.done, end.totalS], [true, 90]);
  for (const tl of [long, short]) assert.match(M.stepCounter(tl, M.stepAt(tl, end.totalS), { state: "done" }), /^Run over after/);
  // the repaint key changes when either side steps, also on a different pace
  const slow = M.stepTimeline(M.steppedOf(preview({ stepped: steppedJSON({ pace: { nine: 9 } }) })), "nine");
  assert.notEqual(M.runsAt([long, slow], 8.9).key, M.runsAt([long, slow], 9.1).key);
  // errored sides are left out; no side at all is done at once
  assert.equal(M.runsAt([{ error: "x" }], 1).done, true);
});

test("a run that fits only the still step (step 0) can't be played: no colour would change", () => {
  const one = M.stepTimeline(M.steppedOf(preview({ stepped: steppedJSON({ run_s: 10 }) })), "nine");
  assert.match(one.error, /fits only the first step, which is the still design/);
  const two = M.stepTimeline(M.steppedOf(preview({ stepped: steppedJSON({ run_s: 15 }) })), "nine");
  assert.ok(!two.error && two.count === 2);
  // the server's own still reason gets its consequence once
  const held = M.steppedOf(preview({ stepped: steppedJSON({ runs: { nine: { still: "its run (5 s) is shorter than one 7.5 s step" }, today: { still: null } } }) }));
  assert.equal(M.stepTimeline(held, "nine").error, "Its run (5 s) is shorter than one 7.5 s step: the still design holds instead.");
  // step 1 of a run is the still look itself: the counter says so
  const tl = M.stepTimeline(M.steppedOf(preview({ stepped: steppedJSON() })), "nine");
  assert.equal(M.stepCounter(tl, M.stepAt(tl, 1), { state: "playing" }), "Step 1 of 12 (the still look) · every 7.5 s");
  assert.equal(M.stepCounter(tl, M.stepAt(tl, 16), { state: "playing" }), "Step 3 of 12 · every 7.5 s");
});

test("setStepped snaps only the field being edited: an approved 6.25 s survives a direction change", () => {
  const a = golden({ stepped: { direction: "forward", steps_per_cycle: 6, step_s: 6.25, run_s: 90.25 } });
  const turned = M.setStepped(a, { direction: "reverse" }, CATALOG_LIMITS);
  assert.deepEqual([turned.stepped.step_s, turned.stepped.run_s, turned.stepped.direction], [6.25, 90.25, "reverse"]);
  assert.equal(M.setStepped(a, { steps_per_cycle: 8 }, CATALOG_LIMITS).stepped.step_s, 6.25);
  assert.equal(M.setStepped(a, { step_s: 6.3 }, CATALOG_LIMITS).stepped.step_s, 6.5, "an edited step time snaps");
  assert.equal(M.setStepped(a, { step_s: 6.3 }, CATALOG_LIMITS).stepped.run_s, 90.25, "the run isn't edited");
  assert.equal(M.setStepped(a, { run_s: 60.2 }, CATALOG_LIMITS).stepped.run_s, 60);
  // a step time raised past the run still takes the run with it
  assert.equal(M.setStepped(golden({ stepped: { direction: "forward", steps_per_cycle: 6, step_s: 6.25, run_s: 7.25 } }), { step_s: 8 }, CATALOG_LIMITS).stepped.run_s, 8);
});

test("a stepped rung under painted rungs says it steps only where they can't run (the server's words)", () => {
  const when = "Golden Hour steps only where its higher rungs (Painted (all 270 lights), Painted in the app) can't run; where one of them runs, that look shows instead and nothing steps";
  assert.equal(M.steppedOf(preview({ stepped: steppedJSON({ steps_when: when }) })).stepsWhen, when);
  assert.equal(M.steppedOf(preview({ stepped: steppedJSON({ steps_when: null }) })).stepsWhen, null, "Coastal Tides: stepped is its first rung");
});

test("the tier list's moving line at the show box's pace names whose sentence it is", () => {
  const st = M.steppedOf(preview({ stepped: steppedJSON({ today_moving_feature: "… both Hubspace strands …" }) }));
  const tl = M.stepTimeline(st, "today");
  const at = M.stepAt(tl, 7);
  const tier = { tier: "stepped", moving_feature: "… across the canopy …" };
  assert.deepEqual(M.tierMovingLines(tier, st, "playing", at, "nine"), ["While it moves: … across the canopy …"]);
  assert.deepEqual(M.tierMovingLines(tier, st, "playing", at, "today"), [
    "While it moves on all nine modules: … across the canopy …",
    "While it moves on the show box today: … both Hubspace strands …",
  ]);
  for (const state of ["idle", "paused", "done"]) assert.deepEqual(M.tierMovingLines(tier, st, state, at, "today"), [], state);
  assert.deepEqual(M.tierMovingLines(tier, st, "playing", M.stepAt(tl, 999), "today"), [], "the run is over");
  assert.deepEqual(M.tierMovingLines({ tier: "modules" }, st, "playing", at, "today"), []);
});
