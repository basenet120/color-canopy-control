// Behaviour checks for web/kiosk-resting/index.html (the kiosk's local resting page), run by tests/test_deploy.py
// under Node (skipped when Node is absent). Runs the page's inline script with fake fetch, timers and location;
// nothing touches the network.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const html = readFileSync(process.argv[2], "utf8");
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
assert.equal(scripts.length, 1, "one inline script");
const source = scripts[0];

async function run(search, { upAfter = 0 } = {}) {
  const fetched = [];
  const replaced = [];
  const timers = [];
  let calls = 0;
  const context = {
    URL,
    URLSearchParams,
    AbortController,
    location: { search, replace: (url) => replaced.push(url) },
    setTimeout: (fn, ms) => timers.push({ fn, ms }) && timers.length,
    clearTimeout: () => {},
    fetch: async (url, options) => {
      fetched.push({ url, options });
      calls += 1;
      if (calls <= upAfter) throw new TypeError("Failed to fetch"); // nothing listening yet
      return { type: "opaque", status: 0 };
    },
  };
  vm.runInNewContext(source, context);
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  await settle();
  // Fire the poll timers (not the fetch timeouts) until the page moves on or gives up polling.
  for (let i = 0; i < 20 && replaced.length === 0; i += 1) {
    const poll = timers.filter((t) => t.ms === 2000).shift();
    if (!poll) break;
    timers.splice(timers.indexOf(poll), 1);
    poll.fn();
    await settle();
  }
  return { fetched, replaced, timers };
}

// The Conductor is down for three checks, then answers: the page stays put, then opens the canopy page.
{
  const { fetched, replaced } = await run("?next=http://localhost:8000/kiosk/", { upAfter: 3 });
  assert.equal(fetched.length, 4);
  for (const { url, options } of fetched) {
    assert.equal(url, "http://localhost:8000/healthz");
    assert.equal(options.mode, "no-cors");
    assert.equal(options.cache, "no-store");
  }
  assert.deepEqual(replaced, ["http://localhost:8000/kiosk/"]);
}

// While enrolling, `next` is a setup link on the box: it is followed as it is.
{
  const link = "http://localhost:8000/enroll/abcdefghijklmnopqrstuvwxyz";
  const { replaced } = await run(`?next=${link}`);
  assert.deepEqual(replaced, [link]);
}

// Anything that isn't the Conductor on this box falls back to the canopy page; no `next` at all too.
for (const search of ["?next=http://example.invalid/", "?next=javascript:alert(1)", "?next=file:///etc/passwd", ""]) {
  const { fetched, replaced } = await run(search);
  assert.equal(fetched[0].url, "http://localhost:8000/healthz", search);
  assert.deepEqual(replaced, ["http://localhost:8000/kiosk/"], search);
}

// Never answering: it keeps polling every 2 s and never navigates (so never to an error page).
{
  const { fetched, replaced } = await run("?next=http://localhost:8000/kiosk/", { upAfter: 1e9 });
  assert.ok(fetched.length >= 10);
  assert.deepEqual(replaced, []);
}

console.log("resting ok");
