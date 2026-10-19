// The /ops "On the iPad" card's words and rows (D76): pure, no DOM. Drawn from GET /api/ops/ipad-scenes
// (conductor/ipad_menu.py menu_json): every scene with its "Show on the iPad" switch, the counts and the notes. A scene
// off the iPad keeps its look: ops can still choose it, the Design Lab still edits it, and it can still be showing.
// The idle scene is always on.

// More visible scenes than this and the iPad shows the review menu (conductor/ipad_menu.py and
// web/kiosk/panel-model.mjs carry the same number; a test checks all three agree).
export const GRID_MAX = 6;

/** "N of M scenes are on the iPad." plus what guests see at that count. */
export function ipadSummary(body) {
  const visible = Number(body && body.visible) || 0;
  const total = Number(body && body.total) || 0;
  const head = `${visible} of ${total} ${total === 1 ? "scene is" : "scenes are"} on the iPad.`;
  if (visible > GRID_MAX) {
    return (
      `${head} More than ${GRID_MAX}: guests see the review menu (one row per scene, its options side by side; ` +
      "the scene list scrolls)."
    );
  }
  return `${head} Guests see the usual grid.`;
}

/** One row per scene, in catalog order: its name, whether it is an option, its switch and the switch's words. */
export function ipadRows(body) {
  const scenes = (body && Array.isArray(body.scenes) && body.scenes) || [];
  return scenes.map((s) => {
    const idle = Boolean(s.idle);
    const on = idle || s.on_ipad !== false;
    const name = String(s.name || s.id);
    return {
      id: String(s.id),
      name,
      option: Boolean(s.option_of),
      on,
      idle,
      text: idle ? "Always on (idle scene)" : on ? "On the iPad" : "Off the iPad",
      label: `Show ${name} on the iPad`,
      disabled: idle,
    };
  });
}

/** What the rows depend on: the card redraws its rows only when this changes. */
export function ipadKey(body) {
  return JSON.stringify(ipadRows(body).map((r) => [r.id, r.name, r.option, r.on, r.idle, r.disabled]));
}
