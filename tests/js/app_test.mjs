// Behaviour checks for web/static/app.js, run by tests/test_static.py under Node (skipped when Node is absent).
// Fakes fetch, WebSocket, window, document and timers; nothing touches the network.
import assert from "node:assert/strict";

const timers = [];
globalThis.setTimeout = (fn, ms) => {
  timers.push({ fn, ms });
  return timers.length;
};
globalThis.clearTimeout = () => {};
const listeners = {};
globalThis.window = { addEventListener: (k, f) => (listeners[k] = f), removeEventListener: () => {}, isSecureContext: false };
globalThis.document = { visibilityState: "visible", addEventListener: () => {}, removeEventListener: () => {} };
globalThis.location = {
  protocol: "https:",
  host: "canopy.example",
  set href(_v) {
    throw new Error("connectLive must never navigate");
  },
};

let ticketN = 0;
let ticketStatus = 200;
globalThis.fetch = async (path) => {
  assert.equal(path, "/api/ws-ticket");
  if (ticketStatus !== 200) return { ok: false, status: ticketStatus, text: async () => "" };
  ticketN += 1;
  return { ok: true, status: 200, text: async () => JSON.stringify({ ticket: `t${ticketN}`, expires_in: 30 }) };
};

const sockets = [];
class FakeWS {
  static OPEN = 1;
  constructor(url) {
    this.url = url;
    this.sent = [];
    this.readyState = 1;
    sockets.push(this);
  }
  send(text) {
    this.sent.push(JSON.parse(text));
  }
  close() {}
  serverSays(msg) {
    this.onmessage({ data: JSON.stringify(msg) });
  }
}
globalThis.WebSocket = FakeWS;

const { connectLive, backoffDelay, fmtMs, fmtAgo, api, ApiError, setSimBadge } = await import(process.argv[2]);
const flush = () => new Promise((r) => setImmediate(r));

// Backoff: exponential, jittered, capped at 30 s.
assert.equal(backoffDelay(0, () => 0), 500);
assert.equal(backoffDelay(0, () => 1), 1000);
assert.equal(backoffDelay(3, () => 1), 8000);
assert.equal(backoffDelay(20, () => 1), 30000);
assert.equal(backoffDelay(20, () => 0), 15000);

// Formatting helpers.
assert.equal(fmtMs(850), "850 ms");
assert.equal(fmtMs(1234), "1.2 s");
assert.equal(fmtMs(95000), "1 min 35 s");
assert.equal(fmtAgo(1000, 1000 * 1000 + 2000), "just now");
assert.equal(fmtAgo(1000, (1000 + 300) * 1000), "5 min ago");

// connectLive: ticket first, auth as the first message, subscribe after hello, pong, reconnect with a new ticket.
const events = [];
const states = [];
const live = connectLive({
  topics: ["module:bench-kit"],
  onEvent: (topic, data) => events.push([topic, data]),
  onStatus: (s) => states.push(s),
});
await flush();
assert.equal(sockets.length, 1);
const ws = sockets[0];
assert.equal(ws.url, "wss://canopy.example/ws/live"); // no ticket in the URL
ws.onopen();
assert.deepEqual(ws.sent[0], { type: "auth", ticket: "t1" });
ws.serverSays({ type: "hello", role: "tester" });
assert.deepEqual(ws.sent[1], { type: "subscribe", topics: ["module:bench-kit"] });
ws.serverSays({ type: "ping" });
assert.deepEqual(ws.sent[2], { type: "pong" });
ws.serverSays({ type: "event", topic: "module:bench-kit", data: { ok: 1 } });
assert.deepEqual(events, [["module:bench-kit", { ok: 1 }]]);

ws.onclose({ code: 1006 });
assert.equal(timers.length, 1);
assert.ok(timers[0].ms <= 1000);
timers.shift().fn();
await flush();
assert.equal(sockets.length, 2);
sockets[1].onopen();
assert.deepEqual(sockets[1].sent[0], { type: "auth", ticket: "t2" }); // a fresh ticket every time

// A revoked device keeps retrying (never navigates), with growing delays.
sockets[1].onclose({ code: 1008 });
ticketStatus = 401;
timers.shift().fn();
await flush();
assert.equal(states.at(-1), "unauthorized");
assert.ok(timers[0].ms >= 2000 && timers[0].ms <= 4000);
live.close();
assert.equal(states.at(-1), "closed");

// api(): readable errors.
globalThis.fetch = async () => ({ ok: false, status: 400, text: async () => JSON.stringify({ detail: "Bad thing." }) });
await assert.rejects(api("/api/x"), (e) => e instanceof ApiError && e.message === "Bad thing." && e.status === 400);
globalThis.fetch = async () => {
  throw new TypeError("Failed to fetch");
};
await assert.rejects(api("/api/x"), /Can't reach the Conductor/);

// setSimBadge: a page whose own target decides the badge (the module page) can show and remove it.
const body = [];
globalThis.document.querySelector = (sel) => (sel === ".sim-badge" ? body.find((e) => e.className === "sim-badge") || null : null);
globalThis.document.createElement = () => ({
  attrs: {},
  setAttribute(k, v) {
    this.attrs[k] = v;
  },
  remove() {
    body.splice(body.indexOf(this), 1);
  },
});
globalThis.document.body = { appendChild: (e) => body.push(e) };
assert.equal(setSimBadge(false), null);
assert.equal(body.length, 0);
const badge = setSimBadge(true);
assert.equal(badge.textContent, "Simulator");
assert.equal(setSimBadge(true), badge); // one badge, not two
assert.equal(body.length, 1);
assert.equal(setSimBadge(false), null);
assert.equal(body.length, 0);

console.log("app.js ok");
