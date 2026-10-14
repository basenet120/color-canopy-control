// The /ops stepped card's pure wording (Tier.STEPPED, D65 draft; conductor/sequence.py) and the stepped rehearsal's
// line (Gate R; conductor/rehearsal.py). No DOM; run with `node --test tests/*.test.mjs` (tests/ops-stepped.test.mjs).
//
// Inputs: `program`, the engine's CanopyState.program ({t0, step_s, step, ends_at, frames}; live over the "canopy"
// topic, null unless the run is moving), and `gauge`, /api/canopy/state's `stepped` (conductor/routes/ops.py
// stepped_gauge: the account's writes in the last 60 s, the cap, the governor's cool-offs; read on each poll).
// `clock(t)` formats a wall time in seconds. Every number comes from the server: nothing here estimates a pace.

function seconds(v) {
  return `${Number(v).toFixed(1)} s`;
}

// "81 writes/min on account canopy (cap 90)", or "" when the gauge hasn't been read yet.
export function paceText(gauge) {
  if (!gauge || gauge.writes_60s == null || gauge.cap == null) return "";
  const where = gauge.account ? ` on account ${gauge.account}` : "";
  return `${gauge.writes_60s} writes/min${where} (cap ${Number(gauge.cap)})`;
}

/**
 * The card's lines (none = hide the card):
 * - moving: "Stepped look: step 3 of 15 · every 6.0 s · 81 writes/min on account canopy (cap 90) · holds at 2:32:10"
 * - a run whose writes are held (paused, resting, set-up, the guest turned it off): "Stepped look held: …"
 * - each account the throttle governor is cooling off: "Throttle governor: stepped looks on account canopy hold
 *   still until 2:37:10 …"
 */
export function steppedLines(program, gauge, clock) {
  const lines = [];
  const pace = paceText(gauge && gauge.running ? gauge : null);
  if (program && Array.isArray(program.frames) && program.frames.length > 1) {
    const steps = program.frames.length - 1; // the last frame is the still look the run ends on
    const step = Math.min(steps, Math.max(0, Number(program.step)) + 1);
    const parts = [`Stepped look: step ${step} of ${steps}`, `every ${seconds(program.step_s)}`];
    if (pace) parts.push(pace);
    parts.push(`holds at ${clock(program.ends_at)}`);
    lines.push(parts.join(" · "));
  } else if (gauge && gauge.running && gauge.moving === false) {
    // (a gauge that says moving while the live state has no program is older than the state: say nothing)
    const parts = [
      "Stepped look held: nothing is sent while guest input is paused, the canopy rests or is being set up, or a " +
        "guest has it off; the current step goes out when that ends",
    ];
    if (pace) parts.push(pace);
    if (gauge.holds_at != null) parts.push(`holds at ${clock(gauge.holds_at)}`);
    lines.push(parts.join(" · "));
  }
  for (const c of (gauge && gauge.cooloff) || []) {
    lines.push(
      `Throttle governor: Hubspace throttled a stepped run on account ${c.account}, so stepped looks there hold ` +
        `still until ${clock(c.until)} (trips since start: ${Number(gauge.governor_trips) || 0}).`,
    );
  }
  return lines;
}

/**
 * The stepped rehearsal's line under its controls (Gate R; conductor/rehearsal.py). `status` is
 * /api/canopy/state's `rehearsal` ({running, last, max_minutes}); "" when there's nothing to say.
 * - running: "Rehearsing Golden Hour for 10 min on 9 modules · ends at 2:42:10 · guests wait meanwhile"
 * - else the last one: "Last rehearsal: Golden Hour, 10 min — pass (SIMULATED: never counts for Gate R) · report
 *   rehearsal-….json"
 */
export function rehearsalText(status, clock) {
  if (!status) return "";
  const run = status.running;
  if (run) {
    const parts = [`Rehearsing ${run.label} for ${run.minutes} min on ${run.modules} modules`];
    if (run.ends_at != null) parts.push(`ends at ${clock(run.ends_at)}`);
    parts.push("guests wait meanwhile");
    if (run.simulated) parts.push("SIMULATED");
    return parts.join(" · ");
  }
  const last = status.last;
  if (!last) return "";
  const verdict = last.verdict ? `${last.verdict}` : "report pending";
  const sim = last.simulated ? " (SIMULATED: never counts for Gate R)" : "";
  const file = last.file ? ` · report ${String(last.file).split("/").pop()}` : "";
  return `Last rehearsal: ${last.label}, ${last.minutes} min — ${verdict}${sim}${file}. ${last.summary || ""}`.trim();
}
