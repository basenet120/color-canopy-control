// /ops/accounts: account status, sign-in (the server-rendered form, only when turned on), device discovery and
// registration. Signing in never goes through this script: the form posts straight to /ops/accounts.
import { api, simBadge } from "/static/app.js";
import { fill, fmtAge, h, pill, show, simTag } from "/ops/dom.js";

const $ = (id) => document.getElementById(id);
let current = null; // the account whose devices are shown

function accountPill(a) {
  if (a.needs_reauth) return pill("attention", "Needs a new sign-in");
  if (a.connected) return pill("ready", "Connected");
  return pill("offline", "Not connected");
}

function renderAccounts(summary) {
  fill(
    $("accounts"),
    (summary.accounts || []).map((a) =>
      h(
        "li",
        {},
        h("div", { class: "row" }, h("span", { class: "title" }, a.label || a.account), accountPill(a),
          a.simulated ? simTag() : null),
        h("div", { class: "sub" }, `Name: ${a.account}`),
        a.connected && a.token_age_s != null ? h("div", { class: "sub" }, `Token saved ${fmtAge(a.token_age_s)} ago`) : null,
        a.token_generation ? h("div", { class: "sub" }, `Token generation ${a.token_generation}`) : null,
        a.last_error && !a.connected ? h("div", { class: "sub" }, a.last_error) : null,
        a.unknown_devices && a.unknown_devices.length
          ? h("div", { class: "problem" },
              `Can see ${a.unknown_devices.length} device(s) that aren't registered — read-only until registered.`)
          : null,
        a.connected
          ? h(
              "div",
              { class: "actions" },
              h("button", { type: "button", class: "secondary", onclick: () => showDevices(a.account) }, "Show devices"),
              h("button", { type: "button", class: "danger", onclick: () => disconnect(a) }, "Disconnect"),
            )
          : null,
      ),
    ),
  );
}

function renderSignIn(summary) {
  const select = $("account");
  const chosen = select.value || "bench";
  fill(
    select,
    (summary.accounts || []).map((a) => h("option", { value: a.account, selected: a.account === chosen }, a.label || a.account)),
  );
  const blocked = !summary.connect_allowed;
  show($("connect-blocked"), blocked ? `Sign-in isn't possible on this runtime: ${summary.connect_reason}` : "");
  if (summary.token_store && !summary.simulated) show($("connect-blocked"), summary.token_store);
  $("form-on").hidden = !summary.form_enabled || blocked;
  $("form-off").hidden = summary.form_enabled && !blocked;
  $("form-reason").textContent = summary.form_reason || "";
  $("cli-command").textContent =
    `python -m conductor.auth login --server ${location.origin} --account ${chosen} --user <ops user>`;
  $("sim-note").hidden = !summary.simulated;
}

async function load() {
  try {
    const summary = await api("/api/accounts");
    show($("load-error"), "");
    renderAccounts(summary);
    renderSignIn(summary);
    if (summary.simulated) simBadge({ simulated: true });
    else simBadge();
  } catch (err) {
    show($("load-error"), err.message);
  }
}

async function disconnect(a) {
  if (!window.confirm(`Disconnect ${a.label || a.account}? The stored token is deleted and someone will need to sign in again.`)) return;
  try {
    await api(`/api/accounts/${encodeURIComponent(a.account)}/disconnect`, { method: "POST", body: {} });
  } catch (err) {
    show($("load-error"), err.message);
  }
  if (current === a.account) $("devices-card").hidden = true;
  load();
}

function registerControls(account, device, registry) {
  const choices = registry.filter((r) => !r.metadevice_id || r.metadevice_id === device.metadevice_id);
  if (!choices.length) return h("div", { class: "sub" }, "Every registry device on this account already has an id.");
  const bench = choices.find((r) => r.role === "bench");
  const select = h(
    "select",
    { "aria-label": "Register as" },
    choices.map((r) => h("option", { value: r.id }, `${r.label} (${r.id})`)),
  );
  return h(
    "div",
    { class: "actions" },
    bench
      ? h("button", { type: "button", onclick: () => register(account, device, bench.id) }, `Use as ${bench.label}`)
      : null,
    select,
    h("button", { type: "button", class: "secondary", onclick: () => register(account, device, select.value) }, "Register"),
  );
}

async function showDevices(account) {
  current = account;
  $("devices-card").hidden = false;
  $("devices-account").textContent = account;
  show($("devices-error"), "");
  show($("register-result"), "");
  fill($("devices"), h("li", { class: "muted" }, "Asking Hubspace…"));
  let body;
  try {
    body = await api(`/api/accounts/${encodeURIComponent(account)}/devices`);
  } catch (err) {
    fill($("devices"));
    show($("devices-error"), err.message);
    return;
  }
  $("devices-note").textContent = body.note || "";
  const registry = body.registry || [];
  fill(
    $("devices"),
    body.devices.length
      ? body.devices.map((d) =>
          h(
            "li",
            {},
            h("div", { class: "row" }, h("span", { class: "title" }, d.name || "Unnamed device"),
              d.read_only ? h("span", { class: "tag ro" }, "Read-only") : h("span", { class: "tag ok" }, "Writable"),
              d.simulated ? simTag() : null),
            h("div", { class: "sub" }, [d.model, h("code", {}, d.metadevice_id)].filter(Boolean).flatMap((x, i) => (i ? [" · ", x] : [x]))),
            h(
              "div",
              { class: "sub" },
              d.registered_as
                ? `Registered as ${d.registered_as}${d.allowlisted ? "" : " — not in HUBSPACE_DEVICE_ALLOWLIST yet"}`
                : "Not registered: the Conductor won't write to it.",
            ),
            d.registered_as ? null : registerControls(account, d, registry),
          ),
        )
      : h("li", { class: "muted" }, "This account can't see any devices."),
  );
}

async function register(account, device, deviceId) {
  show($("devices-error"), "");
  if (!window.confirm(`Register ${device.name || device.metadevice_id} as ${deviceId}?`)) return;
  try {
    const result = await api(`/api/accounts/${encodeURIComponent(account)}/register`, {
      method: "POST",
      body: { metadevice_id: device.metadevice_id, device_id: deviceId },
    });
    await showDevices(account);
    show($("register-result"), result.message);
  } catch (err) {
    show($("devices-error"), err.message);
  }
}

const params = new URLSearchParams(location.search);
if (params.get("result") === "connected") {
  show($("result"), "Connected. Signing in from this page is now off until the Conductor restarts.");
  history.replaceState(null, "", location.pathname);
}
$("account").addEventListener("change", () => {
  $("cli-command").textContent =
    `python -m conductor.auth login --server ${location.origin} --account ${$("account").value} --user <ops user>`;
});
load();
