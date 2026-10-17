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
 * - today / showBox: `paintFor(lit.modules[pos], k)` per head (the guest panel's rule); dark, off and unknown → UNLIT.
 */
export function viewPainter(preview, view, { heads = CANOPY.heads, width = CANOPY.width } = {}) {
  if (!preview || typeof preview !== "object") return { error: "Loading the preview…" };
  if (preview.error) return { error: String(preview.error) };
  const design = preview.design;
  const caption = tierCaption(view, preview);
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

export const BADGE_TEXT = { approved: "Approved", draft: "Draft", "in try-on": "In try-on", "approved design": "Approved design" };

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

/** The draft Present shows for (scene, entry): the working draft, the version's draft for the scene, or approved. */
export function presentDraft(entry, sceneId, { approved, working, versionDrafts }) {
  if (entry === WORKING) return (working && working[sceneId]) || (approved && approved[sceneId]) || null;
  if (entry === APPROVED || !entry) return (approved && approved[sceneId]) || null;
  const drafts = versionDrafts && versionDrafts[entry];
  if (!drafts) return null; // not loaded yet
  return drafts[sceneId] || (approved && approved[sceneId]) || null;
}

/** The label of a compare-set entry ("(unsaved)" only while the working draft has unsaved changes). */
export function entryLabel(entry, versions, { unsaved = false } = {}) {
  if (entry === APPROVED) return "Approved";
  if (entry === WORKING) return unsaved ? "Working draft (unsaved)" : "Working draft";
  const v = (versions || []).find((x) => x.id === entry);
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
export function labEyebrow(api, trial) {
  if (trial) return "DESIGN LAB · A TRY-ON IS ON THE LIGHTS";
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
