// The guest panel on the iPad stand (docs/design/guest-panel.md). ES module, no build step, vendored helpers only.
//
// Data: GET /api/kiosk/panel (the scenes, capabilities, `canopy` and `state`), and POST /api/kiosk/scene|
// brightness|power. A 200 on a write means "accepted"; the page shows the server's state (phase "changing" until the
// lights read back) and reconciles its optimistic switch and slider to it. The panel reads its state the moment the
// server pushes a change on the live socket (topic "canopy", used only as that nudge), and every 2 s regardless, so a
// dead socket costs speed, never correctness. Every request has a timeout: a hung read never stalls the panel.
// Now showing draws the canopy's 270 lights from `state.lit` (canopy-preview.mjs; what each panel may truthfully
// claim) with the 650 fins under them, each mixed from its nearest lights (D74), and the 30-light serpentine only
// when there is no such picture (no look yet).
// Honesty (CLAUDE.md, 07 §6): the feature sentence is always `state.feature` from the server and the subtitle prefers
// `state.message`; this page only adds the spec's calm fallbacks (panel-model.mjs COPY). A refusal shows the server's
// calm `detail`, never an HTTP code; anything else reads "Hang on, we're reconnecting."
// Resilience (07 §10): the page never navigates away; every fetch failure is caught.
// Ops: `?debug=1` (only when the server says the viewer is ops) adds a debug drawer; `?debug=1&frame=1` is the iPad
// frame preview. A kiosk-role device gets the plain panel whatever the URL says.
// Versions: index.html loads kiosk.js?v=… and kiosk.js its modules with the same tag; bump all four together, so a
// browser that cached an older copy never runs a new kiosk.js over an older module (or the reverse).

import { api, connectLive, setSimBadge } from "/static/app.js?v=breathe-2";
import {
  BRIGHTNESS_HOLD_MS,
  COPY,
  DRAG_CANCEL_PX,
  FRAME_SIZES,
  IDLE_MS,
  LOST_AFTER_FAILURES,
  POLL_MS,
  POLL_TIMEOUT_MS,
  POWER_HOLD_MS,
  SAME_CARD_DEBOUNCE_MS,
  WRITE_TIMEOUT_MS,
  attractDue,
  brightnessToSend,
  cardsKey,
  cardState,
  cardTap,
  clampLevel,
  cooldownChip,
  cooldownLeft,
  debugAllowed,
  fractionForLevel,
  frameScale,
  headerSubtitle,
  headerTint,
  hexToRgb,
  levelFromX,
  nowShowing,
  pendingValue,
  phaseOf,
  reconcile,
  isWaitRefusal,
  refusalToast,
  rgb,
  sceneLabel,
  snakeLayout,
  stripGradient,
  switchView,
  waitToastOver,
} from "/kiosk/panel-model.mjs?v=breathe-2";
import {
  ariaSummary,
  finPaint,
  geometryKey,
  layout,
  levelOpacity,
  moduleOpacity,
  motionActive,
  motionColors,
  paintFor,
} from "/kiosk/canopy-preview.mjs?v=breathe-2";

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
const reducedMotion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const TOAST_MS = 3500;
const KNOB_HALF = 32;
const TRACK_LEAD = 20; // always-filled track left of the knob at the floor, so 10% never looks like off

const ui = {
  panel: null, // the last GET /api/kiosk/panel
  readAt: 0, // local ms when `panel.state` was read (cooldown countdown)
  pendingId: null, // the scene this panel asked for, until the server has taken it and left "changing"
  pendingPosted: 0, // local ms when the server accepted the pending scene (0 = not yet)
  lastTapAt: new Map(),
  powerHold: null,
  brightHold: null,
  dragging: null, // {pointerId, level}
  failures: 0,
  lost: false,
  lastTouch: Date.now(),
  attract: false,
  debug: false,
  latencyMs: null,
  lastPollAt: null,
  restingDetail: "",
  stopped: false, // the iPad frame preview took over the page
  writeSeq: 0, // bumped when a write is sent and when its answer lands: a poll that spans either is stale
  serverClock: null, // {serverNow, at}: the server's wall clock (s) as last read, and the local ms it was read at
  waitToast: null, // {at}: the toast up is a "wait for staff" refusal (a try-on, the rehearsal) that landed at `at`
};

/** The server's wall clock now, estimated: server_now + the local time since it was read (motion is phase-synced). */
function serverNowEst() {
  const c = ui.serverClock;
  return c ? c.serverNow + (Date.now() - c.at) / 1000 : Date.now() / 1000;
}

function readServerClock(serverNow) {
  if (Number.isFinite(serverNow)) ui.serverClock = { serverNow, at: Date.now() };
}

// ---- tiny DOM helpers -------------------------------------------------------------------------------------------
function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const kid of kids) if (kid) node.append(kid);
  return node;
}

function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

const rgba = (hex, a) => `rgba(${hexToRgb(hex).join(", ")}, ${a})`;
function tintBackground([a, b], alpha = 0.3) {
  return (
    `radial-gradient(60% 140% at 18% 30%, ${rgba(a, alpha)} 0%, transparent 70%), ` +
    `radial-gradient(55% 140% at 82% 70%, ${rgba(b, alpha)} 0%, transparent 70%)`
  );
}

/**
 * A pressable control: `.is-pressed` while the finger is down (iOS fires :active late), cancelled when the finger
 * moves more than 10 px; activates on click (immediate with touch-action: manipulation).
 */
function pressable(node, onActivate) {
  let start = null;
  let cancelled = false;
  const clear = () => node.classList.remove("is-pressed");
  node.addEventListener("pointerdown", (e) => {
    if (!e.isPrimary) return;
    start = { x: e.clientX, y: e.clientY };
    cancelled = false;
    node.classList.add("is-pressed");
  });
  node.addEventListener("pointermove", (e) => {
    if (!start || !e.isPrimary) return;
    if (Math.hypot(e.clientX - start.x, e.clientY - start.y) > DRAG_CANCEL_PX) {
      cancelled = true;
      clear();
    }
  });
  for (const type of ["pointerup", "pointercancel", "pointerleave"]) {
    node.addEventListener(type, () => {
      if (type !== "pointerup") cancelled = cancelled || type === "pointercancel";
      start = null;
      clear();
    });
  }
  node.addEventListener("click", (e) => {
    if (cancelled) {
      cancelled = false;
      e.preventDefault();
      return;
    }
    onActivate(e);
  });
}

// ---- toast --------------------------------------------------------------------------------------------------------
let toastTimer = null;
let toastTick = null;
function toast(text, { retryAfterS = 0, sticky = false, spinner = false } = {}) {
  const box = $("toast");
  ui.waitToast = null; // refused() marks its own after this
  clearTimeout(toastTimer);
  clearInterval(toastTick);
  box.classList.remove("is-leaving", "is-multiline");
  $("toast-spinner").hidden = !spinner;
  $("toast-text").textContent = text;
  let count = box.querySelector(".toast-count");
  if (count) count.remove();
  const until = Date.now() + Math.max(TOAST_MS, retryAfterS * 1000);
  if (retryAfterS > 0) {
    count = el("span", { class: "chip toast-count" });
    box.append(count);
    const tick = () => {
      const n = Math.max(0, Math.ceil((until - Date.now()) / 1000));
      count.textContent = `${n} s`;
    };
    tick();
    toastTick = setInterval(tick, 250);
  }
  box.hidden = false;
  box.style.animation = "none";
  void box.offsetWidth; // restart the entrance
  box.style.animation = "";
  if (box.offsetHeight > 64) box.classList.add("is-multiline");
  if (!sticky) toastTimer = setTimeout(hideToast, until - Date.now());
}

function hideToast() {
  const box = $("toast");
  ui.waitToast = null;
  clearTimeout(toastTimer);
  clearInterval(toastTick);
  if (box.hidden) return;
  box.classList.add("is-leaving");
  toastTimer = setTimeout(() => {
    box.hidden = true;
    box.classList.remove("is-leaving");
  }, 180);
}

// ---- building ----------------------------------------------------------------------------------------------------
let builtFor = "";
function buildCards(scenes) {
  const key = cardsKey(scenes); // contents, not only ids: a changed catalog redraws its cards
  if (key === builtFor) return;
  builtFor = key;
  const grid = $("grid");
  grid.replaceChildren();
  for (const scene of scenes) {
    const check = svg("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" });
    check.append(svg("path", { d: "M5 12.5l4.5 4.5L19 7.5" }));
    const mark = el("span", { class: "mark", "aria-hidden": "true" }, check, el("span", { class: "spinner" }));
    // the palette strip: the scene's colours in order (Now showing is the canopy's own picture)
    const strip = el("span", { class: "strip", "aria-hidden": "true" });
    strip.style.background = stripGradient(scene.frame);
    const swatches = el("span", { class: "swatches", "aria-hidden": "true" });
    for (const p of scene.palette || []) {
      const dot = el("span", { class: "swatch" });
      dot.style.background = p.hex;
      swatches.append(dot);
    }
    const card = el(
      "button",
      { class: "card scene", type: "button", "data-scene": scene.id, "aria-label": sceneLabel(scene), "aria-pressed": "false" },
      mark,
      strip,
      el("span", { class: "scene-name", text: scene.name }),
      el("span", { class: "scene-mood", text: scene.mood }),
      swatches,
    );
    pressable(card, () => chooseScene(scene.id));
    grid.append(card);
  }
  buildAttractStrips(scenes);
}

/** The 3 × 10 serpentine of the 30 lights, with half-round U-turns (the app's custom-pattern preview). */
const SNAKE = { t: 30, gap: 2, rowGap: 22 };
function buildSnake() {
  const { t, gap, rowGap } = SNAKE;
  const cap = t + rowGap / 2; // a U-turn's outer radius
  const rowW = 10 * t + 9 * gap;
  const w = 2 * cap + rowW;
  const h = 3 * t + 2 * rowGap;
  const snake = $("snake");
  snake.setAttribute("viewBox", `0 0 ${w} ${h}`);
  snake.replaceChildren();
  const tiles = [];
  for (const { i, row, col } of snakeLayout(30, 10)) {
    const r = svg("rect", { x: cap + col * (t + gap), y: row * (t + rowGap), width: t, height: t, rx: 3 });
    tiles[i] = r;
    snake.append(r);
  }
  // U-turns: right between rows 0→1 (lights 9|10), left between rows 1→2 (lights 19|20). Each half takes the colour
  // of the light it continues.
  const half = (cx, cy, side, upper) => {
    const ro = t + rowGap / 2;
    const ri = rowGap / 2;
    const sx = side === "right" ? 1 : -1;
    const y0 = upper ? cy - ro : cy + ro;
    const yi = upper ? cy - ri : cy + ri;
    const sweepOut = (side === "right") === upper ? 1 : 0;
    return svg("path", {
      d:
        `M${cx} ${y0} A${ro} ${ro} 0 0 ${sweepOut} ${cx + sx * ro} ${cy} ` +
        `L${cx + sx * ri} ${cy} A${ri} ${ri} 0 0 ${1 - sweepOut} ${cx} ${yi} Z`,
    });
  };
  const rightX = cap + rowW + 1;
  const leftX = cap - 1;
  const caps = [
    { node: half(rightX, t + rowGap / 2, "right", true), light: 9 },
    { node: half(rightX, t + rowGap / 2, "right", false), light: 10 },
    { node: half(leftX, 2 * t + rowGap * 1.5, "left", true), light: 19 },
    { node: half(leftX, 2 * t + rowGap * 1.5, "left", false), light: 20 },
  ];
  for (const c of caps) snake.append(c.node);
  return { tiles, caps };
}
let snakeParts = null;

function paintSnake(frame) {
  snakeParts = snakeParts || buildSnake();
  snakeParts.tiles.forEach((r, i) => r.setAttribute("fill", rgb(frame[i] || [211, 210, 216])));
  for (const c of snakeParts.caps) c.node.setAttribute("fill", rgb(frame[c.light] || [211, 210, 216]));
}

/**
 * The canopy's 270 lights on its nine panels (canopy-preview.mjs), drawn in canopy inches and turned to the view a
 * guest at the stand sees overhead, with the 650 fins under them and the apron's outline when the payload has them
 * (D74). Laid out only when the geometry or view changes (a new poll of the same canopy compares its key first);
 * painted on every render, and every animation frame while `lit.motion` still has a frame to come. A fin's paint
 * follows its four nearest dots, so the fins are repainted only on a frame where some dot's paint changed.
 */
const canopyView = {
  key: "",
  canopy: null, // the geometry object last laid out: a ripple or stepped run repaints every frame from the same one
  dots: [],
  panels: [],
  fins: [], // {fin, node, paint}
  motionKey: null,
  prevLit: null,
  shown: null,
  raf: 0,
  label: "",
};
function buildCanopy(canopy) {
  if (canopy === canopyView.canopy) return; // same read: skip the key and the layout on every frame
  canopyView.canopy = canopy;
  const key = geometryKey(canopy);
  if (key === canopyView.key) return; // a new poll of the same geometry: no layout, nothing rebuilt
  const drawing = layout(canopy);
  const root = $("canopy-preview");
  root.setAttribute("viewBox", `0 0 ${drawing.width} ${drawing.height}`);
  root.replaceChildren();
  if (drawing.apron > 0) {
    const o = drawing.outline;
    root.append(svg("rect", { class: "outline", x: o.x, y: o.y, width: o.w, height: o.h, rx: 1.5 }));
  }
  canopyView.panels = drawing.panels.map((p) => {
    const rect = svg("rect", { class: "panel", x: p.x, y: p.y, width: p.w, height: p.h, rx: 1.5 });
    root.append(rect);
    return rect;
  });
  canopyView.dots = drawing.dots.map((d) => {
    const node = svg("circle", { class: "dot", cx: d.cx, cy: d.cy, r: d.r });
    root.append(node);
    return { pos: d.pos, k: d.k, node, paint: "" };
  });
  canopyView.fins = drawing.fins.map((f) => {
    const node = svg("line", {
      class: "fin",
      x1: f.x1,
      y1: f.y1,
      x2: f.x2,
      y2: f.y2,
      "stroke-width": drawing.strokeWidth,
    });
    root.append(node);
    return { fin: f, node, paint: "" };
  });
  canopyView.key = key;
}

function paintCanopy(canopy, lit) {
  buildCanopy(canopy);
  const now = serverNowEst();
  const motion = lit.motion || null;
  const motionKey = motion ? `${motion.kind}|${motion.t0}` : null;
  if (motionKey !== canopyView.motionKey) {
    // a new ripple or run starts from the picture on screen now
    canopyView.prevLit = canopyView.shown ? { modules: canopyView.shown } : null;
    canopyView.motionKey = motionKey;
  }
  const modules = motionColors(motion, canopyView.prevLit, lit, now);
  canopyView.shown = modules;
  const byPos = new Map(modules.map((m) => [m.pos, m]));
  const reduced = reducedMotion();
  const floor = canopy.view && canopy.view.dim_floor;
  const opacity = levelOpacity(lit.level, floor); // the canopy's level: a fin's fallback when a head has none
  const paints = new Array(canopyView.dots.length); // every head's paint, with its own opacity, for the fins
  let dirty = false;
  canopyView.dots.forEach((dot, i) => {
    // a breathing run's frame gives each panel its own level (D75); the fins it lights follow it
    const dotOpacity = moduleOpacity(byPos.get(dot.pos), lit, floor);
    const p = paintFor(byPos.get(dot.pos), dot.k, { reduced });
    const lightOn = p.kind === "lit" || p.kind === "changing";
    const paint = `${p.kind}|${p.fill}|${p.pulse}|${lightOn ? dotOpacity : 1}`;
    paints[i] = { ...p, opacity: lightOn ? dotOpacity : 1 };
    if (paint === dot.paint) return; // untouched dots keep their transition and pulse running
    dirty = true;
    dot.paint = paint;
    dot.node.setAttribute("fill", p.fill || "none");
    dot.node.setAttribute("fill-opacity", String(lightOn ? dotOpacity : 1));
    dot.node.setAttribute("class", `dot is-${p.kind}${p.pulse ? " changing" : ""}`);
  });
  // A fin's paint is a function of its near dots' paints: a frame where no dot changed changes no fin.
  if (dirty) {
    for (const f of canopyView.fins) {
      const q = finPaint(f.fin, paints, { opacity, reduced });
      const key = `${q.kind}|${q.stroke}|${q.opacity}|${q.pulse}`;
      if (key === f.paint) continue;
      f.paint = key;
      f.node.setAttribute("stroke", q.stroke || "none");
      f.node.setAttribute("stroke-opacity", String(q.opacity));
      f.node.setAttribute("class", `fin is-${q.kind}${q.pulse ? " changing" : ""}`);
    }
  }
  const label = ariaSummary({ modules });
  if (label !== canopyView.label) {
    canopyView.label = label;
    $("canopy-preview").setAttribute("aria-label", label);
  }
  if (motionActive(motion, now) && !canopyView.raf) canopyView.raf = requestAnimationFrame(tickCanopy);
}

/** One animation frame of a ripple or a stepped run; the loop stops once the motion has no frame to come. */
function tickCanopy() {
  canopyView.raf = 0;
  const panel = ui.panel;
  const lit = panel && panel.state && panel.state.lit;
  if (!panel || !panel.canopy || !lit || ui.stopped) return;
  paintCanopy(panel.canopy, lit);
}

function stopCanopy() {
  if (canopyView.raf) cancelAnimationFrame(canopyView.raf);
  canopyView.raf = 0;
}

// ---- rendering ----------------------------------------------------------------------------------------------------
let tintKey = null;
function setTint(layers, colors, alpha = 0.3) {
  const key = colors ? colors.join() : "";
  const [a, b] = layers.map($);
  if (layers.key === key) return;
  layers.key = key;
  const next = a.classList.contains("is-on") ? b : a;
  const prev = next === a ? b : a;
  if (colors) {
    next.style.background = tintBackground(colors, alpha);
    next.classList.add("is-on");
  }
  prev.classList.remove("is-on");
}
const HEADER_LAYERS = Object.assign(["tint-a", "tint-b"], { key: null });
const ATTRACT_LAYERS = Object.assign(["attract-a", "attract-b"], { key: null });

/** The server's value, or the guest's own change still on its way to the lights (`state.next`). */
function shownValue(state, kind) {
  const queued = pendingValue(state, kind);
  return queued !== undefined ? queued : state[kind];
}

function displayedPower(state) {
  const r = reconcile(shownValue(state, "power"), ui.powerHold, Date.now());
  ui.powerHold = r.hold;
  return r.value;
}

function displayedBrightness(state) {
  if (ui.dragging) return ui.dragging.level;
  const r = reconcile(shownValue(state, "brightness"), ui.brightHold, Date.now());
  ui.brightHold = r.hold;
  return r.value;
}

function render() {
  const panel = ui.panel;
  if (!panel) return;
  const state = panel.state || {};
  const phase = phaseOf(state);
  const now = Date.now();
  const scenes = panel.scenes || [];
  document.body.dataset.view = "panel";
  document.body.dataset.lost = String(ui.lost);
  $("app").dataset.phase = phase;

  // resting covers everything; the panel stays underneath
  $("resting").hidden = phase !== "resting";
  if (phase === "resting") {
    $("resting-text").textContent = state.message || COPY.resting;
    const last = scenes.find((s) => s.id === state.scene) || scenes[0];
    if (last) $("resting-glow").style.background = tintBackground(headerTint(last, true), 0.9);
  }

  // header
  const power = displayedPower(state);
  const sub = headerSubtitle({ ...state, power });
  $("hdr-sub-text").textContent = sub.text;
  $("hdr-spinner").hidden = !sub.spinner;
  const sw = switchView(power);
  const powerBtn = $("power");
  powerBtn.setAttribute("aria-checked", String(sw.checked));
  powerBtn.dataset.unknown = String(sw.unknown);
  $("power-label").textContent = sw.label;
  const controlsOff = phase === "paused" || ui.lost || !panel.capabilities?.power;
  powerBtn.setAttribute("aria-disabled", String(controlsOff));
  const showing = nowShowing(scenes, { ...state, power });
  setTint(HEADER_LAYERS, headerTint(showing.scene, power));

  // cards
  const left = cooldownLeft(state.cooldown_remaining_s, ui.readAt, now);
  for (const card of $("grid").children) {
    const id = card.dataset.scene;
    let s = cardState(id, { ...state, power }, ui.pendingId);
    if (phase === "cooldown" && left <= 0 && s === "disabled") s = "idle";
    if (!panel.capabilities?.scene && s !== "selected") s = "disabled";
    card.dataset.state = s;
    card.setAttribute("aria-pressed", String(s === "selected" || s === "pending"));
    card.setAttribute("aria-disabled", String(s === "disabled"));
  }
  const chip = $("chip");
  chip.hidden = !(phase === "cooldown" && left > 0);
  if (!chip.hidden) chip.textContent = cooldownChip(left);

  // now showing: the canopy's picture when the server sends one, else the serpentine
  $("now-name").textContent = showing.name;
  $("now-desc").textContent = showing.description;
  const drawCanopy = Boolean(showing.lit && panel.canopy);
  // (an svg has no `hidden` property: toggle the attribute, which kiosk.css's [hidden] rule hides)
  $("canopy-preview").toggleAttribute("hidden", !drawCanopy);
  $("snake").toggleAttribute("hidden", drawCanopy);
  if (drawCanopy) paintCanopy(panel.canopy, showing.lit);
  else {
    stopCanopy();
    paintSnake(showing.frame || nowShowing(scenes, { ...state, power, lit: null }).frame);
  }

  // brightness
  const range = panel.brightness_range || [10, 100];
  const level = displayedBrightness(state);
  const slider = $("slider");
  slider.setAttribute("aria-valuemin", range[0]);
  slider.setAttribute("aria-valuemax", range[1]);
  const sliderOff = phase === "paused" || ui.lost || !panel.capabilities?.brightness;
  slider.setAttribute("aria-disabled", String(sliderOff));
  paintSlider(level, range);

  // feature line: the server's sentence, or the page's neutral fallback (both in index.html)
  const kicker = $("feature-kicker");
  const text = $("feature-text");
  const feature = typeof state.feature === "string" && state.feature ? state.feature : null;
  kicker.textContent = feature ? kicker.dataset.on : kicker.dataset.off;
  const sentence = feature || text.dataset.fallback;
  if (text.textContent !== sentence) text.textContent = sentence;

  // paused
  $("paused").hidden = phase !== "paused";
  if (phase === "paused") $("paused-body").textContent = state.message || COPY.paused;

  if (ui.debug) renderDebug();
}

function paintSlider(level, range) {
  const slider = $("slider");
  const known = Number.isFinite(level);
  const value = known ? clampLevel(level, range) : range[0];
  const f = fractionForLevel(value, range);
  const pos = `calc(${TRACK_LEAD + KNOB_HALF}px + ${f} * (100% - ${TRACK_LEAD + 2 * KNOB_HALF}px))`;
  $("slider-knob").style.left = pos;
  $("slider-fill").style.width = pos;
  $("bright-value").textContent = known ? String(value) : "—";
  $("bright-unit").hidden = !known;
  slider.setAttribute("aria-valuenow", String(value));
  slider.setAttribute("aria-valuetext", known ? `${value} percent` : "Unknown");
}

// ---- data ---------------------------------------------------------------------------------------------------------
/** Drop the pending scene once the server has accepted it and is no longer changing (30 s at most, as a safety net). */
function settlePending(state) {
  if (!ui.pendingId || !ui.pendingPosted || !state) return;
  if (phaseOf(state) !== "changing" || Date.now() - ui.pendingPosted > 30000) ui.pendingId = null;
}

/** A waiting toast's countdown is only the server's upper bound: once a state read begun after it says nothing
 * waits (the try-on or rehearsal ended), drop it, so the guest is never told to wait for a canopy that is free. */
function dropWaitToast(state, readStartedAt) {
  if (waitToastOver(ui.waitToast, state, readStartedAt)) hideToast();
}

function applyState(state, serverNow, sentAt = 0) {
  ui.writeSeq += 1;
  if (!ui.panel || !state) return;
  readServerClock(serverNow);
  ui.panel.state = state;
  ui.readAt = Date.now();
  settlePending(state);
  dropWaitToast(state, sentAt);
  render();
}

/** `api()` with a timeout: a request that hangs is aborted (and fails like any network error). */
async function timed(path, ms, options = {}) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ms);
  try {
    return await api(path, { ...options, signal: ctl.signal });
  } finally {
    clearTimeout(timer);
  }
}

let pollTimer = null;
let polling = false; // one read in flight at a time
let pollAgain = false; // a nudge landed during a read: read again as soon as it ends
async function poll() {
  clearTimeout(pollTimer);
  if (polling) {
    pollAgain = true;
    return;
  }
  polling = true;
  const started = performance.now();
  const startedAt = Date.now();
  const seq = ui.writeSeq;
  try {
    const panel = await timed("/api/kiosk/panel", POLL_TIMEOUT_MS);
    ui.latencyMs = Math.round(performance.now() - started);
    ui.lastPollAt = Date.now();
    readServerClock(panel.server_now);
    const first = !ui.panel;
    // A write was sent or answered while this poll was in flight: its state may predate the write, so keep the
    // write's answer (otherwise a stale "ready" would clear the pending card and let a second scene through).
    const stale = !first && seq !== ui.writeSeq;
    if (stale) panel.state = ui.panel.state;
    ui.panel = panel;
    if (!stale) ui.readAt = Date.now();
    settlePending(panel.state);
    buildCards(panel.scenes || []);
    if (ui.failures >= LOST_AFTER_FAILURES || ui.lost) hideToast();
    if (!stale) dropWaitToast(panel.state, startedAt);
    ui.failures = 0;
    ui.lost = false;
    if (first && onFirstPanel(panel) === "frame") {
      ui.stopped = true; // the frame preview replaced this page; its iframe runs its own panel
      return;
    }
    if (first) wireLive();
    render();
    if (first) {
      // controls paint in their real state first; only later changes animate
      requestAnimationFrame(() => requestAnimationFrame(() => document.body.classList.remove("no-anim")));
    }
  } catch (err) {
    ui.failures += 1;
    if (err && err.status === 503 && ui.panel) {
      // the panel can't read its scenes or the show is down: rest calmly
      ui.panel.state = { ...ui.panel.state, phase: "resting", message: (err.body && err.body.detail) || "" };
      render();
    } else if (ui.failures >= LOST_AFTER_FAILURES) {
      if (!ui.panel) {
        document.body.dataset.view = "panel";
        $("resting").hidden = false;
        $("resting-text").textContent = COPY.reconnecting;
      } else if (!ui.lost) {
        ui.lost = true;
        toast(COPY.reconnecting, { sticky: true, spinner: true });
        render();
      }
    }
  } finally {
    polling = false;
    const again = pollAgain;
    pollAgain = false;
    if (!document.hidden && !ui.stopped) pollTimer = setTimeout(poll, again ? 0 : POLL_MS);
  }
}

/** The server pushed a change: read the panel's state now rather than at the next 2 s poll. */
function pollSoon() {
  if (!ui.stopped && !document.hidden) poll();
}

function wireLive() {
  // only a nudge: the event's own data is the ops view of the engine, never shown here
  connectLive({ topics: ["canopy"], onEvent: () => pollSoon() });
}

async function post(path, body) {
  ui.writeSeq += 1;
  return timed(path, WRITE_TIMEOUT_MS, { method: "POST", body });
}

function refused(err) {
  const t = refusalToast(err);
  toast(t.text, { retryAfterS: t.retryAfterS });
  if (isWaitRefusal(err)) ui.waitToast = { at: Date.now() };
}

// ---- actions ------------------------------------------------------------------------------------------------------
async function chooseScene(id) {
  const panel = ui.panel;
  if (!panel || ui.lost) return;
  const now = Date.now();
  if (now - (ui.lastTapAt.get(id) || 0) < SAME_CARD_DEBOUNCE_MS) return;
  ui.lastTapAt.set(id, now);
  const state = panel.state || {};
  if (!panel.capabilities?.scene) return;
  const decision = cardTap(id, state, {
    pendingId: ui.pendingId,
    cooldownS: cooldownLeft(state.cooldown_remaining_s, ui.readAt, now),
  });
  if (decision.toast) return toast(decision.toast);
  if (!decision.send) return;
  ui.pendingId = id;
  ui.pendingPosted = 0;
  render();
  try {
    const sentAt = Date.now();
    const res = await post("/api/kiosk/scene", { scene: id });
    ui.pendingPosted = Date.now();
    ui.powerHold = null; // a scene turns the lights on; follow the server
    applyState(res.state, res.server_now, sentAt);
  } catch (err) {
    ui.pendingId = null;
    render();
    refused(err);
  }
}

async function togglePower() {
  const panel = ui.panel;
  if (!panel || ui.lost || !panel.capabilities?.power) return;
  if (phaseOf(panel.state) === "paused") return toast(panel.state.message || COPY.paused);
  const current = displayedPower(panel.state || {});
  const on = current !== true;
  ui.powerHold = { value: on, until: Date.now() + POWER_HOLD_MS };
  render();
  try {
    const sentAt = Date.now();
    const res = await post("/api/kiosk/power", { on });
    applyState(res.state, res.server_now, sentAt);
  } catch (err) {
    ui.powerHold = null;
    render();
    refused(err);
  }
}

async function sendBrightness(level) {
  try {
    const sentAt = Date.now();
    const res = await post("/api/kiosk/brightness", { level });
    if (ui.brightHold && ui.brightHold.value === level) ui.brightHold.until = Date.now() + BRIGHTNESS_HOLD_MS;
    applyState(res.state, res.server_now, sentAt);
  } catch (err) {
    ui.brightHold = null;
    render();
    refused(err);
  }
}

function sliderBlocked() {
  const panel = ui.panel;
  if (!panel || ui.lost || !panel.capabilities?.brightness) return true;
  const phase = phaseOf(panel.state);
  if (phase === "paused") {
    toast(panel.state.message || COPY.paused);
    return true;
  }
  return false;
}

function levelAt(clientX) {
  const box = $("slider").getBoundingClientRect();
  const left = box.left + TRACK_LEAD + KNOB_HALF;
  return levelFromX(clientX, left, box.width - TRACK_LEAD - 2 * KNOB_HALF, ui.panel.brightness_range || [10, 100]);
}

function wireSlider() {
  const slider = $("slider");
  slider.addEventListener("pointerdown", (e) => {
    if (ui.dragging || !e.isPrimary || sliderBlocked()) return;
    e.preventDefault();
    try {
      slider.setPointerCapture(e.pointerId);
    } catch {
      /* a synthetic pointer can't be captured */
    }
    const level = levelAt(e.clientX);
    ui.dragging = { pointerId: e.pointerId, level };
    slider.classList.add("is-dragging");
    paintSlider(level, ui.panel.brightness_range);
  });
  slider.addEventListener("pointermove", (e) => {
    if (!ui.dragging || e.pointerId !== ui.dragging.pointerId) return;
    const level = levelAt(e.clientX);
    if (level === ui.dragging.level) return;
    ui.dragging.level = level;
    paintSlider(level, ui.panel.brightness_range);
  });
  const end = (e) => {
    if (!ui.dragging || e.pointerId !== ui.dragging.pointerId) return;
    const level = ui.dragging.level;
    ui.dragging = null;
    slider.classList.remove("is-dragging");
    const send = brightnessToSend(level, ui.panel.state);
    ui.brightHold = { value: level, until: Date.now() + BRIGHTNESS_HOLD_MS };
    if (send !== null) sendBrightness(send); // once, on release (see brightnessToSend)
    render();
  };
  slider.addEventListener("pointerup", end);
  slider.addEventListener("pointercancel", end);
  slider.addEventListener("lostpointercapture", end);
  slider.addEventListener("keydown", (e) => {
    const step = { ArrowRight: 5, ArrowUp: 5, ArrowLeft: -5, ArrowDown: -5 }[e.key];
    if (!step || sliderBlocked()) return;
    e.preventDefault();
    const range = ui.panel.brightness_range || [10, 100];
    const from = displayedBrightness(ui.panel.state || {});
    const level = clampLevel((Number.isFinite(from) ? from : range[0]) + step, range);
    const send = brightnessToSend(level, ui.panel.state);
    ui.brightHold = { value: level, until: Date.now() + BRIGHTNESS_HOLD_MS };
    render();
    if (send !== null) sendBrightness(send);
  });
}

// ---- idle attract --------------------------------------------------------------------------------------------------
let attractTimer = null;
let attractIndex = 0;
function buildAttractStrips(scenes) {
  const row = $("attract-strips");
  row.replaceChildren(
    ...scenes.map((s) => {
      const strip = el("span");
      strip.style.background = stripGradient(s.frame);
      return strip;
    }),
  );
}

function attractStep() {
  const scenes = (ui.panel && ui.panel.scenes) || [];
  if (!scenes.length) return;
  attractIndex = (attractIndex + 1) % scenes.length;
  const scene = scenes[attractIndex];
  setTint(ATTRACT_LAYERS, headerTint(scene, true), 0.42);
  $("attract-mood").textContent = scene.mood;
  [...$("attract-strips").children].forEach((s, i) => s.classList.toggle("is-on", i === attractIndex));
}

function showAttract() {
  if (ui.attract || !ui.panel) return;
  ui.attract = true;
  const box = $("attract");
  box.classList.remove("is-leaving");
  box.hidden = false;
  hideToast();
  attractIndex = -1;
  attractStep();
  attractTimer = setInterval(attractStep, reducedMotion() ? 8000 : 6000);
}

let swallowUntil = 0;
function dismissAttract() {
  if (!ui.attract) return;
  ui.attract = false;
  clearInterval(attractTimer);
  const box = $("attract");
  box.classList.add("is-leaving");
  setTimeout(() => {
    if (!ui.attract) box.hidden = true;
  }, 240);
}

function wireIdle() {
  // The first touch on the attract screen only dismisses it; it never reaches a control underneath.
  document.addEventListener(
    "pointerdown",
    (e) => {
      ui.lastTouch = Date.now();
      if (ui.attract) {
        e.preventDefault();
        e.stopPropagation();
        swallowUntil = Date.now() + 600;
        dismissAttract();
      }
    },
    { capture: true },
  );
  document.addEventListener(
    "click",
    (e) => {
      if (Date.now() < swallowUntil) {
        e.preventDefault();
        e.stopPropagation();
      }
    },
    { capture: true },
  );
  setInterval(() => {
    if (ui.stopped) return;
    const phase = ui.panel ? phaseOf(ui.panel.state) : "ready";
    if (!ui.attract && !ui.lost && !ui.dragging && attractDue(ui.lastTouch, Date.now(), phase, IDLE_MS)) {
      if (!document.querySelector(".dbg")) showAttract();
    }
    if (ui.attract && phase !== "ready" && phase !== "cooldown") dismissAttract();
    if (ui.panel && phaseOf(ui.panel.state) === "cooldown") render(); // the countdown chip
  }, 1000);
}

// ---- touch hygiene --------------------------------------------------------------------------------------------------
function wireTouch() {
  document.addEventListener("touchstart", () => {}, { passive: true }); // iOS: makes :active prompt
  for (const type of ["gesturestart", "gesturechange", "gestureend"]) {
    document.addEventListener(type, (e) => e.preventDefault(), { passive: false });
  }
  document.addEventListener("dblclick", (e) => e.preventDefault(), { passive: false });
  document.addEventListener("contextmenu", (e) => {
    if (!e.target.closest || !e.target.closest(".dbg")) e.preventDefault();
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !ui.stopped) poll();
  });
  $("toast").addEventListener("click", () => {
    if (!ui.lost) hideToast();
  });
}

// ---- ops debug --------------------------------------------------------------------------------------------------
const BUG_ICON =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="7" width="8" height="12" rx="4"/><path d="M9.5 7.5 8 5M14.5 7.5 16 5M4 11h4M16 11h4M4 17h4M16 17h4M12 11v8"/></svg>';
const X_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';

const dbg = { open: false, data: null, forbidden: false, openRows: new Set(), timer: null, error: "" };

function mountDebug() {
  ui.debug = true;
  const btn = el("button", { class: "dbg-btn", type: "button", "aria-label": "Open the debug drawer" });
  btn.innerHTML = BUG_ICON;
  pressable(btn, openDebug);
  $("bar").prepend(btn);
}

async function fetchDebug() {
  try {
    dbg.data = await api("/api/kiosk/debug");
    dbg.forbidden = false;
    dbg.error = "";
  } catch (err) {
    dbg.forbidden = err && err.status === 403;
    dbg.error = dbg.forbidden ? "" : String((err && err.message) || err);
  }
  renderDebug();
}

function openDebug() {
  if (dbg.open) return;
  dbg.open = true;
  const scrim = el("div", { class: "dbg-scrim" });
  scrim.addEventListener("click", closeDebug);
  const close = el("button", { class: "dbg-x", type: "button", "aria-label": "Close" });
  close.innerHTML = X_ICON;
  close.addEventListener("click", closeDebug);
  const drawer = el(
    "aside",
    { class: "dbg", role: "dialog", "aria-label": "Debug" },
    el("div", { class: "dbg-head" }, close, el("p", { text: "Debug" }), el("span")),
    el("div", { class: "dbg-body", id: "dbg-body" }),
  );
  document.body.append(scrim, drawer);
  fetchDebug();
  dbg.timer = setInterval(fetchDebug, POLL_MS);
}

function closeDebug() {
  dbg.open = false;
  clearInterval(dbg.timer);
  for (const n of document.querySelectorAll(".dbg, .dbg-scrim")) n.remove();
}

function kv(pairs) {
  const dl = el("dl", { class: "dbg-kv" });
  for (const [k, v] of pairs) dl.append(el("dt", { text: k }), el("dd", {}, v instanceof Node ? v : document.createTextNode(String(v))));
  return dl;
}

const card = (title, ...kids) => el("section", { class: "dbg-card" }, el("h3", { text: title }), ...kids);
const fmtAt = (at) => (typeof at === "number" ? new Date(at * 1000).toLocaleTimeString() : String(at ?? ""));

function renderDebug() {
  const body = $("dbg-body");
  if (!body || !dbg.open) return;
  const panel = ui.panel || {};
  const d = dbg.data || {};
  const keepScroll = body.scrollTop;
  const simChip = el("span", { class: `dbg-chip${panel.simulated ? "" : " is-live"}`, text: panel.simulated ? "Simulator" : "LIVE" });
  const sections = [
    card(
      "Target",
      kv([
        ["target", panel.target ?? "—"],
        ["label", panel.target_label ?? "—"],
        ["environment", d.environment ?? "—"],
        ["runtime", simChip],
        ["viewer", panel.viewer ?? "—"],
      ]),
    ),
    card(
      "Live updates",
      kv([
        ["method", `reading /api/kiosk/panel on each live push and every ${POLL_MS / 1000} s`],
        ["status", ui.lost ? "reconnecting" : ui.failures ? `${ui.failures} failed` : "ok"],
        ["latency", ui.latencyMs === null ? "—" : `${ui.latencyMs} ms`],
        ["last poll", ui.lastPollAt ? new Date(ui.lastPollAt).toLocaleTimeString() : "—"],
        ["idle", `${Math.round((Date.now() - ui.lastTouch) / 1000)} s (attract at ${IDLE_MS / 1000} s)`],
      ]),
    ),
    card("Capabilities", el("pre", { class: "dbg-pre", text: JSON.stringify(panel.capabilities ?? {}, null, 2) })),
    card("State", el("pre", { class: "dbg-pre", text: JSON.stringify(panel.state ?? {}, null, 2) })),
  ];
  const rows = el("div", { class: "dbg-rows" });
  if (dbg.forbidden) rows.append(el("p", { text: "Sign in as ops to see command history." }));
  else if (dbg.error) rows.append(el("p", { text: dbg.error }));
  else if (!(d.commands || []).length) rows.append(el("p", { text: "No commands yet." }));
  (d.commands || []).forEach((c, i) => {
    const key = `${c.at}|${c.action}|${i}`;
    const row = el("button", { class: `dbg-row${dbg.openRows.has(key) ? " is-open" : ""}`, type: "button" });
    row.append(
      document.createTextNode(
        `${fmtAt(c.at)} · ${c.action} · ${c.outcome}${c.latency_ms != null ? ` · ${Math.round(c.latency_ms)} ms` : ""}`,
      ),
    );
    if (c.error) row.append(el("div", { class: "err", text: c.error }));
    if (dbg.openRows.has(key)) row.append(el("pre", { text: JSON.stringify({ params: c.params, target: c.target, error: c.error }, null, 2) }));
    row.addEventListener("click", () => {
      if (dbg.openRows.has(key)) dbg.openRows.delete(key);
      else dbg.openRows.add(key);
      renderDebug();
    });
    rows.append(row);
  });
  sections.push(card("Commands", rows));
  if (d.internals) sections.push(card("Internals", el("pre", { class: "dbg-pre", text: JSON.stringify({ internals: d.internals, lease_holders: d.lease_holders }, null, 2) })));
  const actions = el("div", { class: "dbg-actions" });
  const action = (label, fn, primary = false) => {
    const b = el("button", { class: `dbg-action${primary ? " is-primary" : ""}`, type: "button", text: label });
    pressable(b, fn);
    actions.append(b);
  };
  action("Show attract now", () => {
    closeDebug();
    swallowUntil = Date.now() + 400;
    showAttract();
  });
  action("Simulate toast", () => toast("You can choose a new scene in 6 s.", { retryAfterS: 6 }));
  action(
    "iPad frame preview",
    () => {
      location.search = "?debug=1&frame=1";
    },
    true,
  );
  sections.push(card("Controls", actions));
  body.replaceChildren(...sections);
  body.scrollTop = keepScroll;
}

// ---- iPad frame preview -----------------------------------------------------------------------------------------
function mountFrameView() {
  document.body.className = "frame-view";
  document.body.dataset.view = "frame";
  for (const n of [...document.body.children]) n.remove();
  const sizes = FRAME_SIZES;
  let current = sizes[0];
  try {
    current = sizes.find((s) => s.id === localStorage.getItem("guestFrame")) || current;
  } catch {
    /* storage may be blocked */
  }
  const seg = el("div", { class: "seg", role: "group", "aria-label": "Device size" });
  const safe = el("input", { type: "checkbox" });
  const bar = el(
    "div",
    { class: "fv-bar" },
    seg,
    el("label", { class: "fv-toggle" }, safe, document.createTextNode("Show safe areas")),
    el("a", { class: "fv-link", href: "/kiosk/?debug=1", text: "Back to the panel" }),
  );
  const iframe = el("iframe", { src: "/kiosk/", title: "Guest panel preview" });
  const screen = el("div", { class: "fv-screen" }, iframe, el("div", { class: "fv-safe top", hidden: "" }), el("div", { class: "fv-safe bottom", hidden: "" }));
  const device = el("div", { class: "fv-device" }, screen);
  const stage = el("div", { class: "fv-stage" }, device);
  document.body.append(el("div", { class: "fv" }, bar, stage));
  const layout = () => {
    iframe.width = current.w;
    iframe.height = current.h;
    screen.style.width = `${current.w}px`;
    screen.style.height = `${current.h}px`;
    const box = stage.getBoundingClientRect();
    const s = frameScale(current.w, current.h, box.width + 64, box.height + 64);
    device.style.transform = `translateX(-50%) scale(${s})`;
    for (const b of seg.children) b.setAttribute("aria-pressed", String(b.dataset.id === current.id));
  };
  for (const s of sizes) {
    const b = el("button", { type: "button", "data-id": s.id, text: s.label });
    b.addEventListener("click", () => {
      current = s;
      try {
        localStorage.setItem("guestFrame", s.id);
      } catch {
        /* storage may be blocked */
      }
      layout();
    });
    seg.append(b);
  }
  safe.addEventListener("change", () => {
    for (const n of screen.querySelectorAll(".fv-safe")) n.hidden = !safe.checked;
  });
  window.addEventListener("resize", layout);
  layout();
}

// ---- start ----------------------------------------------------------------------------------------------------------
function onFirstPanel(panel) {
  setSimBadge(Boolean(panel.simulated));
  if (debugAllowed(location.search, panel.viewer)) {
    const params = new URLSearchParams(location.search);
    if (params.get("frame") === "1") {
      mountFrameView();
      return "frame";
    }
    mountDebug();
  }
  return "panel";
}

function start() {
  wireTouch();
  wireSlider();
  wireIdle();
  pressable($("power"), togglePower);
  poll().catch(() => {}); // the first good read opens the live socket (wireLive)
}

start();
