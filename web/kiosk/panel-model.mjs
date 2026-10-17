// The guest panel's pure logic (no DOM, no network), shared by kiosk.js and tests/guest-panel.test.mjs.
//
// Honesty (CLAUDE.md "kiosk copy must be true"; 07 §6): every sentence about what the lights are doing comes from
// the server (`state.message`, `state.feature`); the only copy written here is the calm fallbacks of the design
// spec (docs/design/guest-panel.md §9). Nothing here names a Hubspace feature — that sentence is always the server's.

export const COPY = {
  ready: "Choose a scene below.",
  changing: "Changing the lights…",
  off: "Lights are off. Tap a scene or the switch to turn them on.",
  busy: "One moment, the lights are changing.",
  reconnecting: "Hang on, we're reconnecting.",
  paused: "Our team is adjusting the canopy. It will be back shortly.",
  resting: "Come back soon.",
  noScene: "No scene yet",
  noSceneHelp: "Choose a scene to light the canopy.",
  lightsOff: "Lights are off",
};

export const BRIGHTNESS_SEND_MS = 1000; // ≤ 1 request/s while dragging (the rate budget), plus the final value
export const BRIGHTNESS_HOLD_MS = 3000; // after release, show the guest's value this long before following the poll
export const POWER_HOLD_MS = 8000; // an optimistic switch position holds until the server agrees, or this long
export const IDLE_MS = 120000; // attract after ~2 min without a touch
export const POLL_MS = 2000;
export const LOST_AFTER_FAILURES = 3;
export const SAME_CARD_DEBOUNCE_MS = 600;
export const DRAG_CANCEL_PX = 10;

const PHASES = new Set(["ready", "changing", "resting", "paused", "cooldown"]);

/** The phase, defaulting anything unexpected to "ready" (the server owns the list). */
export function phaseOf(state) {
  const p = state && state.phase;
  return PHASES.has(p) ? p : "ready";
}

// ---- brightness -------------------------------------------------------------------------------------------------
export function clampLevel(value, range = [10, 100]) {
  const [lo, hi] = range;
  const n = Math.round(Number(value));
  if (!Number.isFinite(n)) return lo;
  return Math.min(hi, Math.max(lo, n));
}

/** 0..1 position of a level along the track. */
export function fractionForLevel(level, range = [10, 100]) {
  const [lo, hi] = range;
  return (clampLevel(level, range) - lo) / (hi - lo);
}

/** The level under a finger at clientX on a track starting at `left` and `width` wide. */
export function levelFromX(x, left, width, range = [10, 100]) {
  const [lo, hi] = range;
  const t = width > 0 ? Math.min(1, Math.max(0, (x - left) / width)) : 0;
  return clampLevel(lo + t * (hi - lo), range);
}

/**
 * Paces brightness sends while dragging: at most one per `intervalMs`, and always the last value on release.
 * `move(level, now)` returns the level to send now, or null; `release(level)` returns the final level unless it was
 * just sent. Deterministic for tests (the clock is passed in).
 */
export function createPacer(intervalMs = BRIGHTNESS_SEND_MS) {
  let lastAt = -Infinity;
  let lastSent = null;
  return {
    move(level, now) {
      if (level === lastSent) return null;
      if (now - lastAt < intervalMs) return null;
      lastAt = now;
      lastSent = level;
      return level;
    },
    release(level, now) {
      if (level === lastSent) return null;
      lastAt = now;
      lastSent = level;
      return level;
    },
    reset() {
      lastAt = -Infinity;
      lastSent = null;
    },
  };
}

/**
 * Optimistic UI reconciled to the server: an optimistic `{value, until}` wins until the server reports the same
 * value or the hold expires; then the server's value is shown. Returns `{value, hold}` (hold = the optimistic value,
 * or null once it should be dropped).
 */
export function reconcile(serverValue, hold, now) {
  if (!hold) return { value: serverValue, hold: null };
  if (now >= hold.until || serverValue === hold.value) return { value: serverValue, hold: null };
  return { value: hold.value, hold };
}

// ---- cooldown ---------------------------------------------------------------------------------------------------
/** Whole seconds of cooldown left now, from the server's remaining seconds when it was read (`readAt`, local ms). */
export function cooldownLeft(remainingS, readAt, now) {
  const r = Number(remainingS) || 0;
  if (r <= 0) return 0;
  return Math.max(0, Math.ceil(r - (now - readAt) / 1000));
}

export const cooldownChip = (n) => `New scene in ${n} s`;
export const cooldownToast = (n) => `You can choose a new scene in ${n} s.`;

// ---- header -------------------------------------------------------------------------------------------------------
/** The header's subtitle: `{text, spinner}`. The server's message wins; then the spec's fallbacks by state. */
export function headerSubtitle(state) {
  const phase = phaseOf(state);
  const message = state && typeof state.message === "string" ? state.message.trim() : "";
  const spinner = phase === "changing";
  if (message) return { text: message, spinner };
  if (phase === "changing") return { text: COPY.changing, spinner };
  if (state && state.power === false) return { text: COPY.off, spinner: false };
  if (phase === "paused") return { text: COPY.paused, spinner: false };
  return { text: COPY.ready, spinner: false };
}

/** The switch's look: `{checked, label, unknown}`; an optimistic value (true/false) overrides the server's. */
export function switchView(power) {
  if (power === true) return { checked: true, label: "On", unknown: false };
  if (power === false) return { checked: false, label: "Off", unknown: false };
  return { checked: false, label: "—", unknown: true };
}

/** The two palette colours tinting the header (first and last), or null for the plain header. */
export function headerTint(scene, power) {
  if (!scene || power === false || !Array.isArray(scene.palette) || !scene.palette.length) return null;
  const hex = scene.palette.map((p) => p.hex);
  return [hex[0], hex[hex.length - 1]];
}

// ---- scene cards --------------------------------------------------------------------------------------------------
/**
 * One card's state: "pending", "selected", "disabled" or "idle". `pendingId` is the scene this panel asked for and
 * hasn't seen settle yet (from the tap until the server leaves "changing"). The server's "changing" phase alone
 * doesn't make a card pending: a brightness or power change is "changing" too, and the scene showing hasn't changed.
 */
export function cardState(sceneId, state, pendingId = null) {
  const phase = phaseOf(state);
  const current = state ? state.scene : null;
  if (pendingId === sceneId) return "pending";
  if (phase === "paused" || phase === "resting") return "disabled";
  if (current === sceneId && state.power !== false) return "selected";
  if (phase === "cooldown") return "disabled";
  return "idle";
}

/** What a tap on a card should do: `{send: true}` or `{toast: "…"}` (a disabled card is never silent). */
export function cardTap(sceneId, state, { pendingId = null, cooldownS = 0 } = {}) {
  const phase = phaseOf(state);
  if (phase === "paused" || phase === "resting") {
    return { toast: (state && state.message) || COPY.paused };
  }
  if (pendingId) return { toast: COPY.busy }; // this panel's scene is still on its way
  if (phase === "cooldown" && cooldownS > 0) {
    if (state.scene === sceneId && state.power !== false) return { none: true };
    return { toast: cooldownToast(cooldownS) };
  }
  return { send: true };
}

/** The scene cards' build key: everything a card draws (id, name, mood, solid, the palette's names and hexes, the
 * strip's frame), so a catalog whose contents change under the same ids (a deploy, a new approved look) rebuilds the
 * cards instead of keeping the old names and colours. PLAN §6.5 named id, name, mood, solid and the hexes; the colour
 * names (the card's accessible name) and the frame (its strip) are drawn too, so they are in the key. */
export function cardsKey(scenes) {
  return JSON.stringify(
    (scenes || []).map((s) => [
      s.id,
      s.name,
      s.mood,
      s.solid ?? null,
      (s.palette || []).map((p) => [p.name, p.hex]),
      Array.isArray(s.frame) ? s.frame : null,
    ]),
  );
}

/** The card's accessible name. */
export function sceneLabel(scene) {
  const colors = (scene.palette || []).map((p) => p.name).join(", ");
  return `${scene.name}. ${scene.mood}. Colors: ${colors}.`;
}

export const rgb = (c) => `rgb(${c[0]}, ${c[1]}, ${c[2]})`;

/** A CSS hard-stop gradient of the scene's 30 colours in order: the card's palette strip (not the canopy's picture). */
export function stripGradient(frame) {
  if (!Array.isArray(frame) || !frame.length) return "none";
  const n = frame.length;
  const stops = frame.map((c, i) => {
    const a = ((i / n) * 100).toFixed(3);
    const b = (((i + 1) / n) * 100).toFixed(3);
    return `${rgb(c)} ${a}% ${b}%`;
  });
  return `linear-gradient(90deg, ${stops.join(", ")})`;
}

// ---- now showing ------------------------------------------------------------------------------------------------
/** Serpentine 3 × 10 layout: tile i is frame[i]; rows run L→R, R→L, L→R. Returns [{i, row, col}]. */
export function snakeLayout(n = 30, cols = 10) {
  const out = [];
  for (let i = 0; i < n; i += 1) {
    const row = Math.floor(i / cols);
    const k = i % cols;
    out.push({ i, row, col: row % 2 === 0 ? k : cols - 1 - k });
  }
  return out;
}

export const OFF_TILE = [211, 210, 216]; // #D3D2D8, an unlit puck

export const hexToRgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));

/**
 * The Now-showing card: `{name, description, lit, scene}` when the server sends the canopy's picture (`state.lit`,
 * conductor/canopy_view.py: per panel what may truthfully be claimed; canopy-preview.mjs draws it), else the 30-light
 * serpentine fallback `{name, description, frame, scene}` (the workshop strands, or no look yet). The serpentine draws
 * what is lit: every light of the scene, or, when the server says the strands took the one-colour fallback
 * (`state.look === "solid"`), the scene's solid colour on every light. The kiosk copy must be true, and a picture is
 * copy too.
 */
export function nowShowing(scenes, state) {
  const scene = state && state.scene ? (scenes || []).find((s) => s.id === state.scene) : null;
  const off = state && state.power === false;
  const name = off ? COPY.lightsOff : scene ? scene.name : COPY.noScene;
  const description = !off && scene ? scene.description : COPY.noSceneHelp;
  const shown = scene || null;
  const lit = state && state.lit && Array.isArray(state.lit.modules) ? state.lit : null;
  if (lit) return { name, description, lit, scene: shown };
  const fill = (c) => Array.from({ length: 30 }, () => c);
  if (off || !scene) return { name, description, frame: fill(OFF_TILE), scene: shown };
  const solid = state.look === "solid" && /^#[0-9a-f]{6}$/i.test(scene.solid || "");
  const frame = solid ? fill(hexToRgb(scene.solid)) : scene.frame;
  return { name, description, frame, scene };
}

// ---- idle ---------------------------------------------------------------------------------------------------------
/** True when the attract screen should show: idle long enough while the lights are steady. */
export function attractDue(lastTouchAt, now, phase, idleMs = IDLE_MS) {
  if (phase !== "ready" && phase !== "cooldown") return false;
  return now - lastTouchAt >= idleMs;
}

// ---- refusals -----------------------------------------------------------------------------------------------------
/** The kiosk refusals whose `detail` is a calm sentence written for guests (conductor/routes/guest.py). */
const GUEST_REFUSALS = new Set([404, 409, 423, 429, 503]);

/** The toast for a failed write: the server's calm `detail`, else the reconnecting line. Never an HTTP code. */
export function refusalToast(err) {
  const body = err && err.body;
  const detail = body && typeof body === "object" && typeof body.detail === "string" ? body.detail : "";
  if (!detail || !GUEST_REFUSALS.has(err.status)) return { text: COPY.reconnecting, retryAfterS: 0 };
  const retry = Number(body.retry_after_s);
  return { text: detail, retryAfterS: Number.isFinite(retry) && retry > 0 ? retry : 0 };
}

// ---- debug -----------------------------------------------------------------------------------------------------
export const FRAME_SIZES = [
  { id: "1180x820", w: 1180, h: 820, label: "1180×820" },
  { id: "1024x768", w: 1024, h: 768, label: "1024×768" },
  { id: "820x1180", w: 820, h: 1180, label: "820×1180" },
];

/** The scale that fits a w × h device (plus an 18 px bezel) in the window with a 32 px margin, never above 1. */
export function frameScale(w, h, winW, winH, bezel = 18, margin = 32, chrome = 0) {
  const sw = (winW - 2 * margin) / (w + 2 * bezel);
  const sh = (winH - 2 * margin - chrome) / (h + 2 * bezel);
  return Math.max(0.1, Math.min(1, sw, sh));
}

/** Debug is for ops only: `?debug=1` and the server says the viewer is ops. */
export function debugAllowed(search, viewer) {
  const params = new URLSearchParams(search || "");
  return params.get("debug") === "1" && viewer === "ops";
}
