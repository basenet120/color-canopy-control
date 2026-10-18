// The Design Lab's page model (docs/design-lab/PLAN.md §5.2). Pure: no DOM, no network, no storage. lab.js draws what
// these functions return; tests/lab-model.test.mjs checks them under `node --test`.
//
// Truth rules (CLAUDE.md "kiosk copy must be true"; a picture is copy too):
// - Every colour on the stage comes from the SERVER's answer (POST /api/lab/preview): the design the real compiler
//   made from the draft, each tier's sentence, and "today" in lit_state's module shape. `viewPainter` is the only
//   painter entry point. It paints through demo-model's `designPaint` (the Studio's own design painter) and the guest
//   panel's `paintFor`; it never touches demo-model's handoff palette or any palette maths of its own, so a Draft
//   label can never sit over Paris's handoff colours.
// - Brightness is a level (drawn as opacity by paintCanopy), never multiplied into a colour (D59).
// - A module the running tier wouldn't light is drawn UNLIT, and a colour nobody can vouch for is UNLIT too.

import { CANOPY, defaultDesignLevel, designPaint, rgbFromHex } from "./demo-model.mjs?v=show-design-3";
import { paintFor } from "../../kiosk/canopy-preview.mjs";

export const VIEWS = ["canvas", "modules", "strand", "today", "showBox"]; // the stage's tabs, in order
export const UNLIT = "#D3D2D8"; // = canopy-preview.mjs UNLIT (an unlit puck)
export const REALITY_LINE = "Colours are RGB only; per-puck white isn’t verified yet.";
export const HINT = "check on the real strand";
export const T1_LOCKED = "changing this needs a slot repaint at commissioning";
export const UNDO_LIMIT = 50;
export const PREVIEW_GAP_MS = 150;
export const SWIPE_MIN_PX = 60;
export const SWIPE_MAX_MS = 600;
export const STORE_PREFIX = "lab:";
export const APPROVED = "approved"; // the compare set's first entry (git)
export const WORKING = "working"; // the working draft (unsaved), when there is one
export const LIVE = "live"; // an A/B side: what the show runs now (the approved looks or a published version)
export const D63_LOCKS = Object.freeze({
  cycles: "Cycles: 1 (locked, D63) — one static design, no repeats; unlocking it is a D63 change for Alon.",
  sample_at: "Sample at: centre (locked, D63) — each strand shows the design at its own centre.",
});
export const CANOPY_IN = Object.freeze({ width: 225, height: 120 });
export const CENTRE_STEP = 2.5; // every approved centre, module centre, edge middle and corner is a multiple of 2.5
export const LINEAR_DEFAULT_DEG = 45; // 0° reads as stripes at module level (a D63 warning)
// Inside the canopy, so the sweep goes all the way round ("around one spot"): on an edge it covers half the circle and
// on a corner a quarter, so some palette colours would never reach a strand. Never a module centre (schema.py refuses).
export const SWEEP_DEFAULT_CENTRE = Object.freeze([75, 60]);
export const HEADS_APPROVED_CAPTION = "the approved design (the slots hold it)";
export const CONFIG_CHANGED =
  "The approved config changed (the Conductor restarted on a new config). Save your changes as a version, then reload the Lab; edits made now aren’t kept in this browser.";

const VIEW_LABELS = {
  canvas: "Painted (all 270)",
  modules: "Module level (one per strand)",
  strand: "Strand (one colour)",
  today: "Today",
  showBox: "Show box today", // the caption carries "(Modules 1–2, module level)"
};
const CAPTIONS = {
  canvas: "Painted · all 270 lights",
  modules: "Module level · one colour per strand",
  strand: "Strand · one colour on every strand",
  stepped: "Module level, stepped · one colour per strand, a step at a time",
};
const HEX6 = /^#[0-9a-f]{6}$/i;
const rgbObj = (a) => ({ r: a[0], g: a[1], b: a[2] });
const UNLIT_RGB = rgbObj(rgbFromHex(UNLIT));
const headKey = (module, x, y) => `${module}|${Math.round(2 * Number(x))},${Math.round(2 * Number(y))}`;
const isRgb = (a) => Array.isArray(a) && a.length === 3 && a.every((v) => Number.isFinite(Number(v)));

// ---- the stage ------------------------------------------------------------------------------------------------------

/** The label of a stage tab. `today` takes the server's own title ("What this simulator shows" in sim). */
export function viewLabel(view, preview) {
  if (view === "today") return (preview && preview.today && preview.today.title) || "What the lights can show today";
  return VIEW_LABELS[view] || String(view);
}

function ladderOf(preview) {
  const ladder = preview && preview.design && Array.isArray(preview.design.ladder) ? preview.design.ladder : null;
  if (ladder) return ladder;
  return ((preview && preview.tiers) || []).map((t) => t.tier);
}

/** The tabs the stage offers for this preview: painted only when the ladder has a painted rung (canvas/slot), module
 * level when it has modules, strand when it has a strand, today and the show box when the server sent them. */
export function availableViews(preview) {
  if (!preview || typeof preview !== "object") return [];
  const ladder = ladderOf(preview);
  const out = [];
  if ((ladder.includes("canvas") || ladder.includes("slot")) && Array.isArray(preview.design && preview.design.heads)) out.push("canvas");
  if (ladder.includes("modules") && Array.isArray(preview.design && preview.design.modules)) out.push("modules");
  if (ladder.includes("strand")) out.push("strand");
  if (preview.today) out.push("today");
  if (preview.show_box_today) out.push("showBox");
  return out;
}

function todayOf(view, preview) {
  return view === "today" ? preview && preview.today : view === "showBox" ? preview && preview.show_box_today : null;
}

/** The caption that always sits with a picture, so a painted design is never read as what module level shows. When
 * "today" paints the approved design (the slots hold it), the caption says so: the picture isn't the draft. */
export function tierCaption(view, preview) {
  if (CAPTIONS[view]) return CAPTIONS[view];
  const today = todayOf(view, preview);
  if (!today) return "";
  const title = today.title || viewLabel(view, preview);
  const caption = today.label ? `${title} · ${today.label}` : title;
  return today.heads === "approved" ? `${caption} · ${HEADS_APPROVED_CAPTION}` : caption;
}

/** The stage's badge for what is actually drawn: "approved design" when today's view paints the slots' approved
 * design (whatever the draft), else the draft's own state. */
export function stageState(view, preview, draftStateValue) {
  const today = todayOf(view, preview);
  if (today && today.heads === "approved" && draftStateValue !== "in try-on") return "approved design";
  return draftStateValue;
}

/** The note under a "today" picture (the server's own sentence: why it shows what it shows), else "". */
export function todayNote(view, preview) {
  const today = todayOf(view, preview);
  return (today && typeof today.note === "string" && today.note) || "";
}

/** "Module 5" for "r1c1": the canopy's own numbering (row by row, 1–9), as the canopy view labels them. */
export function moduleLabel(pos) {
  const m = /^r(\d)c(\d)$/.exec(String(pos));
  return m ? `Module ${Number(m[1]) * 3 + Number(m[2]) + 1}` : String(pos);
}

/** "Modules 1–2", "Modules 1, 3 and 5", "Module 4", "none": positions in the canopy's numbering, runs collapsed. */
export function moduleList(positions) {
  const ns = [...new Set((positions || []).map((p) => Number(moduleLabel(p).replace("Module ", ""))).filter(Number.isFinite))].sort((a, b) => a - b);
  if (!ns.length) return "none";
  const runs = [];
  for (const n of ns) {
    const last = runs[runs.length - 1];
    if (last && n === last[1] + 1) last[1] = n;
    else runs.push([n, n]);
  }
  const parts = runs.map(([a, b]) => (a === b ? `${a}` : `${a}–${b}`));
  const list = parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
  return ns.length === 1 ? `Module ${list}` : `Modules ${list}`;
}

/** The "today" view's positions as one line for the client: "Lit: Modules 1, 2 · dark: Modules 3–9". */
export function positionsLine(positions) {
  if (!positions || !Array.isArray(positions.driven)) return "";
  const lit = `Lit: ${moduleList(positions.driven)}`;
  return positions.left_out && positions.left_out.length ? `${lit} · dark: ${moduleList(positions.left_out)}` : lit;
}

/** True when an answer came from a Conductor holding another approved config than the one the page loaded. */
export function configChanged(loadedSha, answeredSha) {
  return !!(loadedSha && answeredSha && loadedSha !== answeredSha);
}

/** The view to open on: `wanted` when this preview has it, else the level the show tries first for the scene. */
export function pickView(preview, wanted) {
  const views = availableViews(preview);
  if (views.includes(wanted)) return wanted;
  const level = preview && preview.design ? defaultDesignLevel(preview.design) : null;
  const first = level === "heads" ? "canvas" : level === "modules" ? "modules" : null;
  if (first && views.includes(first)) return first;
  return views[0] || null;
}

/** head → lattice index k (the index into lit.modules[].colors), from the design's heads, which the server lists per
 * module in lattice order (routes/design.py `_heads`). */
export function latticeIndex(design) {
  const heads = design && Array.isArray(design.heads) ? design.heads : null;
  if (!heads) return null;
  const out = new Map();
  const seen = new Map();
  for (const h of heads) {
    const k = seen.get(h.module) || 0;
    seen.set(h.module, k + 1);
    out.set(headKey(h.module, h.x, h.y), k);
  }
  return out;
}

function spillOf(heads, colours, width) {
  return (p) => {
    const near = colours.filter((_, i) => Math.abs(heads[i].x / width - p) <= 1 / 6);
    const pool = near.length ? near : colours;
    if (!pool.length) return UNLIT_RGB;
    return rgbObj(["r", "g", "b"].map((k) => Math.round(pool.reduce((s, c) => s + c[k], 0) / pool.length)));
  };
}

/**
 * The ONLY painter entry point: `{head(h) → {r,g,b}, spill(p) → {r,g,b}, level, caption}` for paintCanopy, or
 * `{error}` (lab.js then paints every head UNLIT and shows the reason).
 * - canvas / modules: demo-model's `designPaint(preview.design, 'heads' | 'modules')`;
 * - strand: every head the strand tier's solid;
 * - today / showBox: `paintFor(lit.modules[pos], k)` per head (the guest panel's rule); dark, off and unknown → UNLIT;
 * - stepped (Slice C): `step` = {colours: {pos: "#RRGGBB"}, positions: [pos] | null} — one of the SERVER's compiled
 *   steps (PreviewJSON.stepped.steps[i].colours), each module its step colour through `designPaint(…, 'modules')`;
 *   a position outside `positions` (the show box's two kits) or with no colour is UNLIT;
 * - `landed` (a Set of positions: the ripple preview): any view, with the modules not landed yet UNLIT.
 */
export function viewPainter(preview, view, { heads = CANOPY.heads, width = CANOPY.width, step = null, landed = null } = {}) {
  const painter = basePainter(preview, view, { heads, width, step });
  if (painter.error || !(landed instanceof Set)) return painter;
  const colours = heads.map((h) => (landed.has(h.module) ? painter.head(h) : UNLIT_RGB));
  const byKey = new Map(heads.map((h, i) => [headKey(h.module, h.x, h.y), colours[i]]));
  return { ...painter, head: (h) => byKey.get(headKey(h.module, h.x, h.y)) || UNLIT_RGB, spill: spillOf(heads, colours, width) };
}

function steppedPainter(preview, step, heads, width) {
  if (!step || !step.colours || typeof step.colours !== "object") return { error: "There is no step to draw." };
  const lit = Array.isArray(step.positions) ? new Set(step.positions) : null;
  const modules = [...new Set(heads.map((h) => h.module))].map((pos) => {
    const hex = step.colours[pos];
    const on = HEX6.test(String(hex || "")) && (!lit || lit.has(pos));
    return { module: pos, rgb: rgbFromHex(on ? hex : UNLIT) };
  });
  const paint = designPaint({ ladder: ["stepped"], modules }, "modules", heads, width);
  if (!paint) return { error: "This step can't be drawn." };
  return { head: paint.head, spill: paint.spill, level: preview.brightness, caption: CAPTIONS.stepped };
}

function basePainter(preview, view, { heads, width, step }) {
  if (!preview || typeof preview !== "object") return { error: "Loading the preview…" };
  if (preview.error) return { error: String(preview.error) };
  const design = preview.design;
  const caption = tierCaption(view, preview);
  if (view === "stepped") return steppedPainter(preview, step, heads, width);
  if (view === "canvas" || view === "modules") {
    if (!design) return { error: "The server sent no design for this draft." };
    if (design.error) return { error: String(design.error) };
    const paint = designPaint(design, view === "canvas" ? "heads" : "modules", heads, width);
    if (!paint) {
      return {
        error:
          view === "canvas"
            ? "This scene has no painted (per-light) design to draw."
            : "This scene has no module-level design to draw.",
      };
    }
    return { head: paint.head, spill: paint.spill, level: preview.brightness, caption };
  }
  if (view === "strand") {
    const tier = (preview.tiers || []).find((t) => t.tier === "strand");
    if (!tier || !isRgb(tier.solid)) return { error: "This scene has no strand colour to draw." };
    const c = rgbObj(tier.solid.map(Number));
    return { head: () => c, spill: () => c, level: preview.brightness, caption };
  }
  if (view === "today" || view === "showBox") {
    const today = view === "today" ? preview.today : preview.show_box_today;
    if (!today) return { error: "The server didn't say what the lights can show." };
    const lit = today.lit;
    if (!lit || !Array.isArray(lit.modules)) return { error: today.note || "Nothing is lit here." };
    const byPos = new Map(lit.modules.map((m) => [m.pos, m]));
    const lattice = latticeIndex(design);
    const colours = heads.map((h) => {
      const k = lattice ? lattice.get(headKey(h.module, h.x, h.y)) : undefined;
      const p = paintFor(byPos.get(h.module), k === undefined ? -1 : k);
      return p.fill ? rgbObj(rgbFromHex(p.fill)) : UNLIT_RGB;
    });
    // keyed by module and canopy point: buildCanopy draws copies of CANOPY's heads, never the same objects
    const byKey = new Map(heads.map((h, i) => [headKey(h.module, h.x, h.y), colours[i]]));
    return {
      head: (h) => byKey.get(headKey(h.module, h.x, h.y)) || UNLIT_RGB,
      spill: spillOf(heads, colours, width),
      level: lit.level === null || lit.level === undefined ? preview.brightness : lit.level,
      caption,
    };
  }
  return { error: `There is no ${String(view)} view.` };
}

/** paintCanopy's arguments for an `{error}` or a loading stage: every head UNLIT, at a low level. */
export function unlitPainter(reason) {
  return { head: () => UNLIT_RGB, spill: () => UNLIT_RGB, level: 20, caption: reason || "" };
}

// ---- colour reality ---------------------------------------------------------------------------------------------------

/** HSV of "#RRGGBB": `{h: 0–360, s: 0–1, v: 0–1}`, or null. */
export function hsv(hex) {
  if (typeof hex !== "string" || !HEX6.test(hex)) return null;
  const [r, g, b] = rgbFromHex(hex).map((c) => c / 255);
  const max = Math.max(r, g, b);
  const d = max - Math.min(r, g, b);
  let h = 0;
  if (d) {
    if (max === r) h = 60 * (((g - b) / d) % 6);
    else if (max === g) h = 60 * ((b - r) / d + 2);
    else h = 60 * ((r - g) / d + 4);
  }
  return { h: (h + 360) % 360, s: max ? d / max : 0, v: max };
}

/** 04: saturated orange, yellow and purple render weakly on these pucks. One line, never a dialog. */
export function colourHint(hex) {
  const c = hsv(hex);
  if (!c || c.s < 0.45 || c.v < 0.4) return null;
  const orangeYellow = c.h >= 15 && c.h < 70;
  const purple = c.h >= 255 && c.h < 320;
  return orangeYellow || purple ? HINT : null;
}

// ---- drafts ---------------------------------------------------------------------------------------------------------

const LAYOUT_DEFAULTS = {
  transform: "linear",
  invert: false,
  cycles: 1,
  angle_deg: 0,
  radius_in: "auto",
  metric: "inches",
  start_deg: 0,
};

function numeric(v) {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  return null;
}

/** A draft in one canonical form: hexes upper-cased, numeric strings as numbers, missing defaults filled, null keys
 * dropped. What `draftEquals` compares, and what lab.js sends. */
export function normaliseDraft(d) {
  if (!d || typeof d !== "object") return d;
  const out = JSON.parse(JSON.stringify(d));
  out.palette = (out.palette || []).map((c) => ({ name: String(c.name ?? ""), hex: String(c.hex ?? "").toUpperCase() }));
  const layout = { ...LAYOUT_DEFAULTS, ...(out.layout || {}) };
  for (const [k, v] of Object.entries(layout)) {
    if (v === null || v === undefined) delete layout[k];
    else if (k !== "radius_in" || v !== "auto") {
      const n = numeric(v);
      if (n !== null && typeof v !== "boolean") layout[k] = n;
    }
    if (Array.isArray(v)) layout[k] = v.map((p) => (Array.isArray(p) ? p.map((n) => numeric(n) ?? n) : numeric(p) ?? p));
  }
  if (layout.kind !== "linear") delete layout.angle_deg;
  if (layout.kind !== "radial") {
    delete layout.radius_in;
    delete layout.metric;
    delete layout.centres_in;
  }
  if (layout.kind !== "sweep") delete layout.start_deg;
  out.layout = layout;
  out.sample_at = out.sample_at || "centre";
  for (const k of ["brightness", "solid"]) if (numeric(out[k]) !== null) out[k] = numeric(out[k]);
  if (out.stepped) {
    for (const k of ["steps_per_cycle", "step_s", "run_s"]) if (numeric(out.stepped[k]) !== null) out.stepped[k] = numeric(out.stepped[k]);
  } else out.stepped = null;
  out.copy = { modules: "auto", stepped: "auto", ...(out.copy || {}) };
  return out;
}

function deepEqual(a, b) {
  if (a === b) return true;
  const na = numeric(a);
  const nb = numeric(b);
  if (na !== null && nb !== null && (typeof a === "number" || typeof b === "number")) return na === nb;
  if (Array.isArray(a) || Array.isArray(b)) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => deepEqual(v, b[i]));
  }
  if (a && b && typeof a === "object" && typeof b === "object") {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    for (const k of keys) if (!deepEqual(a[k], b[k])) return false;
    return true;
  }
  return false;
}

/** Equal as drafts: hex case ignored (<input type=color> gives lower case), numbers compared numerically (45 ==
 * "45" == 45.0), defaults filled; copy "auto" equals only "auto". */
export function draftEquals(a, b) {
  if (!a || !b) return a === b;
  return deepEqual(normaliseDraft(a), normaliseDraft(b));
}

/** 'in try-on' (the engine's trial is this scene) over 'draft' (differs from approved) over 'approved'. */
export function draftState(approvedDraft, draft, trial) {
  const id = (draft && draft.scene_id) || (approvedDraft && approvedDraft.scene_id);
  if (trial && trial.scene_id && trial.scene_id === id) return "in try-on";
  if (!draft || draftEquals(approvedDraft, draft)) return "approved";
  return "draft";
}

export const BADGE_TEXT = { approved: "Approved", draft: "Draft", "in try-on": "In try-on", "approved design": "Approved design", live: "Live" };

/** True when colour `i` of `draft` renames or rehexes the approved colour at that index (D59: needs sign-off). */
export function changesApprovedColour(approvedDraft, draft, i) {
  const a = approvedDraft && approvedDraft.palette && approvedDraft.palette[i];
  const d = draft && draft.palette && draft.palette[i];
  if (!a || !d) return false;
  return a.name !== d.name || String(a.hex).toUpperCase() !== String(d.hex).toUpperCase();
}

// ---- editing (each returns a new draft; the input is never changed) ---------------------------------------------------

const clone = (d) => JSON.parse(JSON.stringify(d));
const ADDED_NAMES = ["New colour", "Extra colour", "Added colour", "Fresh colour", "Another colour", "Spare colour"];

export function setColour(draft, i, patch) {
  const d = clone(draft);
  if (!d.palette[i]) return d;
  d.palette[i] = { ...d.palette[i], ...patch };
  if (patch.hex !== undefined) d.palette[i].hex = String(patch.hex).toUpperCase();
  return d;
}

/** Move colour `i` by `delta` places; the solid follows its colour. */
export function moveColour(draft, i, delta) {
  const d = clone(draft);
  const j = i + delta;
  if (j < 0 || j >= d.palette.length || i < 0 || i >= d.palette.length) return d;
  [d.palette[i], d.palette[j]] = [d.palette[j], d.palette[i]];
  if (d.solid === i) d.solid = j;
  else if (d.solid === j) d.solid = i;
  return d;
}

/** Remove colour `i` (never below the minimum); the solid stays on its colour, or the first one if it was removed. */
export function removeColour(draft, i, min = 2) {
  const d = clone(draft);
  if (d.palette.length <= min || i < 0 || i >= d.palette.length) return d;
  d.palette.splice(i, 1);
  if (d.solid === i) d.solid = 0;
  else if (d.solid > i) d.solid -= 1;
  return d;
}

/** Add a colour (never above the maximum): a copy of the last colour's hex under an unused name with no number. */
export function addColour(draft, max = 6) {
  const d = clone(draft);
  if (d.palette.length >= max) return d;
  const taken = new Set(d.palette.map((c) => c.name.toLowerCase()));
  const name = ADDED_NAMES.find((n) => !taken.has(n.toLowerCase())) || "Spare tint";
  const last = d.palette[d.palette.length - 1];
  d.palette.push({ name, hex: last ? last.hex : "#FFFFFF" });
  return d;
}

export function setLayout(draft, patch) {
  const d = clone(draft);
  d.layout = { ...d.layout, ...patch };
  return d;
}

/** Switch the draft's layout kind (withLayoutKind's defaults). */
export function setLayoutKind(draft, kind) {
  const d = clone(draft);
  d.layout = withLayoutKind(d.layout, kind);
  return d;
}

export function setField(draft, key, value) {
  const d = clone(draft);
  d[key] = value;
  return d;
}

// ---- layout controls ---------------------------------------------------------------------------------------------------

/** Which layout controls show for this layout. linear → angle; radial → centre (step 2.5), metric; sweep → centre,
 * start angle; every kind → transform (Mirror) and Invert; cycles and sample_at are locked (D63). */
export function controlsFor(layout) {
  const kind = layout && layout.kind;
  const centre = {
    x: { min: 0, max: CANOPY_IN.width, step: CENTRE_STEP },
    y: { min: 0, max: CANOPY_IN.height, step: CENTRE_STEP },
    step: CENTRE_STEP,
    numberStep: 0.5,
  };
  return {
    kind,
    angle: kind === "linear" ? { min: 0, max: 359, step: 1 } : null,
    centre: kind === "radial" || kind === "sweep" ? centre : null,
    metric: kind === "radial",
    radius: kind === "radial" ? "auto" : null,
    start: kind === "sweep" ? { min: 0, max: 359, step: 1 } : null,
    transform: true,
    invert: true,
    locked: { cycles: D63_LOCKS.cycles, sample_at: D63_LOCKS.sample_at },
  };
}

/** The module centres (canopy inches): a sweep may not sit on one while the sample is at the centre. */
export const MODULE_CENTRES = [20, 60, 100].flatMap((y) => [37.5, 112.5, 187.5].map((x) => [x, y]));

export function onModuleCentre(point) {
  return Array.isArray(point) && MODULE_CENTRES.some(([x, y]) => Number(point[0]) === x && Number(point[1]) === y);
}

/** Switch the layout's kind, keeping invert, transform and cycles. Linear opens at 45° (0° stripes, D63); radial keeps
 * a centre it had (or the canopy's middle); sweep opens at SWEEP_DEFAULT_CENTRE (inside, never a module centre). */
export function withLayoutKind(layout, kind) {
  const base = layout || {};
  if (base.kind === kind) return { ...base };
  const keep = {
    transform: base.transform || "linear",
    invert: base.invert === true,
    cycles: base.cycles || 1,
  };
  const had = Array.isArray(base.centre_in) ? base.centre_in : Array.isArray(base.centres_in) ? base.centres_in[0] : null;
  if (kind === "linear") return { kind, ...keep, angle_deg: LINEAR_DEFAULT_DEG };
  if (kind === "radial") {
    return { kind, ...keep, centre_in: had ? [...had] : [112.5, 60], radius_in: "auto", metric: "inches" };
  }
  if (kind === "sweep") return { kind, ...keep, centre_in: [...SWEEP_DEFAULT_CENTRE], start_deg: 0 };
  return { ...base };
}

/** Snap a centre coordinate to the slider's 2.5-inch step, within the canopy. */
export function snapCentre(v, max) {
  const n = Math.min(max, Math.max(0, Number(v) || 0));
  return Math.round(n / CENTRE_STEP) * CENTRE_STEP;
}

// ---- server answers ------------------------------------------------------------------------------------------------------

/** `{field: [message]}` from a 422's problems. */
export function problemsByField(problems) {
  const out = {};
  for (const p of problems || []) {
    const field = (p && p.field) || "draft";
    (out[field] = out[field] || []).push(String((p && p.message) || "invalid"));
  }
  return out;
}

/** Where a problem shows in the editor: palette.<i> (its row), palette, layout, name, mood, description, brightness,
 * solid, copy, stepped, or draft (the #lab-alert line). */
export function fieldSlot(field) {
  const f = String(field || "draft");
  const m = /^palette\.(\d+)/.exec(f);
  if (m) return `palette.${m[1]}`;
  const head = f.split(".")[0];
  if (["palette", "layout", "name", "mood", "description", "brightness", "solid", "stepped"].includes(head)) return head;
  if (head === "sample_at") return "layout";
  if (head === "copy") return "copy";
  return "draft";
}

/** The 422's messages grouped by editor slot. */
export function problemSlots(problems) {
  const out = {};
  for (const p of problems || []) {
    const slot = fieldSlot(p && p.field);
    (out[slot] = out[slot] || []).push(String((p && p.message) || "invalid"));
  }
  return out;
}

/** When to send the next preview: now, or `minGapMs` after the last one (latest wins; lab.js aborts the older one). */
export function nextPreviewAt(lastSentMs, nowMs, minGapMs = PREVIEW_GAP_MS) {
  if (lastSentMs === null || lastSentMs === undefined || !Number.isFinite(Number(lastSentMs))) return nowMs;
  return Math.max(nowMs, Number(lastSentMs) + minGapMs);
}

/** The read-only line under each generated or approved sentence. */
export function copyLine(choice) {
  if (!choice) return "";
  if (choice.chosen === "approved") return "Approved wording";
  return choice.why ? `Generated: ${choice.why}` : "Generated";
}

export function brightnessLine(level) {
  return `Level ${Math.round(Number(level))}% — a device setting; colours stay full strength`;
}

/** The layout words for the controls: the server's own when it sends them, else the change's, else "as approved". */
export function layoutWords(preview) {
  if (!preview) return "";
  if (typeof preview.layout_words === "string") return preview.layout_words;
  const change = preview.changes && preview.changes.layout;
  if (change && typeof change.words_to === "string") return change.words_to;
  return "as approved";
}

/** The D63 warnings for the layout line (they never block the preview; they block only the export). */
export function warningLines(preview) {
  return ((preview && preview.warnings) || []).map((w) => String(w.message || w.rule || "D63 warning"));
}

/** The notes on the free client copy (a description the colours or layout moved under): never a refusal. */
export function copyNotes(preview) {
  return ((preview && preview.copy_notes) || []).map(String);
}

/** The #lab-alert lines of a refused save: each problem with the scene it belongs to (its path names it), so a
 * problem in a scene that isn't on screen still says where it is. */
export function saveProblemLines(problems, sceneNames = {}, limit = 4) {
  return (problems || []).slice(0, limit).map((p) => {
    const path = String((p && p.path) || "");
    const id = Object.keys(sceneNames).find((sid) => path.includes(sid));
    const where = id ? `${sceneNames[id]}, ` : "";
    return `${where}${(p && p.field) || "draft"}: ${(p && p.message) || "invalid"}`;
  });
}

/** A read-only summary of the stepped rung (its editor is Slice C). */
export function steppedSummary(stepped) {
  if (!stepped) return "";
  const dir = stepped.direction === "reverse" ? "Reverse" : "Forward";
  return `${dir} · ${stepped.steps_per_cycle} steps · ${Number(stepped.step_s)} s a step · runs ${Number(stepped.run_s)} s`;
}

/** The flags on a version row. */
export function versionFlags(summary) {
  const out = [];
  if (!summary) return out;
  if (summary.unreadable) out.push("Unreadable");
  if (summary.stale) out.push("Stale");
  if (summary.older_config) out.push("Older config");
  if (summary.needs_d59) out.push("Changes an approved colour");
  return out;
}

/** "Oct 17, 15:30" in the viewer's local time, from epoch seconds. */
export function versionDate(epochSeconds, locale = undefined) {
  const n = Number(epochSeconds);
  if (!Number.isFinite(n)) return "";
  const d = new Date(n * 1000);
  const date = d.toLocaleDateString(locale || "en-US", { month: "short", day: "numeric" });
  const time = d.toLocaleTimeString(locale || "en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
  return `${date}, ${time}`;
}

/** The scenes whose working draft differs from `base` (the last saved or opened state), in `order`. */
export function unsavedScenes(working, base, order) {
  return (order || Object.keys(working || {})).filter((id) => working[id] && base[id] && !draftEquals(working[id], base[id]));
}

/** The scenes whose working draft differs from approved: what a save sends (the store keeps only those). */
export function draftedScenes(working, approved, order) {
  return (order || Object.keys(working || {})).filter((id) => working[id] && approved[id] && !draftEquals(working[id], approved[id]));
}

/** "Export" while the screen is the selected version, "Save & export" when it differs (so what's exported is what's on
 * screen), and no export at all with nothing selected and nothing drafted. */
export function exportLabel({ selected, unsaved, drafted }) {
  if (unsaved || (!selected && drafted)) return "Save & export";
  return selected ? "Export" : null;
}

/** The unsaved-changes question, or null when there is nothing to lose. */
export function replaceQuestion(names) {
  if (!names || !names.length) return null;
  const list = names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  return `Replace your unsaved changes to ${list}?`;
}

// ---- alerts --------------------------------------------------------------------------------------------------------------

/** The #lab-alert sentence for a store problem (StateJSON), an API error, or null. Field problems show at their fields;
 * only `draft`/`scene_id` problems come here. */
export function alertFor(x) {
  if (!x) return null;
  if (x.store && typeof x.store === "object") {
    const s = x.store;
    if (s.read_only) {
      return `The Lab's saved versions couldn't be read or set aside (${s.error || "unknown error"}); the Lab won't save until the file is moved by hand. The show doesn't use this file.`;
    }
    if (s.error) {
      const aside = s.set_aside ? `; the file was set aside as ${s.set_aside}` : "";
      return `Saved versions couldn't be read (${s.error})${aside}. The show doesn't use this file.`;
    }
    if (s.unreadable) return `${s.unreadable} saved version(s) couldn't be read; they are kept in the file as they are.`;
    return null;
  }
  const status = Number(x.status);
  const body = x.body && typeof x.body === "object" ? x.body : null;
  const detail = (body && typeof body.detail === "string" && body.detail) || x.message || "";
  if (x.name === "AbortError") return null;
  if (status === 0 || !Number.isFinite(status)) return "Can't reach the Conductor — showing the last valid draft.";
  if (status === 501 || (body && body.not_built)) return `${detail || "Not built yet."} This part of the Design Lab isn't available on this server yet.`;
  if (status >= 500) return detail ? `The Conductor had a problem (HTTP ${status}): ${detail}` : `The Conductor had a problem (HTTP ${status}). Try again.`;
  if (status === 409 || status === 404 || status === 401 || status === 403) return detail || `Request failed (HTTP ${status}).`;
  if (status === 422) {
    const general = ((body && body.problems) || []).filter((p) => fieldSlot(p.field) === "draft");
    if (!general.length) return null;
    return general.map((p) => p.message).join(" ");
  }
  return detail || null;
}

export function isNotBuilt(err) {
  return !!err && (Number(err.status) === 501 || !!(err.body && err.body.not_built));
}

// ---- undo ----------------------------------------------------------------------------------------------------------------

/** A new stack with `state` on top (≤ 50 working states, oldest dropped; a repeat of the top isn't pushed). */
export function undoPush(stack, state, limit = UNDO_LIMIT) {
  const s = Array.isArray(stack) ? stack : [];
  if (s.length && draftEquals(s[s.length - 1], state)) return s;
  const next = [...s, clone(state)];
  return next.length > limit ? next.slice(next.length - limit) : next;
}

/** `{stack, state}`: the stack without its top, and the top (null when empty). */
export function undoPop(stack) {
  const s = Array.isArray(stack) ? stack : [];
  if (!s.length) return { stack: [], state: null };
  return { stack: s.slice(0, -1), state: s[s.length - 1] };
}

// ---- the browser's working drafts (a per-viewer convenience; the store is the record) -----------------------------------

export function draftStoreKey(configSha, sceneId) {
  return `${STORE_PREFIX}${configSha}:${sceneId}`;
}

/** The Lab's keys saved against another config (`lab:<sha>:<scene>` with a different sha): to drop. */
export function staleDraftKeys(keys, configSha) {
  return (keys || []).filter((k) => typeof k === "string" && k.startsWith(STORE_PREFIX) && !k.startsWith(`${STORE_PREFIX}${configSha}:`) && /^lab:[^:]+:[a-z]/.test(k));
}

/** The draft kept under a key, or null when it's missing, unreadable or for another scene. */
export function readStoredDraft(text, sceneId) {
  if (typeof text !== "string" || !text) return null;
  try {
    const d = JSON.parse(text);
    return d && typeof d === "object" && d.scene_id === sceneId && Array.isArray(d.palette) && d.layout ? d : null;
  } catch {
    return null;
  }
}

// ---- Present ----------------------------------------------------------------------------------------------------------

/** The compare set: [Approved, ...versions newest first] (the working draft first while it has unsaved changes). */
export function compareSet(versions, { unsaved = false } = {}) {
  const ids = (versions || []).filter((v) => v && !v.unreadable).map((v) => v.id);
  return [...(unsaved ? [WORKING] : []), APPROVED, ...ids];
}

const KEYS = {
  ArrowLeft: "prevScene",
  ArrowRight: "nextScene",
  ArrowUp: "prevVersion",
  ArrowDown: "nextVersion",
  // a swipe moves the content with the finger: left brings the next scene, up the next version
  left: "nextScene",
  right: "prevScene",
  up: "nextVersion",
  down: "prevVersion",
};

const wrap = (i, n) => ((i % n) + n) % n;

/** Present's next `{scene, version}` after a key or a swipe. `state` = {scenes: [ids], compare: [ids], scene, version};
 * both lists wrap. Anything else leaves the state as it was. */
export function presentStep(state, input) {
  const action = KEYS[input];
  if (!state || !action) return state;
  const scenes = state.scenes || [];
  const compare = state.compare || [APPROVED];
  if (action === "nextScene" || action === "prevScene") {
    if (!scenes.length) return state;
    const i = Math.max(0, scenes.indexOf(state.scene));
    return { ...state, scene: scenes[wrap(i + (action === "nextScene" ? 1 : -1), scenes.length)] };
  }
  if (!compare.length) return state;
  const j = Math.max(0, compare.indexOf(state.version));
  return { ...state, version: compare[wrap(j + (action === "nextVersion" ? 1 : -1), compare.length)] };
}

/** A touch gesture: 'left' | 'right' | 'up' | 'down' | null — at least 60 px along the dominant axis within 600 ms. */
export function swipeOf(dx, dy, dtMs) {
  const x = Number(dx);
  const y = Number(dy);
  if (!Number.isFinite(x) || !Number.isFinite(y) || !(Number(dtMs) <= SWIPE_MAX_MS) || Number(dtMs) < 0) return null;
  if (Math.abs(x) >= Math.abs(y)) {
    if (Math.abs(x) < SWIPE_MIN_PX) return null;
    return x < 0 ? "left" : "right";
  }
  if (Math.abs(y) < SWIPE_MIN_PX) return null;
  return y < 0 ? "up" : "down";
}

/** The draft Present (or an A/B side) shows for (scene, entry): the working draft, the show's live look, the version's
 * draft for the scene, or approved. */
export function presentDraft(entry, sceneId, { approved, working, versionDrafts, live = null }) {
  if (entry === WORKING) return (working && working[sceneId]) || (approved && approved[sceneId]) || null;
  if (entry === LIVE) return (live && live[sceneId]) || (approved && approved[sceneId]) || null;
  if (entry === APPROVED || !entry) return (approved && approved[sceneId]) || null;
  const drafts = versionDrafts && versionDrafts[entry];
  if (!drafts) return null; // not loaded yet
  return drafts[sceneId] || (approved && approved[sceneId]) || null;
}

/** The label of a compare-set entry ("(unsaved)" only while the working draft has unsaved changes). */
export function entryLabel(entry, versions, { unsaved = false } = {}) {
  if (entry === APPROVED) return "Approved";
  if (entry === WORKING) return unsaved ? "Working draft (unsaved)" : "Working draft";
  if (entry === LIVE) return "Live on the show";
  const v =(versions || []).find((x) => x.id === entry);
  return v ? v.name : String(entry);
}

/** A stable key for a preview cache: the scene and its normalised draft. */
export function previewKey(draft) {
  return draft ? JSON.stringify(normaliseDraft(draft)) : "";
}

// ---- the try-on (Slice B: PLAN §4 TrialJSON, §6.4) -------------------------------------------------------------------
// A try-on sends the working draft to the real lights through the engine, module level only, for a bounded time. The
// server owns every sentence that says what the lights do (TrialJSON.message, the confirm sentence, the cost, each
// refusal); these helpers only pick which one to show, count the time down, and keep a double tap from sending twice.

export const EARLY_REASSERT_LIMIT = 3; // = conductor/canopy.py: up to 3 early re-sends per module per change
export const DOUBLE_TAP_MS = 800; // a second tap on Apply this soon after the first is ignored
export const TRIAL_POLL_MS = 2000; // GET /api/lab/trial while a try-on runs (the WS "canopy" topic nudges it sooner)
export const TRIAL_IDLE_POLL_MS = 15000; // and while none does (a try-on started from another screen still shows)
export const TRIAL_COPY = Object.freeze({
  missing: "Trying a draft on the lights isn’t on this server yet; everything here is screen only.",
  noEngine: "The canopy engine isn’t running here, so nothing can be tried on the lights.",
  unreachable: "Can’t reach the Conductor, so whether a try-on is running is unknown.",
  stopped: "The canopy engine is stopped, so a try-on can’t start.",
  commissioning: "The canopy is being set up (commissioning), so a try-on can’t start until that finishes.",
  paused: "Guest input is paused by staff, so a try-on can’t start; resume guest input on /ops first.",
  outage: "The canopy is resting after a connection problem, so a try-on can’t start.",
  problems: "Fix the draft’s problems first: only a draft the server accepts can be tried on.",
  loading: "Checking whether a try-on is running…",
  moduleLevel:
    "A try-on is module level only: one colour per strand (or one colour on every strand if module level can’t be compiled).",
  painted: "The painted design isn’t on the lights during a try-on; the slots keep the approved one.",
  stepped: "A try-on never steps: the lights hold the still module-level look.",
  guests: "Guests on the iPad are asked to wait while it runs. A look chosen on /ops, a pause or a stop ends it early.",
});
const TIER_PHRASE = { modules: "at module level (one colour per strand)", strand: "as one colour on every strand" };

/** The trial in a GET /api/lab/trial answer (TrialJSON or null), or in a start/stop answer ({trial, state}). */
export function trialFromAnswer(answer) {
  if (!answer || typeof answer !== "object") return null;
  if ("trial" in answer && !("scene_id" in answer)) return trialFromAnswer(answer.trial);
  return typeof answer.scene_id === "string" && answer.scene_id ? answer : null;
}

/** What a failed GET /api/lab/trial says about the try-on API: 'missing' (the route isn't there — FastAPI's own
 * "Not Found" — or a 501 stub), 'no-engine' (503), 'unreachable' (status 0), 'none' (any other 404: the API is there
 * and no try-on runs), else 'error'. */
export function trialApiFromError(err) {
  const status = Number(err && err.status);
  const body = err && err.body && typeof err.body === "object" ? err.body : null;
  const detail = body && typeof body.detail === "string" ? body.detail : "";
  if (status === 501 || (body && body.not_built)) return "missing";
  if (status === 404 || status === 405) {
    return !detail || detail === "Not Found" || detail === "Method Not Allowed" ? "missing" : "none";
  }
  if (status === 503) return "no-engine";
  if (status === 0 || !Number.isFinite(status)) return "unreachable";
  return "error";
}

/**
 * Whether the Try-on panel offers its controls, and if not, the one readable line it shows instead.
 * `api`: 'loading' | 'ready' | 'missing' | 'no-engine' | 'unreachable' | 'error'; `engine`: StateJSON.engine.
 * → {controls, canApply, canStop, reason}. The controls show only when the API exists and the engine runs; a pause,
 * commissioning or an outage keeps them (a running try-on can still be ended) with Apply off and the reason.
 */
export function trialAvailability({ api, engine, trial = null, problems = 0, error = "" } = {}) {
  const note = (reason) => ({ controls: false, canApply: false, canStop: false, reason });
  if (api === "loading") return note(TRIAL_COPY.loading);
  if (api === "missing") return note(TRIAL_COPY.missing);
  if (api === "no-engine") return note(TRIAL_COPY.noEngine);
  if (api === "unreachable") return note(TRIAL_COPY.unreachable);
  if (api !== "ready") return note(error ? `The try-on state couldn’t be read: ${error}` : "The try-on state couldn’t be read.");
  const e = engine && typeof engine === "object" ? engine : null;
  if (e && e.running === false) return note(TRIAL_COPY.stopped);
  let reason = "";
  if (e && e.commissioning) reason = TRIAL_COPY.commissioning;
  else if (e && e.paused) reason = TRIAL_COPY.paused;
  else if (e && e.outage) reason = TRIAL_COPY.outage;
  else if (problems > 0) reason = TRIAL_COPY.problems;
  return { controls: true, canApply: !reason, canStop: !!trial, reason };
}

/** The lines under the panel's title: true to what a try-on of this scene can do, before anything is applied. */
export function trialScopeLines(scene) {
  const out = [TRIAL_COPY.moduleLevel];
  if (scene && scene.painted) out.push(TRIAL_COPY.painted);
  if (scene && scene.stepped) out.push(TRIAL_COPY.stepped);
  out.push(TRIAL_COPY.guests);
  return out;
}

/** "sends 2 writes now, up to ~8 over the next 10 s": one write per driven module, then the engine's re-assert and
 * early re-sends (EARLY_REASSERT_LIMIT) per module. The server's own `cost` wins whenever it sent one. */
export function costLine(n) {
  const k = Math.max(0, Math.round(Number(n) || 0));
  return `sends ${k} write${k === 1 ? "" : "s"} now, up to ~${k * (1 + EARLY_REASSERT_LIMIT)} over the next 10 s`;
}

/** Before the first apply: the modules the running show drives (the preview's today.positions.driven) and the cost
 * line, or null when the preview doesn't say (no engine here, or no preview yet). */
export function trialEstimate(preview) {
  const today = preview && preview.today;
  const pos = today && today.tier && today.positions;
  const driven = pos && Array.isArray(pos.driven) ? pos.driven : null;
  if (!driven || !driven.length) return null;
  return { positions: driven, modules: moduleList(driven), cost: costLine(driven.length) };
}

/** Seconds left on the try-on's bound: the server's `left_s` at `readAtMs`, counted down on this clock (never the
 * wall-clock `ends_at`, so a skewed laptop clock can't stretch or cut it). Null with no trial. */
export function trialLeft(trial, readAtMs, nowMs) {
  if (!trial) return null;
  const base = Number(trial.left_s);
  if (!Number.isFinite(base)) return null;
  const gone = (Number(nowMs) - Number(readAtMs)) / 1000;
  return Math.max(0, base - (Number.isFinite(gone) && gone > 0 ? gone : 0));
}

/** "2:05 left", "45 s left", "ending…" (at zero, until the server says it ended). */
export function formatLeft(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return "";
  const s = Math.ceil(Number(seconds));
  if (s <= 0) return "ending…";
  if (s < 60) return `${s} s left`;
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")} left`;
}

const trialName = (trial) => trial.draft_name || trial.label || trial.scene_id;

/** The Lab's eyebrow: "screen only" stays true only where no try-on can reach the lights. */
export function labEyebrow(api, trial, publishing = false) {
  if (trial) return "DESIGN LAB · A TRY-ON IS ON THE LIGHTS";
  if (api === "ready" && publishing) return "DESIGN LAB · SCREEN ONLY UNTIL YOU APPLY OR PUBLISH";
  if (publishing) return "DESIGN LAB · SCREEN ONLY UNTIL YOU PUBLISH";
  if (api === "ready") return "DESIGN LAB · SCREEN ONLY UNTIL YOU APPLY";
  return "DESIGN LAB · SCREEN ONLY";
}

/** The header badge while a try-on runs: "In try-on · Golden Hour · 2:05 left". */
export function trialBadge(trial, leftS) {
  if (!trial) return "";
  return ["In try-on", trialName(trial), formatLeft(leftS)].filter(Boolean).join(" · ");
}

/** What the lights are doing now, from the server: the try-on's own sentence and the tier the engine installed. */
export function trialStatusLines(trial) {
  if (!trial) return [];
  const name = trial.draft_name && trial.label && trial.draft_name !== trial.label ? `${trial.draft_name} (${trial.label})` : trialName(trial);
  const tier = TIER_PHRASE[trial.tier] || (trial.tier ? `at the ${String(trial.tier)} tier` : "");
  const lines = [`On the lights now: ${name}${tier ? `, ${tier}` : ""}.`];
  if (trial.message) lines.push(String(trial.message));
  if (Array.isArray(trial.positions) && trial.positions.length) lines.push(`Writing to ${moduleList(trial.positions)}.`);
  if (Number(trial.applies) > 1) lines.push(`Applied ${Number(trial.applies)} times; re-applying never extends the time limit.`);
  return lines;
}

/** Whether Apply may send now. `busy`: a start or stop is on its way; `lastTapMs`: the last tap on Apply (double-tap
 * guard); `acceptedAtMs` + `nextApplyInS`: the server's gap after the last accepted apply (it refuses inside it too).
 * → {ok, waitS, why: 'busy' | 'double-tap' | 'gap' | null}. */
export function applyReady({ busy = false, lastTapMs = null, acceptedAtMs = null, nextApplyInS = 0, nowMs } = {}) {
  if (busy) return { ok: false, waitS: 0, why: "busy" };
  if (lastTapMs !== null && lastTapMs !== undefined && nowMs - lastTapMs < DOUBLE_TAP_MS) return { ok: false, waitS: 0, why: "double-tap" };
  const gap = Number(nextApplyInS);
  if (acceptedAtMs !== null && acceptedAtMs !== undefined && Number.isFinite(gap) && gap > 0) {
    const waitS = gap - (nowMs - acceptedAtMs) / 1000;
    if (waitS > 0) return { ok: false, waitS, why: "gap" };
  }
  return { ok: true, waitS: 0, why: null };
}

/** The Apply button's words (never a slider: one tap, one apply). */
export function applyLabel({ busy = false, waitS = 0, reapply = false } = {}) {
  if (busy) return "Sending…";
  if (waitS > 0) return `Apply again in ${Math.ceil(waitS)} s`;
  return reapply ? "Apply again" : "Apply to the lights";
}

/** A refused POST /api/lab/trial/start, classified: {kind: 'confirm', sentence, cost} (the server wants its confirm
 * sentence back), 'problems' (422, shown at their fields), 'refused' (a 409 engine refusal or the apply gap, with
 * retryAfterS), 'no-engine' (503), 'missing' (the route isn't on this server), 'unreachable', 'error'. */
export function startOutcome(err) {
  const status = Number(err && err.status);
  const body = err && err.body && typeof err.body === "object" ? err.body : {};
  const detail = typeof body.detail === "string" ? body.detail : (err && err.message) || "";
  const retry = Number(body.retry_after_s);
  const retryAfterS = Number.isFinite(retry) && retry > 0 ? retry : 0;
  if (status === 409 && body.confirm_required) {
    return { kind: "confirm", sentence: String(body.confirm || body.detail || ""), cost: typeof body.cost === "string" ? body.cost : "" };
  }
  if (status === 422) return { kind: "problems", problems: Array.isArray(body.problems) ? body.problems : [], text: detail || "The draft has problems." };
  if (status === 409) return { kind: "refused", text: detail || "The try-on was refused.", retryAfterS };
  if (status === 503) return { kind: "no-engine", text: detail || TRIAL_COPY.noEngine, retryAfterS };
  if (trialApiFromError(err) === "missing") return { kind: "missing", text: TRIAL_COPY.missing };
  if (status === 0 || !Number.isFinite(status)) return { kind: "unreachable", text: "Can’t reach the Conductor, so the try-on wasn’t sent." };
  return { kind: "error", text: detail || `The try-on failed (HTTP ${status}).`, retryAfterS };
}

/** The body of POST /api/lab/trial/start: the working draft as the preview sends it, plus the confirm sentence once the
 * server has asked for it. */
export function trialStartBody(sceneId, draft, confirm = null) {
  const body = { scene_id: sceneId, draft: normaliseDraft(draft) };
  if (confirm) body.confirm = confirm;
  return body;
}

/** A line for the panel when the working draft changed after it was applied (the lights still show the applied one). */
export function editedSinceApply(trial, sceneId, working, applied) {
  if (!trial || trial.scene_id !== sceneId || !applied || !working) return "";
  if (draftEquals(working, applied)) return "";
  return "You’ve edited this scene since it was applied: the lights show the applied draft until you apply again.";
}

/** The note under a stage picture that isn't what the lights show during a try-on of this scene: the painted view, and
 * "today" while it paints the slots' approved design. Empty otherwise. */
export function trialStageNote(view, preview, trial, sceneId) {
  if (!trial || trial.scene_id !== sceneId) return "";
  const today = todayOf(view, preview);
  if (view !== "canvas" && !(today && today.heads === "approved")) return "";
  return `During the try-on the lights show the tried draft ${TIER_PHRASE[trial.tier] || TIER_PHRASE.modules}, not this picture.`;
}

// ---- motion (Slice C: PLAN §7) ---------------------------------------------------------------------------------------
// Stepped waves (D65) and the ripple (D64), on the screen only. Every step and every pace comes from the server
// (PreviewJSON.stepped: the real compiler's steps, and `pace`, the ProgramRunner's own step for nine modules and for the
// show box's kits); nothing here estimates a pace or mixes a colour. On the real lights stepped looks stay refused until
// Gate R passes and Alon sets motion.stepped.live_enabled, and the ripple stays off until he signs it off (D64): the Lab
// says both and has no control for either. The timeline follows conductor/sequence.py: step n (n = 0 … count − 1, step 0
// is the still look) shows steps[n mod cycle] from n × step, count = ⌊run_s / step⌋, then the still look holds.

export const STEP_S_STEP = 0.5; // step time and run length move in half seconds
export const FAST_FACTOR = 4; // the optional fast preview: clearly labelled as sped up
export const RIPPLE_STAGGER_MAX_S = 1.2; // RIPPLE_SPAN_MAX_S 2.4 / (RIPPLE_MAX_WAVES 3 − 1), conductor/config.py
export const RIPPLE_STAGGER_STEP_S = 0.1;
export const RIPPLE_DEFAULT_S = 0.6;
export const RIPPLE_HOLD_S = 1.5; // the landed look stays this long, then the stage goes back to its view
export const STEPPED_DEFAULT_LIMITS = Object.freeze({ steps_per_cycle: [3, 12], step_s: [4, 12], run_s_max: 180 });
export const MOTION_COPY = Object.freeze({
  live: "Stepped motion is off on the real lights until Gate R passes; Alon sets motion.stepped.live_enabled",
  liveOn: "Stepped motion is on for the real lights (motion.stepped.live_enabled is set); only Alon changes that, never the Lab",
  ripple: "Screen preview only — the canopy's ripple is off (kiosk.ripple_stagger_s: 0) until Alon signs off (D64)",
  effects: "Effects — not enabled: no code has passed the bench (06 #4)",
  effectsOn: "Effects — the Lab doesn't offer Hubspace's on-device effects; enabling a benched code is Alon's call (06 #4)",
  effectsNote: "Hubspace's on-device effects (motionEffect, brightnessEffect) on a custom slot stay off until a code passes the bench.",
  fast: `Fast preview: ${FAST_FACTOR}× faster than the lights would step — not the real pace`,
  noSteps: "The server sent no stepped steps for this draft, so there is nothing to play (this server may not have the stepped preview yet).",
  waiting: "Waiting for the server's steps…",
  noRings: "The server sent no ripple rings for this draft, so the ripple can't be previewed here.",
  idle: "This is the idle look: the canopy's resting look stays still, so it never steps.",
  none: "This scene has no stepped rung: it stays still at module level. Adding one is a modes.yaml change for Alon (D65).",
  noDraft: "This draft carries no stepped timing; Revert to approved brings the approved timing back.",
  design: "Moves the scene's own layout (the same design as every rung): change it under Layout.",
  direction: "Forward flows the way the layout reads; reverse flows back.",
  cycle: "Steps per cycle: how many steps it takes the design to move by one full repeat.",
});
const EPS = 1e-6;
const COUNT_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"];

/** 7.5 → "7.5", 6 → "6" (whole milliseconds, as the runner rounds). */
export function fmtS(v) {
  const n = Number(v);
  return Number.isFinite(n) ? String(Math.round(n * 1000) / 1000) : "";
}

const countWord = (n) => COUNT_WORDS[n] || String(n);
const capital = (t) => t.charAt(0).toUpperCase() + t.slice(1);
const positive = (v) => (v !== null && v !== undefined && v !== "" && Number.isFinite(Number(v)) && Number(v) > 0 ? Number(v) : null);
const pairOf = (v, d) => (Array.isArray(v) && v.length === 2 && v.every((x) => Number.isFinite(Number(x))) ? v.map(Number) : [...d]);
const snap = (v, step) => Math.round(Number(v) / step) * step;
const clampTo = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** A line as a sentence: one final full stop, never two (the server's STEPPED_LIVE_ON ends in one already). */
export function sentence(t) {
  const text = String(t || "").trim();
  if (!text) return "";
  return /[.!?]$/.test(text) ? text : `${text}.`;
}
const unstop = (t) => String(t).replace(/\.$/, "");

/** CatalogJSON.limits.stepped with the schema's defaults where it says nothing: {steps_per_cycle, step_s, run_s_max}. */
export function steppedLimits(limits) {
  const s = (limits && limits.stepped) || {};
  return {
    steps_per_cycle: pairOf(s.steps_per_cycle, STEPPED_DEFAULT_LIMITS.steps_per_cycle),
    step_s: pairOf(s.step_s, STEPPED_DEFAULT_LIMITS.step_s),
    run_s_max: positive(s.run_s_max) || STEPPED_DEFAULT_LIMITS.run_s_max,
  };
}

/** The Motion section's controls for a scene (a CatalogJSON scene) and its working draft: {editable, reason} plus, when
 * editable, the direction and the three number controls with their bounds (SteppedTechnique + show._check_stepped). */
export function steppedControls(scene, draft, limits) {
  if (!scene || !scene.stepped) return { editable: false, reason: scene && scene.idle ? MOTION_COPY.idle : MOTION_COPY.none };
  const s = draft && draft.stepped;
  if (!s) return { editable: false, reason: MOTION_COPY.noDraft };
  const b = steppedLimits(limits);
  const stepS = Number(s.step_s);
  return {
    editable: true,
    reason: "",
    direction: s.direction === "reverse" ? "reverse" : "forward",
    steps: { min: b.steps_per_cycle[0], max: b.steps_per_cycle[1], step: 1, value: Number(s.steps_per_cycle) },
    step_s: { min: b.step_s[0], max: b.step_s[1], step: STEP_S_STEP, value: stepS },
    run_s: { min: Math.min(stepS, b.run_s_max), max: b.run_s_max, step: STEP_S_STEP, value: Number(s.run_s) },
    runLimit: `Run length: ${fmtS(b.run_s_max)} s at most (a run ends within 180 s and before the kiosk's idle return, kiosk.idle_return_s), and at least one step.`,
  };
}

/** A new draft with its stepped timing patched and held inside the bounds: whole steps, half-second times for the
 * field being edited (an approved time off the half second, which SteppedTechnique allows, is kept until it is
 * edited), and a run never shorter than one step (raising the step time past the run raises the run with it). */
export function setStepped(draft, patch, limits) {
  if (!draft || !draft.stepped) return draft;
  const d = clone(draft);
  const b = steppedLimits(limits);
  const s = { ...d.stepped, ...patch };
  const edited = (k) => patch && Object.prototype.hasOwnProperty.call(patch, k);
  s.direction = s.direction === "reverse" ? "reverse" : "forward";
  s.steps_per_cycle = clampTo(Math.round(Number(s.steps_per_cycle) || b.steps_per_cycle[0]), ...b.steps_per_cycle);
  const stepS = Number(s.step_s) || b.step_s[0];
  s.step_s = clampTo(edited("step_s") ? snap(stepS, STEP_S_STEP) : stepS, ...b.step_s);
  const runS = Number(s.run_s) || s.step_s;
  s.run_s = clampTo(edited("run_s") ? snap(runS, STEP_S_STEP) : runS, s.step_s, Math.max(s.step_s, b.run_s_max));
  d.stepped = s;
  return d;
}

/** PreviewJSON.stepped, checked: {steps, cycle, stepsPerCycle, designStepS, runS, direction, moving, pace: {nine, today,
 * todayModules}, liveSentence}, or {error} (null on a server without the stepped preview; missing or broken steps). */
export function steppedOf(preview) {
  if (!preview || typeof preview !== "object") return { error: MOTION_COPY.waiting };
  const s = preview.stepped;
  if (!s || typeof s !== "object") return { error: preview.fromCatalog ? MOTION_COPY.waiting : MOTION_COPY.noSteps };
  let steps = (Array.isArray(s.steps) ? s.steps : []).filter((st) => st && st.colours && typeof st.colours === "object");
  if (!steps.length) return { error: MOTION_COPY.noSteps };
  if (steps.every((st) => Number.isFinite(Number(st.index)))) steps = [...steps].sort((a, b) => Number(a.index) - Number(b.index));
  const pace = s.pace && typeof s.pace === "object" ? s.pace : {};
  const runs = s.runs && typeof s.runs === "object" ? s.runs : {};
  const stillOf = (k) => (runs[k] && typeof runs[k].still === "string" && runs[k].still ? runs[k].still : null);
  return {
    steps,
    cycle: steps.length,
    stepsPerCycle: positive(s.steps_per_cycle) || steps.length,
    designStepS: positive(s.design_step_s),
    runS: positive(s.run_s),
    direction: s.direction === "reverse" ? "reverse" : "forward",
    moving: typeof s.moving_feature === "string" && s.moving_feature ? s.moving_feature : null,
    // the partial canopy's moving copy (D62: counts rewritten for the show box's driven kits)
    todayMoving: typeof s.today_moving_feature === "string" && s.today_moving_feature ? s.today_moving_feature : null,
    pace: { nine: positive(pace.nine), today: positive(pace.today), todayModules: positive(pace.today_modules) },
    // the server's own reason a pace holds still (ProgramRunner: a step past max_step_s, or a run shorter than a step)
    still: { nine: stillOf("nine"), today: stillOf("today") },
    liveEnabled: s.live_enabled === true,
    liveSentence: typeof s.live_sentence === "string" && s.live_sentence ? s.live_sentence : null,
    liveNote: typeof s.live_note === "string" && s.live_note ? s.live_note : null,
    // when a scene whose stepped rung sits under higher rungs actually steps (the server's sentence; null for a
    // scene whose first rung is stepped)
    stepsWhen: typeof s.steps_when === "string" && s.steps_when ? s.steps_when : null,
  };
}

/** The pace toggle: "Nine modules (7.5 s)" and "Show box today (two modules, 6 s)", from the server's numbers only. */
export function paceOptions(st) {
  if (!st || st.error) return [];
  const out = [];
  if (st.pace.nine) out.push({ key: "nine", stepS: st.pace.nine, label: `Nine modules (${fmtS(st.pace.nine)} s)` });
  if (st.pace.today) {
    const n = st.pace.todayModules;
    const mods = n ? `${countWord(n)} module${n === 1 ? "" : "s"}, ` : "";
    out.push({ key: "today", stepS: st.pace.today, label: `Show box today (${mods}${fmtS(st.pace.today)} s)` });
  }
  return out;
}

/** The run at a pace (`nine` | `today`; the first the server sent when that one is missing): {pace, label, stepS,
 * count, cycle, runS, totalS} or {error}. count = ⌊run_s / step⌋, as ProgramRunner.count_for. */
export function stepTimeline(st, paceKey) {
  if (!st || st.error) return { error: (st && st.error) || MOTION_COPY.noSteps };
  const options = paceOptions(st);
  const opt = options.find((o) => o.key === paceKey) || options[0];
  if (!opt) return { error: "The server sent no pace for these steps, so they can't be played at the real pace." };
  if (!st.runS) return { pace: opt.key, error: "The server sent no run length for these steps, so they can't be played." };
  const still = st.still && st.still[opt.key];
  if (still) return { pace: opt.key, error: `${capital(still)}: the still design holds instead.` };
  const count = Math.floor(st.runS / opt.stepS + EPS);
  if (count < 1) {
    return { pace: opt.key, error: `Its run (${fmtS(st.runS)} s) is shorter than one ${fmtS(opt.stepS)} s step, so the lights would hold still.` };
  }
  if (count < 2) {
    // step 0 IS the still design (the mode change sends it as the hold); the runner sends steps 1 … count − 1 only
    return { pace: opt.key, error: `Its run (${fmtS(st.runS)} s) fits only the first step, which is the still design (${fmtS(opt.stepS)} s steps), so no colour would change.` };
  }
  return { pace: opt.key, label: opt.label, stepS: opt.stepS, count, cycle: st.cycle, runS: st.runS, totalS: count * opt.stepS };
}

/** Where the run is `elapsedS` seconds in (the lights' seconds): {n, index (into steps), done, nextAtS}; after the
 * last step {done: true, index: 0}: the still look (step 0's colours) holds. Null for a timeline with an error. */
export function stepAt(tl, elapsedS) {
  if (!tl || tl.error) return null;
  const e = Math.max(0, Number(elapsedS) || 0);
  const n = Math.floor(e / tl.stepS + EPS);
  if (n >= tl.count) return { n: tl.count, index: 0, done: true, nextAtS: null };
  return { n, index: n % tl.cycle, done: false, nextAtS: (n + 1) * tl.stepS };
}

/** The step counter: "12 steps of 7.5 s (90 s), then the still look holds" before it plays, "Step 3 of 12 · every 7.5 s"
 * (· paused, · sped up 4×) while it does, "Run over after 12 steps (90 s): the still look holds" at the end. */
export function stepCounter(tl, at, { state = "idle", fast = false } = {}) {
  if (!tl) return "";
  if (tl.error) return tl.error;
  if (state === "idle" || !at) return `${plural(tl.count, "step")} of ${fmtS(tl.stepS)} s (${fmtS(tl.totalS)} s), then the still look holds`;
  if (at.done) return `Run over after ${plural(tl.count, "step")} (${fmtS(tl.totalS)} s): the still look holds`;
  // step 1 (n = 0) is the still look the run starts from: the colours change from step 2
  const parts = [`Step ${at.n + 1} of ${tl.count}${at.n === 0 ? " (the still look)" : ""}`, `every ${fmtS(tl.stepS)} s`];
  if (state === "paused") parts.push("paused");
  if (fast) parts.push(`sped up ${FAST_FACTOR}×`);
  return parts.join(" · ");
}

/** Where every shown side's run stands `elapsedS` in (A/B: each side has its own timeline): {done, totalS, key}. The
 * run is over only when the longest side's is; `key` changes whenever any side steps (a repaint). Sides with an
 * error play nothing and are left out. */
export function runsAt(timelines, elapsedS) {
  const live = (timelines || []).filter((tl) => tl && !tl.error);
  const ats = live.map((tl) => stepAt(tl, elapsedS));
  return {
    done: ats.every((a) => a && a.done),
    totalS: live.reduce((m, tl) => Math.max(m, tl.totalS), 0),
    key: live.map((tl, i) => `${tl.pace}|${ats[i] ? ats[i].n : "-"}`).join(","),
  };
}

/** The tier list's moving line(s) for a tier, only while the steps play: the tier's own (nine-module) sentence and, at
 * the show box's pace, the partial canopy's too, each saying whose it is (D62). */
export function tierMovingLines(tier, st, state, at, paceKey = "nine") {
  if (!tier || !tier.moving_feature || state !== "playing" || !at || at.done) return [];
  if (paceKey === "today" && st && !st.error && st.todayMoving) {
    return [`While it moves on all nine modules: ${tier.moving_feature}`, `While it moves on the show box today: ${st.todayMoving}`];
  }
  return [`While it moves: ${tier.moving_feature}`];
}

/** The moving sentence, only while the steps play (the schema-checked moving_feature; at the `today` pace the
 * partial canopy's, D62, when the server sent it); "" otherwise. */
export function movingLine(st, state, at, paceKey = "nine") {
  if (!st || st.error || state !== "playing" || !at || at.done) return "";
  if (paceKey === "today" && st.todayMoving) return st.todayMoving;
  return st.moving || "";
}

/** The modules a step lights at a pace: the show box's driven kits for `today` (the rest dark), every module for
 * `nine`. → {positions: [pos] | null, note}. */
export function stepPositions(preview, paceKey) {
  if (paceKey !== "today") return { positions: null, note: "" };
  const box = preview && preview.show_box_today;
  const driven = box && box.positions && Array.isArray(box.positions.driven) ? box.positions.driven : null;
  if (!driven) return { positions: null, note: "Which modules the show box drives isn't in this preview, so every module is drawn." };
  // the live clause follows the server's gate (motion.stepped.live_enabled): never "off" once Alon has set it
  const live = !!(preview && preview.stepped && preview.stepped.live_enabled === true);
  const off = live ? "" : " (stepped stays off on the real lights)";
  return { positions: driven, note: `Paced as the show box today: ${moduleList(driven)} step, the rest stay dark${off}.` };
}

/** One line on what the draft's timing comes to at each pace, once the server has answered for exactly this timing. */
export function steppedRunLine(draftStepped, st) {
  if (!draftStepped || !st || st.error) return "";
  const same = st.runS === Number(draftStepped.run_s) && (st.designStepS === null || st.designStepS === Number(draftStepped.step_s));
  if (!same) return "";
  const parts = paceOptions(st).map((o) => {
    const tl = stepTimeline(st, o.key);
    return tl.error ? `${o.label}: holds still` : `${o.label}: ${plural(tl.count, "step")}`;
  });
  return parts.length ? `A ${fmtS(st.runS)} s run — ${parts.join(" · ")}; then the still look holds.` : "";
}

/** The live line, without its final full stop (callers end it with `sentence`): the Lab never flips
 * motion.stepped.live_enabled, it only says where it stands. */
export function steppedLiveLine(live, st = null) {
  const catalogNote = live && typeof live.stepped_live_note === "string" && live.stepped_live_note ? live.stepped_live_note : "";
  const answered = st && !st.error;
  // the server's own sentence (show.STEPPED_LIVE_OFF) and note win, word for word (PLAN §7)
  if (answered && st.liveSentence) {
    const note = st.liveNote || catalogNote;
    return note ? `${capital(st.liveSentence)}. ${unstop(note)}` : capital(st.liveSentence);
  }
  // live: the server sends no off sentence, and its note (STEPPED_LIVE_ON) is the whole line
  if (answered && st.liveEnabled && st.liveNote) return unstop(st.liveNote);
  const on = !!(live && live.stepped_live_enabled === true);
  if (on && catalogNote) return unstop(catalogNote);
  return on ? MOTION_COPY.liveOn : MOTION_COPY.live;
}

/** The export's D65 line: where the live gate stands (the server's clause; the catalog's gate before it answers). */
export function steppedSectionClause(exported, live) {
  if (exported && typeof exported.stepped_live_clause === "string" && exported.stepped_live_clause) return exported.stepped_live_clause;
  return live && live.stepped_live_enabled === true ? "live on (motion.stepped.live_enabled)" : "live off until Gate R";
}

/** The ripple preview's label: screen only, with the canopy's own setting (the Lab never changes it). */
export function rippleLabel(live) {
  if (live && typeof live.ripple_note === "string" && live.ripple_note) return live.ripple_note; // the server's, word for word
  const v = live ? Number(live.ripple_stagger_s) : 0;
  if (!live || !Number.isFinite(v) || v === 0) return MOTION_COPY.ripple;
  return `Screen preview only — the canopy's ripple is set to kiosk.ripple_stagger_s: ${fmtS(v)} s (D64); the Lab never changes it`;
}

export function effectsLine(live) {
  return live && live.effects_enabled ? MOTION_COPY.effectsOn : MOTION_COPY.effects;
}

/** A stagger the slider can hold: 0–1.2 s in tenths. */
export function clampStagger(v) {
  if (v === null || v === undefined || v === "" || !Number.isFinite(Number(v))) return RIPPLE_DEFAULT_S;
  return Math.round(clampTo(Number(v), 0, RIPPLE_STAGGER_MAX_S) * 10) / 10;
}

/** PreviewJSON.rings (the server's `field.bands` of the design, lowest t first) at a stagger: ring i lands at
 * i × stagger. → {stagger, rings: [{ring, at, positions}], spanS}, or null with no rings. */
export function rippleSchedule(rings, staggerS) {
  if (!Array.isArray(rings)) return null;
  const list = rings.filter((r) => Array.isArray(r) && r.length).map((r) => r.map(String));
  if (!list.length) return null;
  const stagger = clampStagger(staggerS);
  const at = (i) => Math.round(i * stagger * 1000) / 1000;
  return { stagger, rings: list.map((positions, i) => ({ ring: i, at: at(i), positions })), spanS: at(list.length - 1) };
}

/** The positions landed `elapsedS` seconds after the first ring. */
export function rippleLanded(schedule, elapsedS) {
  const out = new Set();
  if (!schedule) return out;
  const e = Number(elapsedS) || 0;
  for (const r of schedule.rings) if (r.at <= e + EPS) r.positions.forEach((p) => out.add(p));
  return out;
}

/** "Three rings, 0.6 s apart: the last lands 1.2 s after the first". It says what the canopy does today only against
 * `liveStaggerS` (kiosk.ripple_stagger_s, from the server): "as the canopy does today" only at that stagger, and with
 * no live value no claim about the canopy at all. */
export function rippleLine(schedule, liveStaggerS = null) {
  if (!schedule) return MOTION_COPY.noRings;
  const n = schedule.rings.length;
  if (n === 1) return "One ring: every module lands together.";
  const rings = `${capital(countWord(n))} rings`;
  const lv = liveStaggerS === null || liveStaggerS === undefined || liveStaggerS === "" ? NaN : Number(liveStaggerS);
  const known = Number.isFinite(lv) && lv >= 0;
  const same = known && Math.abs(schedule.stagger - lv) < EPS;
  if (schedule.stagger === 0) {
    if (same) return `${rings}, 0 s apart: every module lands at once, as the canopy does today.`;
    if (known) return `${rings}, 0 s apart: every module lands at once (the canopy's own ripple is ${fmtS(lv)} s apart).`;
    return `${rings}, 0 s apart: every module lands at once.`;
  }
  const line = `${rings}, ${fmtS(schedule.stagger)} s apart: the last lands ${fmtS(schedule.spanS)} s after the first`;
  return same ? `${line}, as the canopy lands a new mood today.` : `${line}.`;
}

// ---- the live show (docs/live-lab/PLAN.md §3: the Lab as one system) ----------------------------------------------------
// The show runs the approved (git) looks or a published Lab version; working drafts and saved versions are drafts until
// a publish. Every sentence that says what a publish does or why it was refused is the SERVER's (the confirm sentence,
// the cost, each 409); these helpers only word the show bar from LiveJSON (PLAN §2.11), pick which server sentence to
// show, and count waits down. Nothing here decides what reaches the lights.

export const LIVE_POLL_MS = 5000; // GET /api/lab/live while the Lab is visible
export const LIVE_REPAINT_POLL_MS = 1000; // and while a slot repaint is running or queued (B2)
export const WHO_MAX = 40; // the publisher's name (`who`, kept as a UI pref in this browser)
export const NOTE_MAX = 60; // a version's note
export const NAME_MAX = 80; // a version's name
export const VERSION_FILE_KIND = "color-canopy-lab-version";
export const VERSION_FILE_MAX_BYTES = 2_000_000;
export const PUBLISH = "Publish to the show";
export const REVERT = "Revert the show to approved";
export const UNDO_PUBLISH = "Undo last publish";
export const SAVE_PUBLISH = "Save & publish";
export const LIVE_NOW = "Live now";
export const WORKING_TEXT = Object.freeze({ live: "Live", draft: "Draft", "in try-on": "In try-on" });
export const LIVE_COPY = Object.freeze({
  loading: "checking what it runs…",
  missing: "publishing from the Lab isn’t on this server yet",
  missingAction: "Publishing from the Lab isn’t on this server yet; the show runs the approved looks.",
  unreachable: "Can’t reach the Conductor, so what the show runs is unknown",
  unknownAction: "What the show runs is unknown, so publishing waits until the Conductor answers.",
  noData: "This runtime has no data folder, so a publish couldn’t survive a restart: publishing is off here.",
  liveNow: "The working drafts are what the show runs.",
  gitChanged: "config/ changed since this was published; the show still runs the published looks.",
  inGit: "(the same as approved — it’s in git)",
  canopyOff: "The canopy is off (guest power): a publish changes the look, not the power.",
  ipad: "Drafts and try-ons never change the iPad’s cards; a publish does.",
  d59: "changes an approved colour — needs the client’s sign-off",
  endsTrial: "The running try-on ends.",
  repaintOff:
    "A new painted design (its colours’ hexes or its layout) can’t be published from this box yet: there is no slot repaint here. Brightness, solid, colour names, copy and stepped timing can.",
});
const OUTCOME_TEXT = { live: "Live", replaced: "Replaced", "rolled back": "Rolled back", "in git": "In git" };
const STATUS_TEXT = { pending: "waiting", applied: "repainted", skipped: "skipped", locked: "held by the module page", failed: "failed", mismatch: "read back different" };

const str = (v) => (typeof v === "string" ? v : "");
const strs = (v) => (Array.isArray(v) ? v.filter((x) => typeof x === "string" && x) : []);
const listWords = (items) => (items.length <= 1 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`);

/** "Modules 1–2" for module labels ("Module 1", "Module 2"): runs collapsed as moduleList does; other labels joined. */
export function labelList(labels) {
  const list = strs(labels);
  const ns = list.map((l) => /^Module (\d)$/.exec(l)).map((m) => (m ? Number(m[1]) : NaN));
  if (!list.length || ns.some((n) => !Number.isFinite(n) || n < 1)) return listWords(list);
  return moduleList(ns.map((n) => `r${Math.floor((n - 1) / 3)}c${(n - 1) % 3}`));
}

/** The scene's live badge: 'Published' when the show runs a published look for it, else 'Approved' (also from a server
 * without publishing, whose show runs git). LiveJSON's per-scene `source` wins over the catalog's (it is polled). */
export function liveBadge(scene, liveScene = null) {
  const source = (liveScene && liveScene.source) || (scene && scene.live_source);
  return source === "published" ? "Published" : "Approved";
}

/** The look the show runs for a catalog scene: its `live_look`, else (a server without publishing) its approved one. */
export function liveLookOf(scene) {
  return (scene && (scene.live_look || scene.approved)) || null;
}

/** The working draft against the show: 'in try-on' (the engine's trial is this scene) over 'draft' (differs from the
 * LIVE look — not from approved) over 'live'. */
export function workingState(working, liveLook, trial) {
  const id = (working && working.scene_id) || (liveLook && liveLook.scene_id);
  if (trial && trial.scene_id && trial.scene_id === id) return "in try-on";
  if (!working || !liveLook || draftEquals(working, liveLook)) return "live";
  return "draft";
}

/** Where a scene's working draft starts: the draft this browser kept, else the opened version's draft, else (no
 * version opened) the show's live look; a scene an opened version doesn't name is approved in that version. */
export function seedFor(scene, stored = null, { versionDraft = null, fromVersion = false } = {}) {
  if (stored) return stored;
  if (versionDraft) return versionDraft;
  if (fromVersion) return (scene && scene.approved) || null;
  return liveLookOf(scene);
}

/** 'Publish to the show' ↔ 'Publish to this simulator': on a simulator runtime "the show" is this simulator. */
export function publishLabel(simulated, base) {
  const text = String(base ?? "");
  return simulated ? text.replace(/\bthe show\b/g, "this simulator") : text;
}

/** The one Publish button: 'Save & publish' while the working drafts differ from the selected version (or nothing is
 * saved), 'Publish to the show' for the selected version ("approved" when every working draft is git's), disabled
 * 'Live now' when the working drafts are what the show runs. → {label, enabled, version, save, why}. */
export function publishAction({ working = {}, base = {}, approved = {}, liveLooks = {}, order = null, selected = null, api = "ready", writable = true, readOnly = false, busy = false, simulated = false } = {}) {
  const ids = order || Object.keys(working || {});
  const label = (t) => publishLabel(simulated, t);
  const off = (text, why) => ({ label: label(text), enabled: false, version: null, save: false, why });
  if (api === "missing") return off(PUBLISH, LIVE_COPY.missingAction);
  if (api !== "ready") return off(PUBLISH, api === "loading" ? `The show: ${LIVE_COPY.loading}` : LIVE_COPY.unknownAction);
  if (!writable) return off(PUBLISH, LIVE_COPY.noData);
  const differs = ids.some((id) => working[id] && liveLooks[id] && !draftEquals(working[id], liveLooks[id]));
  if (!differs) return off(LIVE_NOW, LIVE_COPY.liveNow);
  const unsaved = unsavedScenes(working, base, ids).length > 0;
  const allApproved = ids.every((id) => !working[id] || !approved[id] || draftEquals(working[id], approved[id]));
  if (!unsaved && !selected && allApproved) return { label: label(PUBLISH), enabled: !busy, version: APPROVED, save: false, why: "" };
  if (unsaved || !selected) {
    // save first (the note field names it), then publish the version just saved: what goes live is what's on screen
    const why = readOnly ? "The Lab’s saved versions are read-only here, so nothing can be saved first." : "";
    return { label: SAVE_PUBLISH, enabled: !busy && !readOnly, version: null, save: true, why };
  }
  return { label: label(PUBLISH), enabled: !busy, version: selected, save: false, why: "" };
}

/** The id the publish dialog was opened against (`expected_live`): the live publication, or "approved". */
export function expectedLive(live) {
  return live && live.current && live.current.id ? live.current.id : APPROVED;
}

/** The publisher's name as sent: spaces collapsed, trimmed, ≤ 40 characters, "" when blank. */
export function whoValue(text) {
  return String(text ?? "").replace(/\s+/g, " ").trim().slice(0, WHO_MAX);
}

/** The body of POST /api/lab/publish (PLAN §2.11). */
export function publishBody({ version, confirm = null, expected = APPROVED, who = "", show = null } = {}) {
  const body = { version: String(version), expected_live: String(expected || APPROVED) };
  if (confirm) body.confirm = String(confirm);
  const name = whoValue(who);
  if (name) body.who = name;
  if (show !== null && show !== undefined) body.show = !!show;
  return body;
}

/** The dialog's title for what is being published. */
export function publishTitle(kind, simulated = false) {
  if (kind === "approved") return publishLabel(simulated, REVERT);
  if (kind === "previous") return UNDO_PUBLISH;
  return publishLabel(simulated, PUBLISH);
}

function repaintPlanLine(r, names = {}) {
  const modules = strs(r && r.modules);
  const writes = Number(r && r.writes);
  const inS = Number(r && r.in_s);
  const where = modules.length ? ` on ${labelList(modules)}` : "";
  const count = Number.isFinite(writes) && r.writes !== null ? `: ${plural(writes, "slot write")}` : "";
  const when = Number.isFinite(inS) && inS > 0 ? `, in ${Math.ceil(inS)} s` : "";
  const scene = (r && (r.name || names[r.scene] || r.scene)) || "A painted scene";
  return `${scene} repaints ${(r && r.slot) || "its slot"}${where}${count}${when}`;
}

/** The 409 confirm_required body as the dialog shows it: the server's sentence verbatim, its cost, the changes, the
 * repaint lines, the (non-blocking) warnings, the notes, the try-on line (only when the sentence doesn't say it) and
 * the scene the "Show it on the canopy" checkbox offers. */
export function publishDialog(body409) {
  const b = body409 && typeof body409 === "object" ? body409 : {};
  const sentence = str(b.confirm) || str(b.detail);
  const changes = (Array.isArray(b.changes) ? b.changes : []).map((c) => {
    const name = str(c && c.name) || str(c && c.scene);
    const what = strs(c && c.what);
    const back = c && c.to === "approved" ? " — back to approved" : "";
    return { scene: str(c && c.scene), name, text: `${name}${what.length ? `: ${what.join(", ")}` : ""}${back}`, d59: !!(c && c.needs_d59) };
  });
  const offer = str(b.show_offer) || null;
  const offered = offer ? changes.find((c) => c.scene === offer) : null;
  return {
    sentence,
    cost: str(b.cost),
    replaces: b.replaces && typeof b.replaces === "object" ? str(b.replaces.name) || null : null,
    changes,
    repaint: (Array.isArray(b.repaint) ? b.repaint : []).map((r) => repaintPlanLine(r, Object.fromEntries(changes.map((c) => [c.scene, c.name])))),
    warnings: strs(b.warnings),
    notes: strs(b.notes),
    endsTrial: !!b.ends_trial,
    trialLine: b.ends_trial && !sentence.includes(LIVE_COPY.endsTrial) ? LIVE_COPY.endsTrial : "",
    showOffer: offer,
    showLabel: offer ? `Show ${(offered && offered.name) || offer} on the canopy after publishing` : "",
  };
}

/** A refused POST /api/lab/publish, classified (PLAN §2.8): 'confirm' (the dialog), 'conflict' (someone published
 * since: ask again from its `live`), 'wait' (retry_after_s), 'field' (marks a scene), 'stale' (422 / stale: marks the
 * version), 'nothing', 'read-only', 'refused' (any other 409, or a 404 for an unknown version), 'missing' (no route
 * here), 'no-engine' (503), 'unreachable', 'error'. */
export function publishOutcome(err) {
  const status = Number(err && err.status);
  const body = err && err.body && typeof err.body === "object" ? err.body : {};
  const detail = str(body.detail) || str(err && err.message);
  const retry = Number(body.retry_after_s);
  const retryAfterS = Number.isFinite(retry) && retry > 0 ? retry : 0;
  if (status === 409 && body.confirm_required) return { kind: "confirm", dialog: publishDialog(body), text: detail };
  if (status === 409 && body.conflict) return { kind: "conflict", text: detail, live: body.live && typeof body.live === "object" ? body.live : null };
  if (status === 409 && body.nothing) return { kind: "nothing", text: detail || "This is already what the show runs." };
  if (status === 409 && body.read_only) return { kind: "read-only", text: detail };
  if (status === 422 || (status === 409 && body.stale)) {
    return { kind: "stale", text: detail || "That version doesn’t apply to the show’s config.", problems: Array.isArray(body.problems) ? body.problems : [] };
  }
  if (status === 409 && typeof body.field === "string" && body.field) {
    const [scene, ...rest] = body.field.split(".");
    return { kind: "field", text: detail, field: body.field, scene, slot: rest.join(".") || "draft" };
  }
  if (status === 409 && retryAfterS) return { kind: "wait", text: detail, retryAfterS };
  if (status === 409) return { kind: "refused", text: detail || "The publish was refused." };
  if (liveApiFromError(err) === "missing") return { kind: "missing", text: LIVE_COPY.missingAction };
  if (status === 404) return { kind: "refused", text: detail };
  if (status === 503) return { kind: "no-engine", text: detail || "The canopy engine isn’t running here, so nothing can be published." };
  if (status === 0 || !Number.isFinite(status)) return { kind: "unreachable", text: "Can’t reach the Conductor, so nothing was published." };
  return { kind: "error", text: detail ? `The publish failed (HTTP ${status}): ${detail}` : `The publish failed (HTTP ${status}).` };
}

/** What a failed GET /api/lab/live says: 'missing' (the route isn't on this server: FastAPI's own "Not Found", 405 or
 * a 501 stub), 'unreachable' (status 0), else 'error'. */
export function liveApiFromError(err) {
  const status = Number(err && err.status);
  const body = err && err.body && typeof err.body === "object" ? err.body : null;
  const detail = body && typeof body.detail === "string" ? body.detail : "";
  if (status === 501 || (body && body.not_built) || status === 405 || (status === 404 && (!detail || detail === "Not Found"))) return "missing";
  if (status === 0 || !Number.isFinite(status)) return "unreachable";
  return "error";
}

/** "12:04" for a time today, "Oct 22, 12:04" otherwise (the viewer's local time, from epoch seconds). */
export function whenLine(epochSeconds, nowMs = Date.now(), locale = undefined) {
  const n = Number(epochSeconds);
  if (epochSeconds === null || epochSeconds === undefined || !Number.isFinite(n)) return "";
  const d = new Date(n * 1000);
  const now = new Date(Number(nowMs));
  const sameDay = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
  if (!sameDay) return versionDate(n, locale);
  return d.toLocaleTimeString(locale || "en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
}

/** Seconds left on a server wait (`retry_after_s` read at `sentMs`, or a queued repaint), counted down on this clock:
 * whole seconds, never negative. */
export function gateCountdown(retryAfterS, nowMs, sentMs) {
  const r = Number(retryAfterS);
  if (!Number.isFinite(r) || r <= 0) return 0;
  const gone = (Number(nowMs) - Number(sentMs)) / 1000;
  return Math.max(0, Math.ceil(r - (Number.isFinite(gone) && gone > 0 ? gone : 0)));
}

/** A queued repaint's seconds left: `queued_until` (the server's wall clock) as read at `readAtMs`, counted down here. */
export function queuedLeft(repaint, nowMs, readAtMs) {
  const until = Number(repaint && repaint.queued_until);
  if (!repaint || repaint.queued_until === null || !Number.isFinite(until)) return 0;
  return gateCountdown(until - Number(readAtMs) / 1000, nowMs, readAtMs);
}

const sceneNameIn = (live, catalog) => (id) => {
  const s = ((live && live.scenes) || []).find((x) => x.id === id) || ((catalog && catalog.scenes) || []).find((x) => x.id === id);
  return (s && s.name) || id;
};

/** RepaintJSON (B2) as the show bar shows it: "Repainting Golden Hour into custom-3: 6 of 9 modules done", the queue's
 * wait, or how it ended; `rows` for the per-module list. Null without a repaint (B1, or none yet). */
export function repaintProgress(repaint, { nowMs = Date.now(), readAtMs = nowMs } = {}) {
  if (!repaint || typeof repaint !== "object") return null;
  const rows = (Array.isArray(repaint.modules) ? repaint.modules : []).map((m) => {
    const label = str(m && m.label) || str(m && m.id);
    const status = str(m && m.status) || "pending";
    const reason = str(m && m.reason);
    return { id: str(m && m.id), label, status, reason, text: `${label}: ${STATUS_TEXT[status] || status}${reason ? ` (${reason})` : ""}` };
  });
  const total = rows.length;
  const done = rows.filter((r) => r.status !== "pending").length;
  const applied = rows.filter((r) => r.status === "applied").length;
  const label = str(repaint.label) || str(repaint.scene) || "A painted scene";
  const slot = str(repaint.slot) || "its slot";
  const queued = repaint.queued_until !== null && repaint.queued_until !== undefined && !repaint.started_at && !repaint.finished_at;
  const finished = !!repaint.finished_at || (!repaint.running && !queued);
  const base = { done, total, applied, rows, running: !!repaint.running && !queued && !finished, queued, finished, scene: str(repaint.scene) };
  if (queued) {
    const left = queuedLeft(repaint, nowMs, readAtMs);
    return { ...base, tone: "warn", left, text: `${label}’s repaint into ${slot} is queued: it starts ${left > 0 ? `in ${left} s` : "now"} (one repaint per scene every 2 min at most)` };
  }
  if (!finished) return { ...base, tone: "warn", left: 0, text: `Repainting ${label} into ${slot}: ${done} of ${plural(total, "module")} done` };
  if (total && applied === total) return { ...base, tone: "ok", left: 0, text: `${label}’s painted design is in ${slot} on ${plural(total, "module")}` };
  const missed = rows.filter((r) => r.status !== "applied").map((r) => `${r.label}: ${STATUS_TEXT[r.status] || r.status}${r.reason ? `, ${r.reason}` : ""}`);
  return {
    ...base,
    tone: "warn",
    left: 0,
    text: `${label}: ${slot} repainted on ${applied} of ${plural(total, "module")}${missed.length ? ` (${missed.join("; ")})` : ""}; it shows at module level until the rest are current (the reconciler retries after 10 min)`,
  };
}

/** The live look equals git: approve-into-git merged and deployed (LiveLooks.equals_approved, or the load's "in git"
 * history row for the current publication). */
function inGit(live) {
  const c = live && live.current;
  if (!c) return false;
  if (live.equals_approved || c.equals_approved) return true;
  const top = (live.history || [])[0];
  return c.kind === "approved" && !!top && top.id === c.id && top.outcome === "in git";
}

/**
 * The show bar (PLAN §3.1) from LiveJSON: {tone: 'ok'|'warn'|'error'|'unknown', text, chip, lines: [{tone, text}],
 * repaint (repaintProgress), actions: {undo, revert, history}}. `api`: the state of GET /api/lab/live ('loading' |
 * 'ready' | 'missing' | 'unreachable' | 'error'); `host`: the simulator's host for its line.
 */
export function showBar(live, catalog, { api = "ready", host = "", nowMs = Date.now(), readAtMs = nowMs } = {}) {
  const simulated = live && typeof live.simulated === "boolean" ? live.simulated : !!(catalog && catalog.simulated);
  const sim = `● Simulator on ${host || "this machine"}: nothing here reaches the canopy`;
  const chip = simulated ? "Simulator" : "Live hardware";
  const none = { undo: null, revert: null, history: null };
  const sha = catalog && typeof catalog.config_sha === "string" ? catalog.config_sha.slice(0, 6) : "";
  const git = sha ? ` · git ${sha}` : "";
  if (api === "loading" || (api === "ready" && !live)) {
    return { tone: "unknown", text: `${simulated ? sim : "● The show"} · ${LIVE_COPY.loading}`, chip, lines: [], repaint: null, actions: none };
  }
  if (api === "missing") {
    const text = simulated ? `${sim} · it runs the approved looks${git} · ${LIVE_COPY.missing}` : `● Live on the show: the approved looks${git} · ${LIVE_COPY.missing}`;
    return { tone: "ok", text, chip, lines: [], repaint: null, actions: none };
  }
  if (api !== "ready") {
    const last = live && live.current && live.source === "published" ? ` (last known: “${live.current.name}”)` : live ? " (last known: the approved looks)" : "";
    return { tone: "warn", text: `● ${LIVE_COPY.unreachable}${last}`, chip, lines: [], repaint: null, actions: none };
  }
  const name = sceneNameIn(live, catalog);
  const c = live.current;
  const published = live.source === "published" && !!c;
  let what;
  if (published) {
    const changed = (c.changed || []).map(name);
    const differ = changed.length ? ` · ${listWords(changed)} ${changed.length === 1 ? "differs" : "differ"} from approved` : "";
    const by = c.by ? ` by ${c.by}` : "";
    what = `“${c.name}” · published ${whenLine(c.published_at, nowMs)}${by}${differ}`;
  } else what = `the approved looks${inGit(live) ? ` ${LIVE_COPY.inGit}` : ""}${git}`;
  const text = simulated ? `${sim} · it runs ${what}` : `● Live on the show: ${what}`;
  const lines = [];
  if (live.fallback) {
    lines.push({ tone: "error", text: `The published looks couldn’t be loaded (${live.problem || "unreadable"}), so the show runs the approved looks. Publish again, or Revert to approved.` });
  }
  if (published && c.git_changed && !inGit(live)) lines.push({ tone: "warn", text: LIVE_COPY.gitChanged });
  const progress = repaintProgress(live.repaint, { nowMs, readAtMs });
  for (const s of live.scenes || []) {
    if (!s || !s.held) continue;
    const queued = progress && progress.queued && progress.scene === s.id ? ` · repaint in ${progress.left} s` : "";
    lines.push({ tone: "warn", text: `${s.name || s.id} shows at module level until its slots are repainted${queued}` });
  }
  if (progress && !progress.queued) lines.push({ tone: progress.tone, text: progress.text, repaint: true });
  if (live.canopy && live.canopy.power === false) lines.push({ tone: "note", text: LIVE_COPY.canopyOff });
  if (live.writable === false) lines.push({ tone: "note", text: LIVE_COPY.noData });
  const tone = live.fallback ? "error" : lines.some((l) => l.tone === "warn") ? "warn" : "ok";
  const prev = live.previous;
  const actions = {
    undo: prev ? { label: UNDO_PUBLISH, title: `Back to “${prev.name}”${prev.by ? ` (published by ${prev.by})` : ""}` } : null,
    revert: published || live.fallback || (c && c.kind === "version") ? { label: publishLabel(simulated, REVERT) } : null,
    history: (live.history || []).length ? { label: `History (${Math.min(live.history.length, 20)})` } : null,
  };
  return { tone, text, chip, lines, repaint: progress, actions };
}

/** LiveJSON.history (newest first, ≤ 20) as the drawer's rows, with what each row offers: Open in editor (the restore
 * route; "approved" for an approved row), Publish again (never the live row; a version only while it exists), Export
 * (a version that exists). */
export function historyRows(history, { scenes = {}, nowMs = Date.now() } = {}) {
  const nameOf = (id) => (scenes && scenes[id]) || id;
  return (Array.isArray(history) ? history : []).slice(0, 20).map((h) => {
    const kind = h && h.kind === "approved" ? "approved" : "version";
    const exists = kind === "approved" || (!!(h && h.version) && h.version_exists !== false);
    const repaint =
      h && h.repaint && typeof h.repaint === "object"
        ? Object.entries(h.repaint).map(([sid, r]) => {
            const total = ["applied", "skipped", "failed"].reduce((n, k) => n + (Number(r && r[k]) || 0), 0);
            return `${nameOf(sid)}: ${(r && r.slot) || "slot"} repainted on ${Number(r && r.applied) || 0} of ${plural(total, "module")}`;
          })
        : [];
    const outcome = str(h && h.outcome);
    return {
      id: str(h && h.id),
      kind,
      version: kind === "approved" ? APPROVED : str(h && h.version),
      name: kind === "approved" ? "The approved looks" : str(h && h.name) || str(h && h.version),
      when: whenLine(h && h.published_at, nowMs),
      by: str(h && h.by),
      scenes: h && Array.isArray(h.changed) && h.changed.length ? h.changed.map(nameOf).join(", ") : "no scene changed",
      repaint: repaint.join(" · "),
      outcome,
      outcomeText: OUTCOME_TEXT[outcome] || outcome,
      live: outcome === "live",
      canOpen: exists,
      canRepublish: outcome !== "live" && exists,
      canExport: kind === "version" && exists,
      gone: kind === "version" && !exists ? "Its Lab version was deleted." : "",
    };
  });
}

/** "The show changed: “<name>” was published at 12:10 by Alon." — only when the live publication's id changed between
 * two reads (null on the first read, and for the same id). */
export function liveNotice(prev, next, { nowMs = Date.now() } = {}) {
  if (!prev || !next) return null;
  const id = (l) => (l.current && l.current.id) || APPROVED;
  if (id(prev) === id(next)) return null;
  const c = next.current;
  const by = c && c.by ? ` by ${c.by}` : "";
  const at = c && Number.isFinite(Number(c.published_at)) ? ` at ${whenLine(c.published_at, nowMs)}` : "";
  if (!c || c.kind === "approved") return `The show changed: it runs the approved looks again${at}${by}.`;
  return `The show changed: “${c.name}” was published${at}${by}.`;
}

/** What the iPad's six cards show now: LiveJSON's scenes (name + swatches), else (no publishing here) the catalog's
 * approved ones — the iPad follows the live catalog, never a draft or a try-on. */
export function ipadCards(live, catalog) {
  const fromLive = !!(live && Array.isArray(live.scenes) && live.scenes.length);
  const scenes = fromLive ? live.scenes : (catalog && catalog.scenes) || [];
  return scenes.map((s) => {
    const palette = fromLive ? s.palette : s.approved && s.approved.palette;
    return { id: s.id, name: s.name, source: fromLive ? s.source || "approved" : "approved", palette: (Array.isArray(palette) ? palette : []).map((c) => String(c.hex)) };
  });
}

/** The editor's line about a painted scene's slot (the catalog's / preview's RepaintInfo): the server's sentence, plus
 * the B1 note when a new painted design can't be published here (commissioned slots, no repaint on this box yet). */
export function repaintNote(info) {
  if (!info || typeof info !== "object") return "";
  // B1's note (a design change over commissioned slots, no repaint here); never under B2's app lock, whose sentence says it
  const blocked = info.available === false && Array.isArray(info.commissioned) && info.commissioned.length > 0 && strs(info.app).length === 0;
  return [str(info.sentence), blocked ? LIVE_COPY.repaintOff : ""].filter(Boolean).join(" ");
}

/** The download's object for a version (GET /api/lab/versions/{id}): name, note, drafts and the config it was made
 * against, so another Lab (a laptop, staging, the show box) can open it. */
export function versionFile(versionJson, configSha = null) {
  const v = versionJson && typeof versionJson === "object" ? versionJson : {};
  return {
    kind: VERSION_FILE_KIND,
    format: 1,
    id: str(v.id),
    name: str(v.name),
    note: str(v.note),
    created_at: Number.isFinite(Number(v.created_at)) && v.created_at !== null ? Number(v.created_at) : null,
    config_sha: str(v.config_sha) || str(configSha) || null,
    drafts: v.drafts && typeof v.drafts === "object" ? JSON.parse(JSON.stringify(v.drafts)) : {},
  };
}

/** "lab-version-v0007-client-oct-22-v3-warmer-sunset.json" */
export function versionFileName(versionJson) {
  const v = versionJson || {};
  const slug = String(v.name || "")
    .normalize("NFKD")
    .replace(/[^\w\s-]/g, "")
    .trim()
    .toLowerCase()
    .replace(/[\s_-]+/g, "-")
    .slice(0, 48)
    .replace(/-+$/, "");
  return `lab-version-${String(v.id || "file").replace(/[^\w-]/g, "")}${slug ? `-${slug}` : ""}.json`;
}

/** The note a version opened from a file is saved with: "from <original name>" (≤ 60 characters). */
export function fromNote(name) {
  const n = String(name || "").trim() || "a version file";
  return `from ${n}`.slice(0, NOTE_MAX);
}

/** A version file's text → {name, note, drafts, configSha} for POST /api/lab/versions (the server validates them like
 * any save), or {error} for anything that isn't one. */
export function readVersionFile(text) {
  if (typeof text !== "string" || !text.trim()) return { error: "That file is empty: it isn’t a Lab version." };
  if (text.length > VERSION_FILE_MAX_BYTES) return { error: "That file is too large to be a Lab version." };
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    return { error: "That file isn’t a Lab version (it isn’t JSON)." };
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) return { error: "That file isn’t a Lab version." };
  if (data.kind !== undefined && data.kind !== VERSION_FILE_KIND) return { error: "That file isn’t a Lab version (it is another kind of file)." };
  const drafts = data.drafts;
  if (!drafts || typeof drafts !== "object" || Array.isArray(drafts)) return { error: "That file isn’t a Lab version (it has no drafts)." };
  for (const [sid, d] of Object.entries(drafts)) {
    if (!d || typeof d !== "object" || d.scene_id !== sid || !Array.isArray(d.palette) || !d.layout || typeof d.layout !== "object") {
      return { error: `That file’s draft for ${sid} isn’t a scene draft.` };
    }
  }
  const name = str(data.name).trim().slice(0, NAME_MAX);
  return { name, note: fromNote(name), drafts, configSha: str(data.config_sha) || null };
}

/** The toast after a publish: "Live now: <name>" ("the approved looks" after a revert). */
export function publishedToast(live, simulated = false) {
  const c = live && live.current;
  const what = c && live.source === "published" ? `“${c.name}”` : "the approved looks";
  return `${simulated ? "Live on this simulator now" : "Live now"}: ${what}`;
}
