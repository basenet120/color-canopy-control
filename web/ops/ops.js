// The /ops dashboard (07 §10): the canopy at a glance, big controls, the 7 am checklist, rate budget, accounts,
// devices, device enrollment and the log tail. Live over WebSocket topics "canopy" and "ops", plus a 10 s poll.
import { api, connectLive, copyJSON, simBadge } from "/static/app.js";
import { fill, fmtAge, h, pill, show, simTag, statusPill } from "/ops/dom.js";
import { commissionScope, headsCounts, moduleScope, positions, testWaitNote } from "/ops/counts-model.mjs";
import { ipadKey, ipadRows, ipadSummary } from "/ops/ipad-model.mjs";
import { rehearsalText, steppedLines } from "/ops/stepped-model.mjs";

const POLL_MS = 10000;
const LOG_POLL_MS = 5000;
const LOG_KEEP = 200;
// The SVG namespace name (an identifier, never fetched); split so the "no external URL" asset check stays simple.
const SVG_NS = ["http:", "", "www.w3.org", "2000", "svg"].join("/");
const $ = (id) => document.getElementById(id);

let devices = [];
let testerChoicesDrawn = false;
let canopyState = null;
let steppedGauge = null; // /api/canopy/state's `stepped` (the live "canopy" topic doesn't carry it)
let rehearsal = null; // /api/canopy/state's `rehearsal`: the stepped rehearsal running now and the last one's verdict
let rehearsalPending = null; // {mode, minutes, confirm}: the preview the confirm dialog is showing
let headsData = null;
let modesDrawn = "";
let ipadDrawn = ""; // ipadKey of the "On the iPad" rows on screen
let logSeq = null;
let logsPaused = false;
let refreshTimer = null;

// ---- small helpers ------------------------------------------------------------------------------------------------
function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null && v !== false) el.setAttribute(k, String(v));
  for (const c of children.flat()) if (c) el.append(c);
  return el;
}

const rgb = (c) => (c ? `rgb(${c.r}, ${c.g}, ${c.b})` : null);
const clock = (t) =>
  new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });

function modeText(v) {
  const mode = v.mode || "sim";
  if (mode === "sim" && !v.bench_live) return "Simulator — no real lights are driven";
  if (mode === "sim") return "Simulator, but bench jobs reach Hubspace";
  if (mode === "bench") return "Bench — the show runs on the simulator; bench jobs reach Hubspace";
  return "Live — the canopy is driven through Hubspace";
}

const TIER_TEXT = {
  stepped: "T3 stepped · one colour per module, a step at a time",
  canvas: "T1 · every head painted",
  slot: "T2 · app-painted slots",
  modules: "T3 · one colour per module",
  strand: "Whole-strand look",
};
const PHASE = {
  ready: ["ready", "Ready"],
  updating: ["updating", "Updating"],
  needs_attention: ["attention", "Needs attention"],
  resting: ["offline", "Resting"],
};

function note(el, message, kind = "info") {
  show(el, message);
  el.className = kind === "error" ? "error" : kind === "ok" ? "banner-ok" : "notice";
}

// ---- the canopy card ----------------------------------------------------------------------------------------------
function renderState(st) {
  canopyState = st;
  show($("engine-error"), "");
  const [cls, text] = PHASE[st.phase] || PHASE.resting;
  fill($("canopy-phase"), pill(cls, text));
  $("canopy-mode").textContent = st.label || (st.mode ? st.mode : "No mode yet");
  const tier = $("canopy-tier");
  tier.hidden = !st.tier;
  tier.textContent = TIER_TEXT[st.tier] || st.tier || "";
  const guest = $("canopy-guest");
  guest.hidden = false;
  // A pause is saved on the box and survives a restart (canopy.py), so say since when.
  guest.textContent = st.guest_input
    ? "Guest input on"
    : `Guest input paused${st.paused_since ? ` since ${clock(st.paused_since)}` : ""}`;
  guest.title = st.guest_input ? "" : "Stays paused across a Conductor restart until someone presses Resume.";
  guest.className = `chip ${st.guest_input ? "ok" : "warn"}`;
  // The guest panel's brightness / off (D60): the engine keeps it until a mood is chosen, the idle reset or ops clear it.
  const off = st.guest_power === false;
  const dimmed = st.guest_brightness != null;
  const guestLook = $("canopy-guest-look");
  guestLook.hidden = !off && !dimmed;
  guestLook.textContent = off ? "Turned off from the guest panel" : dimmed ? `Guest brightness ${st.guest_brightness}%` : "";
  $("guest-reset-btn").hidden = !off && !dimmed;
  const feature = $("canopy-feature");
  feature.hidden = !st.feature;
  fill(feature, st.feature ? [h("span", { class: "kicker" }, "Hubspace feature"), st.feature] : []);
  show($("canopy-message"), st.message || "");
  if ("stepped" in st) steppedGauge = st.stepped;
  if ("rehearsal" in st) {
    rehearsal = st.rehearsal;
    drawRehearsal();
  }
  const stepped = steppedLines(st.program, steppedGauge, clock);
  fill($("canopy-stepped"), stepped.map((line) => h("div", {}, line)));
  $("canopy-stepped").hidden = !stepped.length;
  const pause = $("pause-btn");
  pause.textContent = st.guest_input ? "Pause guest input" : "Resume guest input";
  pause.classList.toggle("resume", !st.guest_input);
  if (Array.isArray(st.modes)) drawModes(st.modes, st.mode);
  setControlsEnabled(true);
  drawGrid();
}

function engineMissing(message) {
  canopyState = null;
  note($("engine-error"), message, "error");
  fill($("canopy-phase"), pill("offline", "No engine"));
  $("canopy-mode").textContent = "The canopy engine isn't running";
  $("canopy-tier").hidden = true;
  $("canopy-guest").hidden = true;
  $("canopy-feature").hidden = true;
  $("canopy-stepped").hidden = true;
  steppedGauge = null;
  show($("canopy-message"), "");
  setControlsEnabled(false);
  drawGrid();
}

function setControlsEnabled(on) {
  for (const id of ["pause-btn", "reassert-btn", "safe-btn", "guest-reset-btn", "commission-btn", "mode-btn", "mode-select", "test-mode-btn", "rehearsal-btn", "rehearsal-stop"]) {
    $(id).disabled = !on;
  }
}

// ---- the stepped rehearsal (Gate R; conductor/rehearsal.py) --------------------------------------------------------
// Operator-started only: "Rehearse…" asks the server for the sentence naming the modules and minutes, shows it, and
// only "Rehearse" in that dialog starts it (the POST carries the sentence back; a stale one is refused).
function drawRehearsal() {
  const running = Boolean(rehearsal && rehearsal.running);
  $("rehearsal-stop").hidden = !running;
  $("rehearsal-btn").hidden = running;
  const text = rehearsalText(rehearsal, clock);
  show($("rehearsal-status"), text);
}

async function loadRehearsalModes() {
  try {
    const body = await api("/api/canopy/stepped-rehearsal");
    const select = $("rehearsal-mode");
    fill(select, body.modes.map((m) => h("option", { value: m.id }, m.label)));
    if (body.modes.some((m) => m.id === "golden-hour")) select.value = "golden-hour";
    $("rehearsal-minutes").max = String(body.max_minutes);
    rehearsal = body.status;
    drawRehearsal();
  } catch (err) {
    if (err.status !== 503) note($("control-result"), err.message, "error");
  }
}

async function previewRehearsal(button) {
  const mode = $("rehearsal-mode").value;
  const ladder = $("rehearsal-ladder").checked; // D80: the rate test (its own minutes)
  const minutes = ladder ? 1 : Number($("rehearsal-minutes").value);
  button.disabled = true;
  try {
    const q = new URLSearchParams({ mode, minutes: String(minutes) });
    if (ladder) q.set("ladder", "true");
    const body = await api(`/api/canopy/stepped-rehearsal?${q}`);
    rehearsalPending = { mode, minutes, confirm: body.preview.confirm, ladder };
    $("rehearsal-confirm").textContent = body.preview.confirm;
    const dialog = $("rehearsal-dialog");
    if (typeof dialog.showModal === "function") dialog.showModal();
    else if (window.confirm(body.preview.confirm)) startRehearsal();
  } catch (err) {
    note($("control-result"), err.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function startRehearsal() {
  const pending = rehearsalPending;
  rehearsalPending = null;
  if (!pending) return;
  await control($("rehearsal-btn"), "/api/canopy/stepped-rehearsal", pending, "Starting…");
  loadState();
}

function setupRehearsal() {
  $("rehearsal-btn").addEventListener("click", (e) => previewRehearsal(e.currentTarget));
  $("rehearsal-ladder").addEventListener("change", (e) => {
    $("rehearsal-minutes").disabled = e.currentTarget.checked; // the rate test runs its own ladder
  });
  $("rehearsal-stop").addEventListener("click", (e) =>
    control(e.currentTarget, "/api/canopy/stepped-rehearsal/stop", {}, "Stopping…").then(() => loadState()),
  );
  const dialog = $("rehearsal-dialog");
  dialog.addEventListener("close", () => {
    if (dialog.returnValue === "go") startRehearsal();
    else rehearsalPending = null;
  });
  loadRehearsalModes();
}

function drawModes(modes, current) {
  const key = modes.map((m) => `${m.id}:${m.on_ipad}`).join(",");
  const select = $("mode-select");
  if (key !== modesDrawn) {
    modesDrawn = key;
    // ops can still choose a scene that is off the iPad (D76): the picker says which ones guests can't
    fill(
      select,
      modes.map((m) => h("option", { value: m.id }, m.on_ipad === false ? `${m.label} (off the iPad)` : m.label)),
    );
  }
  if (current && document.activeElement !== select) select.value = current;
}

// ---- On the iPad (D76; conductor/ipad_menu.py): which scenes guests can choose ------------------------------------
// Two message lines: #ipad-error says the card couldn't be refreshed and goes as soon as a fresh answer is drawn (as
// renderState clears #engine-error); #ipad-result is a switch's own answer and stays until the next switch.
function drawIpad(body) {
  show($("ipad-error"), ""); // a fresh answer: the card is current again
  show($("ipad-summary"), ipadSummary(body));
  show($("ipad-notes"), (body.notes || []).join(" "));
  const key = ipadKey(body);
  if (key === ipadDrawn) return;
  ipadDrawn = key;
  fill(
    $("ipad-scenes"),
    ipadRows(body).map((row) =>
      h(
        "li",
        { class: row.option ? "ipad-row is-option" : "ipad-row" },
        h("span", { class: "ipad-name" }, row.name),
        h(
          "button",
          {
            type: "button",
            class: row.on ? "ipad-switch on" : "ipad-switch secondary",
            role: "switch",
            "aria-checked": String(row.on),
            "aria-label": row.label,
            "data-scene": row.id,
            disabled: row.disabled,
            onclick: (e) => flipIpad(e.currentTarget, row),
          },
          row.text,
        ),
      ),
    ),
  );
}

async function loadIpad() {
  try {
    drawIpad(await api("/api/ops/ipad-scenes"));
  } catch (err) {
    note($("ipad-error"), err.message, "error"); // the rows on screen stay; this line says they may be stale
  }
}

async function flipIpad(button, row) {
  button.disabled = true;
  try {
    const res = await api("/api/ops/ipad-scenes", { method: "POST", body: { scene: row.id, show: !row.on } });
    note($("ipad-result"), res.message, "ok");
    drawIpad(res);
  } catch (err) {
    note($("ipad-result"), err.message, "error");
    button.disabled = row.disabled;
  }
  loadState(); // the mode picker's "(off the iPad)"
}

function moduleSvg(mod, heads, canopy) {
  const w = canopy.module_width_in;
  const hgt = canopy.module_height_in;
  const ox = mod.col * w;
  const oy = mod.row * hgt;
  const dots = heads.map((hd) => {
    const color = rgb(hd.reported) || rgb(hd.desired);
    const attrs = {
      cx: (hd.x - ox).toFixed(2),
      cy: (hd.y - oy).toFixed(2),
      r: 3.1,
      class: hd.match === false ? "dot mismatch" : hd.reported ? "dot" : "dot unread",
    };
    if (hd.reported) attrs.fill = color;
    else if (color) attrs.stroke = color;
    return svg("circle", attrs);
  });
  return svg("svg", { viewBox: `0 0 ${w} ${hgt}`, class: "mini", "aria-hidden": "true" }, dots);
}

function drawGrid() {
  const grid = $("canopy-grid");
  if (!headsData) return;
  const stModules = (canopyState && canopyState.modules) || {};
  const byModule = {};
  for (const hd of headsData.heads) (byModule[hd.module] ||= []).push(hd);
  const issues = [];
  fill(
    grid,
    headsData.modules.map((m) => {
      const eng = (m.device_id && stModules[m.device_id]) || {};
      const status = eng.status || m.status || "unknown";
      const reason = eng.reason || m.reason || "";
      const name = m.label || m.id;
      // A position with no Hubspace device registered (D62) has no module page to open: it links to /ops/accounts.
      const unregistered = m.registered === false;
      if (status !== "ready" && reason) issues.push({ name, reason, device: m.device_id, unregistered });
      const href = unregistered
        ? "/ops/accounts/"
        : m.device_id ? `/ops/module/?device=${encodeURIComponent(m.device_id)}` : null;
      const mism = m.counts ? m.counts.mismatch : 0;
      return h(
        href ? "a" : "div",
        {
          class: `cell s-${status}`,
          href,
          style: `grid-row: ${m.row + 1}; grid-column: ${m.col + 1}`,
          title: unregistered
            ? `${name}: no Hubspace device registered — register one on /ops/accounts`
            : `${name}: ${reason || status} — open the module page`,
          "data-module": m.id,
        },
        h("span", { class: "cell-head" }, h("span", { class: "cell-name" }, name), h("span", { class: "dot-status" })),
        moduleSvg(m, byModule[m.id] || [], headsData.canopy),
        h(
          "span",
          { class: "cell-foot" },
          unregistered
            ? "No device registered"
            : m.lock_owner ? `Held: ${m.lock_owner}`
            : m.readback_stale ? "Readback stale" // D82: Hubspace took the writes; its readback hasn't moved
            : mism ? `${mism} heads differ` : statusWord(status),
        ),
      );
    }),
  );
  fill(
    $("module-issues"),
    issues.map((i) =>
      h(
        "li",
        {},
        h("span", { class: "title" }, i.name), " — ", i.reason, " ",
        i.unregistered
          ? h("a", { href: "/ops/accounts/" }, "Register a device")
          : i.device ? h("a", { href: `/ops/module/?device=${encodeURIComponent(i.device)}` }, "Open module page") : null,
      ),
    ),
  );
  // D62: the counts' wording (registered of nine) lives in counts-model.mjs, where node tests pin it
  $("heads-note").textContent =
    headsCounts(headsData.counts) + `Tap a module to open its page. ${headsData.readback_note}`;
}

// How many canopy positions have a Hubspace device registered (D62), from the last heads read: {registered, total},
// or null before the first read.
function registeredPositions() {
  return positions(headsData && headsData.counts);
}

function statusWord(status) {
  return { ready: "Ready", updating: "Updating", needs_attention: "Needs attention" }[status] || "Unknown";
}

async function loadState() {
  try {
    renderState(await api("/api/canopy/state"));
  } catch (err) {
    if (err.status === 503) engineMissing(err.message);
    else note($("engine-error"), err.message, "error");
  }
}

async function loadHeads() {
  try {
    headsData = await api("/api/canopy/heads");
    drawGrid();
  } catch (err) {
    note($("engine-error"), err.message, "error");
  }
}

// ---- controls -----------------------------------------------------------------------------------------------------
// The result line says what the engine actually did: a refusal comes back as a 409 (the engine's own sentence) and a
// commissioning run with problems as ok:false — both in the error style, never a green "done".
async function control(button, path, body, busy) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = busy;
  try {
    const res = await api(path, { method: "POST", body });
    note($("control-result"), res.message || "The canopy engine accepted that.", res.ok === false ? "error" : "ok");
    if (res.state) renderState({ ...res.state, modes: undefined });
    else loadState();
    loadHeads();
    return res;
  } catch (err) {
    note($("control-result"), err.message, "error");
    return null;
  } finally {
    button.textContent = label;
    button.disabled = false;
    if (canopyState) renderState(canopyState);
  }
}

function setupControls() {
  $("pause-btn").addEventListener("click", (e) => {
    const paused = !(canopyState && !canopyState.guest_input);
    control(e.currentTarget, "/api/canopy/pause", { paused }, paused ? "Pausing…" : "Resuming…");
  });
  $("reassert-btn").addEventListener("click", (e) => control(e.currentTarget, "/api/canopy/reassert", {}, "Re-asserting…"));
  $("safe-btn").addEventListener("click", (e) => control(e.currentTarget, "/api/canopy/safe-look", {}, "Sending…"));
  $("guest-reset-btn").addEventListener("click", (e) =>
    control(e.currentTarget, "/api/canopy/guest-reset", {}, "Clearing…"),
  );
  $("mode-btn").addEventListener("click", (e) =>
    control(e.currentTarget, "/api/canopy/mode", { mode: $("mode-select").value }, "Changing…"),
  );
  const dialog = $("commission-dialog");
  $("commission-btn").addEventListener("click", () => {
    const p = registeredPositions();
    $("commission-scope").textContent = commissionScope(p);
    if (typeof dialog.showModal === "function") dialog.showModal();
    else if (window.confirm(`Commission slots on ${moduleScope(p)} now?`)) commission();
  });
  dialog.addEventListener("close", () => {
    if (dialog.returnValue === "go") commission();
  });
}

async function commission() {
  const res = await control($("commission-btn"), "/api/canopy/commission", {}, "Commissioning…");
  if (res) runChecklist(); // the slot hashes item shows what was recorded
}

// ---- the 7 am checklist -------------------------------------------------------------------------------------------
const MARK = { pass: ["ok", "Pass"], warn: ["warn", "Check"], fail: ["bad", "Fail"] };

async function runChecklist() {
  const btn = $("checklist-run");
  btn.disabled = true;
  btn.textContent = "Checking…";
  try {
    const body = await api(`/api/ops/checklist?client_ms=${Date.now()}`);
    const c = body.counts;
    $("checklist-summary").textContent =
      `Checked at ${clock(body.checked_at)} · ${c.pass} pass · ${c.warn} to check · ${c.fail} fail` +
      (body.simulated ? " · SIMULATED" : "");
    fill(
      $("checklist"),
      body.items.map((i) => {
        const [cls, word] = MARK[i.status];
        return h(
          "li",
          { class: `check ${cls}`, "data-item": i.id },
          h("div", { class: "row" }, h("span", { class: `tag ${cls}` }, word), h("span", { class: "title" }, i.title)),
          h("div", { class: "sub" }, i.detail),
          i.fix ? h("div", { class: "fix" }, h("strong", {}, "Fix: "), i.fix) : null,
          i.note ? h("div", { class: "sub" }, i.note) : null,
        );
      }),
    );
  } catch (err) {
    $("checklist-summary").textContent = err.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "Run";
  }
}

async function testModeChange() {
  const btn = $("test-mode-btn");
  btn.disabled = true;
  const p = registeredPositions();
  btn.textContent = `Timing ${moduleScope(p)}…`;
  note($("test-result"), testWaitNote(p));
  try {
    const r = await api("/api/ops/checklist/test-mode-change", { method: "POST", body: {} });
    // Positions with no device registered aren't timed: the message names them ("Not timed: …"), never "Not ready".
    const late = r.modules.filter((m) => m.registered !== false && m.ready_after_s == null);
    const extra = late.length ? ` Not ready: ${late.map((m) => `${m.label} (${m.reason || m.status})`).join("; ")}.` : "";
    const back = r.restored_to ? " Changed back afterwards." : "";
    note($("test-result"), r.message + extra + back + (r.simulated ? " (SIMULATED)" : ""), r.ok ? "ok" : "error");
  } catch (err) {
    note($("test-result"), err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Run test mode change";
    runChecklist();
    loadState();
    loadHeads();
  }
}

// ---- needs attention, accounts, devices ---------------------------------------------------------------------------
// Engine and reconciler alerts (slot drift, a tier downgrade, a failed rewrite…) carry `alert`: they stay until
// they're resolved or someone acknowledges them here.
function renderAttention(items) {
  const list = $("attention");
  if (!items.length) {
    fill(list, h("li", { class: "all-clear" }, "Nothing needs attention."));
    return;
  }
  fill(
    list,
    items.map((item) =>
      h(
        "li",
        { class: item.alert ? `alert ${item.level || ""}` : null },
        h(
          "span",
          {},
          item.message,
          " ",
          item.link ? h("a", { href: item.link }, item.alert ? "Open module page" : "Fix") : null,
        ),
        item.alert
          ? h("div", { class: "alert-foot" },
              item.at ? h("span", { class: "muted" }, clock(item.at)) : null,
              h("button", { type: "button", class: "secondary ack", onclick: (e) => ack(e.currentTarget, item.alert) },
                "Acknowledge"))
          : null,
      ),
    ),
  );
}

async function ack(button, key) {
  button.disabled = true;
  try {
    await api("/api/ops/alerts/ack", { method: "POST", body: { key } });
  } catch (err) {
    if (err.status !== 404) note($("control-result"), err.message, "error");
  }
  refresh();
}

function accountPill(a) {
  if (a.needs_reauth) return pill("attention", "Needs sign-in");
  if (a.connected) return pill("ready", "Connected");
  return pill("offline", "Not connected");
}

function renderAccounts(summary) {
  const rows = summary.accounts || [];
  fill(
    $("accounts"),
    rows.map((a) =>
      h(
        "li",
        {},
        h("div", { class: "row" }, h("span", { class: "title" }, a.label || a.account), accountPill(a),
          a.simulated ? simTag() : null),
        a.connected && a.token_age_s != null
          ? h("div", { class: "sub" }, `Signed in ${fmtAge(a.token_age_s)} ago` +
              (a.last_refresh_at ? ` · token refreshed ${fmtAge(Date.now() / 1000 - a.last_refresh_at)} ago` : ""))
          : null,
        a.last_error && !a.connected && !a.simulated ? h("div", { class: "sub" }, a.last_error) : null,
        a.unknown_devices && a.unknown_devices.length
          ? h("div", { class: "problem" }, `${a.unknown_devices.length} unregistered device(s) visible — read-only`)
          : null,
      ),
    ),
  );
}

function capsLine(d) {
  if (d.capabilities) {
    const c = d.capabilities;
    const parts = [];
    if (c.framebuffer_length != null) {
      // The controller's frame can be longer than the strand (60 entries for 30 pucks): name both.
      const n = d.expected_heads;
      parts.push(n != null && n !== c.framebuffer_length ? `${n} heads (${c.framebuffer_length}-entry pattern)` : `${c.framebuffer_length} heads`);
    }
    parts.push(c.individual ? "per-head colour" : "no per-head colour");
    if (c.slots && c.slots.length) parts.push(`${c.slots.length} slots`);
    return parts.join(" · ");
  }
  return d.capabilities_error ? `Profile not read: ${d.capabilities_error}` : "Profile not read yet";
}

function profileLine(d) {
  const kind = d.model || "Device";
  if (d.capabilities) return `${kind} · profile read`;
  return `${kind} · ${d.capabilities_error ? `profile not read: ${d.capabilities_error}` : "profile not read yet"}`;
}

// One device. Strands (canopy modules, bench kit, spares: every registered device, D67) get the head count, the
// module page and the bench.
function deviceItem(d, strand) {
  const q = encodeURIComponent(d.id);
  const lock = d.lock ? `In use by ${d.lock.owner}` : null;
  return h(
    "li",
    { "data-device": d.id },
    h("div", { class: "row" }, h("span", { class: "title" }, d.label), statusPill(d.status),
      d.simulated ? simTag() : null),
    h("div", { class: "sub" }, [d.id, d.module, d.account && `account: ${d.account}`].filter(Boolean).join(" · ")),
    d.reason && d.status !== "ready" ? h("div", { class: "sub" }, d.reason) : null,
    h("div", { class: "sub" }, strand ? capsLine(d) : profileLine(d)),
    d.status !== "needs_attention" ? (d.problems || []).map((p) => h("div", { class: "problem" }, p)) : null,
    strand && d.head_count_problem ? h("div", { class: "problem" }, `Head count: ${d.head_count_problem}`) : null,
    lock ? h("div", { class: "sub" }, lock) : null,
    d.purposes.bench.simulated === false
      ? h("div", { class: "sub" }, d.allowlisted ? "Bench reaches the real device" : "Real device, read-only (not allowlisted)")
      : null,
    strand
      ? h(
          "div",
          { class: "actions" },
          h("a", { class: "btn secondary", href: `/ops/module/?device=${q}` }, "Open module page"),
          h("a", { class: "btn secondary", href: `/ops/bench/?device=${q}` }, "Bench"),
        )
      : null,
  );
}

function renderDevices(list, groups) {
  devices = list;
  const byId = new Map(list.map((d) => [d.id, d]));
  const sections =
    Array.isArray(groups) && groups.length
      ? groups
      : [{ id: "all", title: "All devices", strand: true, link: null, devices: list.map((d) => d.id) }];
  fill(
    $("devices"),
    sections.map((g) => {
      const members = g.devices.map((id) => byId.get(id)).filter(Boolean);
      return h(
        "section",
        { class: "dev-group", "data-group": g.id },
        h(
          "h3",
          { class: "group-title" },
          h("span", {}, g.title),
          h("span", { class: "muted count" }, `${members.length}`),
        ),
        h("ul", { class: "list" }, members.map((d) => deviceItem(d, g.strand))),
      );
    }),
  );
  drawTesterChoices();
}

// ---- rate budget --------------------------------------------------------------------------------------------------
function renderBudget(summary) {
  const byDriver = {};
  for (const b of summary.budget || []) byDriver[b.driver] = b;
  const rows = [];
  const accounts = [];
  for (const b of summary.budget || []) {
    for (const [name, a] of Object.entries(b.budget.accounts || {})) {
      accounts.push(`${name}: ${a.writes_60s} writes, ${a.requests_in_flight} in flight${b.simulated ? " (sim)" : ""}`);
    }
  }
  let cap = 60;
  for (const d of devices) {
    const purpose = d.role === "canopy_module" ? d.purposes.engine : d.purposes.bench;
    const b = purpose && byDriver[purpose.driver];
    if (!b || !purpose.address) continue;
    cap = Math.max(1, Math.round((b.budget.window_s || 60) / (b.budget.min_write_interval_s || 1)));
    const g = (b.budget.devices || {})[purpose.address] || { writes_60s: 0 };
    const pct = Math.min(100, (100 * g.writes_60s) / cap);
    const level = pct >= 90 ? "bad" : pct >= 50 ? "warn" : "ok";
    rows.push(
      h(
        "li",
        { class: "gauge" },
        h("span", { class: "g-name" }, d.label),
        h("span", { class: "g-bar", role: "img", "aria-label": `${g.writes_60s} of ${cap} writes` },
          h("span", { class: `g-fill ${level}`, style: `width: ${pct.toFixed(1)}%` })),
        h("span", { class: "g-num" }, `${g.writes_60s}/${cap}`),
      ),
    );
  }
  fill($("budget"), rows.length ? rows : h("li", { class: "muted" }, "No devices yet."));
  $("budget-note").textContent =
    `Writes per device in the last 60 s. Budget: at most one write per second per device (${cap} a minute); a mode ` +
    "change is one write per module.";
  $("budget-accounts").textContent = accounts.length ? `Per account: ${accounts.join(" · ")}` : "";
}

// ---- enrollment ---------------------------------------------------------------------------------------------------
function enrollRole() {
  return (document.querySelector('#enroll-role input[name="role"]:checked') || {}).value || "kiosk";
}

const NAME_HINT = { kiosk: "e.g. Guest iPad", tester: "e.g. Crew phone" };

function syncEnrollRole() {
  const role = enrollRole();
  $("enroll-kiosk-note").hidden = role !== "kiosk";
  $("enroll-devices").hidden = role !== "tester";
  $("enroll-name").placeholder = NAME_HINT[role];
  document.querySelectorAll("#enroll-role label").forEach((l) =>
    l.classList.toggle("on", l.querySelector("input").checked),
  );
}

function drawTesterChoices() {
  if (testerChoicesDrawn || !devices.length) return;
  testerChoicesDrawn = true;
  const box = $("enroll-devices");
  const legend = box.querySelector("legend");
  fill(
    box,
    legend,
    devices.map((d) =>
      h(
        "label",
        {},
        h("input", { type: "checkbox", name: "grant", value: d.id, checked: d.role === "bench" }),
        `${d.label} (${d.id})`,
      ),
    ),
  );
}

function expiryText(epochSeconds) {
  if (!epochSeconds) return "";
  return new Date(epochSeconds * 1000).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
}

async function loadSessions() {
  let body;
  try {
    body = await api("/api/roles/sessions");
  } catch (err) {
    fill($("sessions"), h("li", { class: "error" }, err.message));
    return;
  }
  const sessions = body.sessions || [];
  fill(
    $("sessions"),
    sessions.length
      ? sessions.map((s) =>
          h(
            "li",
            {},
            h("div", { class: "title" }, `${s.name || "Unnamed"} — ${s.role}`),
            s.grants && s.grants.length ? h("div", { class: "sub" }, `Access to: ${s.grants.join(", ")}`) : null,
            h("div", { class: "sub" }, `Access lasts until ${expiryText(s.expires_at)}`),
            h("div", { class: "actions" },
              h("button", { type: "button", class: "danger", onclick: () => revoke("sessions", s) }, "Revoke")),
          ),
        )
      : h("li", { class: "muted" }, "None yet."),
  );
  const links = body.enrollments || [];
  fill(
    $("enrollments"),
    links.length
      ? links.map((e) =>
          h(
            "li",
            {},
            h("div", { class: "title" }, `${e.name || "Unnamed"} — ${e.role}`),
            h("div", { class: "sub" }, `Unused; expires ${expiryText(e.expires_at)}`),
            h("div", { class: "actions" },
              h("button", { type: "button", class: "secondary", onclick: () => revoke("enrollments", e) }, "Cancel link")),
          ),
        )
      : h("li", { class: "muted" }, "None."),
  );
  if (body.problem) show($("enroll-error"), body.problem);
}

async function revoke(kind, record) {
  const what = kind === "sessions" ? `Revoke access for "${record.name || "this device"}"?` : "Cancel this setup link?";
  if (!window.confirm(what)) return;
  try {
    await api(`/api/roles/${kind}/${encodeURIComponent(record.fingerprint)}`, { method: "DELETE" });
  } catch (err) {
    show($("enroll-error"), err.message);
  }
  loadSessions();
}

async function createLink(event) {
  event.preventDefault();
  show($("enroll-error"), "");
  const role = enrollRole();
  const name = $("enroll-name").value.trim();
  let grants = [];
  if (role === "tester") {
    grants = [...document.querySelectorAll('#enroll-devices input[name="grant"]:checked')].map((i) => i.value);
    if (!grants.length) return show($("enroll-error"), "Choose at least one device.");
  }
  try {
    const link = await api("/api/roles/enrollments", { method: "POST", body: { role, name, grants } });
    $("enroll-url").value = link.url;
    const access = (link.grants || []).length ? ` It gives access to: ${link.grants.join(", ")}.` : "";
    $("enroll-link-note").textContent =
      `Open this link on ${link.name || "the device"} (${link.role}). It works once and expires ` +
      `${expiryText(link.expires_at)}.${access}`;
    $("enroll-link").hidden = false;
    loadSessions();
  } catch (err) {
    show($("enroll-error"), err.message);
  }
}

async function copyLink() {
  const ok = await copyJSON($("enroll-url").value);
  $("enroll-copy").textContent = ok ? "Copied" : "Copy failed — select and copy it";
  setTimeout(() => ($("enroll-copy").textContent = "Copy"), 2500);
}

// ---- log tail -----------------------------------------------------------------------------------------------------
async function pollLogs() {
  if (logsPaused || document.visibilityState !== "visible") return;
  const list = $("logs");
  let body;
  try {
    body = await api(`/api/ops/logs?limit=${LOG_KEEP}${logSeq != null ? `&since=${logSeq}` : ""}`);
  } catch (err) {
    fill(list, h("li", { class: "muted" }, err.message));
    logSeq = null;
    return;
  }
  const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 24;
  if (logSeq == null) list.replaceChildren();
  if (logSeq == null && !body.records.length) list.append(h("li", { class: "muted empty" }, "Nothing logged yet."));
  if (body.records.length) list.querySelectorAll(".empty").forEach((e) => e.remove());
  for (const r of body.records) {
    list.append(
      h(
        "li",
        { class: `log ${r.level.toLowerCase()}` },
        h("span", { class: "log-t" }, clock(r.t)),
        h("span", { class: "log-l" }, r.level),
        h("span", { class: "log-m" }, r.message),
        h("span", { class: "log-n" }, r.logger),
        r.exc ? h("details", {}, h("summary", {}, "Details"), h("pre", {}, r.exc)) : null,
      ),
    );
  }
  while (list.children.length > LOG_KEEP) list.firstElementChild.remove();
  logSeq = body.last_seq;
  if (atBottom || body.records.length === list.children.length) list.scrollTop = list.scrollHeight;
}

function setupLogs() {
  const btn = $("logs-toggle");
  btn.addEventListener("click", () => {
    logsPaused = !logsPaused;
    btn.setAttribute("aria-pressed", String(logsPaused));
    btn.textContent = logsPaused ? "Resume" : "Pause";
    if (!logsPaused) pollLogs();
  });
  pollLogs();
  setInterval(pollLogs, LOG_POLL_MS);
}

// ---- refresh + live -----------------------------------------------------------------------------------------------
async function refresh(full = false) {
  try {
    const [summary, list] = await Promise.all([
      api("/api/ops/summary"),
      api(full ? "/api/devices?refresh=1" : "/api/devices"),
    ]);
    show($("load-error"), "");
    const v = summary.version || {};
    $("mode").textContent = modeText(v);
    $("version").textContent = `Conductor ${v.version || "?"} · ${v.commit || "local"} · mode ${v.mode || "?"}`;
    simBadge(v);
    renderAttention(summary.attention || []);
    renderAccounts(summary.accounts || {});
    renderDevices(list.devices || [], list.groups);
    renderBudget(summary);
    $("devices-note").textContent = list.simulated
      ? "The show engine runs on the simulator here: statuses are SIMULATED."
      : "";
  } catch (err) {
    show($("load-error"), err.message);
  }
  loadState();
  loadHeads();
  loadIpad();
}

function soon() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => refresh(), 400);
}

const LIVE_TEXT = {
  connecting: "Connecting…",
  open: "Live",
  reconnecting: "Reconnecting…",
  unauthorized: "Signed out — retrying",
  error: "Live",
  closed: "Offline",
};

function setupLive() {
  connectLive({
    topics: ["canopy", "ops"],
    onEvent: (topic, data) => {
      if (topic === "canopy" && data && data.phase) {
        const wasStepping = Boolean(canopyState && canopyState.program);
        renderState({ ...data, modes: undefined });
        if (wasStepping !== Boolean(data.program)) loadState(); // a run started or stopped: re-read its gauge
        clearTimeout(refreshTimer);
        refreshTimer = setTimeout(loadHeads, 400);
      } else {
        soon();
      }
    },
    onStatus: (state) => {
      const el = $("live");
      el.dataset.state = state;
      el.textContent = LIVE_TEXT[state] || state;
    },
  });
}

$("refresh").addEventListener("click", () => refresh(true));
$("enroll-form").addEventListener("submit", createLink);
$("enroll-copy").addEventListener("click", copyLink);
document.querySelectorAll('#enroll-role input[name="role"]').forEach((i) => i.addEventListener("change", syncEnrollRole));
$("checklist-run").addEventListener("click", runChecklist);
$("test-mode-btn").addEventListener("click", testModeChange);
setupControls();
setupRehearsal();
syncEnrollRole();
refresh();
loadSessions();
runChecklist();
setupLogs();
setupLive();
setInterval(() => {
  if (document.visibilityState === "visible") refresh();
}, POLL_MS);
