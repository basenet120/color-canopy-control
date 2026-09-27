// The Color Canopy kiosk (P3 /kiosk). ES module, no build step, no dependencies, vendored helpers only.
//
// Honesty rules (05 #12, 07 §6):
//   - "Changing the light…" only while the server says the canopy phase is `updating`; "Ready" only when it says
//     `ready`. The Hubspace feature sentence is always `state.feature` from the server — never written here.
//   - The illustrated canopy is a preview drawn from the mode's own preview colours (config/modes.yaml), laid out
//     for the tier actually in use: at module level (T3) it is one flat colour per module, from the plan the server
//     reports (`module_colours`), because that is all the ceiling shows.
//   - A refused choice (any non-2xx) never shows "Changing the light…": the tile falls back to the mode now
//     showing and a gentle hint explains. Live events only trigger a re-read of /api/kiosk/state, so the page always
//     shows the kiosk's view of the engine (e.g. slot commissioning reads as "resting", not "changing").
//   - The 8 s cooldown and the 3 min return to Lowcountry Calm are the server's; this page only reflects them.
//     The cooldown counts down in server-relative time (`cooldown_remaining_s`), so the kiosk clock can't skew it.
// Resilience (07 §10): the page never navigates away and never shows a browser error. Every fetch and socket
// failure is caught; a lost connection, a missing engine or paused guest input shows the calm resting screen.

import { api, connectLive, setSimBadge, simBadge } from "/static/app.js";

const COPY = {
  updating: "Changing the light…",
  ready: "Ready",
  sending: "One moment…",
  settling: "Some of the lights are still catching up.",
  cooldown: "Enjoy the light — you can choose again in a moment.",
  busy: "The light is still changing. Choose again in a moment.",
  unavailable: "That mood isn't available right now. Choose another.",
  later: "The canopy needs a moment. Choose again shortly.",
  nowShowing: "Now showing",
  yourChoice: "Your choice",
};

const OFFLINE_GRACE_MS = 6000; // no contact for this long (and no live socket) → the canopy is resting
const POLL_LIVE_MS = 30000; // re-sync over HTTP even while the socket is open (a missed event can't stick)
const POLL_DOWN_MS = 4000; // while the socket is down
const MODES_RETRY_MS = 5000;
const SELECT_TIMEOUT_MS = 8000;
const CHOSEN_WAIT_MS = 10000; // an accepted choice the state hasn't shown yet stays previewed this long
const MAX_COOLDOWN_S = 60;
const LIVE_REFRESH_MS = 120; // a burst of live events becomes one state read
const MODES_RELOAD_MS = 30000; // at most this often when the tier in use differs from the listing's
const HEX = /^#[0-9a-f]{6}$/i;

// ---- canopy geometry (config/canopy.yaml; 02): 3 × 3 modules of 75″ × 40″, heads staggered 8/7/8/7 ---------
const MODULE_W = 75;
const MODULE_H = 40;
const ROWS_A = [
  { y: 5, x0: 2.5, n: 8 },
  { y: 15, x0: 7.5, n: 7 },
  { y: 25, x0: 2.5, n: 8 },
  { y: 35, x0: 7.5, n: 7 },
];
const ORIENT = [
  ["A", "B", "A"],
  ["A", "B", "A"],
  ["A", "B", "A"],
]; // p14 scheme; B = A rotated 180°

export function canopyHeads() {
  const out = [];
  ORIENT.forEach((row, r) =>
    row.forEach((orient, c) => {
      for (const line of ROWS_A) {
        for (let k = 0; k < line.n; k += 1) {
          let x = line.x0 + 10 * k;
          let y = line.y;
          if (orient === "B") {
            x = MODULE_W - x;
            y = MODULE_H - y;
          }
          out.push({ x: c * MODULE_W + x, y: r * MODULE_H + y, module: r * 3 + c, i: out.length });
        }
      }
    }),
  );
  return out;
}

// ---- colour ------------------------------------------------------------------------------------------------
const rgbOf = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
const mix = (a, b, t) => a.map((v, i) => v + (b[i] - v) * t);
const css = (c, alpha = 1) =>
  alpha >= 1
    ? `rgb(${c.map((v) => Math.round(v)).join(",")})`
    : `rgba(${c.map((v) => Math.round(v)).join(",")},${alpha})`;
const clamp01 = (t) => Math.min(1, Math.max(0, t));
const smooth = (e0, e1, x) => {
  const t = clamp01((x - e0) / (e1 - e0));
  return t * t * (3 - 2 * t);
};

function ramp(palette, t) {
  if (palette.length === 1) return palette[0];
  const scaled = clamp01(t) * (palette.length - 1);
  const i = Math.min(palette.length - 2, Math.floor(scaled));
  return mix(palette[i], palette[i + 1], scaled - i);
}

function hash01(i) {
  const s = Math.sin(i * 127.1 + 311.7) * 43758.5453;
  return s - Math.floor(s);
}

/** Per-head colours for a mode's illustration. `pattern` comes from the server (how the mode's technique lays out
 * at the tier in use); "modules" paints every head of a module in that module's colour (`module_colours`, r0c0…r2c2). */
export function headColours(mode, heads) {
  const palette = (mode.preview || []).filter((c) => HEX.test(c)).map(rgbOf);
  if (!palette.length) palette.push([255, 228, 188]);
  const [p0, p1 = p0, p2 = p1] = palette;
  const W = MODULE_W * 3;
  const H = MODULE_H * 3;
  const flat = Array.isArray(mode.module_colours) ? mode.module_colours : [];
  return heads.map((h) => {
    const u = h.x / W;
    const v = h.y / H;
    switch (mode.pattern) {
      case "modules": {
        const c = flat[h.module];
        return typeof c === "string" && HEX.test(c) ? rgbOf(c) : ramp(palette, h.module / 8);
      }
      case "gradient":
        return ramp(palette, v + 0.03 * Math.sin(u * 9));
      case "pathway": {
        const centre = H / 2 + 30 * Math.sin(2 * Math.PI * 1.5 * u);
        const d = Math.abs(h.y - centre);
        let c = mix(p0, p2, smooth(26, 12, d));
        c = mix(c, p1, smooth(13, 4, d));
        return c;
      }
      case "bands": {
        const d = Math.abs((h.x % MODULE_W) - MODULE_W / 2);
        let c = mix(p0, p1, smooth(24, 10, d));
        c = mix(c, p2, smooth(10, 2, d));
        return c;
      }
      case "scatter": {
        const r = hash01(h.i);
        return r < 0.5 ? p1 : r < 0.76 ? p0 : p2;
      }
      default: {
        const t = 0.5 + 0.32 * Math.sin(u * 5.1 + v * 2.3) + 0.16 * Math.sin(u * 11.7 - v * 7.9);
        return ramp(palette, t);
      }
    }
  });
}

// ---- DOM ---------------------------------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
const svgEl = (name, attrs = {}) => {
  const el = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
};

const view = {
  heads: canopyHeads(),
  bloom: [],
  halo: [],
  core: [],
  washes: [],
  painted: null,
};

function buildCanopy() {
  const svg = $("canopy-svg");
  if (!svg) return;
  svg.textContent = "";
  const defs = svgEl("defs");
  const soft = svgEl("filter", { id: "soft", x: "-50%", y: "-50%", width: "200%", height: "200%" });
  soft.appendChild(svgEl("feGaussianBlur", { stdDeviation: "2.2" }));
  const wide = svgEl("filter", { id: "wide", x: "-50%", y: "-50%", width: "200%", height: "200%" });
  wide.appendChild(svgEl("feGaussianBlur", { stdDeviation: "5" }));
  defs.append(soft, wide);
  svg.appendChild(defs);

  // nine painted-white panels, each catching a little of its own module's light
  const panels = svgEl("g", { class: "panels" });
  view.washes = [];
  for (let r = 0; r < 3; r += 1) {
    for (let c = 0; c < 3; c += 1) {
      const box = { x: c * MODULE_W + 0.7, y: r * MODULE_H + 0.7, width: MODULE_W - 1.4, height: MODULE_H - 1.4, rx: 1.4 };
      const wash = svgEl("rect", { ...box, class: "panel-wash", fill: "#2E241C", "fill-opacity": "0.16" });
      panels.append(svgEl("rect", { ...box, class: "panel" }), wash);
      view.washes.push(wash);
    }
  }
  const bloom = svgEl("g", { class: "bloom", filter: "url(#wide)", opacity: "0.55" });
  const halo = svgEl("g", { class: "halo", filter: "url(#soft)" });
  const core = svgEl("g", { class: "core" });
  view.bloom = [];
  view.halo = [];
  view.core = [];
  for (const h of view.heads) {
    const b = svgEl("circle", { cx: h.x, cy: h.y, r: 6.5, fill: "#2E241C" });
    const g = svgEl("circle", { cx: h.x, cy: h.y, r: 3.6, fill: "#2E241C", opacity: "0.9" });
    const k = svgEl("circle", { cx: h.x, cy: h.y, r: 1.55, fill: "#3A2E24" });
    // a gentle left-to-right sweep when the mood changes (an on-screen transition, not a claim about the lights)
    const delay = `${Math.round((h.x / (MODULE_W * 3)) * 380)}ms`;
    g.style.transitionDelay = delay;
    k.style.transitionDelay = delay;
    b.style.transitionDelay = delay;
    bloom.appendChild(b);
    halo.appendChild(g);
    core.appendChild(k);
    view.bloom.push(b);
    view.halo.push(g);
    view.core.push(k);
  }
  svg.append(panels, bloom, halo, core);
}

/** The picture for `mode`: its listing (the tier the engine would use), or — when it is the mode now showing — the
 * tier the server says is in use, with the plan's own module colours at module level. */
function pictureOf(mode, s) {
  if (!mode) return null;
  if (!s || s.mode !== mode.id || typeof s.tier !== "string") return mode;
  if (s.tier === "modules" && Array.isArray(s.module_colours)) {
    return { ...mode, pattern: "modules", module_colours: s.module_colours };
  }
  if (s.tier !== "modules" && mode.pattern === "modules") {
    return { ...mode, pattern: mode.pattern_primary || "wash", module_colours: null };
  }
  return mode;
}

function pictureKey(p) {
  return `${p.id}|${p.pattern}|${Array.isArray(p.module_colours) ? p.module_colours.join(",") : ""}`;
}

function paintCanopy(mode) {
  if (!mode) return;
  const key = pictureKey(mode);
  if (view.painted === key) return;
  view.painted = key;
  const colours = headColours(mode, view.heads);
  const white = [255, 250, 240];
  colours.forEach((c, i) => {
    const lum = (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255;
    view.bloom[i].setAttribute("fill", css(c));
    view.halo[i].setAttribute("fill", css(c));
    view.halo[i].setAttribute("opacity", String(0.55 + 0.4 * lum));
    view.core[i].setAttribute("fill", css(mix(c, white, 0.1 + 0.5 * lum)));
  });
  const sums = view.washes.map(() => [0, 0, 0, 0]);
  colours.forEach((c, i) => {
    const m = sums[view.heads[i].module];
    m[0] += c[0];
    m[1] += c[1];
    m[2] += c[2];
    m[3] += 1;
  });
  sums.forEach((m, k) => {
    if (m[3]) view.washes[k].setAttribute("fill", css([m[0] / m[3], m[1] / m[3], m[2] / m[3]]));
  });
  const avg = colours.reduce((a, c) => a.map((v, j) => v + c[j] / colours.length), [0, 0, 0]);
  $("canopy")?.style.setProperty("--wash", css(avg, 0.28));
}

// ---- state -------------------------------------------------------------------------------------------------
const st = {
  modes: [],
  byId: new Map(),
  server: null, // the last CanopyState JSON
  engineMissing: false,
  unauthorized: false,
  skewMs: null, // server clock − kiosk clock, from the last HTTP state
  lastContact: performance.now(), // the last successful exchange with the Conductor (page load counts)
  liveOpen: false,
  chosen: null, // the mode this guest just tapped, until the server's state shows it
  chosenUntil: 0, // give up waiting for the server to show `chosen` after this (performance.now())
  inflight: false,
  cooldownEnd: 0, // performance.now() when the cooldown ends
  cooldownTotal: 0,
  hintUntil: 0,
  hintText: "",
  modesFailed: false,
  modesLoadedAt: 0,
};

function contact() {
  st.lastContact = performance.now();
}

function setCooldown(remainingS) {
  const rem = Math.min(MAX_COOLDOWN_S, Math.max(0, Number(remainingS) || 0));
  const now = performance.now();
  if (rem <= 0.05) {
    st.cooldownEnd = 0;
    return;
  }
  const end = now + rem * 1000;
  const active = st.cooldownEnd > now;
  if (!active || Math.abs(end - st.cooldownEnd) > 400) {
    if (!active || end > st.cooldownEnd) st.cooldownTotal = rem;
    st.cooldownEnd = end;
  }
  tickCooldown();
}

function applyState(data, { http = false } = {}) {
  if (!data || typeof data !== "object") return;
  const s = data.phase === undefined && data.state && typeof data.state === "object" ? data.state : data;
  if (typeof s.phase !== "string") return;
  st.server = s;
  if (st.chosen !== null && !st.inflight && s.mode === st.chosen) st.chosen = null;
  const listed = s.mode ? st.byId.get(s.mode) : null;
  if (listed && listed.tier && s.tier && listed.tier !== s.tier && performance.now() - st.modesLoadedAt > MODES_RELOAD_MS) {
    st.modesLoadedAt = performance.now(); // the tier changed (a downgrade, or flags set): re-read the pictures
    loadModes();
  }
  st.engineMissing = false;
  st.unauthorized = false;
  contact();
  if (http && Number.isFinite(s.server_now)) st.skewMs = s.server_now * 1000 - Date.now();
  if (Number.isFinite(s.cooldown_remaining_s)) {
    setCooldown(s.cooldown_remaining_s);
  } else if (Number.isFinite(s.cooldown_until)) {
    const serverNowS = (Date.now() + (st.skewMs ?? 0)) / 1000;
    setCooldown(s.cooldown_until - serverNowS);
  } else {
    setCooldown(0);
  }
  setSimBadge(s.simulated === true);
  render();
}

function hint(text, ms = 4000) {
  st.hintText = text;
  st.hintUntil = performance.now() + ms;
  render();
}

function resting() {
  if (st.engineMissing || st.unauthorized || st.modesFailed) return true;
  if (!st.liveOpen && performance.now() - st.lastContact > OFFLINE_GRACE_MS) return true;
  const s = st.server;
  if (!s) return false; // still loading (the grace timer turns a failed start into "resting")
  return s.phase === "resting" || s.guest_input === false;
}

function statusOf(s, pending) {
  if (st.inflight) return ["sending", COPY.sending];
  if (!s) return ["idle", "\u00a0"];
  // accepted, but the server's state doesn't show the new mode yet: never "Ready" for the old one
  if (pending && s.phase !== "updating") return ["sending", COPY.sending];
  if (s.phase === "updating") return ["updating", COPY.updating];
  if (s.phase === "ready") return ["ready", COPY.ready];
  if (s.phase === "needs_attention") return ["settling", COPY.settling];
  return ["idle", "\u00a0"];
}

function render() {
  const now = performance.now();
  const body = document.body;
  const isResting = resting();
  const viewName = isResting ? "resting" : st.server && st.modes.length ? "live" : "loading";
  if (body.dataset.view !== viewName) body.dataset.view = viewName;
  const rest = $("resting");
  if (rest) rest.hidden = !isResting;

  const s = st.server;
  if (st.chosen !== null && !st.inflight && now > st.chosenUntil) st.chosen = null; // the server never showed it
  const shownId = st.chosen ?? s?.mode ?? null;
  const shown = shownId ? st.byId.get(shownId) : null;
  if (shown) paintCanopy(pictureOf(shown, s));

  const pending = st.chosen !== null && st.chosen !== s?.mode;
  $("now-label").textContent = shown?.label ?? s?.label ?? "\u00a0";

  const [state, text] = statusOf(s, pending);
  const line = $("status-line");
  if (line.dataset.state !== state) line.dataset.state = state;
  $("status-text").textContent = text;

  const feature = !pending && s && s.mode && typeof s.feature === "string" && s.feature ? s.feature : "";
  $("feature").hidden = !feature;
  $("feature-text").textContent = feature;

  for (const tile of document.querySelectorAll(".tile")) {
    const id = tile.dataset.mode;
    const isChosen = pending && id === st.chosen;
    const isCurrent = !pending && id === s?.mode;
    tile.classList.toggle("chosen", isChosen);
    tile.classList.toggle("current", isCurrent);
    const chip = tile.querySelector(".chip");
    if (chip) chip.textContent = isChosen ? COPY.yourChoice : COPY.nowShowing;
    tile.setAttribute("aria-pressed", String(isCurrent || isChosen));
  }

  const cooling = st.cooldownEnd > now;
  body.classList.toggle("cooling", cooling);
  const hintEl = $("hint");
  let hintText = "";
  if (st.hintUntil > now) hintText = st.hintText;
  else if (cooling) hintText = COPY.cooldown;
  hintEl.textContent = hintText || "\u00a0";
  hintEl.classList.toggle("shown", Boolean(hintText));
}

let coolFrame = 0;
function tickCooldown() {
  if (coolFrame) return;
  const step = () => {
    coolFrame = 0;
    const now = performance.now();
    const left = Math.max(0, st.cooldownEnd - now);
    const total = Math.max(0.001, st.cooldownTotal * 1000);
    document.body.style.setProperty("--cool", String(clamp01(1 - left / total)));
    if (left > 0) {
      coolFrame = requestAnimationFrame(step);
    } else {
      render();
    }
  };
  coolFrame = requestAnimationFrame(step);
  render();
}

// ---- server ------------------------------------------------------------------------------------------------
async function refresh() {
  try {
    applyState(await api("/api/kiosk/state"), { http: true });
  } catch (err) {
    if (err?.status === 503) {
      st.engineMissing = true;
      contact(); // the Conductor answered; only the engine is away
    } else if (err?.status === 401 || err?.status === 403) {
      st.unauthorized = true;
    }
    render();
  }
}

// A live event says "something changed"; the kiosk re-reads its own view of it (/api/kiosk/state), which is what
// guests should see (the raw engine state says "updating" while slots are commissioned, for example).
let liveTimer = 0;
let liveBusy = false;
function liveRefresh() {
  if (liveTimer) return;
  liveTimer = setTimeout(async () => {
    liveTimer = 0;
    if (liveBusy) {
      liveRefresh();
      return;
    }
    liveBusy = true;
    try {
      await refresh();
    } finally {
      liveBusy = false;
    }
  }, LIVE_REFRESH_MS);
}

let pollTimer = 0;
function schedulePoll() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(
    async () => {
      await refresh();
      schedulePoll();
    },
    st.liveOpen ? POLL_LIVE_MS : POLL_DOWN_MS,
  );
}

async function loadModes() {
  try {
    const list = await api("/api/kiosk/modes");
    if (!Array.isArray(list) || !list.length) throw new Error("no modes");
    st.modes = list.filter((m) => m && typeof m.id === "string");
    st.byId = new Map(st.modes.map((m) => [m.id, m]));
    st.modesFailed = false;
    st.modesLoadedAt = performance.now();
    view.painted = null;
    contact();
    buildTiles();
    render();
  } catch {
    st.modesFailed = !st.modes.length;
    render();
    setTimeout(loadModes, MODES_RETRY_MS);
  }
}

function buildTiles() {
  const tiles = $("tiles");
  tiles.textContent = "";
  for (const mode of st.modes) {
    const colours = (mode.preview || []).filter((c) => HEX.test(c));
    const stops = colours.length ? colours : ["#FFE4BC"];
    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "tile";
    tile.dataset.mode = mode.id;
    tile.setAttribute("aria-label", `${mode.label}. ${mode.blurb || ""}`.trim());

    const swatch = document.createElement("span");
    swatch.className = "swatch";
    const gradient =
      stops.length === 1
        ? `linear-gradient(115deg, ${stops[0]}, ${stops[0]})`
        : `linear-gradient(115deg, ${stops.map((c, i) => `${c} ${Math.round((i / (stops.length - 1)) * 100)}%`).join(", ")})`;
    swatch.style.setProperty("--swatch", gradient);
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = COPY.nowShowing;
    const ring = document.createElement("span");
    ring.className = "ring";
    ring.setAttribute("aria-hidden", "true");
    swatch.append(chip, ring);

    const text = document.createElement("span");
    text.className = "tile-text";
    const label = document.createElement("span");
    label.className = "tile-label";
    label.textContent = mode.label;
    const blurb = document.createElement("span");
    blurb.className = "tile-blurb";
    blurb.textContent = mode.blurb || "";
    text.append(label, blurb);

    tile.append(swatch, text);
    tile.addEventListener("pointerdown", () => tile.classList.add("pressed"));
    for (const ev of ["pointerup", "pointercancel", "pointerleave"]) {
      tile.addEventListener(ev, () => tile.classList.remove("pressed"));
    }
    tile.addEventListener("click", () => choose(mode.id, tile));
    tiles.appendChild(tile);
  }
}

function nudge(tile) {
  tile.classList.remove("nudge");
  void tile.offsetWidth; // restart the animation
  tile.classList.add("nudge");
}

async function choose(modeId, tile) {
  try {
    if (resting()) return;
    const s = st.server;
    if (st.inflight || st.cooldownEnd > performance.now()) {
      nudge(tile);
      hint(COPY.cooldown);
      return;
    }
    if (s && s.mode === modeId && st.chosen === null) {
      nudge(tile); // already showing: nothing to send
      return;
    }
    st.chosen = modeId; // the instant on-screen preview
    st.inflight = true;
    render();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), SELECT_TIMEOUT_MS);
    try {
      const data = await api("/api/kiosk/select", {
        method: "POST",
        body: { mode: modeId },
        signal: controller.signal,
      });
      st.inflight = false;
      st.chosenUntil = performance.now() + CHOSEN_WAIT_MS; // kept until the state shows it (latest wins)
      applyState(data, { http: true });
    } catch (err) {
      st.inflight = false;
      st.chosen = null;
      const status = err?.status ?? 0;
      if (status === 429) {
        setCooldown(err.body?.retry_after_s ?? 8);
        hint(COPY.busy);
      } else if (status === 404 || status === 409) {
        hint(COPY.unavailable);
      } else if (status === 423 || status === 503) {
        await refresh(); // paused, resting or being set up: the resting screen says so
        if (!resting()) hint(COPY.later);
      } else if (status === 401 || status === 403) {
        st.unauthorized = true;
      } else {
        // network trouble or a timeout: re-sync; if the Conductor is gone the grace timer shows "resting"
        refresh();
      }
      render();
    } finally {
      clearTimeout(timer);
    }
  } catch (err) {
    st.inflight = false;
    st.chosen = null;
    console.error("kiosk selection failed", err);
    render();
  }
}

// ---- guard rails: no zoom, no selection menus, no navigation, no error pages -----------------------------
function guardRails() {
  const stop = (e) => e.preventDefault();
  window.addEventListener("error", (e) => {
    console.error("kiosk error", e.error || e.message);
    e.preventDefault();
  });
  window.addEventListener("unhandledrejection", (e) => {
    console.error("kiosk rejection", e.reason);
    e.preventDefault();
  });
  document.addEventListener("contextmenu", stop);
  document.addEventListener("dragstart", stop);
  document.addEventListener("selectstart", stop);
  document.addEventListener("gesturestart", stop); // iOS pinch
  document.addEventListener(
    "touchmove",
    (e) => {
      if (e.touches && e.touches.length > 1) e.preventDefault();
    },
    { passive: false },
  );
  window.addEventListener(
    "wheel",
    (e) => {
      if (e.ctrlKey) e.preventDefault();
    },
    { passive: false },
  );
  window.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && ["+", "-", "=", "0"].includes(e.key)) e.preventDefault();
  });
  document.addEventListener("click", (e) => {
    const link = e.target instanceof Element ? e.target.closest("a[href]") : null;
    if (link) e.preventDefault();
  });
}

function start() {
  guardRails();
  buildCanopy();
  render();
  simBadge().catch(() => null);
  loadModes();
  refresh().finally(schedulePoll);
  connectLive({
    topics: ["canopy"],
    onEvent: (topic) => {
      if (topic === "canopy") liveRefresh();
    },
    onStatus: (state) => {
      const was = st.liveOpen;
      st.liveOpen = state === "open";
      if (st.liveOpen) {
        contact();
        if (!was) refresh(); // the server-relative clock and anything missed while away
      }
      if (st.liveOpen !== was) schedulePoll();
      render();
    },
  });
  setInterval(render, 1000); // the offline grace and hint timers
}

try {
  start();
} catch (err) {
  console.error("kiosk failed to start", err);
  document.body.dataset.view = "resting";
  const rest = document.getElementById("resting");
  if (rest) rest.hidden = false;
}
