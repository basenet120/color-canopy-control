// The /ops stepped card's wording (web/ops/stepped-model.mjs, Tier.STEPPED D65 draft): step, pace against the cap,
// when it holds, the governor's cool-off. Run with `node --test tests/*.test.mjs`.
import test from "node:test";
import assert from "node:assert/strict";
import { paceText, steppedLines } from "../web/ops/stepped-model.mjs";

const clock = (t) => `@${t}`;
const frames = (n) => Array.from({ length: n + 1 }, () => Array(9).fill("#FFFFFF"));
const PROGRAM = { t0: 1000, step_s: 6, step: 2, ends_at: 1090, frames: frames(15) };
const GAUGE = {
  running: true,
  moving: true,
  cap: 90,
  account: "canopy",
  writes_60s: 81,
  cooloff: [],
  governor_trips: 0,
  steps: 15,
  holds_at: 1090,
};

test("a moving run says its step, its pace against the cap and when it holds", () => {
  assert.deepEqual(steppedLines(PROGRAM, GAUGE, clock), [
    "Stepped look: step 3 of 15 · every 6.0 s · 81 writes/min on account canopy (cap 90) · holds at @1090",
  ]);
  assert.equal(paceText(GAUGE), "81 writes/min on account canopy (cap 90)");
});

test("before the gauge is read the card still says the step, never a pace it doesn't have", () => {
  assert.deepEqual(steppedLines(PROGRAM, null, clock), ["Stepped look: step 3 of 15 · every 6.0 s · holds at @1090"]);
  assert.equal(paceText({ ...GAUGE, writes_60s: null }), "");
  // the last step and a step past the end stay inside the run
  assert.match(steppedLines({ ...PROGRAM, step: 14 }, GAUGE, clock)[0], /step 15 of 15/);
  assert.match(steppedLines({ ...PROGRAM, step: 15 }, GAUGE, clock)[0], /step 15 of 15/);
});

test("a held run says nothing is sent; a stale moving gauge without a program says nothing", () => {
  const held = steppedLines(null, { ...GAUGE, moving: false }, clock);
  assert.equal(held.length, 1);
  assert.match(held[0], /^Stepped look held: nothing is sent/);
  assert.match(held[0], /81 writes\/min on account canopy \(cap 90\) · holds at @1090$/);
  assert.deepEqual(steppedLines(null, GAUGE, clock), []);
  assert.deepEqual(steppedLines(null, null, clock), []);
});

test("the governor's cool-off is its own line, with or without a run", () => {
  const cooling = { running: false, cap: 90, cooloff: [{ account: "canopy", until: 2000 }], governor_trips: 1 };
  assert.deepEqual(steppedLines(null, cooling, clock), [
    "Throttle governor: Hubspace throttled a stepped run on account canopy, so stepped looks there hold still " +
      "until @2000 (trips since start: 1).",
  ]);
  const both = steppedLines(PROGRAM, { ...GAUGE, cooloff: cooling.cooloff, governor_trips: 1 }, clock);
  assert.equal(both.length, 2);
});

test("the rehearsal line says what runs and until when, else the last verdict and its report", async () => {
  const { rehearsalText } = await import("../web/ops/stepped-model.mjs");
  assert.equal(rehearsalText(null, clock), "");
  assert.equal(rehearsalText({ running: null, last: null }, clock), "");
  const running = { label: "Golden Hour", minutes: 10, modules: 9, ends_at: 1600, simulated: false };
  assert.equal(
    rehearsalText({ running, last: null }, clock),
    "Rehearsing Golden Hour for 10 min on 9 modules · ends at @1600 · guests wait meanwhile",
  );
  assert.match(rehearsalText({ running: { ...running, simulated: true } }, clock), / · SIMULATED$/);
  const last = {
    label: "Golden Hour",
    minutes: 2,
    verdict: "pass",
    simulated: true,
    file: "/data/bench/rehearsals/rehearsal-20261023T100110Z-golden-hour.json",
    summary: "Golden Hour, 2 min on 9 modules: pass.",
  };
  assert.equal(
    rehearsalText({ running: null, last }, clock),
    "Last rehearsal: Golden Hour, 2 min — pass (SIMULATED: never counts for Gate R) · report " +
      "rehearsal-20261023T100110Z-golden-hour.json. Golden Hour, 2 min on 9 modules: pass.",
  );
  assert.match(rehearsalText({ last: { ...last, verdict: null, simulated: false, file: null } }, clock), /— report pending\./);
});
