// The guest panel's pure logic (web/kiosk/panel-model.mjs): run with `node --test tests/*.test.mjs`.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  COPY,
  BRIGHTNESS_SEND_MS,
  IDLE_MS,
  attractDue,
  cardsKey,
  cardState,
  cardTap,
  clampLevel,
  cooldownChip,
  cooldownLeft,
  cooldownToast,
  createPacer,
  debugAllowed,
  fractionForLevel,
  frameScale,
  headerSubtitle,
  headerTint,
  levelFromX,
  nowShowing,
  phaseOf,
  reconcile,
  refusalToast,
  isWaitRefusal,
  waitToastOver,
  sceneLabel,
  snakeLayout,
  stripGradient,
  switchView,
} from "../web/kiosk/panel-model.mjs";

const scene = (id, extra = {}) => ({
  id,
  name: id.toUpperCase(),
  mood: "Settle in",
  description: `${id} description`,
  palette: [
    { name: "Soft ivory", hex: "#F7F2E7" },
    { name: "Sea glass", hex: "#D1EAE3" },
  ],
  frame: Array.from({ length: 30 }, (_, i) => [i, i, i]),
  ...extra,
});
const ready = (extra = {}) => ({ power: true, brightness: 70, scene: "a", phase: "ready", message: "", feature: null, cooldown_remaining_s: 0, ...extra });

test("brightness maps the 10–100 range across the track and never goes below the floor", () => {
  assert.equal(clampLevel(3), 10);
  assert.equal(clampLevel(100.4), 100);
  assert.equal(clampLevel("x"), 10);
  assert.equal(levelFromX(0, 100, 200), 10); // left of the track
  assert.equal(levelFromX(100, 100, 200), 10);
  assert.equal(levelFromX(200, 100, 200), 55);
  assert.equal(levelFromX(300, 100, 200), 100);
  assert.equal(levelFromX(999, 100, 200), 100);
  assert.equal(fractionForLevel(10), 0);
  assert.equal(fractionForLevel(55), 0.5);
  assert.equal(fractionForLevel(100), 1);
});

test("dragging sends at most one brightness per second, and always the final value on release", () => {
  assert.equal(BRIGHTNESS_SEND_MS, 1000);
  const pacer = createPacer();
  const sent = [];
  const at = (level, t) => {
    const v = pacer.move(level, t);
    if (v !== null) sent.push([v, t]);
  };
  // a 2.5 s drag with a move every 16 ms
  for (let t = 0, level = 20; t <= 2500; t += 16, level = Math.min(100, level + 1)) at(level, t);
  assert.deepEqual(
    sent.map(([, t]) => t),
    [0, 1008, 2016],
  );
  const last = pacer.release(90, 2510);
  assert.equal(last, 90);
  // releasing on the value just sent sends nothing more
  const p2 = createPacer();
  assert.equal(p2.move(40, 0), 40);
  assert.equal(p2.release(40, 10), null);
});

test("an optimistic value holds until the server agrees or the hold expires", () => {
  const hold = { value: 40, until: 3000 };
  assert.deepEqual(reconcile(70, hold, 1000), { value: 40, hold });
  assert.deepEqual(reconcile(40, hold, 1000), { value: 40, hold: null });
  assert.deepEqual(reconcile(70, hold, 3000), { value: 70, hold: null });
  assert.deepEqual(reconcile(true, null, 0), { value: true, hold: null });
});

test("the cooldown counts down from the server's remaining seconds, on the local clock", () => {
  assert.equal(cooldownLeft(7.2, 1000, 1000), 8);
  assert.equal(cooldownLeft(7.2, 1000, 3500), 5);
  assert.equal(cooldownLeft(7.2, 1000, 9000), 0);
  assert.equal(cooldownLeft(0, 0, 0), 0);
  assert.equal(cooldownChip(6), "New scene in 6 s");
  assert.equal(cooldownToast(6), "You can choose a new scene in 6 s.");
});

test("the header subtitle prefers the server's message, then the spec's fallbacks", () => {
  assert.deepEqual(headerSubtitle(ready()), { text: COPY.ready, spinner: false });
  assert.deepEqual(headerSubtitle(ready({ phase: "changing" })), { text: COPY.changing, spinner: true });
  assert.deepEqual(headerSubtitle(ready({ phase: "changing", message: "Changing the light…" })), {
    text: "Changing the light…",
    spinner: true,
  });
  assert.deepEqual(headerSubtitle(ready({ power: false })), { text: COPY.off, spinner: false });
  assert.equal(headerSubtitle(ready({ phase: "paused" })).text, COPY.paused);
  assert.equal(phaseOf({ phase: "bogus" }), "ready");
});

test("the switch shows on, off and unknown", () => {
  assert.deepEqual(switchView(true), { checked: true, label: "On", unknown: false });
  assert.deepEqual(switchView(false), { checked: false, label: "Off", unknown: false });
  assert.deepEqual(switchView(null), { checked: false, label: "—", unknown: true });
});

test("cards: selected, pending only for this panel's own request, disabled in cooldown/paused", () => {
  assert.equal(cardState("a", ready()), "selected");
  assert.equal(cardState("b", ready()), "idle");
  assert.equal(cardState("b", ready(), "b"), "pending");
  // a brightness change is "changing" too: the scene showing stays selected, not pending
  assert.equal(cardState("a", ready({ phase: "changing" })), "selected");
  assert.equal(cardState("b", ready({ phase: "cooldown" })), "disabled");
  assert.equal(cardState("a", ready({ phase: "cooldown" })), "selected");
  assert.equal(cardState("a", ready({ phase: "paused" })), "disabled");
  assert.equal(cardState("a", ready({ power: false })), "idle");
});

test("a tap on a card sends, or explains why not — never silently", () => {
  assert.deepEqual(cardTap("b", ready()), { send: true });
  assert.deepEqual(cardTap("b", ready({ phase: "changing" })), { send: true }); // the server decides
  assert.deepEqual(cardTap("b", ready(), { pendingId: "c" }), { toast: COPY.busy });
  assert.deepEqual(cardTap("b", ready({ phase: "cooldown" }), { cooldownS: 4 }), { toast: cooldownToast(4) });
  assert.deepEqual(cardTap("b", ready({ phase: "cooldown" }), { cooldownS: 0 }), { send: true });
  assert.deepEqual(cardTap("b", ready({ phase: "paused", message: "Back soon." })), { toast: "Back soon." });
  assert.deepEqual(cardTap("b", ready({ phase: "paused" })), { toast: COPY.paused });
  assert.deepEqual(cardTap("b", ready({ power: false })), { send: true }); // a scene turns the lights on
});

test("refusals show the server's calm detail; anything else reads as reconnecting", () => {
  const err = (status, body) => ({ status, body });
  assert.deepEqual(refusalToast(err(429, { detail: "Enjoy this one.", retry_after_s: 6.2 })), {
    text: "Enjoy this one.",
    retryAfterS: 6.2,
  });
  assert.deepEqual(refusalToast(err(409, { detail: "Someone is using the module page right now." })), {
    text: "Someone is using the module page right now.",
    retryAfterS: 0,
  });
  for (const e of [err(0, null), err(500, { detail: "Traceback" }), err(422, { detail: "Send {level}" }), err(403, { detail: "x" })]) {
    assert.deepEqual(refusalToast(e), { text: COPY.reconnecting, retryAfterS: 0 });
  }
});

test("a try-on's waiting toast is dropped by the first read after it that says nothing waits", () => {
  const TRYING_ON = "Staff are trying a new look. It will be back shortly.";
  const wait = { status: 503, body: { detail: TRYING_ON, retry_after_s: 60 } };
  assert.equal(isWaitRefusal(wait), true);
  assert.equal(isWaitRefusal({ status: 503, body: { detail: TRYING_ON, retry_after_s: null } }), false);
  assert.equal(isWaitRefusal({ status: 429, body: { detail: "Enjoy this one.", retry_after_s: 6 } }), false); // cooldown
  assert.equal(isWaitRefusal({ status: 0, body: null }), false);
  const shown = { at: 1000 };
  // during the try-on a fresh read is "ready" with an empty message: only `waiting` tells the two apart
  assert.equal(waitToastOver(shown, ready({ waiting: "try-on" }), 3000), false);
  assert.equal(waitToastOver(shown, ready({ waiting: "testing" }), 3000), false);
  assert.equal(waitToastOver(shown, ready({ waiting: null }), 3000), true); // Back to approved, the bound, ops
  assert.equal(waitToastOver(shown, ready({ phase: "changing", waiting: null }), 3000), true); // a guest's scene
  assert.equal(waitToastOver(shown, ready({ waiting: null }), 999), false); // a read begun before the refusal
  assert.equal(waitToastOver(shown, ready(), 3000), false); // a state without `waiting` (an older server)
  assert.equal(waitToastOver(shown, ready({ phase: "resting", waiting: null }), 3000), false); // being set up
  assert.equal(waitToastOver(null, ready({ waiting: null }), 3000), false); // no waiting toast up
});

test("during a try-on the guest sees the server's trying-on sentence with its retry, never 'unavailable'", () => {
  // PLAN §6.5: routes/kiosk.py refusal_for_state answers 503 TRYING_ON + Retry-After while the engine's trial runs
  const TRYING_ON = "Staff are trying a new look. It will be back shortly.";
  for (const status of [503, 409]) {
    assert.deepEqual(refusalToast({ status, body: { detail: TRYING_ON, retry_after_s: 142.4 } }), { text: TRYING_ON, retryAfterS: 142.4 });
  }
  assert.deepEqual(refusalToast({ status: 503, body: { detail: TRYING_ON, retry_after_s: null } }), { text: TRYING_ON, retryAfterS: 0 });
});

test("cardsKey changes when a scene's contents change, not only its ids", () => {
  const base = [scene("a", { solid: 0 }), scene("b", { solid: 1 })];
  const key = cardsKey(base);
  assert.equal(cardsKey(JSON.parse(JSON.stringify(base))), key, "the same catalog keeps its cards");
  const changed = [
    ["name", (s) => ({ ...s, name: "Golden Hour" })],
    ["mood", (s) => ({ ...s, mood: "Warm and slow" })],
    ["solid", (s) => ({ ...s, solid: 1 })],
    ["a hex", (s) => ({ ...s, palette: [{ ...s.palette[0], hex: "#FF8FBF" }, s.palette[1]] })],
    ["a colour name", (s) => ({ ...s, palette: [{ ...s.palette[0], name: "Shell" }, s.palette[1]] })],
    ["the palette's order", (s) => ({ ...s, palette: [...s.palette].reverse() })],
    ["a colour added", (s) => ({ ...s, palette: [...s.palette, { name: "Marsh", hex: "#6E8B5E" }] })],
    ["the strip's frame", (s) => ({ ...s, frame: s.frame.map((c, i) => (i === 3 ? [9, 9, 9] : c)) })],
  ];
  for (const [what, fn] of changed) assert.notEqual(cardsKey([fn(base[0]), base[1]]), key, what);
  assert.notEqual(cardsKey([base[1], base[0]]), key, "the order of the cards");
  assert.notEqual(cardsKey(base.slice(0, 1)), key);
  assert.equal(cardsKey([]), "[]");
  assert.equal(cardsKey(undefined), "[]");
  // the description isn't drawn on a card, so it doesn't rebuild them
  assert.equal(cardsKey([{ ...base[0], description: "new words" }, base[1]]), key);
});

test("kiosk.js builds its cards on cardsKey, never on the ids alone", () => {
  const js = readFileSync(new URL("../web/kiosk/kiosk.js", import.meta.url), "utf8").replace(/(^|\s)\/\/.*$/gm, "");
  assert.match(js, /const key = cardsKey\(scenes\);/);
  assert.doesNotMatch(js, /scenes\.map\(\(s\) => s\.id\)\.join/);
});

test("now showing: the scene, or calm copy with grey lights when off or unknown", () => {
  const scenes = [scene("a"), scene("b")];
  const shown = nowShowing(scenes, ready());
  assert.equal(shown.name, "A");
  assert.equal(shown.frame, scenes[0].frame);
  const off = nowShowing(scenes, ready({ power: false }));
  assert.equal(off.name, COPY.lightsOff);
  assert.ok(off.frame.every((c) => c.join() === "211,210,216"));
  const none = nowShowing(scenes, ready({ scene: null }));
  assert.equal(none.name, COPY.noScene);
  assert.equal(none.description, COPY.noSceneHelp);
});

test("now showing draws the one colour the strands took when the server says the look is solid", () => {
  const scenes = [scene("a", { solid: "#F4A6A0" })];
  const solid = nowShowing(scenes, ready({ look: "solid" }));
  assert.equal(solid.name, "A");
  assert.ok(solid.frame.length === 30 && solid.frame.every((c) => c.join() === "244,166,160"));
  assert.equal(nowShowing(scenes, ready({ look: "gradient" })).frame, scenes[0].frame);
  assert.equal(nowShowing(scenes, ready({ look: null })).frame, scenes[0].frame); // engine target: the scene itself
});

test("now showing draws the canopy's picture when the server sends state.lit, the serpentine when it is null", () => {
  const scenes = [scene("a"), scene("b")];
  const modules = ["r0c0", "r0c1"].map((pos) => ({ pos, state: "lit", color: "#ABCDEF", colors: null }));
  const lit = { level: 70, modules, motion: null };
  const shown = nowShowing(scenes, ready({ lit }));
  assert.equal(shown.name, "A");
  assert.equal(shown.description, "a description");
  assert.equal(shown.lit, lit);
  assert.equal(shown.frame, undefined); // no serpentine claim next to the real picture
  assert.equal(shown.scene, scenes[0]);
  // the workshop target, an older server, or no look yet: the serpentine of the scene (or grey)
  for (const st of [ready({ lit: null }), ready()]) {
    const fallback = nowShowing(scenes, st);
    assert.equal(fallback.lit, undefined);
    assert.equal(fallback.frame, scenes[0].frame);
  }
  const none = nowShowing(scenes, ready({ scene: null, lit: null }));
  assert.equal(none.name, COPY.noScene);
  assert.ok(none.frame.every((c) => c.join() === "211,210,216"));
  const malformed = nowShowing(scenes, ready({ lit: { level: 70 } })); // no modules: not a picture
  assert.equal(malformed.frame, scenes[0].frame);
});

test("now showing with the canopy off: the off copy and the server's (all off) picture", () => {
  const scenes = [scene("a")];
  const lit = { level: null, modules: [{ pos: "r0c0", state: "off", color: null, colors: null }], motion: null };
  const off = nowShowing(scenes, ready({ power: false, lit }));
  assert.equal(off.name, COPY.lightsOff);
  assert.equal(off.description, COPY.noSceneHelp);
  assert.equal(off.lit, lit);
  const noScene = nowShowing(scenes, ready({ power: false, scene: null, lit }));
  assert.equal(noScene.name, COPY.lightsOff);
  assert.equal(noScene.scene, null);
});

test("the serpentine runs L→R, R→L, L→R over 3 × 10", () => {
  const tiles = snakeLayout();
  assert.equal(tiles.length, 30);
  assert.deepEqual(tiles[0], { i: 0, row: 0, col: 0 });
  assert.deepEqual(tiles[9], { i: 9, row: 0, col: 9 });
  assert.deepEqual(tiles[10], { i: 10, row: 1, col: 9 });
  assert.deepEqual(tiles[19], { i: 19, row: 1, col: 0 });
  assert.deepEqual(tiles[20], { i: 20, row: 2, col: 0 });
  assert.deepEqual(tiles[29], { i: 29, row: 2, col: 9 });
});

test("labels, strips and the header tint come from the scene", () => {
  const s = scene("a");
  assert.equal(sceneLabel(s), "A. Settle in. Colors: Soft ivory, Sea glass.");
  const g = stripGradient(s.frame);
  assert.ok(g.startsWith("linear-gradient(90deg, rgb(0, 0, 0) 0.000% 3.333%"));
  assert.equal(g.match(/rgb\(/g).length, 30);
  assert.deepEqual(headerTint(s, true), ["#F7F2E7", "#D1EAE3"]);
  assert.equal(headerTint(s, false), null);
  assert.equal(headerTint(null, true), null);
});

test("attract shows after ~2 minutes idle, only while the lights are steady", () => {
  assert.equal(IDLE_MS, 120000);
  assert.equal(attractDue(0, IDLE_MS - 1, "ready"), false);
  assert.equal(attractDue(0, IDLE_MS, "ready"), true);
  assert.equal(attractDue(0, IDLE_MS, "cooldown"), true);
  for (const phase of ["changing", "paused", "resting"]) assert.equal(attractDue(0, IDLE_MS * 2, phase), false);
});

test("debug is ops-only and needs ?debug=1; the frame preview fits the window", () => {
  assert.equal(debugAllowed("?debug=1", "ops"), true);
  assert.equal(debugAllowed("?debug=1", "kiosk"), false);
  assert.equal(debugAllowed("", "ops"), false);
  assert.equal(debugAllowed("?debug=0", "ops"), false);
  assert.equal(frameScale(1180, 820, 4000, 4000), 1);
  const s = frameScale(1180, 820, 1280, 800);
  assert.ok(s > 0.5 && s < 1);
  assert.ok((820 + 36) * s <= 800 - 64 + 1e-9);
});

test("the page writes no Hubspace feature claim of its own", () => {
  // The feature sentence is always state.feature; client strings never name Hubspace (index.html holds the
  // neutral kicker/fallback). Comments may.
  for (const name of ["kiosk.js", "panel-model.mjs", "canopy-preview.mjs"]) {
    const js = readFileSync(new URL(`../web/kiosk/${name}`, import.meta.url), "utf8").replace(/(^|\s)\/\/.*$/gm, "");
    assert.doesNotMatch(js, /['"`][^'"`\n]*Hubspace[^'"`\n]*['"`]/, name);
  }
});
