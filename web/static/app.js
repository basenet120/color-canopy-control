// Color Canopy shared browser helpers. ES module, no build step, no dependencies, no CDN.
//
//   import { api, connectLive, simBadge, copyJSON, fmtMs, fmtAgo } from "/static/app.js";
//
// Rules (07 §2, §10): WebSocket tickets are fetched from /api/ws-ticket and sent as the FIRST message, never in
// the URL. The live connection reconnects forever with exponential backoff + jitter (capped at 30 s), fetching a
// fresh ticket every time, and never navigates the page — a kiosk must never show a browser error page.

const BACKOFF_BASE_MS = 1000;
const BACKOFF_CAP_MS = 30000;

/** An error whose message is safe and readable to show on the page. `status` is the HTTP status (0 = network). */
export class ApiError extends Error {
  constructor(message, status = 0, body = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

function describe(status, body) {
  const detail = body && typeof body === "object" ? body.detail ?? body.message ?? body.error : null;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length) {
    // FastAPI validation errors: [{loc: [...], msg: "..."}]
    return detail
      .map((d) => {
        const where = Array.isArray(d.loc) ? d.loc.filter((p) => p !== "body").join(".") : "";
        return where ? `${where}: ${d.msg}` : String(d.msg ?? d);
      })
      .join("; ");
  }
  if (typeof body === "string" && body.trim() && body.length < 300) return body.trim();
  if (status === 401) return "You're not signed in on this device (or your access was revoked).";
  if (status === 403) return "This device isn't allowed to do that.";
  if (status === 404) return "Not found.";
  if (status === 429) return "Too many requests. Wait a moment and try again.";
  if (status >= 500) return `The Conductor had a problem (HTTP ${status}). Try again.`;
  return `Request failed (HTTP ${status}).`;
}

/**
 * JSON fetch against the Conductor. Resolves to the parsed body (or null for an empty one).
 * Throws ApiError with a readable message on network failures and non-2xx responses.
 */
export async function api(path, { method = "GET", body, signal, headers = {} } = {}) {
  const init = {
    method,
    signal,
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json", ...headers },
  };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (err) {
    if (err && err.name === "AbortError") throw err;
    throw new ApiError("Can't reach the Conductor. Check the connection and try again.", 0);
  }
  const text = await response.text();
  let parsed = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }
  if (!response.ok) throw new ApiError(describe(response.status, parsed), response.status, parsed);
  return parsed;
}

/** Exponential backoff with jitter: a random delay in [cap/2, cap], cap = min(30 s, 1 s × 2^attempt). */
export function backoffDelay(attempt, random = Math.random) {
  const cap = Math.min(BACKOFF_CAP_MS, BACKOFF_BASE_MS * 2 ** Math.max(0, attempt));
  return Math.round(cap / 2 + random() * (cap / 2));
}

/**
 * Open the live socket and keep it open.
 *   topics   — e.g. ["canopy"] or ["module:bench-kit"]
 *   onEvent  — (topic, data, message) for every {"type":"event"}
 *   onStatus — (state, info) with state one of: connecting, open, reconnecting, unauthorized, error, closed.
 *              info: {hello} on open; {delayMs, attempt, reason} on reconnecting/unauthorized (it keeps retrying);
 *              {message} on error (e.g. a topic this device may not watch; the socket stays open).
 * Returns {close(), subscribe(topics)}.
 */
export function connectLive({ topics = [], onEvent = () => {}, onStatus = () => {} } = {}) {
  const wanted = new Set(topics);
  let socket = null;
  let attempt = 0;
  let timer = null;
  let opening = false; // fetching a ticket / waiting for the socket
  let stopped = false;

  const status = (state, info = {}) => {
    try {
      onStatus(state, info);
    } catch (err) {
      console.error("onStatus handler failed", err);
    }
  };

  const schedule = (reason, state = "reconnecting") => {
    opening = false;
    if (stopped || timer) return;
    const delayMs = backoffDelay(attempt);
    attempt += 1;
    status(state, { delayMs, attempt, reason });
    timer = setTimeout(() => {
      timer = null;
      open();
    }, delayMs);
  };

  async function open() {
    if (stopped || opening) return;
    opening = true;
    status("connecting");
    let ticket;
    try {
      ticket = (await api("/api/ws-ticket")).ticket;
    } catch (err) {
      schedule(err.message, err.status === 401 ? "unauthorized" : "reconnecting");
      return;
    }
    if (stopped) {
      opening = false;
      return;
    }
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    let ws;
    try {
      ws = new WebSocket(`${scheme}//${location.host}/ws/live`);
    } catch (err) {
      schedule(String(err));
      return;
    }
    socket = ws;
    ws.onopen = () => ws.send(JSON.stringify({ type: "auth", ticket }));
    ws.onmessage = (e) => {
      let msg;
      try {
        msg = JSON.parse(e.data);
      } catch {
        return;
      }
      if (msg.type === "hello") {
        attempt = 0;
        opening = false;
        status("open", { hello: msg });
        if (wanted.size) ws.send(JSON.stringify({ type: "subscribe", topics: [...wanted] }));
      } else if (msg.type === "ping") {
        ws.send(JSON.stringify({ type: "pong" }));
      } else if (msg.type === "event") {
        try {
          onEvent(msg.topic, msg.data, msg);
        } catch (err) {
          console.error("onEvent handler failed", err);
        }
      } else if (msg.type === "error") {
        status("error", { message: msg.message });
      }
    };
    ws.onerror = () => {}; // onclose follows; errors never bubble to the page
    ws.onclose = (e) => {
      if (socket === ws) socket = null;
      schedule(e.code === 1008 ? "ticket refused" : `closed (${e.code})`);
    };
  }

  // Reconnect promptly when the device comes back online or the tab becomes visible again.
  const nudge = () => {
    if (stopped || socket || opening) return;
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    open();
  };
  const onVisible = () => {
    if (document.visibilityState === "visible") nudge();
  };
  window.addEventListener("online", nudge);
  document.addEventListener("visibilitychange", onVisible);

  open();

  return {
    subscribe(more) {
      const fresh = more.filter((t) => !wanted.has(t));
      fresh.forEach((t) => wanted.add(t));
      if (fresh.length && socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "subscribe", topics: fresh }));
      }
    },
    close() {
      stopped = true;
      if (timer) clearTimeout(timer);
      window.removeEventListener("online", nudge);
      document.removeEventListener("visibilitychange", onVisible);
      if (socket) socket.close(1000);
      status("closed");
    },
  };
}

/**
 * Mount the persistent "Simulator" badge when /api/version says the runtime is simulated.
 * Pass the /api/version JSON (or nothing, to fetch it). Returns the badge element, or null.
 */
export async function simBadge(versionJson) {
  let v = versionJson;
  if (v === undefined) {
    try {
      v = await api("/api/version");
    } catch {
      return null;
    }
  }
  if (!v || !(v.mode === "sim" || v.simulated === true)) return null;
  return setSimBadge(true);
}

/**
 * Show (on = true) or remove the persistent "Simulator" badge. For pages whose own target decides it — the
 * module page drives one device, which may be real while the show engine runs on the simulator (bench mode),
 * so it follows that device's `simulated` flag rather than /api/version. Returns the badge element, or null.
 */
export function setSimBadge(on) {
  let badge = document.querySelector(".sim-badge");
  if (!on) {
    if (badge) badge.remove();
    return null;
  }
  if (!badge) {
    badge = document.createElement("div");
    badge.className = "sim-badge";
    badge.setAttribute("role", "status");
    badge.textContent = "Simulator";
    badge.title = "Simulated: no real lights are being driven by this page.";
    document.body.appendChild(badge);
  }
  return badge;
}

/**
 * Copy an object as pretty JSON. Uses the Clipboard API when available (https), else a hidden-textarea
 * fallback (plain http on a phone). Resolves true when copied.
 */
export async function copyJSON(obj) {
  const text = typeof obj === "string" ? obj : JSON.stringify(obj, null, 2);
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // fall through to the textarea fallback
    }
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.top = "0";
  area.style.left = "0";
  area.style.opacity = "0";
  area.style.fontSize = "16px"; // stops iOS zooming in
  document.body.appendChild(area);
  area.focus();
  area.select();
  area.setSelectionRange(0, text.length);
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  area.remove();
  return ok;
}

/** 850 → "850 ms", 1234 → "1.2 s", 95000 → "1 min 35 s". */
export function fmtMs(ms) {
  if (ms == null || !Number.isFinite(Number(ms))) return "–";
  const n = Math.max(0, Number(ms));
  if (n < 1000) return `${Math.round(n)} ms`;
  if (n < 60000) return `${(n / 1000).toFixed(n < 10000 ? 1 : 0)} s`;
  const min = Math.floor(n / 60000);
  const s = Math.round((n % 60000) / 1000);
  return s ? `${min} min ${s} s` : `${min} min`;
}

/** Epoch seconds (the Conductor's wall-clock unit) → "just now", "12 s ago", "5 min ago", "3 h ago", "2 d ago". */
export function fmtAgo(epochSeconds, nowMs = Date.now()) {
  if (epochSeconds == null || !Number.isFinite(Number(epochSeconds))) return "never";
  const s = Math.round(nowMs / 1000 - Number(epochSeconds));
  if (s < 5) return "just now";
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
