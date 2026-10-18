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

test("the model names no count it can't know and fetches nothing", () => {
  const src = readFileSync(new URL("../web/ops/ipad-model.mjs", import.meta.url), "utf8");
  assert.ok(!/nine/i.test(src));
  assert.ok(!/https?:\/\//.test(src));
  assert.ok(!/\b(document|window|fetch)\b/.test(src.replace(/^\s*\/\/.*$/gm, "")));
});
