// The /ops "On the iPad" card's wording and rows (web/ops/ipad-model.mjs, D76). Run with `node --test tests/*.test.mjs`
// (tests/test_ipad_menu.py runs it under pytest, so CI does too).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { GRID_MAX, ipadKey, ipadRows, ipadSummary } from "../web/ops/ipad-model.mjs";

const BODY = {
  scenes: [
    { id: "lowcountry-calm", name: "Lowcountry Calm", group: "lowcountry-calm", option_of: null, on_ipad: true, idle: true },
    { id: "golden-hour", name: "Golden Hour", group: "golden-hour", option_of: null, on_ipad: true, idle: false },
    { id: "golden-hour-2", name: "Golden Hour #2", group: "golden-hour", option_of: "golden-hour", on_ipad: true, idle: false },
    { id: "golden-hour-3", name: "Golden Hour #3", group: "golden-hour", option_of: "golden-hour", on_ipad: false, idle: false },
  ],
  visible: 3,
  total: 4,
  grid_max: 6,
  review: false,
  notes: [],
  file: "overrides/settings.yaml",
  writable: true,
};

test("the On the iPad card's wording and rows", () => {
  assert.equal(GRID_MAX, 6);
  assert.equal(
    ipadSummary({ visible: 12, total: 18 }),
    "12 of 18 scenes are on the iPad. More than 6: guests see the review menu (one row per scene, its options side " +
      "by side; the scene list scrolls).",
  );
  assert.ok(ipadSummary({ visible: 6, total: 18 }).endsWith("Guests see the usual grid."));
  assert.equal(ipadSummary({ visible: 6, total: 6 }), "6 of 6 scenes are on the iPad. Guests see the usual grid.");
  assert.ok(ipadSummary({ visible: 7, total: 18 }).includes("review menu"));

  const rows = ipadRows(BODY);
  assert.deepEqual(
    rows.map((r) => r.id),
    BODY.scenes.map((s) => s.id),
  );
  const [idle, base, option, off] = rows;
  assert.deepEqual(idle, {
    id: "lowcountry-calm",
    name: "Lowcountry Calm",
    option: false,
    on: true,
    idle: true,
    text: "Always on (idle scene)",
    label: "Show Lowcountry Calm on the iPad",
    disabled: true,
  });
  assert.equal(base.option, false);
  assert.equal(base.disabled, false);
  assert.equal(base.text, "On the iPad");
  assert.equal(option.option, true);
  assert.equal(option.label, "Show Golden Hour #2 on the iPad");
  assert.equal(off.on, false);
  assert.equal(off.text, "Off the iPad");
  assert.equal(off.disabled, false);
  assert.deepEqual(ipadRows({}), []);
  assert.deepEqual(ipadRows(null), []);
});

test("the card's key changes on one flip and only then", () => {
  const before = ipadKey(BODY);
  assert.equal(ipadKey(structuredClone(BODY)), before);
  const flipped = structuredClone(BODY);
  flipped.scenes[2].on_ipad = false;
  assert.notEqual(ipadKey(flipped), before);
  const noted = { ...structuredClone(BODY), notes: ["a note"], visible: 99 };
  assert.equal(ipadKey(noted), before); // the summary and notes redraw on their own; the rows don't need to
});

// ops.js's own card functions (note, drawIpad, loadIpad, flipIpad), lifted from the served source and run over stub
// elements: ops.js is a page script (it wires the DOM at load), so it can't be imported under node.
function opsSource(js, name) {
  const at = js.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `ops.js has no function ${name}`);
  const start = js.slice(Math.max(0, at - 6), at) === "async " ? at - 6 : at;
  const open = js.indexOf("{", js.indexOf(")", at));
  let depth = 0;
  for (let i = open; i < js.length; i += 1) {
    if (js[i] === "{") depth += 1;
    else if (js[i] === "}" && (depth -= 1) === 0) return js.slice(start, i + 1);
  }
  throw new Error(`unbalanced ${name}`);
}

async function ipadCard() {
  const js = readFileSync(new URL("../web/ops/ops.js", import.meta.url), "utf8");
  const { show } = await import("../web/ops/dom.js"); // show() touches only the element it is given
  const els = {};
  const $ = (id) => (els[id] ||= { id, textContent: "", hidden: true, className: "notice", children: [] });
  const answers = [];
  const api = async (path, init) => {
    const next = answers.shift();
    assert.ok(next, `no answer queued for ${path}`);
    if (next instanceof Error) throw next;
    return typeof next === "function" ? next(path, init) : next;
  };
  const stubs = {
    $,
    api,
    show,
    fill: (el, kids) => (el.children = kids),
    h: (tag, attrs, ...children) => ({ tag, attrs, children }),
    ipadKey,
    ipadRows,
    ipadSummary,
    loadState: () => {},
  };
  const names = ["note", "drawIpad", "loadIpad", "flipIpad"];
  const src = `let ipadDrawn = "";\n${names.map((n) => opsSource(js, n)).join("\n")}\nreturn { ${names.join(", ")} };`;
  const card = new Function(...Object.keys(stubs), src)(...Object.values(stubs));
  return { ...card, $, answers };
}

test("a failed load says so on the card, and the next load that works takes it away", async () => {
  const { loadIpad, flipIpad, $, answers } = await ipadCard();
  const LOST = "Can't reach the Conductor. Check the connection and try again.";
  answers.push(new Error(LOST)); // e.g. the Conductor restarting during a deploy
  await loadIpad();
  assert.equal($("ipad-error").hidden, false);
  assert.equal($("ipad-error").textContent, LOST);
  assert.equal($("ipad-error").className, "error");
  assert.equal($("ipad-result").hidden, true, "a load never writes the switch's line");

  answers.push(structuredClone(BODY)); // the next 10 s poll gets through
  await loadIpad();
  assert.equal($("ipad-error").hidden, true, "the stale error goes with the first fresh answer");
  assert.equal($("ipad-error").textContent, "");
  assert.equal($("ipad-summary").textContent, ipadSummary(BODY));
  assert.equal($("ipad-scenes").children.length, BODY.scenes.length);

  // a switch's own answer stays until the next switch, through a failed poll and the one after it
  const row = ipadRows(BODY)[3];
  const flipped = (_path, init) => {
    assert.deepEqual(init.body, { scene: row.id, show: true });
    return { ...structuredClone(BODY), message: "Golden Hour #3 is on the iPad." };
  };
  answers.push(flipped);
  await flipIpad({ disabled: false }, row);
  assert.equal($("ipad-result").textContent, "Golden Hour #3 is on the iPad.");
  assert.equal($("ipad-result").className, "banner-ok");
  answers.push(new Error(LOST));
  await loadIpad();
  assert.equal($("ipad-error").hidden, false);
  answers.push(structuredClone(BODY));
  await loadIpad();
  assert.equal($("ipad-error").hidden, true);
  assert.equal($("ipad-result").hidden, false);
  assert.equal($("ipad-result").textContent, "Golden Hour #3 is on the iPad.");

  // a switch that fails says so on its own line, re-enables, and a later good poll leaves its answer alone
  const button = { disabled: false };
  answers.push(new Error("The iPad's menu couldn't be saved."));
  await flipIpad(button, row);
  assert.equal($("ipad-result").className, "error");
  assert.equal(button.disabled, row.disabled);
  answers.push(structuredClone(BODY));
  await loadIpad();
  assert.equal($("ipad-result").textContent, "The iPad's menu couldn't be saved.");
  assert.equal($("ipad-error").hidden, true);
  assert.equal(answers.length, 0);
});

test("the model names no count it can't know and fetches nothing", () => {
  const src = readFileSync(new URL("../web/ops/ipad-model.mjs", import.meta.url), "utf8");
  assert.ok(!/nine/i.test(src));
  assert.ok(!/https?:\/\//.test(src));
  assert.ok(!/\b(document|window|fetch)\b/.test(src.replace(/^\s*\/\/.*$/gm, "")));
});
