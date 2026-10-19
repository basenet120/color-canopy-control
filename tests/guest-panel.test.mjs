// The guest panel's pure logic (web/kiosk/panel-model.mjs): run with `node --test tests/*.test.mjs`.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  COPY,
  GRID_MAX,
  IDLE_MS,
  attractScenes,
  cardLayout,
  thumbDots,
  attractDue,
  brightnessToSend,
  cardsKey,
  cardState,
  cardTap,
  clampLevel,
  cooldownChip,
  cooldownLeft,
  cooldownToast,
  debugAllowed,
  fractionForLevel,
  frameScale,
  headerSubtitle,
  headerTint,
  levelFromX,
  nowShowing,
  pendingValue,
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

test("the slider sends once, on release, and only when it changes something", () => {
  const on70 = { power: true, brightness: 70, next: null };
  assert.equal(brightnessToSend(40, on70), 40);
  assert.equal(brightnessToSend(70, on70), null); // already so: nothing to send, no cooldown started
  // moving the slider while the lights are off turns them on (D60): it always sends
  assert.equal(brightnessToSend(70, { power: false, brightness: 70 }), 70);
  // the guest's own queued change counts as the current level (it is on its way)
  const queued = { power: true, brightness: 70, next: { brightness: 40 } };
  assert.equal(brightnessToSend(40, queued), null);
  assert.equal(brightnessToSend(70, queued), 70);
  // a queued "off" means the lights are going off: a release sends, so they come back on at that level
  assert.equal(brightnessToSend(70, { power: true, brightness: 70, next: { power: false } }), 70);
  assert.equal(brightnessToSend(55, null), 55);
});

test("a queued brightness or power is read from state.next only", () => {
  assert.equal(pendingValue({ next: { brightness: 40 } }, "brightness"), 40);
  assert.equal(pendingValue({ next: { power: false } }, "power"), false);
  assert.equal(pendingValue({ next: { power: false } }, "brightness"), undefined);
  assert.equal(pendingValue({ next: null }, "power"), undefined);
  assert.equal(pendingValue({}, "power"), undefined); // an older server never sends `next`
  assert.equal(pendingValue(null, "power"), undefined);
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

test("cardsKey follows option_of, group and the thumbnail's sig", () => {
  // non-hex sig literals on purpose (.gitleaksignore): the key only compares them
  const base = [scene("a", { option_of: null, group: "a", thumb: null }), scene("b", { option_of: null, group: "b", thumb: null })];
  const key = cardsKey(base);
  const one = (extra) => cardsKey([{ ...base[0], ...extra }, base[1]]);
  assert.notEqual(one({ option_of: "b" }), key, "option_of null → an id");
  assert.notEqual(one({ group: "b" }), key, "the group");
  const thumb = (sig, colour) => ({ sig, basis: "modules", modules: [{ pos: "r0c0", color: colour, colors: null }] });
  assert.notEqual(one({ thumb: thumb("thumb-a", "#111111") }), key, "thumb null → a thumb");
  assert.notEqual(one({ thumb: thumb("thumb-a", "#111111") }), one({ thumb: thumb("thumb-b", "#111111") }), "a new sig");
  // the sig stands for the colours: the same sig with other arrays is the same key (the arrays never go in)
  assert.equal(one({ thumb: thumb("thumb-a", "#111111") }), one({ thumb: thumb("thumb-a", "#222222") }));
  assert.equal(one({ description: "other words" }), key);
});

const review18 = () =>
  ["a", "b", "c", "d", "e", "f"].flatMap((id) => [
    scene(id, { option_of: null, group: id }),
    scene(`${id}-2`, { option_of: id, group: id }),
    scene(`${id}-3`, { option_of: id, group: id }),
  ]);

test("cardLayout: six or fewer keep catalog order, more than six give one row per scene group", () => {
  assert.equal(GRID_MAX, 6);
  const six = [scene("a"), scene("b-2", { option_of: "b", group: "b" }), scene("c"), scene("b"), scene("d"), scene("e")];
  const flat = cardLayout(six);
  assert.equal(flat.review, false);
  assert.deepEqual(flat.cards.map((c) => c.scene.id), six.map((s) => s.id));
  assert.ok(flat.cards.every((c) => c.rowStart === false));
  const all = review18();
  const grouped = cardLayout(all);
  assert.equal(grouped.review, true);
  assert.deepEqual(grouped.cards.map((c) => c.scene.id), all.map((s) => s.id));
  assert.deepEqual(grouped.cards.flatMap((c, i) => (c.rowStart ? [i] : [])), [0, 3, 6, 9, 12, 15]);
  assert.deepEqual(cardLayout([]), { review: false, cards: [] });
  assert.deepEqual(cardLayout(undefined), { review: false, cards: [] });
});

test("cardLayout: a hidden base still forms its row, options listed before their base, and groups of more than three", () => {
  const without = review18().filter((s) => s.id !== "a");
  const rows = cardLayout(without);
  assert.equal(rows.cards.length, 17);
  assert.equal(rows.cards[0].scene.id, "a-2");
  assert.equal(rows.cards[0].rowStart, true);
  assert.equal(rows.cards[1].rowStart, false);
  // b-2 listed before b: one group, at b-2's position, members in catalog order
  const early = [scene("b-2", { option_of: "b", group: "b" }), scene("a"), scene("b"), scene("c"), scene("d"), scene("e"), scene("f")];
  const out = cardLayout(early);
  assert.deepEqual(out.cards.map((c) => c.scene.id), ["b-2", "b", "a", "c", "d", "e", "f"]);
  assert.deepEqual(out.cards.map((c) => c.rowStart), [true, false, true, true, true, true, true]);
  // a five-member group: one row start, five cards (the grid wraps them)
  const five = [scene("g"), ...[2, 3, 4, 5].map((n) => scene(`g-${n}`, { option_of: "g", group: "g" })), scene("h"), scene("i")];
  const big = cardLayout(five);
  assert.equal(big.cards.filter((c) => c.scene.group === "g" || c.scene.id === "g").length, 5);
  assert.deepEqual(big.cards.map((c) => c.rowStart), [true, false, false, false, false, true, true]);
  // with no group or option_of, the id is the group; option_of alone groups too
  const bare = [scene("a"), scene("a-2", { option_of: "a" }), scene("b"), scene("c"), scene("d"), scene("e"), scene("f")];
  assert.deepEqual(cardLayout(bare).cards.map((c) => c.rowStart), [true, false, true, true, true, true, true]);
});

test("attract cycles one scene per group in the review menu, every scene otherwise", () => {
  assert.deepEqual(attractScenes(review18()).map((s) => s.id), ["a", "b", "c", "d", "e", "f"]);
  const hidden = review18().filter((s) => s.id !== "c");
  assert.deepEqual(attractScenes(hidden).map((s) => s.id), ["a", "b", "c-2", "d", "e", "f"]);
  const six = review18().filter((s) => !s.option_of);
  assert.deepEqual(attractScenes(six), six);
  assert.deepEqual(attractScenes(undefined), []);
});

test("thumbDots: each head the design's colour, no colour where none is known", () => {
  const dots = ["r0c0", "r0c1"].flatMap((pos) => [0, 1, 2].map((k) => ({ pos, k, cx: 10 * k, cy: pos === "r0c0" ? 1 : 2, r: 1.5 })));
  const perHead = { sig: "thumb-a", basis: "heads", modules: [{ pos: "r0c0", color: null, colors: ["#aa0000", "#00bb00", "#0000cc"] }, { pos: "r0c1", color: "#123abc", colors: null }] };
  const out = thumbDots(dots, perHead);
  assert.deepEqual(out.map((d) => d.fill), ["#AA0000", "#00BB00", "#0000CC", "#123ABC", "#123ABC", "#123ABC"]);
  assert.deepEqual(out[1], { cx: 10, cy: 1, r: 1.5, fill: "#00BB00" }); // cx, cy and r pass through
  assert.equal(thumbDots(dots, perHead, { r: 0.7 })[0].r, 0.7); // the card's thumbnail radius
  const gaps = { sig: "thumb-b", basis: "heads", modules: [{ pos: "r0c0", color: null, colors: ["#AA0000", null, "#XYZ"] }] };
  assert.deepEqual(thumbDots(dots, gaps).map((d) => [d.cx, d.cy, d.fill]), [[0, 1, "#AA0000"]]); // r0c1 has no module
  const flatBad = { sig: "thumb-c", basis: "modules", modules: [{ pos: "r0c0", color: "#XYZ", colors: null }, { pos: "r0c1", color: null, colors: null }] };
  assert.deepEqual(thumbDots(dots, flatBad), []);
  assert.deepEqual(thumbDots(dots, null), []);
  assert.deepEqual(thumbDots(undefined, perHead), []);
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

test("now showing names a hidden scene from panel.showing", () => {
  const hidden = { id: "h", name: "H #2", description: "hd", frame: scene("h").frame };
  const shown = nowShowing([scene("a")], ready({ scene: "h" }), hidden);
  assert.equal(shown.name, "H #2");
  assert.equal(shown.description, "hd");
  assert.equal(shown.scene, hidden);
  const without = nowShowing([scene("a")], ready({ scene: "h" }));
  assert.equal(without.name, COPY.noScene);
  assert.equal(nowShowing([scene("a")], ready(), hidden).name, "A"); // `showing` never stands in for the scene shown
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
  // an older server, or no look yet: the serpentine of the scene (or grey)
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

/** The body of `function name(` in `js` (brace-matched; strings here hold no braces that matter). */
function fnBody(js, name) {
  const start = js.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `kiosk.js has no function ${name}`);
  const open = js.indexOf("{", js.indexOf(")", start));
  let depth = 0;
  for (let i = open; i < js.length; i += 1) {
    if (js[i] === "{") depth += 1;
    else if (js[i] === "}" && (depth -= 1) === 0) return js.slice(open, i + 1);
  }
  throw new Error(`unbalanced ${name}`);
}

test("kiosk.js draws thumbnails on a rebuild or resize, never on an animation frame", () => {
  const js = readFileSync(new URL("../web/kiosk/kiosk.js", import.meta.url), "utf8").replace(/(^|\s)\/\/.*$/gm, "");
  for (const name of ["paintCanopy", "tickCanopy"]) {
    const body = fnBody(js, name);
    assert.doesNotMatch(body, /drawThumbs|paintThumb/, name);
  }
  const build = fnBody(js, "buildCards");
  for (const needle of [
    "builtFor = key",
    "builtCanopy = Boolean(canopy)",
    "const keep = grid.scrollTop",
    "grid.scrollTop = keep",
    "dataset.review",
    "attractScenes(",
    "cardLayout(",
  ]) {
    assert.ok(build.includes(needle), needle);
  }
  assert.ok(fnBody(js, "attractStep").includes("attractScenes("));
  assert.ok(fnBody(js, "showAttract").includes("scrollTop = 0"));
  assert.ok(fnBody(js, "wireResize").includes("force: true"));
  assert.ok(fnBody(js, "paintThumb").includes("levelOpacity(scene.brightness_draft"));
});
