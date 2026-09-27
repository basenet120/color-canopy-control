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

// A registry entry that already has a (different) Hubspace device gets this one instead: that's a controller swap.
function replaces(entry, device) {
  return Boolean(entry.metadevice_id) && entry.metadevice_id !== device.metadevice_id;
}

// Unregistered devices can be registered as any entry on this account (free entries first). A device registered as
// a spare kit can be swapped in for a module (it moves off the spare, with the bench flags it proved).
function registerControls(account, device, registry, onSpare) {
  const choices = registry
    .filter((r) => r.id !== device.registered_as && (!onSpare || r.role !== "spare"))
    .sort((a, b) => Number(replaces(a, device)) - Number(replaces(b, device)));
  if (!choices.length) return null;
  const bench = choices.find((r) => r.role === "bench" && !replaces(r, device));
  const select = h(
    "select",
    { "aria-label": onSpare ? "Swap in as" : "Register as" },
    choices.map((r) =>
      h("option", { value: r.id }, `${r.label} (${r.id})${replaces(r, device) ? " — replaces its current kit" : ""}`),
    ),
  );
  const chosen = () => choices.find((r) => r.id === select.value);
  return h(
    "div",
    { class: "actions" },
    bench && !onSpare
      ? h("button", { type: "button", onclick: () => register(account, device, bench) }, `Use as ${bench.label}`)
      : null,
    select,
    h(
      "button",
      { type: "button", class: "secondary", onclick: () => register(account, device, chosen()) },
      onSpare ? "Swap in" : "Register",
    ),
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
            d.registered_as && !onSpare(d, registry)
              ? null
              : registerControls(account, d, registry, Boolean(d.registered_as)),
          ),
        )
      : h("li", { class: "muted" }, "This account can't see any devices."),
  );
}

function onSpare(device, registry) {
  const entry = registry.find((r) => r.id === device.registered_as);
  return Boolean(entry && entry.role === "spare");
}

async function register(account, device, entry) {
  show($("devices-error"), "");
  if (!entry) return;
  const name = device.name || device.metadevice_id;
  const question = replaces(entry, device)
    ? `Register ${name} as ${entry.id}? It replaces the kit ${entry.id} uses now: the canopy stops driving that one.`
    : `Register ${name} as ${entry.id}?`;
  if (!window.confirm(question)) return;
  try {
    const result = await api(`/api/accounts/${encodeURIComponent(account)}/register`, {
      method: "POST",
      body: { metadevice_id: device.metadevice_id, device_id: entry.id },
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
