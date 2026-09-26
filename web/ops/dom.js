// Small DOM helpers for the /ops pages. Text always goes in as text nodes (never innerHTML), so nothing the
// server or a device reports can inject markup.

/** h("a", {href: "/x", class: "btn"}, "text", childNode, ...) → element. Falsy children are skipped. */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === false || value == null) continue;
    if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else if (key === "class") el.className = value;
    else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, String(value));
  }
  for (const child of children.flat()) {
    if (child == null || child === false || child === "") continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

/** Replace an element's children. */
export function fill(el, ...children) {
  el.replaceChildren();
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

const PILLS = {
  ready: ["ready", "Ready"],
  updating: ["updating", "Updating"],
  needs_attention: ["attention", "Needs attention"],
  unknown: ["offline", "Unknown"],
};

/** The tracker status as a pill: Ready / Updating / Needs attention / Unknown. */
export function statusPill(status) {
  const [cls, text] = PILLS[status] || PILLS.unknown;
  return h("span", { class: `pill ${cls}` }, text);
}

export function pill(cls, text) {
  return h("span", { class: `pill ${cls}` }, text);
}

export function simTag() {
  return h("span", { class: "tag sim", title: "Simulated: nothing reaches Hubspace" }, "Simulated");
}

export function show(el, message) {
  el.textContent = message || "";
  el.hidden = !message;
}

/** Seconds → "40 s", "12 min", "3 h", "2 d". */
export function fmtAge(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return "–";
  const s = Math.max(0, Math.round(Number(seconds)));
  if (s < 120) return `${s} s`;
  if (s < 7200) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} d`;
}
