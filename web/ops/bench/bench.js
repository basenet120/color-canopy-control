// /ops/bench — run bench jobs one tap at a time, follow them live, answer the questions, copy the result.
// Everything shown comes from /api/bench/*; progress arrives on the "bench" WebSocket topic (with a polling
// fallback). Server text is always inserted with textContent, never as HTML.

import { api, connectLive, simBadge, copyJSON } from "/static/app.js";

const $ = (id) => document.getElementById(id);
const state = { jobs: [], devices: [], runId: null, record: null, pollTimer: null, liveOpen: false, mapAsk: 0 };

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children) if (c) node.append(c);
  return node;
}

function showError(message) {
  const box = $("error");
  box.textContent = message || "";
  box.hidden = !message;
  if (message) box.scrollIntoView({ behavior: "smooth", block: "center" });
}

function clock(epochSeconds) {
  if (epochSeconds == null) return "–";
  const d = new Date(epochSeconds * 1000);
  const pad = (n, w = 2) => String(n).padStart(w, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`;
}

function selectedDevice() {
  return state.devices.find((d) => d.device_id === $("device").value) || null;
}

function updateBanner() {
  const dev = selectedDevice();
  const simulated = (state.record && state.record.simulated) || (dev && dev.simulated);
  $("sim-banner").hidden = !simulated;
}

// ---- devices + jobs ----------------------------------------------------------------------------------------
async function loadJobs() {
  const data = await api("/api/bench/jobs");
  state.jobs = data.jobs || [];
  state.devices = data.devices || [];
  const select = $("device");
  let remembered = null;
  try {
    remembered = localStorage.getItem("bench-device");
  } catch {
    // storage blocked: fall back to the bench kit
  }
  const previous = select.value || remembered || "bench-kit";
  select.replaceChildren(
    ...state.devices.map((d) =>
      el("option", { value: d.device_id, text: `${d.label} (${d.device_id})${d.simulated ? " — simulated" : ""}` }),
    ),
  );
  if (state.devices.some((d) => d.device_id === previous)) select.value = previous;
  renderDeviceStatus();
  renderJobs();
}

function renderDeviceStatus() {
  const d = selectedDevice();
  const line = $("device-status");
  if (!d) {
    line.textContent = "No devices.";
    return;
  }
  const parts = [];
  if (d.simulated) parts.push("Runs on the SIMULATOR (the real kit isn't connected for bench jobs here).");
  else if (d.driver) parts.push("Runs on the REAL kit through Hubspace.");
  if (d.problem) parts.push(d.problem);
  if (d.locked_by) parts.push(`In use by ${d.locked_by}.`);
  line.textContent = parts.join(" ");
  updateBanner();
}

// A job whose setup the person must confirm first (kit off at the wall, strings unplugged) has a `confirm_label`;
// its `confirmed` option is shown as that tick box, outside the Options.
const isConfirm = (job, name) => Boolean(job.confirm_label) && name === "confirmed";

function paramInput(job, name, value) {
  const id = `p-${job.name}-${name}`;
  if (isConfirm(job, name)) {
    return el(
      "label",
      { class: "confirm", for: id },
      el("input", { type: "checkbox", id, "data-param": name }),
      job.confirm_label,
    );
  }
  const type = typeof value === "boolean" ? "checkbox" : typeof value === "number" ? "number" : "text";
  const input = el("input", { id, type, "data-param": name, inputmode: type === "number" ? "decimal" : null });
  if (type === "checkbox") input.checked = value;
  else input.value = String(value);
  if (type === "number") input.step = "any";
  const bounds = (job.bounds || {})[name];
  if (bounds) {
    input.min = String(bounds[0]);
    input.max = String(bounds[1]);
  }
  return el("div", { class: "param" }, el("label", { for: id, text: name }), input);
}

function renderJobs() {
  const wrap = $("jobs");
  wrap.replaceChildren(
    ...state.jobs.map((job, i) => {
      const params = Object.entries(job.params || {});
      const confirm = params.filter(([n]) => isConfirm(job, n));
      const options = params.filter(([n]) => !isConfirm(job, n));
      const card = el(
        "article",
        { class: "card stack job-card", "data-job": job.name },
        el("h3", { text: `${i + 1}. ${job.title}` }),
        el("p", { text: job.summary }),
        job.writes ? null : el("p", { class: "read-only", text: "Read-only: nothing is written to the kit." }),
        job.look_for ? el("p", { class: "look", text: `Look for: ${job.look_for}` }) : null,
        ...confirm.map(([n, v]) => paramInput(job, n, v)),
        options.length
          ? el("details", {}, el("summary", { text: "Options" }), ...options.map(([n, v]) => paramInput(job, n, v)))
          : null,
        el("button", { type: "button", class: "big", text: "Run", onclick: () => runJob(job, card) }),
      );
      return card;
    }),
  );
}

function collectParams(job, card) {
  const params = {};
  for (const input of card.querySelectorAll("[data-param]")) {
    const name = input.dataset.param;
    const def = job.params[name];
    if (input.type === "checkbox") params[name] = input.checked;
    else if (typeof def === "number") params[name] = Number(input.value);
    else params[name] = input.value;
  }
  return params;
}

async function runJob(job, card) {
  showError("");
  const device = $("device").value;
  const button = card.querySelector("button.big");
  button.disabled = true;
  try {
    const started = await api("/api/bench/run", {
      method: "POST",
      body: { job: job.name, device_id: device, params: collectParams(job, card) },
    });
    openRun(started.run_id);
  } catch (err) {
    showError(err.message);
  } finally {
    button.disabled = false;
  }
}

// ---- the current run ---------------------------------------------------------------------------------------
async function openRun(runId) {
  state.runId = runId;
  state.record = null;
  // Nothing from the previous run may show while this one loads (its title, or a "Finished" pill).
  const job = state.jobs.find((j) => runId.includes(`-${j.name}-`));
  $("run-title").textContent = job ? job.title : "Run";
  $("run-pill").className = "pill updating";
  $("run-pill").textContent = "Loading…";
  $("steps").replaceChildren();
  $("result").hidden = true;
  $("run-panel").hidden = false;
  $("run-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  await refreshRun();
}

async function refreshRun() {
  if (!state.runId) return;
  try {
    renderRun(await api(`/api/bench/runs/${encodeURIComponent(state.runId)}`));
  } catch (err) {
    showError(err.message);
  }
}

function stepItem(step) {
  const detail = step.detail || {};
  const li = el("li", { class: detail.kind === "write" ? "write" : "" });
  li.append(el("span", { class: "t", text: `${Number(step.elapsed_s || 0).toFixed(1)} s` }), step.message);
  if (detail.kind === "write" && detail.sent_at) li.append(` (sent ${clock(detail.sent_at)})`);
  return li;
}

function renderRun(record) {
  state.record = record;
  $("run-title").textContent = `${record.title} · ${record.device_id}`;
  $("run-sim").hidden = !record.simulated;
  const running = record.status === "running";
  const pill = $("run-pill");
  pill.className = `pill ${running ? "updating" : record.status === "finished" ? "ready" : "attention"}`;
  pill.textContent = running ? "Running" : record.status === "finished" ? "Finished" : "Interrupted";
  $("stop").hidden = !running;
  $("steps").replaceChildren(...(record.steps || []).map(stepItem));
  const list = $("steps");
  list.scrollTop = list.scrollHeight;
  updateBanner();
  schedulePoll(running);
  if (!record.result) {
    $("result").hidden = true;
    return;
  }
  renderResult(record);
}

function renderResult(record) {
  const result = record.result;
  $("result").hidden = false;
  const verdict = $("verdict");
  verdict.className = `verdict ${result.verdict}`;
  verdict.textContent = (result.verdict || "").replace("_", " ");
  const confirmed = record.confirmed_verdict;
  $("confirmed").textContent =
    result.verdict === "needs_eyes" ? (confirmed ? `After your answers: ${confirmed.toUpperCase()}` : "Answer below") : "";
  $("summary").textContent = result.summary || "";

  $("questions").replaceChildren(
    ...(result.ask_human || []).map((q) => {
      const given = record.answers ? record.answers[q] : undefined;
      const mk = (value, label) =>
        el("button", {
          type: "button",
          class: `${value}${given ? " answered" : ""}`,
          "aria-pressed": given === value ? "true" : "false",
          text: label,
          onclick: () => answer(q, value),
        });
      return el(
        "div",
        { class: "question" },
        el("p", { text: q }),
        el("div", { class: "answers" }, mk("yes", "Yes"), mk("no", "No")),
        given && given !== "yes" && given !== "no" ? el("p", { class: "given", text: `Note: ${given}` }) : null,
      );
    }),
  );

  const obs = result.observations || {};
  const rows = [];
  for (const [k, v] of Object.entries(obs)) {
    let text;
    if (v === null || ["string", "number", "boolean"].includes(typeof v)) text = String(v);
    else if (Array.isArray(v) && v.every((x) => typeof x !== "object")) text = v.join(", ") || "–";
    else continue;
    rows.push(el("dt", { text: k }), el("dd", { text }));
  }
  if (result.flags && Object.keys(result.flags).length) {
    rows.push(el("dt", { text: "proposed flags" }), el("dd", { text: JSON.stringify(result.flags) }));
  }
  $("observations").replaceChildren(...rows);
  $("raw").textContent = JSON.stringify(record, null, 2);

  const profile = $("profile");
  const hasProfile = (record.artifacts || []).includes("profile_raw");
  profile.hidden = !hasProfile;
  if (hasProfile) profile.href = `/api/bench/runs/${encodeURIComponent(record.run_id)}/profile.json`;

  renderFlags(record);
  renderMap(record);
}

function renderFlags(record) {
  const button = $("flags");
  const why = $("flags-why");
  const flags = record.result.flags || {};
  const names = Object.keys(flags);
  let reason = "";
  if (!names.length) reason = "This job doesn't propose any flags.";
  else if (record.simulated) reason = "Disabled: this was a simulated run, so it proves nothing about the real kit.";
  else if (record.flags_applied)
    reason = `Applied: ${JSON.stringify(record.flags_applied.flags)}. Changing your answer to No puts them back.`;
  else {
    const needed = record.flag_questions || {};
    const missing = names.filter((f) => (record.answers || {})[needed[f]] !== "yes");
    if (missing.length) reason = `Answer “${needed[missing[0]] || missing[0]}” with Yes first.`;
  }
  button.textContent = names.length ? `Apply flags ${JSON.stringify(flags)}` : "Apply flags";
  button.disabled = Boolean(reason);
  const revoked = record.flags_revoked;
  const undone =
    revoked && Object.keys(revoked.flags || {}).length
      ? ` Undone after your answer changed: ${JSON.stringify(revoked.flags)} (back to what they were before).`
      : "";
  why.textContent =
    (reason || "Saves these flags to the device's registry overlay, for this kit only.") + undone;
}

// Apply map (D58): a finished address probe can record its kit's map. The server says what it would record from
// this run and the kit's other probe, or why not; the button shows only when the proof holds.
async function renderMap(record) {
  const button = $("map");
  const why = $("map-why");
  const ask = ++state.mapAsk;
  const probe = record.job === "address_probe_safe" && record.status === "finished";
  button.hidden = true;
  why.hidden = !probe;
  if (!probe) return;
  const revoked = record.map_revoked;
  const undone = !revoked
    ? ""
    : revoked.undone
      ? " Taken back after an answer changed: the kit's previous map is back."
      : " An answer changed, but the kit's map had changed since, so it was left as it is.";
  const applied = record.map_applied;
  if (applied) {
    const heads = applied.mapping.indices.length;
    const length = applied.mapping.frame_length;
    why.textContent =
      `Map recorded: entries 0–${heads - 1} drive heads 1–${heads} of this kit's ${length}-entry controller ` +
      `(runs ${applied.runs.join(" and ")}). Changing your answer to No takes it back.`;
    return;
  }
  why.textContent = "Checking whether this probe can record the kit's map…";
  let proposal;
  try {
    proposal = await api(`/api/bench/runs/${encodeURIComponent(record.run_id)}/map`);
  } catch (err) {
    proposal = { ready: false, reason: err.message };
  }
  if (ask !== state.mapAsk) return; // a newer render (another answer, another run) owns the card now
  button.hidden = !proposal.ready;
  why.textContent = proposal.reason + undone;
}

async function answer(question, value) {
  showError("");
  try {
    const record = await api(`/api/bench/runs/${encodeURIComponent(state.runId)}/answers`, {
      method: "POST",
      body: { answers: { [question]: value } },
    });
    renderRun(record);
  } catch (err) {
    showError(err.message);
  }
}

function schedulePoll(running) {
  clearTimeout(state.pollTimer);
  state.pollTimer = null;
  if (running) state.pollTimer = setTimeout(refreshRun, state.liveOpen ? 5000 : 1500);
  else loadRuns();
}

// ---- live events -------------------------------------------------------------------------------------------
function onEvent(topic, data) {
  if (topic !== "bench" || !data) return;
  if (data.event === "started" || data.event === "finished") loadJobs().catch(() => {});
  if (!state.runId || data.run_id !== state.runId) return;
  if (data.event === "step" && data.step && state.record) {
    state.record.steps = [...(state.record.steps || []), data.step];
    $("steps").append(stepItem(data.step));
    $("steps").scrollTop = $("steps").scrollHeight;
  } else {
    refreshRun();
  }
}

function onStatus(s) {
  const live = $("live");
  state.liveOpen = s === "open";
  live.dataset.state = s;
  live.textContent =
    s === "open" ? "Live updates on" : s === "unauthorized" ? "Sign in to see live updates" : "Reconnecting live updates…";
}

// ---- recent runs -------------------------------------------------------------------------------------------
async function loadRuns() {
  try {
    const data = await api("/api/bench/runs?limit=20");
    $("runs").replaceChildren(
      ...(data.runs || []).map((r) =>
        el(
          "li",
          {},
          el(
            "button",
            { type: "button", onclick: () => openRun(r.run_id) },
            el("span", { text: `${r.title || r.job} · ${r.device_id}${r.simulated ? " · SIMULATED" : ""}` }),
            el("span", {
              class: "when",
              text: `${(r.confirmed_verdict || r.verdict || r.status || "").replace("_", " ")} · ${clock(r.started)}`,
            }),
          ),
        ),
      ),
    );
  } catch (err) {
    showError(err.message);
  }
}

// ---- wiring ------------------------------------------------------------------------------------------------
$("device").addEventListener("change", () => {
  try {
    localStorage.setItem("bench-device", $("device").value);
  } catch {
    // private mode: not remembered
  }
  renderDeviceStatus();
});
$("refresh-runs").addEventListener("click", loadRuns);
$("copy").addEventListener("click", async () => {
  const status = $("copy-status");
  try {
    const data = await api(`/api/bench/runs/${encodeURIComponent(state.runId)}/anonymized`);
    const ok = await copyJSON(data);
    if (ok) {
      status.textContent = "Copied. Paste it into the build session.";
    } else {
      $("raw").textContent = JSON.stringify(data, null, 2);
      $("raw").closest("details").open = true;
      status.textContent = "Couldn't copy automatically. The anonymized JSON is shown under “All details”: select it and copy.";
    }
  } catch (err) {
    status.textContent = err.message;
  }
  status.hidden = false;
});
$("stop").addEventListener("click", async () => {
  showError("");
  const button = $("stop");
  button.disabled = true;
  try {
    renderRun(await api(`/api/bench/runs/${encodeURIComponent(state.runId)}/stop`, { method: "POST", body: {} }));
  } catch (err) {
    showError(err.message);
  } finally {
    button.disabled = false;
  }
});
$("flags").addEventListener("click", async () => {
  showError("");
  try {
    renderRun(await api(`/api/bench/runs/${encodeURIComponent(state.runId)}/flags`, { method: "POST", body: {} }));
  } catch (err) {
    showError(err.message);
  }
});
$("map").addEventListener("click", async () => {
  showError("");
  const button = $("map");
  button.disabled = true;
  try {
    renderRun(await api(`/api/bench/runs/${encodeURIComponent(state.runId)}/map`, { method: "POST", body: {} }));
  } catch (err) {
    showError(err.message);
  } finally {
    button.disabled = false;
  }
});

(async function init() {
  try {
    // The shared badge follows the show engine, which stays simulated in bench mode; here what matters is
    // whether bench jobs reach Hubspace, so show it only when they can't (the per-device banner says the rest).
    const version = await api("/api/version");
    if (!version.bench_live) simBadge(version);
    await loadJobs();
  } catch (err) {
    showError(err.message);
  }
  loadRuns();
  connectLive({ topics: ["bench"], onEvent, onStatus });
})();
