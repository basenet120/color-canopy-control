// /ops/module — the single-module test page (BUILD_PLAN P0b). ES module, no build step, no CDN.
//
// Lifecycle: pick a device (default bench-kit) → POST open (the lease lives only in this page's memory) →
// heartbeat every 30 s → POST close with navigator.sendBeacon on pagehide. Live state arrives on the
// WebSocket topic "module:<id>"; every action also returns the fresh state.

import { api, connectLive, copyJSON, fmtMs, setSimBadge } from "/static/app.js";
import { previewFrame, previewKey, previewNotice } from "./preset-preview.js?v=preview-motion-2";

const HEARTBEAT_MS = 30000;
const SVGNS = "http://www.w3.org/2000/svg";
const OFF = { r: 0, g: 0, b: 0, cct: 0, colorBrightness: 0, whiteBrightness: 0 };
const SHOWN = { "top-left": "bottom-right", "top-right": "bottom-left", "bottom-left": "top-right", "bottom-right": "top-left" };
const PRESET_LABELS = {
  'rainbow-road': 'rainbow-road — travelling colors',
  'rainbow-roll': 'rainbow-roll — pulses in place',
  'gradient-rainbow': 'gradient-rainbow — static color pairs',
  'fade-3': 'fade-3 — RGB fades through black',
  'jump-3': 'jump-3 — sharp RGB changes',
};

const S = {
  deviceId: null,
  lease: null,
  sessionId: null,
  state: null,
  role: "ops",
  canTakeOver: false,
  draft: [],
  dirty: false,
  selection: new Set(),
  showWire: true,
  interacted: false,
  live: null,
  heartbeatTimer: null,
  refreshTimer: null,
  previewRaf: null,
  previewKey: null,
  previewStarted: 0,
  previewElapsed: 0,
  previewRate: 1,
  previewPaused: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches || false,
  inflight: 0,
  built: false,
  recipeKind: "blend",
  paintSlot: "custom-3",
  whiteMode: "off",
  cal: { orientation: "A", cable_path: "serpentine_rows", corner_shown: "top-left" },
  drag: null,
};

// ---- small helpers ----------------------------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);

function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined) node.append(c);
  return node;
}

function s(tag, attrs = {}) {
  const node = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

const clamp255 = (v) => Math.max(0, Math.min(255, Math.round(v)));
const hex2 = (n) => clamp255(n).toString(16).padStart(2, "0");
const rgbHex = (c) => `#${hex2(c.r)}${hex2(c.g)}${hex2(c.b)}`;
const css = (c) => `rgb(${clamp255(c.r)}, ${clamp255(c.g)}, ${clamp255(c.b)})`;
function parseHex(value) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(value || "");
  return m ? { r: parseInt(m[1], 16), g: parseInt(m[2], 16), b: parseInt(m[3], 16) } : { r: 0, g: 0, b: 0 };
}
const sameColor = (a, b) => a.r === b.r && a.g === b.g && a.b === b.b;
const sameEntry = (a, b) =>
  ["r", "g", "b", "cct", "colorBrightness", "whiteBrightness"].every((k) => (a[k] ?? 0) === (b[k] ?? 0));
const luminance = (c) => 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b;
const shortLabel = (slot) => slot.replace("custom-", "Custom ");

// Black-body approximation (same fit as conductor/render.py) for drawing per-head white.
function kelvinToRgb(kelvin) {
  const t = Math.max(1000, Math.min(40000, kelvin)) / 100;
  let r;
  let g;
  let b;
  if (t <= 66) {
    r = 255;
    g = 99.4708025861 * Math.log(t) - 161.1195681661;
  } else {
    r = 329.698727446 * (t - 60) ** -0.1332047592;
    g = 288.1221695283 * (t - 60) ** -0.0755148492;
  }
  if (t >= 66) b = 255;
  else if (t <= 19) b = 0;
  else b = 138.5177312231 * Math.log(t - 10) - 305.0447927307;
  return { r: clamp255(r), g: clamp255(g), b: clamp255(b) };
}

/** What a frame entry looks like on the map (render.py's individual-mode rule at full strand brightness). */
function entryColor(e) {
  const k = (e.colorBrightness ?? 100) / 100;
  let r = e.r * k;
  let g = e.g * k;
  let b = e.b * k;
  const noWhite = S.state?.capabilities?.no_white_hint;
  if (!noWhite && e.cct && e.whiteBrightness) {
    const w = kelvinToRgb(e.cct);
    const kw = e.whiteBrightness / 100;
    r += w.r * kw;
    g += w.g * kw;
    b += w.b * kw;
  }
  return { r: clamp255(r), g: clamp255(g), b: clamp255(b) };
}

function lerp(a, b, t) {
  return Math.round(a + (b - a) * t);
}

/** Same blend as conductor/module/cards.py `mix`: a 0 K side only fades its white brightness. */
function mix(a, b, t) {
  const cct = a.cct && b.cct ? lerp(a.cct, b.cct, t) : a.cct || b.cct;
  const wa = a.cct ? a.whiteBrightness : 0;
  const wb = b.cct ? b.whiteBrightness : 0;
  return {
    r: lerp(a.r, b.r, t),
    g: lerp(a.g, b.g, t),
    b: lerp(a.b, b.b, t),
    cct,
    colorBrightness: lerp(a.colorBrightness, b.colorBrightness, t),
    whiteBrightness: cct ? lerp(wa, wb, t) : 0,
  };
}

let toastTimer = null;
function toast(message, kind = "info", ms = 4500) {
  const t = $("toast");
  t.textContent = message;
  t.className = `toast${kind === "error" ? " error" : ""}`;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    t.hidden = true;
  }, ms);
}

function banner(message, actions = [], kind = "error") {
  const b = $("banner");
  b.replaceChildren();
  if (!message) {
    b.hidden = true;
    return;
  }
  b.className = `banner${kind === "info" ? " info" : ""}`;
  b.append(h("span", { text: message }), ...actions);
  b.hidden = false;
}

function markActive() {
  S.interacted = true;
}

// ---- API ------------------------------------------------------------------------------------------------------
async function act(path, body = {}, { label } = {}) {
  markActive();
  S.inflight += 1;
  renderBusy();
  try {
    const res = await api(`/api/module/${encodeURIComponent(S.deviceId)}/${path}`, {
      method: "POST",
      body: { lease: S.lease, ...body },
    });
    if (res && res.state) applyState(res.state);
    if (res && res.result) announce(res.result, res);
    else if (label) toast(label + (res && res.simulated ? " · SIMULATED" : ""));
    return res;
  } catch (err) {
    if (err.status === 409 && /hold|any more|no longer/i.test(err.message) && !/Hold is on/.test(err.message)) {
      lost(err.message);
    } else {
      toast(err.message, "error", 7000);
    }
    return null;
  } finally {
    S.inflight -= 1;
    renderBusy();
  }
}

function announce(result, res) {
  const ok = result.status === "applied";
  const parts = [`${ok ? "Applied" : result.status.toUpperCase()}: ${result.label}`];
  if (result.http_status) parts.push(`HTTP ${result.http_status}`);
  if (result.send_to_readback_ms != null) parts.push(`readback matched in ${fmtMs(result.send_to_readback_ms)}`);
  if (result.error) parts.push(result.error);
  if (res.simulated) parts.push("SIMULATED");
  const notes = result.notes && result.notes.length ? ` — ${result.notes.join(" ")}` : "";
  toast(parts.join(" · ") + notes, ok ? "info" : "error", ok ? 4500 : 8000);
}

// ---- open / close / heartbeat ----------------------------------------------------------------------------------
async function loadDevices() {
  const list = await api("/api/module");
  S.role = list.role;
  $("showcase-link").hidden = S.role !== "ops"; // the Studio is ops-only
  S.canTakeOver = !!list.can_take_over;
  return list.devices;
}

function showPicker(devices, note) {
  setSimBadge(false); // nothing is driven from the picker; each device says "Simulator" or "Real device"
  $("workspace").hidden = true;
  $("picker").hidden = false;
  $("switch-btn").hidden = true;
  $("hold-btn").hidden = true;
  $("status-pill").hidden = true;
  $("device-name").textContent = devices.length ? "No module open" : "No modules available";
  $("picker-note").textContent =
    note || "The page holds the module while it's open, so nothing else changes its lights.";
  const list = $("device-list");
  list.replaceChildren();
  if (!devices.length) {
    list.append(h("p", { class: "muted", text: "This link doesn't give access to any module. Ask the ops team." }));
  }
  for (const d of devices) {
    const sub = [d.simulated ? "Simulator" : "Real device", d.locked ? `in use by ${d.holder}` : "free"].join(" · ");
    list.append(
      h(
        "button",
        { type: "button", class: `device-card${d.locked ? " busy" : ""}`, onclick: () => openDevice(d.id) },
        h("span", { text: d.label }),
        h("span", { class: "sub", text: `${d.id} · ${sub}` }),
      ),
    );
  }
}

async function openDevice(id, { steal = false } = {}) {
  banner(null);
  $("device-name").textContent = `Opening ${id}…`;
  let res;
  try {
    res = await api(`/api/module/${encodeURIComponent(id)}/open`, { method: "POST", body: { steal } });
  } catch (err) {
    const devices = await loadDevices().catch(() => []);
    showPicker(devices, "Pick a module, or try again in a moment.");
    if (err.status === 409 && S.canTakeOver && /in use by/i.test(err.message)) {
      banner(err.message, [
        h("button", { type: "button", class: "secondary", text: "Take over", onclick: () => openDevice(id, { steal: true }) }),
      ]);
    } else {
      banner(err.message);
    }
    return;
  }
  S.deviceId = id;
  S.lease = res.lease;
  S.sessionId = res.state.session_id;
  S.built = false;
  S.selection.clear();
  S.dirty = false;
  history.replaceState(null, "", `?device=${encodeURIComponent(id)}`);
  $("picker").hidden = true;
  $("workspace").hidden = false;
  $("switch-btn").hidden = false;
  $("hold-btn").hidden = false;
  $("hold-btn").disabled = false;
  applyState(res.state);
  startLive();
  startHeartbeat();
  startReadback();
}

function startReadback() {
  clearTimeout(S.refreshTimer);
  const device = S.deviceId, session = S.sessionId, lease = S.lease;
  const current = () => S.lease === lease && S.sessionId === session && S.deviceId === device;
  const refresh = async () => {
    if (!current()) return;
    try {
      if (!document.hidden && !S.inflight && !S.state?.busy) {
        const reply = await api(`/api/module/${encodeURIComponent(device)}?refresh=1`);
        if (!current()) return;
        if (!reply.state?.open || reply.state.session_id !== session) {
          lost('This module session ended. Open it again to continue.');
          return;
        }
        applyState(reply.state);
        $('readback-note').textContent = reply.state.read_error ? 'Readback needs attention.' : 'Settings refreshed every 5 seconds while this page is visible.';
      }
    } catch {
      if (current()) $('readback-note').textContent = 'Could not refresh settings; the viewport is showing the last report.';
    } finally {
      if (current()) S.refreshTimer = setTimeout(refresh, 5000);
    }
  };
  S.refreshTimer = setTimeout(refresh, 5000);
}

function startHeartbeat() {
  clearInterval(S.heartbeatTimer);
  S.heartbeatTimer = setInterval(async () => {
    if (!S.lease) return;
    const active = S.interacted;
    S.interacted = false;
    try {
      const res = await api(`/api/module/${encodeURIComponent(S.deviceId)}/heartbeat`, {
        method: "POST",
        body: { lease: S.lease, active },
      });
      if (res.session_id !== S.sessionId) lost("Someone else opened this module.");
    } catch (err) {
      if (err.status === 409) lost(err.message);
    }
  }, HEARTBEAT_MS);
}

function startLive() {
  if (S.live) S.live.close();
  S.live = connectLive({
    topics: [`module:${S.deviceId}`],
    onEvent: (topic, data) => {
      if (topic !== `module:${S.deviceId}` || !data || !data.state || !S.lease) return;
      const st = data.state;
      if (st.open && st.session_id && st.session_id !== S.sessionId) {
        lost(`Taken over by ${st.lock && st.lock.owner ? st.lock.owner : "someone else"}.`);
        return;
      }
      if (!st.open) return; // our own close, or a stale snapshot
      applyState(st);
    },
  });
}

function releaseBeacon() {
  clearTimeout(S.refreshTimer);
  cancelAnimationFrame(S.previewRaf);
  if (!S.lease || !S.deviceId) return;
  const url = `/api/module/${encodeURIComponent(S.deviceId)}/close`;
  const body = JSON.stringify({ lease: S.lease });
  let sent = false;
  try {
    sent = navigator.sendBeacon(url, body);
  } catch {
    sent = false;
  }
  if (!sent) {
    fetch(url, { method: "POST", body, keepalive: true, credentials: "same-origin" }).catch(() => {});
  }
  S.lease = null;
}

async function closeDevice() {
  const id = S.deviceId;
  if (S.lease) {
    try {
      await api(`/api/module/${encodeURIComponent(id)}/close`, { method: "POST", body: { lease: S.lease } });
    } catch {
      // the lock times out on its own
    }
  }
  stopSession();
  const devices = await loadDevices().catch(() => []);
  showPicker(devices);
}

function stopSession() {
  S.lease = null;
  clearInterval(S.heartbeatTimer);
  clearTimeout(S.refreshTimer);
  cancelAnimationFrame(S.previewRaf);
  if (S.live) {
    S.live.close();
    S.live = null;
  }
  $("hold-overlay").hidden = true;
}

function lost(message) {
  if (!S.lease) return;
  stopSession();
  $("hold-btn").disabled = true;
  banner(`${message} Nothing you do here will reach the lights.`, [
    h("button", { type: "button", class: "secondary", text: "Open again", onclick: () => openDevice(S.deviceId) }),
  ]);
  renderStatus();
}

// ---- state ----------------------------------------------------------------------------------------------------
function applyState(st) {
  const key = previewKey(st.preview?.reported) + previewKey(st.preview?.desired);
  if (key !== S.previewKey) {
    S.previewKey = key;
    S.previewStarted = performance.now();
    S.previewElapsed = 0;
    S.previewRate = 1;
    try {
      const saved = Number(localStorage.getItem(previewRateKey(st)));
      if (Number.isFinite(saved) && saved >= 0.25 && saved <= 4) S.previewRate = saved;
    } catch { /* A preview correction is optional when browser storage is unavailable. */ }
  }
  S.state = st;
  setSimBadge(st.simulated === true);
  if (!S.dirty || S.draft.length !== st.heads.length) {
    S.draft = st.heads.map((x) => ({ ...x.paint }));
    S.dirty = false;
  }
  if (!S.built) buildControls(st);
  renderAll();
  startPreviewAnimation();
}

function previewSeconds(now) {
  return S.previewElapsed + (S.previewPaused ? 0 : (now - S.previewStarted) / 1000 * S.previewRate);
}

function previewRateKey(state = S.state) {
  return `module-preview-rate:${S.deviceId}:${previewKey(state?.preview?.reported)}`;
}

function setPreviewRate(rate) {
  if (!Number.isFinite(rate) || rate < 0.25 || rate > 4) return;
  const now = performance.now();
  S.previewElapsed = previewSeconds(now);
  S.previewStarted = now;
  S.previewRate = rate;
  try { localStorage.setItem(previewRateKey(), String(rate)); } catch { /* Optional local preference. */ }
  startPreviewAnimation();
}

function paintPreviewFrame(now) {
  if (!S.state) return;
  const elapsed = previewSeconds(now), st = S.state;
  const shown = previewFrame(st.preview?.reported, st.heads.length, elapsed);
  const wanted = previewFrame(st.preview?.desired, st.heads.length, elapsed);
  if (!shown && !wanted) return;
  $('map').querySelectorAll('g[data-index]').forEach(group => {
    const i = Number(group.dataset.index), data = st.heads[i], draft = S.draft[i];
    const dirty = draft && !sameEntry(draft, data.paint);
    const color = shown?.[i] || data.reported;
    group.querySelector('.core').setAttribute('fill', css(color));
    if (!dirty) group.querySelector('.ring').setAttribute('stroke', css(wanted?.[i] || data.desired));
    const number = group.querySelector('.num');
    if (number && !dirty) number.setAttribute('fill', luminance(color) > 140 ? '#111' : '#fff');
  });
}

function startPreviewAnimation() {
  cancelAnimationFrame(S.previewRaf);
  const preview = S.state?.preview?.reported;
  $('preview-note').textContent = previewNotice(preview, S.previewPaused);
  $('preview-toggle').hidden = !preview?.animated;
  $('preview-toggle').textContent = S.previewPaused ? 'Play preview' : 'Pause preview';
  $('preview-rate-controls').hidden = !preview?.animated;
  $('preview-rate').value = S.previewRate;
  $('preview-rate-out').textContent = `${S.previewRate.toFixed(2)}×`;
  if (!S.lease || document.hidden || S.previewPaused || !(preview?.animated || S.state?.preview?.desired?.animated)) return;
  let last = 0;
  const frame = now => {
    if (!S.lease || document.hidden || S.previewPaused) return;
    if (now - last >= 50) { paintPreviewFrame(now); last = now; }
    S.previewRaf = requestAnimationFrame(frame);
  };
  S.previewRaf = requestAnimationFrame(frame);
}

function renderAll() {
  const st = S.state;
  if (!st) return;
  $("device-name").textContent = st.label;
  $("device-name").title = st.device_id;
  renderStatus();
  renderMap();
  renderLook();
  renderTelemetry();
  renderT1();
  renderSlots();
  renderSweep();
  renderEffectsNote();
  renderCalibration();
  renderHold();
  renderBusy();
}

function renderStatus() {
  const pill = $("status-pill");
  const st = S.state;
  pill.hidden = false;
  if (!S.lease) {
    pill.className = "pill offline";
    pill.textContent = "Not holding";
    return;
  }
  const last = st.telemetry.recent[0];
  if (st.busy || S.inflight) {
    pill.className = "pill updating";
    pill.textContent = "Updating";
  } else if (st.diff.length || st.settings_mismatches?.length || st.read_error || (last && !["applied", "superseded"].includes(last.status))) {
    pill.className = "pill attention";
    pill.textContent = "Needs attention";
  } else {
    pill.className = "pill ready";
    pill.textContent = "Ready";
  }
}

function renderBusy() {
  const busy = S.inflight > 0 || (S.state && S.state.busy);
  const apply = $("apply-btn");
  const t1 = S.state && S.state.t1.allowed;
  apply.disabled = !S.lease || !t1 || S.inflight > 0 || (S.state && S.state.hold.on);
  apply.textContent = S.inflight > 0 ? "Sending…" : `Apply to ${shortLabel(S.paintSlot)}`;
  apply.classList.toggle("dirty", S.dirty);
  document.body.classList.toggle("is-busy", !!busy);
  if (S.state) renderStatus();
}

// ---- the map ----------------------------------------------------------------------------------------------------
function renderMap() {
  const st = S.state;
  const map = st.map;
  const svg = $("map");
  const pad = 4;
  svg.setAttribute("viewBox", `${-pad} ${-pad} ${map.width_in + 2 * pad} ${map.height_in + 2 * pad}`);
  svg.replaceChildren();
  svg.append(s("rect", { class: "panel", x: 0, y: 0, width: map.width_in, height: map.height_in, rx: 1.5 }));
  const r = 3.3;
  if (S.showWire) {
    const pts = map.polyline.map(([x, y]) => `${x},${y}`).join(" ");
    svg.append(s("polyline", { class: "wire", points: pts }));
  }
  for (const head of map.heads) {
    const i = head.index;
    const data = st.heads[i];
    const g = s("g", { "data-index": i });
    const draft = S.draft[i];
    const isDirty = draft && !sameEntry(draft, data.paint);
    const ringColor = isDirty ? entryColor(draft) : data.desired;
    if (S.selection.has(i)) g.append(s("circle", { class: "sel", cx: head.x, cy: head.y, r: r + 1.35 }));
    g.append(s("circle", { class: "core", cx: head.x, cy: head.y, r: r - 0.9, fill: css(data.reported) }));
    g.append(
      s("circle", {
        class: `ring${isDirty ? " draft" : ""}`,
        cx: head.x,
        cy: head.y,
        r: r - 0.3,
        stroke: isDirty ? "#7FB2FF" : css(ringColor.r + ringColor.g + ringColor.b < 30 ? { r: 70, g: 64, b: 58 } : ringColor),
      }),
    );
    if (isDirty) g.append(s("circle", { cx: head.x, cy: head.y, r: r - 1.6, fill: css(ringColor), opacity: 0.9 }));
    if (!data.match) g.append(s("circle", { class: "miss", cx: head.x + r * 0.72, cy: head.y - r * 0.72, r: 0.95 }));
    if (S.showWire) {
      const shown = isDirty ? entryColor(draft) : data.reported;
      const t = s("text", { class: "num", x: head.x, y: head.y, fill: luminance(shown) > 140 ? "#111" : "#fff" });
      t.textContent = String(i);
      g.append(t);
    }
    svg.append(g);
  }
  if (S.showWire && map.heads.length) {
    const first = map.heads[0];
    svg.append(s("circle", { class: "wire-start", cx: first.x, cy: first.y, r: 0.9, transform: `translate(${-r - 0.9} 0)` }));
  }
  if (S.drag && S.drag.moved) {
    const { x0, y0, x1, y1 } = S.drag;
    svg.append(
      s("rect", {
        class: "lasso",
        x: Math.min(x0, x1),
        y: Math.min(y0, y1),
        width: Math.abs(x1 - x0),
        height: Math.abs(y1 - y0),
      }),
    );
  }
  const n = S.selection.size;
  $("selection-count").textContent = n ? `${n} selected` : "Tap heads to select · drag to select an area";
  paintPreviewFrame(performance.now());
}

function svgPoint(evt) {
  const svg = $("map");
  const pt = svg.createSVGPoint();
  pt.x = evt.clientX;
  pt.y = evt.clientY;
  const m = svg.getScreenCTM();
  return m ? pt.matrixTransform(m.inverse()) : { x: 0, y: 0 };
}

function nearestHead(p, maxDist = 5.2) {
  let best = null;
  let bestD = maxDist;
  for (const head of S.state.map.heads) {
    const d = Math.hypot(head.x - p.x, head.y - p.y);
    if (d < bestD) {
      best = head.index;
      bestD = d;
    }
  }
  return best;
}

function setupMapInput() {
  const svg = $("map");
  svg.addEventListener("pointerdown", (e) => {
    if (!S.state) return;
    markActive();
    const p = svgPoint(e);
    S.drag = { x0: p.x, y0: p.y, x1: p.x, y1: p.y, moved: false, base: new Set(S.selection), id: e.pointerId };
    svg.setPointerCapture(e.pointerId);
  });
  svg.addEventListener("pointermove", (e) => {
    if (!S.drag || S.drag.id !== e.pointerId) return;
    const p = svgPoint(e);
    S.drag.x1 = p.x;
    S.drag.y1 = p.y;
    if (!S.drag.moved && Math.hypot(p.x - S.drag.x0, p.y - S.drag.y0) > 2) S.drag.moved = true;
    if (S.drag.moved) {
      const { x0, y0, x1, y1 } = S.drag;
      const [lx, hx] = [Math.min(x0, x1), Math.max(x0, x1)];
      const [ly, hy] = [Math.min(y0, y1), Math.max(y0, y1)];
      S.selection = new Set(S.drag.base);
      for (const head of S.state.map.heads) {
        if (head.x >= lx - 1 && head.x <= hx + 1 && head.y >= ly - 1 && head.y <= hy + 1) S.selection.add(head.index);
      }
      renderMap();
    }
  });
  const end = (e) => {
    if (!S.drag || S.drag.id !== e.pointerId) return;
    if (!S.drag.moved) {
      const i = nearestHead({ x: S.drag.x0, y: S.drag.y0 });
      if (i !== null) {
        if (S.selection.has(i)) S.selection.delete(i);
        else S.selection.add(i);
      }
    }
    S.drag = null;
    renderMap();
  };
  svg.addEventListener("pointerup", end);
  svg.addEventListener("pointercancel", end);

  document.querySelectorAll("[data-select]").forEach((b) =>
    b.addEventListener("click", () => {
      const all = S.state.heads.map((x) => x.index);
      if (b.dataset.select === "all") S.selection = new Set(all);
      else if (b.dataset.select === "none") S.selection = new Set();
      else S.selection = new Set(all.filter((i) => !S.selection.has(i)));
      renderMap();
    }),
  );
  $("wire-toggle").addEventListener("click", (e) => {
    S.showWire = !S.showWire;
    e.currentTarget.setAttribute("aria-pressed", String(S.showWire));
    renderMap();
  });
}

function renderLook() {
  const st = S.state;
  $("reported-look").textContent = st.reported_look;
  const line = $("diff-line");
  if (st.read_error) {
    line.textContent = `Readback problem: ${st.read_error}`;
    line.className = "bad";
  } else if (st.preview?.reported || st.preview?.desired) {
    const mismatch = st.settings_mismatches || [];
    line.textContent = mismatch.length ? `Settings differ from what we sent: ${mismatch.join(', ')}.` : 'Preset settings match the last cloud readback. Observe the real light to verify the animation.';
    line.className = mismatch.length ? 'bad' : 'muted';
  } else if (st.diff.length) {
    line.textContent = `${st.diff.length} head(s) differ from what we sent: ${st.diff.slice(0, 12).join(", ")}${st.diff.length > 12 ? "…" : ""}`;
    line.className = "bad";
  } else {
    line.textContent = st.reported_at ? "Reported color settings match what we sent; physical color is not measured." : "";
    line.className = "muted";
  }
}

// ---- telemetry --------------------------------------------------------------------------------------------------
function renderTelemetry() {
  const t = S.state.telemetry;
  const last = t.recent[0];
  const line = $("tele-last");
  line.replaceChildren();
  if (!last) {
    line.textContent = "No commands sent yet.";
  } else {
    const ok = last.status === "applied";
    line.append(
      h("span", { class: ok ? "" : "bad", text: ok ? "Applied" : last.status.toUpperCase() }),
      ` · ${last.label}`,
      last.simulated ? " · " : "",
      last.simulated ? h("span", { class: "sim", text: "SIMULATED" }) : "",
    );
  }
  const st = t.session;
  const fmtS = (v) => (v == null ? "–" : fmtMs(v * 1000));
  const lastBits = last
    ? `Last: HTTP ${last.http_status ?? "–"} in ${fmtMs(last.send_to_ack_ms)}, readback ${fmtMs(last.send_to_readback_ms)}. `
    : "";
  $("tele-session").textContent =
    `${lastBits}Session (${st.count}): send→HTTP p50 ${fmtS(st.ack_p50_s)} · p95 ${fmtS(st.ack_p95_s)}; ` +
    `send→readback p50 ${fmtS(st.verified_p50_s)} · p95 ${fmtS(st.verified_p95_s)}. ` +
    `Last error: ${t.last_error || "none"}.`;
  if (!$("tele-details").hidden) renderTelemetryDetails();
}

function renderTelemetryDetails() {
  const rows = S.state.telemetry.recent.map((c) =>
    h(
      "tr",
      {},
      h("td", { text: new Date(c.at * 1000).toLocaleTimeString() }),
      h("td", { text: c.label }),
      h("td", { text: c.status }),
      h("td", { text: c.http_status ?? "–" }),
      h("td", { text: fmtMs(c.send_to_ack_ms) }),
      h("td", { text: fmtMs(c.send_to_readback_ms) }),
      h("td", { text: `${c.bytes} B` }),
      h("td", { text: c.error || "" }),
    ),
  );
  const head = ["Time", "Command", "Result", "HTTP", "Send→HTTP", "Send→readback", "Size", "Error"];
  $("tele-details").replaceChildren(
    h("table", {}, h("thead", {}, h("tr", {}, head.map((x) => h("th", { text: x })))), h("tbody", {}, rows)),
  );
}

// ---- T1 gating --------------------------------------------------------------------------------------------------
function renderT1() {
  const st = S.state;
  const note = $("t1-note");
  const allowed = st.t1.allowed;
  // On the simulator T1 is simply allowed (the Simulator badge says so); on real hardware always say why.
  note.hidden = allowed && st.simulated;
  note.textContent = st.t1.reason;
  for (const id of ["paint-selected", "paint-all", "draft-off", "draft-reset"]) $(id).disabled = !allowed;
  document.querySelectorAll("[data-gradient]").forEach((b) => (b.disabled = !allowed));
  document.querySelectorAll("[data-needs-t1]").forEach((b) => (b.disabled = !allowed));
}

// ---- build the controls once per open ------------------------------------------------------------------------------
function buildControls(st) {
  S.built = true;
  const caps = st.capabilities;
  S.paintSlot = st.paint_slot || "custom-3";

  // Paint: slot picker and recipe swatches
  const slotSeg = $("paint-slot");
  slotSeg.replaceChildren(
    ...(caps.slots.length ? caps.slots : ["custom-1", "custom-2", "custom-3"]).map((slot) =>
      h("button", {
        type: "button",
        "data-slot": slot,
        "aria-pressed": String(slot === S.paintSlot),
        text: shortLabel(slot),
        onclick: () => {
          S.paintSlot = slot;
          slotSeg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.slot === slot)));
          renderBusy();
        },
      }),
    ),
  );
  const swatches = $("recipe-swatches");
  swatches.replaceChildren(
    ...Object.entries(st.recipes).map(([id, rec]) =>
      h(
        "button",
        { type: "button", class: "swatch", title: rec.label, onclick: () => useRecipe(id) },
        h("i", { style: `background:${rec.hex}` }),
        rec.label,
      ),
    ),
  );

  // Paint: brush ranges follow the device's CCT range
  const [lo, hi] = caps.cct_range || [2700, 6500];
  for (const id of ["brush-cct", "m-cct"]) {
    $(id).min = lo;
    $(id).max = hi;
  }
  const [blo, bhi] = caps.brightness_range || [1, 100];
  $("m-bri").min = blo;
  $("m-bri").max = bhi;
  if (caps.speed_range) {
    $("m-speed").min = caps.speed_range[0];
    $("m-speed").max = caps.speed_range[1];
  }
  $("m-preset").replaceChildren(...caps.presets.map((p) => h("option", { value: p, text: PRESET_LABELS[p] || p })));
  $("m-program").replaceChildren(...caps.programs.map((p) => h("option", { value: p, text: p })));
  $("m-preset-btn").disabled = !caps.presets.length;
  $("m-program-btn").disabled = !caps.programs.length;

  buildCards(st);
  buildEffects(st);

  const cal = st.map.calibration;
  S.cal = { orientation: cal.orientation, cable_path: cal.cable_path, corner_shown: cal.start_corner_as_shown };
  updateBrush();
}

// ---- Paint tab --------------------------------------------------------------------------------------------------
function brush() {
  const c = parseHex($("brush-color").value);
  const cb = Number($("brush-cb").value);
  if (S.whiteMode === "off") return { ...c, cct: 0, colorBrightness: cb, whiteBrightness: 0 };
  return { ...c, cct: Number($("brush-cct").value), colorBrightness: cb, whiteBrightness: Number($("brush-wb").value) };
}

function setWhiteMode(mode) {
  S.whiteMode = mode;
  document.querySelectorAll("#white-mode button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.white === mode)));
  $("white-controls").hidden = mode === "off";
  const range = S.state?.capabilities?.cct_range || [2700, 6500];
  if (mode === "warm") $("brush-cct").value = range[0];
  if (mode === "cool") $("brush-cct").value = range[1];
  updateBrush();
}

function updateBrush() {
  $("cb-out").textContent = `${$("brush-cb").value}%`;
  $("wb-out").textContent = `${$("brush-wb").value}%`;
  $("cct-out").textContent = `${$("brush-cct").value} K`;
  $("brush-preview").style.background = css(entryColor(brush()));
}

function useRecipe(id) {
  const rec = S.state.recipes[id];
  const e = rec[S.recipeKind];
  $("brush-color").value = rgbHex(e);
  $("brush-cb").value = e.colorBrightness;
  if (e.cct && e.whiteBrightness) {
    S.whiteMode = "warm";
    document.querySelectorAll("#white-mode button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.white === "warm")));
    $("white-controls").hidden = false;
    $("brush-cct").value = e.cct;
    $("brush-wb").value = e.whiteBrightness;
  } else {
    setWhiteMode("off");
  }
  updateBrush();
  toast(`Brush: ${rec.label} (${S.recipeKind === "blend" ? "RGB + warm white" : "RGB only"})`, "info", 2000);
}

function setDraft(indices, entryFor) {
  for (const i of indices) S.draft[i] = entryFor(i);
  S.dirty = S.draft.some((e, i) => !sameEntry(e, S.state.heads[i].paint));
  renderMap();
  renderBusy();
}

function setupPaint() {
  $("brush-color").addEventListener("input", updateBrush);
  for (const id of ["brush-cb", "brush-wb", "brush-cct"]) $(id).addEventListener("input", updateBrush);
  document.querySelectorAll("#white-mode button").forEach((b) => b.addEventListener("click", () => setWhiteMode(b.dataset.white)));
  document.querySelectorAll("#recipe-kind button").forEach((b) =>
    b.addEventListener("click", () => {
      S.recipeKind = b.dataset.kind;
      document.querySelectorAll("#recipe-kind button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    }),
  );
  $("paint-selected").addEventListener("click", () => {
    markActive();
    if (!S.selection.size) {
      toast("Select some heads on the map first (tap, or drag across them).", "error");
      return;
    }
    const e = brush();
    setDraft(S.selection, () => ({ ...e }));
  });
  $("paint-all").addEventListener("click", () => {
    markActive();
    const e = brush();
    setDraft(S.state.heads.map((x) => x.index), () => ({ ...e }));
  });
  document.querySelectorAll("[data-gradient]").forEach((b) =>
    b.addEventListener("click", () => {
      markActive();
      const axis = b.dataset.gradient;
      const from = brush();
      const to = { ...from, ...parseHex($("brush-to").value) };
      const heads = S.state.map.heads.filter((x) => !S.selection.size || S.selection.has(x.index));
      const coords = heads.map((x) => x[axis]);
      const lo = Math.min(...coords);
      const hi = Math.max(...coords);
      const at = Object.fromEntries(heads.map((x) => [x.index, hi === lo ? 0 : (x[axis] - lo) / (hi - lo)]));
      setDraft(Object.keys(at).map(Number), (i) => mix(from, to, at[i]));
    }),
  );
  $("draft-off").addEventListener("click", () => setDraft(S.state.heads.map((x) => x.index), () => ({ ...OFF })));
  $("draft-reset").addEventListener("click", () => {
    S.draft = S.state.heads.map((x) => ({ ...x.paint }));
    S.dirty = false;
    renderMap();
    renderBusy();
  });
  $("apply-btn").addEventListener("click", async () => {
    const heads = S.draft.map((e, index) => ({ index, ...e }));
    const res = await act("paint", { slot: S.paintSlot, base: "off", heads });
    if (res) {
      S.dirty = false;
      S.draft = res.state.heads.map((x) => ({ ...x.paint }));
      renderMap();
      renderBusy();
    }
  });
}

// ---- Module tab -------------------------------------------------------------------------------------------------
function setupModule() {
  const out = (id, fmt) => $(id).addEventListener("input", () => ($(`${id}-out`).textContent = fmt($(id).value)));
  out("m-bri", (v) => `${v}%`);
  out("m-cct", (v) => `${v} K`);
  out("m-speed", (v) => (Number(v) > 0 ? `+${v}` : v));
  const bri = () => Number($("m-bri").value);
  document.querySelectorAll("[data-power]").forEach((b) => b.addEventListener("click", () => act("whole", { power: b.dataset.power === "on" })));
  $("m-bri-btn").addEventListener("click", () => act("whole", { brightness: bri() }));
  $("m-white-btn").addEventListener("click", () =>
    act("whole", { look: { kind: "white", cct: Number($("m-cct").value), brightness: bri() } }),
  );
  $("m-rgb-btn").addEventListener("click", () => {
    const c = parseHex($("m-rgb").value);
    act("whole", { look: { kind: "rgb", rgb: [c.r, c.g, c.b], brightness: bri() } });
  });
  $("m-preset-btn").addEventListener("click", () =>
    act("whole", { look: { kind: "preset", preset: $("m-preset").value, speed: Number($("m-speed").value), brightness: bri() } }),
  );
  $("m-program-btn").addEventListener("click", () => act("whole", { look: { kind: "program", program: $("m-program").value } }));
}

// ---- Test cards ---------------------------------------------------------------------------------------------------
function cardShell(card, ...body) {
  const tags = [];
  if (card.needs_t1) tags.push(h("span", { class: "tag t1", text: "per head (T1)" }));
  if (card.placeholder) tags.push(h("span", { class: "tag", text: "later" }));
  return h(
    "div",
    { class: `tcard${card.placeholder ? " placeholder" : ""}`, "data-card": card.id },
    h("h3", {}, card.title, ...tags),
    h("p", { text: card.description }),
    ...body,
  );
}

function runCard(id, params = {}) {
  return act("card", { card: id, params });
}

function colorSelect(options, value) {
  return h("select", {}, options.map((c) => h("option", { value: c.id, text: c.label, selected: c.id === value })));
}

function buildCards(st) {
  const root = $("cards");
  root.replaceChildren();
  const t1 = (btn) => {
    btn.setAttribute("data-needs-t1", "");
    return btn;
  };
  for (const card of st.cards) {
    if (card.id === "white") {
      root.append(
        cardShell(
          card,
          h(
            "div",
            { class: "row" },
            card.options.kelvin.map((k) =>
              h("button", { type: "button", class: "big", text: `${k} K`, onclick: () => runCard("white", { kelvin: k }) }),
            ),
          ),
        ),
      );
    } else if (card.id === "color") {
      let kind = card.defaults.recipe;
      const seg = h(
        "div",
        { class: "seg", role: "group", "aria-label": "Painted recipe" },
        ["rgb_only", "blend"].map((k) =>
          h("button", {
            type: "button",
            "aria-pressed": String(k === kind),
            text: k === "blend" ? "Painted: RGB + white" : "Painted: RGB only",
            onclick: (e) => {
              kind = k;
              seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget)));
            },
          }),
        ),
      );
      const rows = card.options.color.map((c) =>
        h(
          "div",
          { class: "color-row" },
          h("span", { class: "name" }, h("i", { style: `background:${c.hex}` }), c.label),
          h("button", { type: "button", class: "secondary", text: "Whole strand", onclick: () => runCard("color", { color: c.id }) }),
          t1(
            h("button", {
              type: "button",
              text: "Painted",
              onclick: () => runCard("color", { color: c.id, painted: true, recipe: kind }),
            }),
          ),
        ),
      );
      root.append(cardShell(card, h("div", {}, rows), seg));
    } else if (card.id === "recipe-compare") {
      const sel = colorSelect(card.options.color, card.defaults.color);
      root.append(
        cardShell(
          card,
          h("label", {}, "Colour ", sel),
          t1(h("button", { type: "button", class: "wide", text: "Compare", onclick: () => runCard(card.id, { color: sel.value }) })),
        ),
      );
    } else if (card.id === "thirds") {
      const bands = card.defaults.bands.map((b, n) => {
        const sel = colorSelect(card.options.color, b.color);
        const range = h("input", { type: "range", min: 0, max: 100, value: b.brightness });
        const out = h("output", { text: `${b.brightness}%` });
        range.addEventListener("input", () => (out.textContent = `${range.value}%`));
        const recipe = h("select", {}, ["blend", "rgb_only"].map((k) => h("option", { value: k, text: k === "blend" ? "RGB + white" : "RGB only", selected: k === b.recipe })));
        return {
          node: h(
            "div",
            { class: "band" },
            h("strong", { text: ["Left", "Middle", "Right"][n] }),
            sel,
            h("span", {}),
            recipe,
            h("label", { class: "slider" }, h("span", {}, "Brightness ", out), range),
          ),
          value: () => ({ color: sel.value, recipe: recipe.value, brightness: Number(range.value) }),
        };
      });
      root.append(
        cardShell(
          card,
          ...bands.map((b) => b.node),
          t1(h("button", { type: "button", class: "wide", text: "Show thirds", onclick: () => runCard(card.id, { bands: bands.map((b) => b.value()) }) })),
        ),
      );
    } else if (card.id === "hue-ramp") {
      root.append(cardShell(card, t1(h("button", { type: "button", class: "wide", text: "Show hue ramp", onclick: () => runCard(card.id) }))));
    } else if (card.id === "index-sweep") {
      const step = h("input", {
        type: "number",
        min: st.settings.min_write_interval_s,
        max: 60,
        step: 0.5,
        value: st.settings.sweep_step_s,
        inputmode: "decimal",
        id: "sweep-step",
      });
      const sweep = (action) => act("sweep", { action, step_s: Number(step.value) || undefined });
      root.append(
        cardShell(
          card,
          h("label", { class: "stepper" }, "Seconds per step ", step),
          h("div", { class: "sweep-status", id: "sweep-status" }),
          h(
            "div",
            { class: "button-grid" },
            t1(h("button", { type: "button", class: "big", id: "sweep-start", text: "Start", onclick: () => sweep("start") })),
            h("button", { type: "button", class: "big secondary", id: "sweep-pause", text: "Pause", onclick: () => sweep("pause") }),
            h("button", { type: "button", class: "big secondary", id: "sweep-resume", text: "Resume", onclick: () => sweep("resume") }),
            h("button", { type: "button", class: "big secondary", id: "sweep-stop", text: "Stop", onclick: () => sweep("stop") }),
          ),
        ),
      );
    } else if (card.id === "single-head") {
      const n = st.heads.length;
      const input = h("input", { type: "number", min: 0, max: n - 1, value: card.defaults.index, inputmode: "numeric", id: "single-index" });
      const bump = (d) => {
        input.value = String((Number(input.value) + d + n) % n);
        runCard(card.id, { index: Number(input.value) });
      };
      root.append(
        cardShell(
          card,
          h(
            "div",
            { class: "stepper" },
            t1(h("button", { type: "button", class: "secondary", text: "−", "aria-label": "Previous head", onclick: () => bump(-1) })),
            input,
            t1(h("button", { type: "button", class: "secondary", text: "+", "aria-label": "Next head", onclick: () => bump(1) })),
            t1(h("button", { type: "button", text: "Light it", onclick: () => runCard(card.id, { index: Number(input.value) }) })),
          ),
        ),
      );
    } else if (card.id === "lowcountry-preview" && !card.placeholder) {
      root.append(lowcountryCard(card));
    } else if (card.placeholder) {
      root.append(cardShell(card, h("button", { type: "button", class: "wide secondary", disabled: true, text: "Arrives with the Lowcountry Modes" })));
    }
  }
}

/** "Preview a Lowcountry Mode": choose a mode and a canopy position; the server compiles that position's slice
 * (T1 frame into the scratch slot where painting is allowed, else the strand / module-level look). */
function lowcountryCard(card) {
  let mode = card.defaults.mode;
  let position = card.defaults.position;
  const byId = Object.fromEntries(card.options.modes.map((m) => [m.id, m]));
  const blurb = h("p", { class: "lc-blurb" });
  const result = h("p", { class: "lc-result", "aria-live": "polite" });
  const modeSel = h(
    "select",
    { id: "lc-mode", "aria-label": "Lowcountry Mode" },
    card.options.modes.map((m) => h("option", { value: m.id, text: m.label, selected: m.id === mode })),
  );
  const swatches = h("div", { class: "lc-swatches", "aria-hidden": "true" });
  const showMode = () => {
    const m = byId[mode];
    blurb.textContent = m.blurb;
    swatches.replaceChildren(...m.preview.map((hex) => h("i", { style: `background:${hex}` })));
  };
  modeSel.addEventListener("change", () => {
    mode = modeSel.value;
    showMode();
  });
  const grid = h(
    "div",
    { class: "lc-grid", role: "group", "aria-label": "Canopy position (looking up)" },
    card.options.positions.flat().map((p) =>
      h("button", {
        type: "button",
        "data-position": p.id,
        "aria-pressed": String(p.id === position),
        title: `Module ${p.id} (orientation ${p.orientation})`,
        text: p.id,
        onclick: (e) => {
          position = p.id;
          grid.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget)));
        },
      }),
    ),
  );
  const go = h("button", {
    type: "button",
    class: "wide",
    id: "lc-apply",
    text: "Preview on this module",
    onclick: async () => {
      result.textContent = "";
      const res = await runCard(card.id, { mode, position });
      const r = res && res.result;
      if (r && r.tier) {
        const tier = { canvas: "painted per head (T1)", modules: "module-level look (T3)", strand: "whole-strand look" }[r.tier] || r.tier;
        result.textContent = `${byId[mode].label} at ${position}: ${tier}. Kiosk copy: “${r.feature}”`;
      }
    },
  });
  showMode();
  return cardShell(
    card,
    h("label", {}, "Mode ", modeSel),
    swatches,
    blurb,
    h("div", { class: "lc-grid-label", text: "Where this module would hang (looking up at the canopy)" }),
    grid,
    go,
    result,
  );
}

function renderSweep() {
  const el = $("sweep-status");
  if (!el) return;
  const sw = S.state.sweep;
  const n = S.state.heads.length;
  const where = sw.index === null || sw.index === undefined ? "no head lit yet" : `head ${sw.index} lit (${sw.index + 1} of ${n})`;
  const label = { idle: "Not running", running: "Running", paused: "Paused", stopped: "Stopped", done: "Done" }[sw.state] || sw.state;
  el.textContent = `${label} · ${where} · ${sw.step_s} s per step${sw.message && sw.state !== "running" ? ` · ${sw.message}` : ""}`;
  $("sweep-start").textContent = ["running", "paused"].includes(sw.state) ? "Start over" : "Start";
  $("sweep-pause").disabled = sw.state !== "running";
  $("sweep-resume").disabled = sw.state !== "paused";
  $("sweep-stop").disabled = !["running", "paused"].includes(sw.state);
}

// ---- Slots --------------------------------------------------------------------------------------------------------
function miniMap(heads, colors) {
  const map = S.state.map;
  const svg = s("svg", { viewBox: `-2 -2 ${map.width_in + 4} ${map.height_in + 4}`, role: "img", "aria-label": "Slot pattern" });
  for (const head of heads) {
    svg.append(s("circle", { cx: head.x, cy: head.y, r: 3.2, fill: css(colors[head.index] || { r: 0, g: 0, b: 0 }) }));
  }
  return svg;
}

function renderSlots() {
  const root = $("slots");
  const st = S.state;
  const t1 = st.t1.allowed;
  // The module's mapped heads; a controller pattern of another length (60 entries on a 30-head kit) is named too.
  const heads = st.map.heads.length;
  const pattern = (slot) => (slot.length != null && slot.length !== heads ? ` (${slot.length}-entry pattern)` : "");
  root.replaceChildren(
    ...st.slots.map((slot) => {
      const origin = slot.recorded_origin === "app" ? "hand-painted (app)" : "commissioned";
      const recorded =
        slot.recorded_hash == null
          ? ""
          : slot.recorded_hash === slot.hash
            ? ` · matches the recorded ${origin} hash`
            : ` · DIFFERS from the recorded ${origin} hash`;
      return h(
        "div",
        { class: `slot-card${slot.active ? " active" : ""}` },
        h("h3", {}, shortLabel(slot.slot), slot.active ? h("span", { class: "pill ready", text: "Showing" }) : ""),
        miniMap(st.map.heads, slot.heads),
        h("div", { class: "meta", text: `${heads} heads${pattern(slot)} · hash ${slot.hash ?? "–"}${recorded}` }),
        h(
          "div",
          { class: "button-grid" },
          h("button", { type: "button", text: "Select", onclick: () => act("slot", { slot: slot.slot, action: "select" }) }),
          h("button", {
            type: "button",
            class: "secondary",
            text: "Write current paint",
            disabled: !t1,
            title: t1 ? "Writes the applied paint into this slot without selecting it" : st.t1.reason,
            onclick: () => act("slot", { slot: slot.slot, action: "write_current" }, { label: `Wrote paint into ${slot.slot}` }),
          }),
          h("button", {
            type: "button",
            class: "secondary",
            text: "Load into paint",
            onclick: async () => {
              const res = await act("slot", { slot: slot.slot, action: "load" }, { label: `Loaded ${slot.slot} into the paint` });
              if (res) {
                S.dirty = false;
                S.draft = res.state.heads.map((x) => ({ ...x.paint }));
                S.paintSlot = res.state.paint_slot;
                document.querySelectorAll("#paint-slot button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.slot === S.paintSlot)));
                renderAll();
              }
            },
          }),
          h("button", {
            type: "button",
            class: "secondary",
            text: "Painted in the Hubspace app — record it",
            title: "Reads this slot back and saves its fingerprint as hand-painted (T2). Sends nothing to the lights.",
            onclick: async () => {
              const ok = window.confirm(
                `Record ${shortLabel(slot.slot)} as a pattern painted in the Hubspace app?\n\n` +
                  "This reads the slot back and saves its fingerprint, so the show can select it at the hand-painted " +
                  "tier (T2). Nothing is sent to the lights. Only do this after painting it in the app: the Conductor " +
                  "never rewrites a hand-painted slot, and commissioning leaves it alone.",
              );
              if (!ok) return;
              const res = await act("slot", { slot: slot.slot, action: "record_app" });
              if (res && res.slot) toast(res.slot.message, "info", 9000);
            },
          }),
          h("button", {
            type: "button",
            class: "secondary",
            text: "Copy JSON",
            onclick: async () => {
              const res = await act("slot", { slot: slot.slot, action: "read" });
              if (res && res.slot) toast((await copyJSON(res.slot.value)) ? `${slot.slot} copied` : "Couldn't copy", "info", 2500);
            },
          }),
        ),
      );
    }),
  );
}

// ---- Effects ------------------------------------------------------------------------------------------------------
function buildEffects(st) {
  $("fx-slot").replaceChildren(...st.slots.map((x) => h("option", { value: x.slot, text: shortLabel(x.slot) })));
  const opts = (codes) => [h("option", { value: "0", text: "0 · none (static)" }), ...Object.entries(codes).map(([c, label]) => h("option", { value: c, text: `${c} · ${label}` }))];
  $("fx-motion").replaceChildren(...opts(st.effects.enabled.motionEffect || {}));
  $("fx-bright").replaceChildren(...opts(st.effects.enabled.brightnessEffect || {}));
  for (const id of ["fx-ms", "fx-bs", "fx-bd"]) $(id).addEventListener("input", () => ($(`${id}-out`).textContent = $(id).value));
}

function renderEffectsNote() {
  const fx = S.state.effects;
  const note = $("effects-note");
  note.hidden = fx.any_enabled;
  note.textContent = fx.message || "";
  for (const id of ["fx-motion", "fx-bright", "fx-ms", "fx-bs", "fx-bd", "fx-start"]) $(id).disabled = !fx.any_enabled || !S.state.t1.allowed;
}

function setupEffects() {
  $("fx-start").addEventListener("click", () =>
    act("effects", {
      slot: $("fx-slot").value,
      action: "start",
      motionEffect: Number($("fx-motion").value),
      brightnessEffect: Number($("fx-bright").value),
      speeds: { motionSpeed: Number($("fx-ms").value), brightnessSpeed: Number($("fx-bs").value), brightnessDepth: Number($("fx-bd").value) },
    }),
  );
  $("fx-restart").addEventListener("click", () => act("effects", { slot: $("fx-slot").value, action: "restart" }));
}

// ---- Calibration --------------------------------------------------------------------------------------------------
function renderCalibration() {
  const map = S.state.map;
  const cal = map.calibration;
  const pressed = (groupId, value) =>
    document.querySelectorAll(`#${groupId} button`).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.value === value)));
  pressed("cal-orientation", S.cal.orientation);
  pressed("cal-path", S.cal.cable_path);
  pressed("cal-corner", S.cal.corner_shown);
  const status = $("cal-status");
  const path = cal.cable_path === "serpentine_cols" ? "columns" : "rows";
  const saved = cal.calibrated ? "Saved" : "Not calibrated yet (default)";
  const flipped = cal.wire_order ? ", custom/flipped order" : "";
  const hop = map.hops_fit
    ? `every hop fits the ${map.puck_spacing_in}″ cable`
    : `a hop of ${map.longest_hop_in}″ is longer than the ${map.puck_spacing_in}″ cable — check the routing`;
  status.textContent = `${saved}: orientation ${cal.orientation}, along ${path}, head 0 at the ${cal.start_corner_as_shown.replace("-", " ")}${flipped}; ${hop}.`;
  status.className = `cal-status${map.hops_fit ? "" : " bad"}`;
}

function setupCalibration() {
  const bind = (groupId, key) =>
    document.querySelectorAll(`#${groupId} button`).forEach((b) =>
      b.addEventListener("click", () => {
        S.cal[key] = b.dataset.value;
        renderCalibration();
      }),
    );
  bind("cal-orientation", "orientation");
  bind("cal-path", "cable_path");
  bind("cal-corner", "corner_shown");
  const body = () => ({
    orientation: S.cal.orientation,
    cable_path: S.cal.cable_path,
    start_corner: S.cal.orientation === "B" ? SHOWN[S.cal.corner_shown] : S.cal.corner_shown,
  });
  $("cal-save").addEventListener("click", () => act("calibration", body(), { label: "Calibration saved" }));
  $("cal-flip").addEventListener("click", () => {
    const order = S.state.map.heads.map((x) => [x.ax, x.ay]);
    act("calibration", { ...body(), wire_order: order, flip: true }, { label: "Wire order flipped and saved" });
  });
}

// ---- Hold (photo mode) --------------------------------------------------------------------------------------------
function renderHold() {
  const hold = S.state.hold;
  $("hold-overlay").hidden = !hold.on || !S.lease;
  $("hold-name").textContent = hold.name || "";
  $("hold-device").textContent = `${S.state.label}${S.state.simulated ? " · simulated" : ""}`;
  $("hold-btn").textContent = hold.on ? "Holding" : "Hold";
}

function setupHold() {
  const dialog = $("hold-dialog");
  $("hold-btn").addEventListener("click", () => {
    if (!S.state) return;
    $("hold-input").value = S.state.look_name || "";
    if (typeof dialog.showModal === "function") dialog.showModal();
    else act("hold", { on: true, name: S.state.look_name });
  });
  dialog.addEventListener("close", () => {
    if (dialog.returnValue === "hold") act("hold", { on: true, name: $("hold-input").value.trim() || null });
  });
  $("hold-release").addEventListener("click", () => act("hold", { on: false }));
}

// ---- tabs, telemetry buttons, layout ---------------------------------------------------------------------------------
function setupTabs() {
  const tabs = document.querySelectorAll(".tabs [data-tab]");
  tabs.forEach((t) =>
    t.addEventListener("click", () => {
      tabs.forEach((x) => x.setAttribute("aria-selected", String(x === t)));
      document.querySelectorAll(".tab-body").forEach((b) => (b.hidden = b.id !== `tab-${t.dataset.tab}`));
      try {
        localStorage.setItem("module-tab", t.dataset.tab);
      } catch {
        // storage can be unavailable (private mode); the default tab is fine
      }
    }),
  );
  // A link can ask for a tab (the /ops calibration hub: ?device=<id>&tab=calibrate); else the last one used here.
  let saved = new URLSearchParams(location.search).get("tab");
  if (!saved) {
    try {
      saved = localStorage.getItem("module-tab");
    } catch {
      saved = null;
    }
  }
  const initial = [...tabs].find((t) => t.dataset.tab === saved);
  if (initial) initial.click();
}

function setupTelemetryButtons() {
  $("tele-details-btn").addEventListener("click", (e) => {
    const box = $("tele-details");
    box.hidden = !box.hidden;
    e.currentTarget.setAttribute("aria-expanded", String(!box.hidden));
    if (!box.hidden) renderTelemetryDetails();
  });
  $("copy-json-btn").addEventListener("click", async () => {
    try {
      const data = await api(`/api/module/${encodeURIComponent(S.deviceId)}/export`);
      toast((await copyJSON(data)) ? "Anonymized JSON copied" : "Couldn't copy on this device", "info", 2500);
    } catch (err) {
      toast(err.message, "error");
    }
  });
}

function trackTopbar() {
  const set = () => document.documentElement.style.setProperty("--topbar-h", `${$("device-name").closest(".topbar").offsetHeight}px`);
  set();
  window.addEventListener("resize", set);
}

// ---- boot ---------------------------------------------------------------------------------------------------------
async function main() {
  // No global badge here: /api/version describes the show engine, which stays on the simulator in bench mode
  // while this page drives the real kit. The badge follows the open device's own `simulated` flag (applyState).
  setupTabs();
  setupMapInput();
  setupPaint();
  setupModule();
  setupEffects();
  setupCalibration();
  setupHold();
  setupTelemetryButtons();
  $('preview-toggle').addEventListener('click', () => {
    const now = performance.now();
    S.previewElapsed = previewSeconds(now);
    S.previewStarted = now;
    S.previewPaused = !S.previewPaused;
    startPreviewAnimation();
  });
  $('preview-rate').addEventListener('input', event => setPreviewRate(Number(event.target.value)));
  $('preview-rate-reset').addEventListener('click', () => setPreviewRate(1));
  document.addEventListener('visibilitychange', startPreviewAnimation);
  trackTopbar();
  $("switch-btn").addEventListener("click", closeDevice);
  window.addEventListener("pagehide", releaseBeacon);
  document.addEventListener("pointerdown", markActive, { passive: true });
  document.addEventListener("keydown", markActive);

  let devices;
  try {
    devices = await loadDevices();
  } catch (err) {
    showPicker([], err.message);
    banner(err.message);
    return;
  }
  const wanted = new URLSearchParams(location.search).get("device");
  if (wanted && !devices.some((d) => d.id === wanted)) {
    // Never fall back to opening (and locking) some other device. A vignette device isn't listed here at all: it
    // has its room page, and opening it here would take it away from the room's tablet.
    showPicker(devices, "Pick a module.");
    banner(`${wanted} can't be opened on this page (it isn't a strand you have access to). Vignette devices are ` +
      "controlled from their room page.");
    return;
  }
  const pick = devices.find((d) => d.id === wanted) || devices.find((d) => d.id === "bench-kit") || devices[0];
  if (!pick) {
    showPicker(devices);
    return;
  }
  await openDevice(pick.id);
}

main();
