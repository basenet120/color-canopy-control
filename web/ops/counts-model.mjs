// The /ops counts' pure wording (D62): how many canopy positions have a Hubspace device registered, and the sentences
// that count registered of nine. No DOM; run with `node --test tests/*.test.mjs` (tests/ops-counts.test.mjs).

export function plural(n) {
  return n === 1 ? "" : "s";
}

// {registered, total} from a /api/canopy/heads `counts`, or null before the first read.
export function positions(counts) {
  const mc = counts && counts.modules;
  return mc ? { registered: mc.registered, total: mc.total } : null;
}

// "all 9 modules" on the full canopy, "the 2 registered modules" on a partial one.
export function moduleScope(p) {
  if (!p) return "every registered module";
  if (p.registered === p.total) return `all ${p.total} modules`;
  return `the ${p.registered} registered module${plural(p.registered)}`;
}

// The 7 am test's progress line while it waits.
export function testWaitNote(p) {
  const which = p && p.registered < p.total ? "registered " : "";
  return `Changing the mode once and waiting up to 10 s for every ${which}module to read back Ready…`;
}

// The commission dialog's "(… now)" scope.
export function commissionScope(p) {
  return p ? `${p.registered} of ${p.total} now` : "as registered";
}

// The canopy grid's heads line. On a partial canopy only the registered modules are read back; the other positions
// stay dark and are counted apart.
export function headsCounts(c) {
  const p = positions(c);
  if (p && p.registered < p.total) {
    const dark = p.total - p.registered;
    return (
      `${c.match} of ${c.heads - c.no_device} heads on the ${p.registered} registered module${plural(p.registered)} ` +
      `match their look · ${c.mismatch} differ · ${c.unknown} not read back yet · ` +
      `${c.no_device} heads on ${dark} position${plural(dark)} with no Hubspace device registered. `
    );
  }
  return `${c.match} of ${c.heads} heads match their look · ${c.mismatch} differ · ${c.unknown} not read back yet. `;
}
