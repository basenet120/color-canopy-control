// The /ops counts' wording (web/ops/counts-model.mjs, D62): registered of nine. Run with `node --test tests/*.test.mjs`.
import test from "node:test";
import assert from "node:assert/strict";
import {
  commissionScope,
  headsCounts,
  moduleScope,
  plural,
  positions,
  testWaitNote,
} from "../web/ops/counts-model.mjs";

const PARTIAL = { heads: 270, match: 60, mismatch: 0, unknown: 0, no_device: 210, modules: { total: 9, registered: 2 } };
const FULL = { heads: 270, match: 268, mismatch: 1, unknown: 1, no_device: 0, modules: { total: 9, registered: 9 } };

test("positions come from the heads counts, or null before the first read", () => {
  assert.deepEqual(positions(PARTIAL), { registered: 2, total: 9 });
  assert.equal(positions(null), null);
  assert.equal(positions({ heads: 270 }), null);
});

test("a partial canopy's heads line counts the registered modules' heads and the dark positions apart", () => {
  assert.equal(
    headsCounts(PARTIAL),
    "60 of 60 heads on the 2 registered modules match their look · 0 differ · 0 not read back yet · " +
      "210 heads on 7 positions with no Hubspace device registered. ",
  );
  const one = { ...PARTIAL, match: 29, unknown: 1, no_device: 240, modules: { total: 9, registered: 1 } };
  assert.equal(
    headsCounts(one),
    "29 of 30 heads on the 1 registered module match their look · 0 differ · 1 not read back yet · " +
      "240 heads on 8 positions with no Hubspace device registered. ",
  );
  const eight = { ...PARTIAL, match: 240, no_device: 30, modules: { total: 9, registered: 8 } };
  assert.match(headsCounts(eight), /· 30 heads on 1 position with no Hubspace device registered\. $/);
});

test("the full canopy's heads line is the one it always was", () => {
  assert.equal(headsCounts(FULL), "268 of 270 heads match their look · 1 differ · 1 not read back yet. ");
  // an older server's counts (no `modules`) read as the full canopy too
  const { modules: _m, no_device: _n, ...old } = FULL;
  assert.equal(headsCounts(old), "268 of 270 heads match their look · 1 differ · 1 not read back yet. ");
});

test("the scope of a commission or the 7 am test says registered of nine", () => {
  assert.equal(moduleScope({ registered: 9, total: 9 }), "all 9 modules");
  assert.equal(moduleScope({ registered: 2, total: 9 }), "the 2 registered modules");
  assert.equal(moduleScope({ registered: 1, total: 9 }), "the 1 registered module");
  assert.equal(moduleScope(null), "every registered module");
  assert.equal(commissionScope({ registered: 2, total: 9 }), "2 of 9 now");
  assert.equal(commissionScope({ registered: 9, total: 9 }), "9 of 9 now");
  assert.equal(commissionScope(null), "as registered");
  assert.equal(
    testWaitNote({ registered: 2, total: 9 }),
    "Changing the mode once and waiting up to 10 s for every registered module to read back Ready…",
  );
  assert.equal(
    testWaitNote({ registered: 9, total: 9 }),
    "Changing the mode once and waiting up to 10 s for every module to read back Ready…",
  );
  assert.equal(plural(1), "");
  assert.equal(plural(0), "s");
});
