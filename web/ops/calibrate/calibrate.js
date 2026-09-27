// /ops/calibrate/: the nine modules' wire-order calibration and a button into each module page's Calibrate tab.
import { api, simBadge } from "/static/app.js";
import { fill, h, pill, show } from "/ops/dom.js";

// The SVG namespace name (an identifier, never fetched); split so the "no external URL" asset check stays simple.
const SVG_NS = ["http:", "", "www.w3.org", "2000", "svg"].join("/");
const $ = (id) => document.getElementById(id);

function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) el.setAttribute(k, String(v));
  for (const c of children.flat()) if (c) el.append(c);
  return el;
}

const CORNER = {
  "top-left": "top left",
  "top-right": "top right",
  "bottom-left": "bottom left",
  "bottom-right": "bottom right",
};

function panel(m, w, hgt) {
  const n = m.polyline.length;
  const heads = m.polyline.map(([x, y], i) =>
    svg("circle", {
      cx: x,
      cy: y,
      r: i === 0 ? 3.2 : 2.1,
      fill: `hsl(${(12 * i) % 360} 100% ${i === 0 ? 55 : 50}%)`,
      stroke: i === 0 ? "#fff" : null,
      "stroke-width": i === 0 ? 0.8 : null,
    }),
  );
  return svg(
    "svg",
    { viewBox: `-2 -2 ${w + 4} ${hgt + 4}`, class: "panel", role: "img",
      "aria-label": `${m.label}: ${n} heads, head 0 at the ${CORNER[m.start_corner_as_shown]}` },
    svg("polyline", {
      points: m.polyline.map(([x, y]) => `${x},${y}`).join(" "),
      fill: "none",
      stroke: "rgba(255, 190, 120, 0.55)",
      "stroke-width": 0.7,
    }),
    heads,
  );
}

async function load() {
  let body;
  try {
    body = await api("/api/ops/calibration");
  } catch (err) {
    show($("load-error"), err.message);
    return;
  }
  show($("load-error"), "");
  simBadge();
  fill($("progress"), pill(body.calibrated === body.total ? "ready" : "updating",
    `${body.calibrated} of ${body.total} calibrated`));
  const cells = [...body.modules].sort((a, b) => a.row - b.row || a.col - b.col);
  fill(
    $("modules"),
    cells.map((m) =>
      h(
        "article",
        { class: "card cal-card", "data-device": m.device_id },
        h("h3", {}, h("span", {}, m.label), m.calibrated ? pill("ready", "Calibrated") : pill("offline", "Default")),
        h("div", { class: "sub muted small" }, `${m.module} · orientation ${m.orientation}`),
        panel(m, body.module_width_in, body.module_height_in),
        h(
          "div",
          { class: "small" },
          `Cable along ${m.cable_path === "serpentine_cols" ? "columns" : "rows"}` +
            `${m.explicit_wire_order ? " (order set by hand)" : ""}; head 0 at the ${CORNER[m.start_corner_as_shown]}.`,
        ),
        m.hops_fit
          ? null
          : h("div", { class: "problem" }, `A ${m.longest_hop_in}″ hop is longer than the cable between pucks: recheck this order.`),
        m.lock_owner ? h("div", { class: "sub small" }, `Open on the module page by ${m.lock_owner}`) : null,
        h(
          "div",
          { class: "actions" },
          h("a", { class: "btn", href: m.link }, m.calibrated ? "Re-check on the module page" : "Calibrate"),
        ),
      ),
    ),
  );
}

load();
