// A vignette's staff tablet: /vignette/<room>/ (P4). ES module, no build step, no CDN.
//
// Rules this page keeps:
// - Honest status per device, straight from the Conductor's state tracker: "Updating…" while a change is on its
//   way, "Ready" only once Hubspace reads back what was sent, "Needs attention" (with the reason) otherwise.
// - Stock-app controls only, drawn from each device's own profile (`controls`): nothing is offered that the device
//   doesn't list. Values the Conductor refuses come back as a plain sentence in a toast.
// - A lock takes two taps: Unlock → Confirm unlock (the arm expires after a few seconds).
// - Never navigates: no links, no reloads. The live feed (topic `vignette:<room>`) reconnects with backoff and the
//   room is re-read whenever it reconnects. The Simulator badge follows the room's own devices.

import { api, connectLive, setSimBadge, simBadge } from "/static/app.js";

const ROOM = decodeURIComponent(location.pathname.split("/")[2] || "");
const ARM_MS = 6000;
const TOAST_MS = 4500;
const STATUS_TEXT = {
  ready: "Ready",
  updating: "Updating…",
  needs_attention: "Needs attention",
  unknown: "Not checked yet",
};
const STATUS_CLASS = { ready: "ready", updating: "updating", needs_attention: "attention", unknown: "offline" };
const POSITION_TEXT = {
  locked: "Locked",
  unlocked: "Unlocked",
  locking: "Locking…",
  unlocking: "Unlocking…",
};

const $ = (id) => document.getElementById(id);
const state = { room: null, devices: new Map(), components: [], sheet: null };

// ---- tiny DOM helpers ----------------------------------------------------------------------------------------
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "style") node.setAttribute("style", value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function button(label, onclick, cls = "") {
  return el("button", { type: "button", class: cls, onclick }, label);
}

// ---- toast --------------------------------------------------------------------------------------------------
let toastTimer = null;
function toast(message, kind = "info") {
  const node = $("toast");
  node.textContent = message;
  node.dataset.kind = kind;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (node.hidden = true), TOAST_MS);
}

// ---- data ---------------------------------------------------------------------------------------------------
function mergeDevices(list) {
  for (const device of list || []) state.devices.set(device.id, device);
  refresh();
}

async function loadRoom(refreshReadback = false) {
  const data = await api(`/api/vignette/${encodeURIComponent(ROOM)}${refreshReadback ? "?refresh=1" : ""}`);
  const first = state.room === null;
  state.room = data;
  state.devices = new Map(data.devices.map((d) => [d.id, d]));
  if (first) build();
  if (data.simulated === true) setSimBadge(true);
  else if (data.simulated === null) simBadge();
  else setSimBadge(false);
  refresh();
}

async function send(body, label) {
  try {
    const report = await api(`/api/vignette/${encodeURIComponent(ROOM)}/action`, { method: "POST", body });
    mergeDevices(report.devices);
    const failed = report.results.filter((r) => r.status !== "applied" && r.status !== "superseded");
    if (failed.length) toast(failed[0].error || `${label} didn't go through. Check the card for why.`, "bad");
    return report;
  } catch (err) {
    toast(err.message, "bad");
    return null;
  }
}

async function runScene(scene) {
  toast(`${scene.label}: sending…`);
  try {
    const report = await api(`/api/vignette/${encodeURIComponent(ROOM)}/scene`, {
      method: "POST",
      body: { scene: scene.id },
    });
    mergeDevices(report.devices);
    const failed = report.results.filter((r) => r.status !== "applied" && r.status !== "superseded");
    if (report.skipped.length) {
      const names = report.skipped.map((s) => deviceLabel(s.device_id)).join(", ");
      toast(`${scene.label}: ${names} skipped — ${report.skipped[0].reason}`, "bad");
    } else if (failed.length) {
      toast(`${scene.label}: ${failed.length} device(s) didn't confirm. Check their cards.`, "bad");
    } else {
      toast(`${scene.label} is on.`, "ok");
    }
  } catch (err) {
    toast(err.message, "bad");
  }
}

function deviceLabel(id) {
  return state.devices.get(id)?.label || id;
}

// ---- status helpers -----------------------------------------------------------------------------------------
function deviceStatus(device) {
  if (!device) return "unknown";
  if (!device.ready) return "needs_attention";
  return device.status || "unknown";
}

function groupStatus(devices) {
  const statuses = devices.map(deviceStatus);
  if (statuses.includes("updating")) return "updating";
  if (statuses.includes("needs_attention")) return "needs_attention";
  if (statuses.length && statuses.every((s) => s === "ready")) return "ready";
  return "unknown";
}

function pill(status) {
  return el("span", { class: `pill ${STATUS_CLASS[status] || "offline"}` }, STATUS_TEXT[status] || "Not checked yet");
}

function reasonOf(devices) {
  for (const d of devices) {
    if (!d.ready && d.error) return `${d.label}: ${d.error}`;
    if (d.status === "needs_attention") return `${d.label}: ${d.reason}`;
  }
  return "";
}

function describeNow(device) {
  const now = device?.now;
  if (!device || !device.ready) return device?.error ? "Can't reach this device yet." : "";
  if (!now) return "Waiting for the first reading.";
  if (device.kind === "lock") return "";
  if (device.kind === "plug") return "";
  if (now.on === false) return "Now: off";
  if (device.kind === "fan") {
    const speeds = device.controls?.speeds || [];
    const step = speeds.find((s) => s.name === now.speed);
    return step ? `Now: on, speed ${step.label}` : "Now: on";
  }
  const pct = now.brightness != null ? ` · ${now.brightness} %` : "";
  if (device.kind === "path") return `Now: on${pct}`;
  if (now.mode === "white") return `Now: white ${now.cct} K${pct}`;
  if (now.mode === "color") return `Now: colour ${now.hex}${pct}`;
  if (now.mode === "effect") return `Now: ${prettyEffect(device, now.effect)}${pct}`;
  if (now.mode === "slot") return `Now: saved pattern ${String(now.slot || "").split("-").pop()}${pct}`;
  return `Now: on${pct}`;
}

// The same, shorter, for a lamp's row: " · 2700 K · 60 %", " · colour · 80 %", " · off".
function describeShort(device) {
  const now = device?.now;
  if (!device?.ready || !now) return "";
  if (now.on === false) return " · off";
  const pct = now.brightness != null ? ` · ${now.brightness} %` : "";
  if (now.mode === "white") return ` · ${now.cct} K${pct}`;
  if (now.mode === "color") return ` · colour${pct}`;
  if (now.mode === "effect") return ` · ${prettyEffect(device, now.effect)}${pct}`;
  return pct;
}

function prettyEffect(device, name) {
  const match = (device.controls?.effects || []).find((e) => e.name === name);
  return match ? match.label : name || "an effect";
}

// ---- controls -----------------------------------------------------------------------------------------------
function toggle(onchange, label) {
  const input = el("input", { type: "checkbox", role: "switch", "aria-label": label });
  input.addEventListener("change", () => onchange(input.checked));
  return { el: el("label", { class: "switch" }, input, el("span", { class: "switch-track", "aria-hidden": "true" })), input };
}

function slider({ min, max, step = 1, unit = "", label, onchange }) {
  const input = el("input", { type: "range", min, max, step, "aria-label": label });
  const out = el("output", { class: "slider-value" });
  let dragging = false;
  const show = () => (out.textContent = `${input.value}${unit}`);
  input.addEventListener("pointerdown", () => (dragging = true));
  input.addEventListener("input", show);
  input.addEventListener("change", () => {
    dragging = false;
    show();
    onchange(Number(input.value));
  });
  const node = el("div", { class: "slider" }, el("span", { class: "slider-label" }, label), input, out);
  return {
    el: node,
    set(value) {
      if (dragging || value === undefined || value === null) return;
      input.value = value;
      show();
    },
  };
}

function chips(items, onpick, cls = "chip") {
  const buttons = items.map((item) => {
    const b = button(item.label, () => onpick(item), cls);
    if (item.sub) b.append(el("small", {}, item.sub));
    if (item.hex) {
      b.style.setProperty("--swatch", item.hex);
      b.setAttribute("aria-label", item.label);
      b.title = item.label;
      b.textContent = "";
    }
    b.dataset.key = item.key;
    return b;
  });
  const box = el("div", { class: `chips ${cls}s` }, buttons);
  let shown = null;
  return {
    el: box,
    select(key) {
      for (const b of buttons) b.setAttribute("aria-pressed", String(b.dataset.key === String(key)));
      const picked = buttons.find((b) => b.dataset.key === String(key));
      if (picked && shown !== picked && box.scrollHeight > box.clientHeight) {
        box.scrollTop = Math.max(0, picked.offsetTop - 4); // keep a long effect list's choice in view
      }
      shown = picked || null;
    },
  };
}

function section(label, body) {
  return el("div", { class: "control" }, el("p", { class: "control-label" }, label), body);
}

// A light's controls — for one bulb, a group of bulbs, the string or the path lights.
function lightControls({ controls, getLook, act }) {
  const parts = [];
  const updaters = [];
  if (controls.brightness) {
    const s = slider({
      min: controls.brightness[0],
      max: controls.brightness[1],
      unit: " %",
      label: "Brightness",
      onchange: (v) => act("brightness", v),
    });
    parts.push(s.el);
    updaters.push((look) => s.set(look?.brightness));
  }
  if (controls.cct) {
    const whites = (state.room.whites || [])
      .filter((w) => w.kelvin >= controls.cct.min && w.kelvin <= controls.cct.max)
      .map((w) => ({ key: w.kelvin, label: w.label, sub: `${w.kelvin} K` }));
    const c = chips(whites, (w) => act("color_temperature", w.key), "chip white");
    parts.push(section("White", c.el));
    updaters.push((look) => c.select(look?.on && look.mode === "white" ? look.cct : null));
  }
  if (controls.rgb) {
    const swatches = (state.room.palette || []).map((p) => ({ key: p.hex.toUpperCase(), label: p.label, hex: p.hex }));
    const c = chips(swatches, (p) => act("color", p.hex), "swatch");
    parts.push(section("Colour", c.el));
    updaters.push((look) => c.select(look?.on && look.mode === "color" ? String(look.hex || "").toUpperCase() : null));
  }
  if (controls.slots && controls.slots.length) {
    const c = chips(controls.slots.map((s) => ({ key: s.name, label: s.label })), (s) => act("slot", s.key));
    parts.push(section("Saved patterns", c.el));
    updaters.push((look) => c.select(look?.on && look.mode === "slot" ? look.slot : null));
  }
  if (controls.effects && controls.effects.length) {
    const c = chips(controls.effects.map((e) => ({ key: e.name, label: e.label })), (e) => act("effect", e.key));
    c.el.classList.add("effects");
    parts.push(section("Effects", c.el));
    updaters.push((look) => c.select(look?.on && look.mode === "effect" ? look.effect : null));
    if (controls.speed) {
      const s = slider({
        min: controls.speed[0],
        max: controls.speed[1],
        label: "Effect speed",
        onchange: (v) => act("speed", v),
      });
      parts.push(s.el);
      updaters.push((look) => {
        s.el.hidden = !(look?.on && (look.mode === "effect" || look.mode === "slot"));
        s.set(look?.speed);
      });
    }
  }
  return {
    el: el("div", { class: "light-controls" }, parts),
    update() {
      const look = getLook();
      for (const fn of updaters) fn(look);
    },
  };
}

// ---- cards --------------------------------------------------------------------------------------------------
function cardShell(title, { big = false } = {}) {
  const status = el("span", { class: "status-slot" });
  const head = el("header", { class: "card-head" }, el("h2", {}, title), status);
  const note = el("p", { class: "card-note" });
  const reason = el("p", { class: "card-reason", hidden: true });
  const check = button("Check again", () => loadRoom(true).catch((e) => toast(e.message, "bad")), "secondary check");
  check.hidden = true;
  const node = el("section", { class: `card${big ? " big" : ""}` }, head);
  return {
    node,
    head,
    note,
    finish(...children) {
      node.append(...children, note, el("div", { class: "card-foot" }, reason, check));
      return node;
    },
    setStatus(status_, why, now) {
      status.replaceChildren(pill(status_));
      reason.textContent = why;
      reason.hidden = !why || status_ !== "needs_attention";
      check.hidden = status_ !== "needs_attention";
      note.textContent = now || "";
    },
  };
}

function lightCard(title, ids, { group = null } = {}) {
  const devices = () => ids.map((id) => state.devices.get(id)).filter(Boolean);
  const first = () => devices().find((d) => d.ready) || devices()[0];
  const act = (action, value) =>
    send(group ? { group, action, value } : { device_id: ids[0], action, value }, title);
  const shell = cardShell(title, { big: true });
  const power = toggle((on) => act("power", on), `${title} power`);
  shell.head.append(power.el);
  const controls = first()?.controls;
  const body = controls ? lightControls({ controls, getLook: () => first()?.look, act }) : null;
  shell.finish(body ? body.el : el("p", { class: "muted" }, "This device can't be reached yet."));
  return {
    el: shell.node,
    update() {
      const list = devices();
      const look = first()?.look;
      power.input.checked = !!look?.on;
      power.input.disabled = !first()?.ready;
      const now = group ? `${list.filter((d) => d.now?.on).length} of ${list.length} on` : describeNow(first());
      shell.setStatus(groupStatus(list), reasonOf(list), now);
      body?.update();
    },
  };
}

function deviceRow(id) {
  const device = () => state.devices.get(id);
  const power = toggle((on) => send({ device_id: id, action: "power", value: on }, device()?.label), "Power");
  const status = el("span", { class: "row-status" });
  const now = el("span", { class: "row-now" });
  const adjust = button("Adjust", () => openSheet(id), "secondary adjust");
  const node = el(
    "div",
    { class: "device-row" },
    el("span", { class: "row-name" }, el("strong", {}, device()?.label || id), el("span", { class: "row-line" }, status, now)),
    adjust,
    power.el,
  );
  return {
    el: node,
    update() {
      const d = device();
      const s = deviceStatus(d);
      status.textContent = STATUS_TEXT[s] || "Not checked yet";
      status.dataset.status = STATUS_CLASS[s] || "offline";
      power.input.checked = !!d?.look?.on;
      power.input.disabled = !d?.ready;
      adjust.disabled = !d?.ready;
      now.textContent = describeShort(d);
      const hex = d?.now?.on && d.now.mode === "color" ? d.now.hex : null;
      node.style.setProperty("--dot", hex || (d?.now?.on ? "#FFD9A0" : "transparent"));
    },
  };
}

function listCard(title, ids) {
  const shell = cardShell(title);
  const rows = ids.map(deviceRow);
  shell.finish(el("div", { class: "device-list" }, rows.map((r) => r.el)));
  return {
    el: shell.node,
    update() {
      const list = ids.map((id) => state.devices.get(id)).filter(Boolean);
      shell.setStatus(groupStatus(list), reasonOf(list), "");
      rows.forEach((r) => r.update());
    },
  };
}

function plugCard(id) {
  const device = () => state.devices.get(id);
  const title = device()?.label || id;
  const shell = cardShell(title);
  const outlets = (device()?.controls?.outlets || []).map((o) => {
    const t = toggle((on) => send({ device_id: id, action: "power", outlet: o.key, value: on }, o.label), o.label);
    return { key: o.key, t, el: el("label", { class: "outlet" }, el("span", {}, o.label), t.el) };
  });
  if (outlets.length === 1) {
    // one outlet: the plug is one thing (the humidifier) — its switch sits in the header like a lamp's
    shell.head.append(outlets[0].t.el);
    shell.finish(el("p", { class: "outlet-note" }, "On or off, like the Hubspace app."));
  } else {
    shell.finish(
      outlets.length
        ? el("div", { class: "outlets" }, outlets.map((o) => o.el))
        : el("p", { class: "muted" }, "This plug can't be reached yet."),
    );
  }
  return {
    el: shell.node,
    update() {
      const d = device();
      for (const o of outlets) {
        o.t.input.checked = !!d?.look?.outlets?.[o.key];
        o.t.input.disabled = !d?.ready;
      }
      shell.setStatus(deviceStatus(d), reasonOf([d].filter(Boolean)), describeNow(d));
    },
  };
}

function fanCard(id) {
  const device = () => state.devices.get(id);
  const title = device()?.label || id;
  const shell = cardShell(title);
  const power = toggle((on) => send({ device_id: id, action: "power", value: on }, title), `${title} power`);
  shell.head.append(power.el);
  const speeds = chips(
    (device()?.controls?.speeds || []).map((s) => ({ key: s.name, label: s.label })),
    (s) => send({ device_id: id, action: "fan_speed", value: s.key }, title),
    "chip speed",
  );
  shell.finish(section("Speed", speeds.el));
  return {
    el: shell.node,
    update() {
      const d = device();
      power.input.checked = !!d?.look?.on;
      power.input.disabled = !d?.ready;
      speeds.select(d?.look?.on ? d.look.speed : null);
      shell.setStatus(deviceStatus(d), reasonOf([d].filter(Boolean)), describeNow(d));
    },
  };
}

function lockCard(id) {
  const device = () => state.devices.get(id);
  const shell = cardShell(device()?.label || id, { big: true });
  const icon = el("div", { class: "lock-icon", "aria-hidden": "true" });
  const position = el("p", { class: "lock-position" });
  const primary = button("", () => arm(), "lock-primary");
  const confirm = button("", () => fire(), "lock-confirm");
  const cancel = button("Cancel", () => disarm(), "secondary lock-cancel");
  const actions = el("div", { class: "lock-actions" }, primary, confirm, cancel);
  let armed = null;
  let armTimer = null;
  const wanted = () => (device()?.now?.position === "unlocked" ? "lock" : "unlock");
  function arm() {
    armed = wanted();
    clearTimeout(armTimer);
    armTimer = setTimeout(disarm, ARM_MS);
    render();
  }
  function disarm() {
    armed = null;
    clearTimeout(armTimer);
    render();
  }
  async function fire() {
    const action = armed;
    disarm();
    if (action) await send({ device_id: id, action, confirm: true }, action === "lock" ? "Lock" : "Unlock");
  }
  function render() {
    const d = device();
    let pos = d?.now?.position;
    const goal = d?.look?.position;
    if (d?.status === "updating" && goal && goal !== pos) pos = goal === "locked" ? "locking" : "unlocking";
    shell.node.dataset.position = pos || "unknown";
    position.textContent = POSITION_TEXT[pos] || "Position unknown";
    const verb = wanted() === "lock" ? "Lock" : "Unlock";
    primary.textContent = `${verb} the door`;
    confirm.textContent = armed === "lock" ? "Confirm: lock" : "Confirm: unlock";
    primary.hidden = !!armed;
    confirm.hidden = cancel.hidden = !armed;
    primary.disabled = !d?.ready || d.status === "updating";
    const hint = armed ? "Tap Confirm within a few seconds." : "Two taps: the bolt doesn't move until you confirm.";
    shell.setStatus(deviceStatus(d), reasonOf([d].filter(Boolean)), hint);
  }
  shell.finish(el("div", { class: "lock-face" }, icon, position), actions);
  return { el: shell.node, update: render };
}

// ---- the room -----------------------------------------------------------------------------------------------
function build() {
  const room = state.room;
  document.title = `${room.space} · ${room.title}`;
  $("space").textContent = room.space;
  $("title").textContent = room.title;
  const scenes = $("scenes");
  scenes.replaceChildren(
    ...room.scenes.map((s) => button(s.label, () => runScene(s), s.id === "all-off" ? "scene secondary" : "scene")),
  );
  scenes.hidden = !room.scenes.length;

  const byKind = (kind) => room.devices.filter((d) => d.kind === kind).map((d) => d.id);
  const bulbs = byKind("bulb");
  const main = [];
  const side = [];
  for (const id of byKind("lock")) main.push(lockCard(id));
  if (bulbs.length > 1) {
    main.push(lightCard("All lamps", bulbs, { group: "bulb" }));
    side.push(listCard("Each lamp", bulbs));
  } else if (bulbs.length === 1) {
    main.push(lightCard(state.devices.get(bulbs[0]).label, bulbs));
  }
  for (const id of byKind("string")) main.push(lightCard(state.devices.get(id).label, [id]));
  for (const id of byKind("path")) side.push(lightCard(state.devices.get(id).label, [id]));
  for (const id of byKind("plug")) side.unshift(plugCard(id));
  for (const id of byKind("fan")) side.push(fanCard(id));

  const roomEl = $("room");
  roomEl.dataset.layout = side.length ? "split" : "single";
  const columns = [el("div", { class: "column main" }, main.map((c) => c.el))];
  if (side.length) columns.push(el("div", { class: "column side" }, side.map((c) => c.el)));
  roomEl.replaceChildren(...columns);
  state.components = [...main, ...side];
  document.body.dataset.view = "room";
}

function refresh() {
  for (const c of state.components) c.update();
  state.sheet?.update();
}

function openSheet(id) {
  const device = state.devices.get(id);
  if (!device?.ready) return;
  const card = lightCard(device.label, [id]);
  $("sheet-title").textContent = "Just this lamp";
  $("sheet-body").replaceChildren(card.el);
  state.sheet = card;
  card.update();
  $("sheet").hidden = false;
}

function closeSheet() {
  $("sheet").hidden = true;
  $("sheet-body").replaceChildren();
  state.sheet = null;
}

// ---- live feed ------------------------------------------------------------------------------------------------
function setLive(stateName, text) {
  $("live").dataset.state = stateName;
  $("live-text").textContent = text;
}

function startLive() {
  connectLive({
    topics: [`vignette:${ROOM}`],
    onEvent(topic, data) {
      if (topic !== `vignette:${ROOM}` || !data) return;
      mergeDevices(data.devices);
    },
    onStatus(s) {
      if (s === "open") {
        setLive("open", "Live");
        loadRoom().catch(() => {});
      } else if (s === "connecting") setLive("connecting", "Connecting…");
      else if (s === "unauthorized") setLive("reconnecting", "This tablet isn't signed in");
      else if (s === "reconnecting") setLive("reconnecting", "Reconnecting…");
    },
  });
}

async function start() {
  document.addEventListener("contextmenu", (e) => e.preventDefault());
  document.addEventListener("gesturestart", (e) => e.preventDefault());
  $("sheet-done").addEventListener("click", closeSheet);
  $("sheet").addEventListener("click", (e) => {
    if (e.target === $("sheet")) closeSheet();
  });
  for (let attempt = 0; ; attempt += 1) {
    try {
      await loadRoom();
      break;
    } catch (err) {
      $("loading").textContent =
        err.status === 404 ? "This room isn't set up." : `Can't load the room yet: ${err.message} Retrying…`;
      if (err.status === 404) return;
      await new Promise((r) => setTimeout(r, Math.min(30000, 1000 * 2 ** attempt)));
    }
  }
  startLive();
}

start();
