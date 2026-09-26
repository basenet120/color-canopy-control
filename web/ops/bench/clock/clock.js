// The bench filming clock: HH:MM:SS.mmm of the SERVER's clock (so the times in bench results line up with the
// video), redrawn every animation frame. The offset between this phone and the server is estimated from
// /api/bench/time round trips (the sample with the shortest round trip wins). When a bench step reports a write,
// the screen flashes and shows "PUT sent HH:MM:SS.mmm".

import { api, connectLive, simBadge } from "/static/app.js";

const $ = (id) => document.getElementById(id);
const SAMPLES = 8;
const RESYNC_MS = 60000;
let offsetMs = 0; // server − phone
let flashTimer = null;
let freshTimer = null;
let wakeLock = null;

function fmt(ms) {
  const d = new Date(ms);
  const pad = (n, w = 2) => String(n).padStart(w, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`;
}

function tick() {
  $("time").textContent = fmt(Date.now() + offsetMs);
  requestAnimationFrame(tick);
}

async function measureOffset() {
  let best = null;
  for (let i = 0; i < SAMPLES; i += 1) {
    const t0 = Date.now();
    let server;
    try {
      server = (await api("/api/bench/time")).epoch_ms;
    } catch (err) {
      $("offset").textContent = `Can't read the server clock (${err.message}); showing this phone's time.`;
      return;
    }
    const t1 = Date.now();
    const rtt = t1 - t0;
    if (best === null || rtt < best.rtt) best = { rtt, offset: server - (t0 + t1) / 2 };
  }
  offsetMs = best.offset;
  const sign = offsetMs >= 0 ? "+" : "−";
  $("offset").textContent =
    `Server clock: ${sign}${Math.abs(Math.round(offsetMs))} ms vs this phone (±${Math.ceil(best.rtt / 2)} ms)`;
}

function flash() {
  const f = $("flash");
  f.classList.add("on");
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => f.classList.remove("on"), 150);
}

function showPut(text) {
  const put = $("put");
  put.textContent = text;
  put.classList.add("fresh");
  clearTimeout(freshTimer);
  freshTimer = setTimeout(() => put.classList.remove("fresh"), 1500);
}

function onEvent(topic, data) {
  if (topic !== "bench" || !data || data.event !== "step" || !data.step) return;
  const detail = data.step.detail || {};
  if (detail.kind === "write_start") {
    flash();
    showPut(`PUT sending ${fmt((detail.t || Date.now() / 1000) * 1000)}${data.simulated ? " · SIMULATED" : ""}`);
  } else if (detail.kind === "write" && detail.sent_at) {
    const ack = detail.acked_at ? ` · ack +${Math.round((detail.acked_at - detail.sent_at) * 1000)} ms` : "";
    const ok = detail.verified_at ? ` · readback +${Math.round((detail.verified_at - detail.sent_at) * 1000)} ms` : "";
    showPut(`PUT sent ${fmt(detail.sent_at * 1000)} → ${detail.status}${ack}${ok}`);
  }
}

function onStatus(s) {
  $("live").textContent =
    s === "open" ? "Listening for bench writes" : s === "unauthorized" ? "Sign in to hear bench writes" : "Reconnecting…";
}

async function keepAwake() {
  if (!("wakeLock" in navigator)) {
    $("wake").textContent = "Keep the screen on manually (no Wake Lock here)";
    return;
  }
  try {
    wakeLock = await navigator.wakeLock.request("screen");
    $("wake").textContent = "Screen stays on";
    wakeLock.addEventListener("release", () => {
      $("wake").textContent = "Screen may sleep: tap the page";
    });
  } catch {
    $("wake").textContent = "Tap the page to keep the screen on";
  }
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    keepAwake();
    measureOffset();
  }
});
document.addEventListener("click", () => {
  if (!wakeLock || wakeLock.released) keepAwake();
});
$("full").addEventListener("click", (e) => {
  e.stopPropagation();
  const root = document.documentElement;
  if (document.fullscreenElement) document.exitFullscreen?.();
  else root.requestFullscreen?.().catch(() => {});
  keepAwake();
});

requestAnimationFrame(tick);
api("/api/version")
  .then((v) => (v.bench_live ? null : simBadge(v)))
  .catch(() => {});
measureOffset();
setInterval(measureOffset, RESYNC_MS);
keepAwake();
connectLive({ topics: ["bench"], onEvent, onStatus });
