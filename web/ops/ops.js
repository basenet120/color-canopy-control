// The /ops landing page: mode, "Needs attention", accounts, devices, tester links. Polls every 10 s.
import { api, copyJSON, simBadge } from "/static/app.js";
import { fill, fmtAge, h, pill, show, simTag, statusPill } from "/ops/dom.js";

const POLL_MS = 10000;
const $ = (id) => document.getElementById(id);
let devices = [];
let testerChoicesDrawn = false;

function modeText(v) {
  const mode = v.mode || "sim";
  if (mode === "sim" && !v.bench_live) return "Simulator — no real lights are driven";
  if (mode === "sim") return "Simulator, but bench jobs reach Hubspace";
  if (mode === "bench") return "Bench — the show runs on the simulator; bench jobs reach Hubspace";
  return "Live — the canopy is driven through Hubspace";
}

function renderAttention(items) {
  const list = $("attention");
  if (!items.length) {
    fill(list, h("li", { class: "all-clear" }, "Nothing needs attention."));
    return;
  }
  fill(
    list,
    items.map((item) =>
      h("li", {}, h("span", {}, item.message, " ", item.link ? h("a", { href: item.link }, "Fix") : null)),
    ),
  );
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
          ? h("div", { class: "sub" }, `Signed in ${fmtAge(a.token_age_s)} ago`)
          : null,
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
    if (c.framebuffer_length != null) parts.push(`${c.framebuffer_length} heads`);
    parts.push(c.individual ? "per-head colour" : "no per-head colour");
    if (c.slots && c.slots.length) parts.push(`${c.slots.length} slots`);
    return parts.join(" · ");
  }
  return d.capabilities_error ? `Profile not read: ${d.capabilities_error}` : "Profile not read yet";
}

function renderDevices(list) {
  devices = list;
  fill(
    $("devices"),
    list.map((d) => {
      const q = encodeURIComponent(d.id);
      const lock = d.lock ? `In use by ${d.lock.owner}` : null;
      return h(
        "li",
        {},
        h("div", { class: "row" }, h("span", { class: "title" }, d.label), statusPill(d.status),
          d.simulated ? simTag() : null),
        h("div", { class: "sub" }, [d.id, d.module, d.account && `account: ${d.account}`].filter(Boolean).join(" · ")),
        d.reason && d.status !== "ready" ? h("div", { class: "sub" }, d.reason) : null,
        h("div", { class: "sub" }, capsLine(d)),
        d.status !== "needs_attention" ? (d.problems || []).map((p) => h("div", { class: "problem" }, p)) : null,
        d.head_count_problem ? h("div", { class: "problem" }, `Head count: ${d.head_count_problem}`) : null,
        lock ? h("div", { class: "sub" }, lock) : null,
        d.purposes.bench.simulated === false
          ? h("div", { class: "sub" }, d.allowlisted ? "Bench reaches the real device" : "Real device, read-only (not allowlisted)")
          : null,
        h(
          "div",
          { class: "actions" },
          h("a", { class: "btn secondary", href: `/ops/module/?device=${q}` }, "Open module page"),
          h("a", { class: "btn secondary", href: `/ops/bench/?device=${q}` }, "Bench"),
        ),
      );
    }),
  );
  drawTesterChoices();
}

function drawTesterChoices() {
  if (testerChoicesDrawn || !devices.length) return;
  testerChoicesDrawn = true;
  const box = $("tester-devices");
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
            s.grants && s.grants.length ? h("div", { class: "sub" }, `Devices: ${s.grants.join(", ")}`) : null,
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
  if (body.problem) show($("tester-error"), body.problem);
}

async function revoke(kind, record) {
  const what = kind === "sessions" ? `Revoke access for "${record.name || "this device"}"?` : "Cancel this setup link?";
  if (!window.confirm(what)) return;
  try {
    await api(`/api/roles/${kind}/${encodeURIComponent(record.fingerprint)}`, { method: "DELETE" });
  } catch (err) {
    show($("tester-error"), err.message);
  }
  loadSessions();
}

async function createTesterLink(event) {
  event.preventDefault();
  show($("tester-error"), "");
  const name = $("tester-name").value.trim();
  const grants = [...document.querySelectorAll('#tester-devices input[name="grant"]:checked')].map((i) => i.value);
  if (!grants.length) {
    show($("tester-error"), "Choose at least one device.");
    return;
  }
  try {
    const link = await api("/api/roles/enrollments", { method: "POST", body: { role: "tester", name, grants } });
    $("tester-url").value = link.url;
    $("tester-link-note").textContent =
      `Send this link to ${link.name || "the tester"}. It works once and expires ${expiryText(link.expires_at)}. ` +
      `It gives access to: ${(link.grants || []).join(", ")}.`;
    $("tester-link").hidden = false;
    loadSessions();
  } catch (err) {
    show($("tester-error"), err.message);
  }
}

async function copyLink() {
  const ok = await copyJSON($("tester-url").value);
  $("tester-copy").textContent = ok ? "Copied" : "Copy failed — select and copy it";
  setTimeout(() => ($("tester-copy").textContent = "Copy"), 2500);
}

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
    renderDevices(list.devices || []);
    $("devices-note").textContent = list.simulated
      ? "The show engine runs on the simulator here: statuses are SIMULATED."
      : "";
  } catch (err) {
    show($("load-error"), err.message);
  }
}

$("refresh").addEventListener("click", () => refresh(true));
$("tester-form").addEventListener("submit", createTesterLink);
$("tester-copy").addEventListener("click", copyLink);
refresh();
loadSessions();
setInterval(() => {
  if (document.visibilityState === "visible") refresh();
}, POLL_MS);
