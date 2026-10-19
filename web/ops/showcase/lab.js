// The Design Lab panel inside the Studio (docs/design-lab/PLAN.md §5): edit a scene's palette, layout, brightness and
// client copy; see the draft through the real compiler (POST /api/lab/preview) at each tier and "today"; save named
// versions, compare any two side by side, present full screen, and export a reviewed git patch.
//
// lab.js owns everything inside <section id="lab">; studio.js and demo-model.mjs are not edited. The stage paints ONLY
// through lab-model's viewPainter (the server's design via demo-model's designPaint, or today's lit modules via the
// guest panel's paintFor); it never uses the Studio's handoff palette. Screen only, except the Try-on panel (Slice B,
// PLAN §6.4): one Apply button sends the working draft to POST /api/lab/trial/start, which the SERVER confirms, gates
// and hands to the canopy engine (module level, bounded, one burst). Nothing here writes to a light any other way.
// The Motion preview (Slice C, PLAN §7) is screen only too: it plays the server's compiled stepped steps at the
// Conductor's own pace and lands the server's ripple rings at a chosen stagger; it never touches
// motion.stepped.live_enabled or kiosk.ripple_stagger_s (the Lab shows where each stands and has no control for either).
// The live show (docs/live-lab/PLAN.md §3, D68): the show bar says what the show runs (GET /api/lab/live: the approved
// looks or a published version), each scene says whether its live look is approved or published and whether the
// working draft is that look; "Publish to the show" / "Save & publish" / Undo / Revert POST /api/lab/publish, and the
// SERVER answers with its confirm sentence and cost first, gates the publish and installs it. Nothing here writes to
// a light any other way, and no publish state is kept in this browser (only working drafts and UI prefs).

import { api as baseApi, connectLive, copyJSON } from "/static/app.js";
import { buildCanopy, paintCanopy } from "./demo-model.mjs?v=show-design-3";
import * as M from "./lab-model.mjs?v=lab-11";

const API = "/api/lab";
const UI_KEY = "lab-ui"; // remembered scene / view / angle / the publisher's name (not a draft key: staleDraftKeys never matches it)
const CACHE_MAX = 80;

// ---- small DOM helpers ------------------------------------------------------------------------------------------------
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, String(v));
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) el.append(c instanceof Node ? c : String(c));
  return el;
}
const $ = (sel, root = document) => root.querySelector(sel);
function storage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}
function store(fn) {
  try {
    const s = storage();
    return s ? fn(s) : null;
  } catch {
    return null;
  }
}

// ---- state ------------------------------------------------------------------------------------------------------------
const L = {
  built: false,
  booting: null,
  catalog: null,
  state: null,
  versions: [],
  selected: null, // the version the working set was opened from or saved as (null = approved)
  approved: {}, // scene → the approved draft (catalog)
  base: {}, // scene → the last saved or opened draft (the unsaved-changes guard compares with it)
  working: {}, // scene → the working draft
  undo: {},
  lastUndoAt: 0,
  scene: null,
  view: "canvas",
  angle: "perspective",
  ab: { on: false, a: M.APPROVED, b: M.WORKING },
  cache: new Map(), // previewKey → PreviewJSON
  pending: new Map(), // previewKey → Promise
  lastGood: {}, // scene → the last PreviewJSON of a working draft that answered 200
  problems: {}, // scene → the last 422's problems for the working draft
  failedKey: {}, // scene → the preview key of a working draft the server refused or couldn't be asked about
  versionDrafts: {}, // version id → {scene: draft}
  inflight: null,
  lastSent: null,
  timer: null,
  alerts: new Map(), // source → sentence (one #lab-alert region)
  notice: "",
  stages: {}, // side → {key, view (buildCanopy's), el}
  present: null,
  saving: false, // a save is on its way: Save and Export wait (a double tap never saves twice)
  configChanged: false, // the Conductor answered from another approved config than the one this page loaded
  // the try-on (GET /api/lab/trial; never kept in the browser: a reload reads the server's)
  tryon: {
    api: "loading", // loading | ready | missing | no-engine | unreachable | error
    error: "",
    trial: null, // TrialJSON | null
    readAt: 0, // Date.now() when `trial.left_s` was read
    busy: false, // a start or stop is on its way
    lastTapAt: null,
    acceptedAt: null, // when the last apply was accepted (or refused with a retry): the gap counts from here
    nextApplyInS: 0,
    confirm: null, // {scene, sentence, cost}: the server's confirm sentence, waiting for "Try it on"
    confirmed: {}, // scene → the sentence last confirmed (a re-apply of the running scene sends it straight back)
    applied: {}, // scene → the draft last applied
    pollTimer: null,
    nudgeTimer: null,
    clock: null,
    ending: false,
    live: null,
    watching: false,
  },
  // the motion preview (Slice C, PLAN §7): screen only, never sent anywhere
  motion: {
    state: "idle", // idle | playing | paused | done
    pace: "nine", // nine | today (the server's pace.nine / pace.today)
    fast: false, // the clearly labelled fast preview
    elapsed: 0, // the lights' seconds into the run when it last paused (or started)
    since: 0, // performance.now() when it last started playing
    clock: null,
    key: "",
    ripple: { stagger: M.RIPPLE_DEFAULT_S, since: null }, // since: performance.now() when the ripple started
  },
  // the live show (GET /api/lab/live; never kept in the browser: every read is the server's)
  live: null, // LiveJSON (PLAN §2.11) | null
  liveKey: "", // the last read, as compared between polls
  liveApi: "loading", // loading | ready | missing | unreachable | error
  liveReadAt: 0, // Date.now() when `live` was read (queued repaints count down from it)
  liveLooks: {}, // scene → the look the show runs (catalog live_look; approved on a server without publishing)
  livePoll: null,
  liveWatching: false,
  historyOpen: false,
  who: "", // the publisher's name (a UI pref, sent as `who`)
  publish: {
    busy: false, // a publish request is on its way: every Publish button waits
    dialog: null, // {version, kind, name, expected, view (publishDialog), conflict, changed, waitS, waitAt}
    clock: null,
    marks: {}, // scene → the server's sentence that refused a publish for it (a `field` 409), until the next publish
    stale: {}, // version id → the 422/stale sentence: the version doesn't apply to the show's config
  },
  toastTimer: null,
};

/** Every Lab call: the shared api() plus a look at which approved config the Conductor answered from. */
function api(path, opts = {}) {
  return baseApi(path, { ...opts, onResponse: checkConfig });
}

function checkConfig(response) {
  const sha = response && response.headers ? response.headers.get("X-Lab-Config-Sha") : null;
  if (L.configChanged || !L.catalog || !M.configChanged(L.catalog.config_sha, sha)) return;
  L.configChanged = true; // stop keeping drafts under the old config's keys: the next load would clear them
  setAlert("config", M.CONFIG_CHANGED);
}
const order = () => (L.catalog ? L.catalog.scenes.map((s) => s.id) : []);
const sceneOf = (id) => (L.catalog ? L.catalog.scenes.find((s) => s.id === id) : null) || null;
const sceneName = (id) => (L.working[id] && L.working[id].name) || (sceneOf(id) && sceneOf(id).name) || id;

// ---- the alert region ---------------------------------------------------------------------------------------------------
function setAlert(source, text) {
  if (text) L.alerts.set(source, text);
  else L.alerts.delete(source);
  const el = $("#lab-alert");
  if (!el) return;
  const lines = [...L.alerts.values()];
  el.replaceChildren(...lines.map((t) => h("p", { text: t })));
  el.hidden = !lines.length;
}
function setNotice(text) {
  L.notice = text || "";
  const el = $("#lab-notice");
  if (el) el.textContent = L.notice;
}

// ---- previews -----------------------------------------------------------------------------------------------------------
function remember(key, preview) {
  if (!preview.failed) for (const [k, v] of L.cache) if (v && v.failed) L.cache.delete(k); // the server answers again
  L.cache.set(key, preview);
  if (L.cache.size > CACHE_MAX) L.cache.delete(L.cache.keys().next().value);
}

/** A preview of any draft (cached, deduplicated): for the A/B sides and Present. Never throws: {error} instead. */
function requestPreview(draft) {
  if (!draft) return Promise.resolve({ error: "Loading…" });
  const key = M.previewKey(draft);
  if (L.cache.has(key)) return Promise.resolve(L.cache.get(key));
  if (L.pending.has(key)) return L.pending.get(key);
  const body = { scene_id: draft.scene_id, draft: M.normaliseDraft(draft) };
  const p = api(`${API}/preview`, { method: "POST", body })
    .then((preview) => {
      remember(key, preview);
      return preview;
    })
    .catch((err) => {
      // remembered too (until the next good answer clears them), so a failing preview is never re-asked in a loop
      const fallback = catalogPreview(draft);
      const problems = err && err.body && err.body.problems;
      const why = problems && problems.length ? problems.map((x) => x.message).join(" ") : M.alertFor(err) || String((err && err.message) || err);
      const answer = fallback || { error: why };
      remember(key, { ...answer, failed: true });
      return answer;
    })
    .finally(() => L.pending.delete(key));
  L.pending.set(key, p);
  return p;
}

/** The approved design as the catalog has it (the server's own), for a draft equal to approved while no preview has
 * answered: an instant, truthful first paint. Null for anything else. */
function catalogPreview(draft) {
  const scene = draft && sceneOf(draft.scene_id);
  if (!scene || !scene.design || !M.draftEquals(scene.approved, draft)) return null;
  const tiers = [];
  if (scene.approved && Array.isArray(scene.approved.palette)) {
    const solid = scene.approved.palette[scene.approved.solid];
    if (solid) {
      const hex = String(solid.hex);
      tiers.push({ tier: "strand", label: "Strand (one colour)", draws: "solid", solid: [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)), feature: (scene.copy || {}).strand || "" });
    }
  }
  return { scene_id: scene.id, label: scene.name, approved: true, design: scene.design, brightness: scene.approved.brightness, tiers, fromCatalog: true };
}

/** The working draft's preview: `fresh` when the server answered for exactly this draft; otherwise the last good one,
 * with `failed` when the server refused or couldn't be reached for this draft (vs. still on its way). */
function workingPreview(sceneId = L.scene) {
  const draft = L.working[sceneId];
  if (!draft) return { preview: null, fresh: false, failed: false };
  const key = M.previewKey(draft);
  const cached = L.cache.get(key);
  if (cached && !cached.failed) return { preview: cached, fresh: true, failed: false };
  const failed = L.failedKey[sceneId] === key;
  const last = L.lastGood[sceneId];
  if (last) return { preview: last, fresh: false, failed };
  return { preview: catalogPreview(draft), fresh: false, failed };
}

/** A try-on started, ended or changed tier: every cached preview's "today" (what the lights show) is out of date, so
 * drop the cache and ask again for the scene on screen. The server's today_view reports the try-on's installed plan. */
function invalidateToday() {
  L.cache.clear();
  schedulePreview();
}

function schedulePreview() {
  clearTimeout(L.timer);
  const now = Date.now();
  L.timer = setTimeout(sendPreview, M.nextPreviewAt(L.lastSent, now) - now);
}

async function sendPreview() {
  const scene = L.scene;
  const draft = L.working[scene];
  if (!draft) return;
  const key = M.previewKey(draft);
  if (L.cache.has(key) && !L.cache.get(key).failed) {
    L.lastGood[scene] = L.cache.get(key);
    L.problems[scene] = [];
    setAlert("preview", null);
    renderAll();
    return;
  }
  if (L.inflight) L.inflight.abort();
  const ctl = new AbortController();
  L.inflight = ctl;
  L.lastSent = Date.now();
  try {
    const preview = await api(`${API}/preview`, { method: "POST", body: { scene_id: scene, draft: M.normaliseDraft(draft) }, signal: ctl.signal });
    remember(key, preview);
    L.lastGood[scene] = preview;
    L.problems[scene] = [];
    delete L.failedKey[scene];
    setAlert("preview", null);
  } catch (err) {
    if (err && err.name === "AbortError") return;
    L.failedKey[scene] = key;
    const status = Number(err && err.status);
    if (status === 422) L.problems[scene] = (err.body && err.body.problems) || [];
    else if (status === 409 && err.body && err.body.field) L.problems[scene] = [{ field: err.body.field, message: err.body.detail }];
    else L.problems[scene] = [];
    setAlert("preview", M.alertFor(err));
  } finally {
    if (L.inflight === ctl) L.inflight = null;
  }
  if (L.scene === scene) renderAll();
}

// ---- boot ---------------------------------------------------------------------------------------------------------------
async function boot() {
  setAlert("boot", null);
  renderShell("Loading the scenes…");
  let catalog;
  try {
    catalog = await api(`${API}/catalog`);
  } catch (err) {
    setAlert("boot", M.alertFor(err) || "The Design Lab couldn't load its scenes.");
    renderShell(M.isNotBuilt(err) ? "The Design Lab isn't built on this server yet." : "The Design Lab couldn't load its scenes.", true);
    return;
  }
  L.catalog = catalog;
  for (const s of catalog.scenes) L.approved[s.id] = s.approved;
  setLiveLooks(catalog);
  const [state, versions] = await Promise.allSettled([api(`${API}/state`), api(`${API}/versions`), refreshLive({ render: false })]);
  if (state.status === "fulfilled") {
    L.state = state.value;
    setAlert("store", M.alertFor(state.value));
  } else setAlert("store", M.alertFor(state.reason));
  if (versions.status === "fulfilled") L.versions = versions.value.versions || [];
  else setAlert("versions", M.alertFor(versions.reason));
  const current = (state.status === "fulfilled" && state.value.current) || (versions.status === "fulfilled" && versions.value.current) || null;
  L.selected = null;
  // no version opened: each scene starts from the show's live look (a published look, or approved)
  for (const id of order()) L.base[id] = clone(M.seedFor(sceneOf(id)));
  if (current) {
    try {
      const drafts = await versionDrafts(current);
      for (const id of order()) L.base[id] = clone(M.seedFor(sceneOf(id), null, { versionDraft: drafts[id], fromVersion: true }));
      L.selected = current;
    } catch (err) {
      setAlert("versions", `The current version ${current} couldn't be opened: ${M.alertFor(err)}`);
    }
  }
  loadWorkingFromBrowser();
  const ui = store((s) => JSON.parse(s.getItem(UI_KEY) || "{}")) || {};
  L.scene = order().includes(ui.scene) ? ui.scene : order()[0];
  if (M.VIEWS.includes(ui.view)) L.view = ui.view;
  if (ui.angle === "plan") L.angle = "plan";
  L.who = M.whoValue(ui.who);
  buildLab();
  renderAll();
  schedulePreview();
  prefetchApproved();
  startTrialWatch();
  startLiveWatch({ fresh: false });
}

/** The look the show runs for each scene, from the catalog (live_look; approved on a server without publishing). */
function setLiveLooks(catalog) {
  for (const s of (catalog && catalog.scenes) || []) L.liveLooks[s.id] = M.liveLookOf(s);
}

const clone = (x) => JSON.parse(JSON.stringify(x));

async function versionDrafts(id) {
  if (L.versionDrafts[id]) return L.versionDrafts[id];
  const v = await api(`${API}/versions/${encodeURIComponent(id)}`);
  L.versionDrafts[id] = v.drafts || {};
  return L.versionDrafts[id];
}

/** The working drafts: the base, then whatever this browser kept for this config (other configs' keys dropped). */
function loadWorkingFromBrowser() {
  const sha = L.catalog.config_sha;
  const keys = store((s) => Array.from({ length: s.length }, (_, i) => s.key(i))) || [];
  const stale = M.staleDraftKeys(keys, sha);
  if (stale.length) {
    store((s) => stale.forEach((k) => s.removeItem(k)));
    setAlert("browser", "Unsaved drafts from before the last config change were cleared.");
  }
  for (const id of order()) {
    const kept = M.readStoredDraft(store((s) => s.getItem(M.draftStoreKey(sha, id))), id);
    L.working[id] = kept || clone(L.base[id]);
    L.undo[id] = [];
  }
}

function persist(id) {
  if (L.configChanged) return;
  const key = M.draftStoreKey(L.catalog.config_sha, id);
  if (M.draftEquals(L.working[id], L.base[id])) store((s) => s.removeItem(key));
  else store((s) => s.setItem(key, JSON.stringify(L.working[id])));
}

function rememberUi() {
  store((s) => s.setItem(UI_KEY, JSON.stringify({ scene: L.scene, view: L.view, angle: L.angle, who: L.who || undefined })));
}

function prefetchApproved() {
  for (const id of order()) {
    const looks = [L.approved[id], ...(M.draftEquals(L.liveLooks[id], L.approved[id]) ? [] : [L.liveLooks[id]])];
    for (const d of looks) requestPreview(d).then(() => {
      if (L.ab.on) renderStage();
    });
  }
}

// ---- editing ------------------------------------------------------------------------------------------------------------
/** Apply `fn` to the working draft of the scene on screen. Typing and dragging coalesce into one undo step. */
function edit(fn, { coalesce = false } = {}) {
  const id = L.scene;
  const before = L.working[id];
  const after = fn(before);
  if (JSON.stringify(before) === JSON.stringify(after)) return;
  const now = Date.now();
  if (!coalesce || now - L.lastUndoAt > 800) L.undo[id] = M.undoPush(L.undo[id], before);
  L.lastUndoAt = coalesce ? now : 0;
  L.working[id] = after;
  setAlert("save", null); // the last failed save was about another draft
  persist(id);
  schedulePreview();
  syncEditor();
  renderSceneList();
  renderVersionsBar();
  renderStageBadges();
}

function undo() {
  const id = L.scene;
  const { stack, state } = M.undoPop(L.undo[id]);
  if (!state) {
    setNotice("Nothing to undo for this scene.");
    return;
  }
  L.undo[id] = stack;
  L.working[id] = state;
  L.lastUndoAt = 0;
  setAlert("save", null);
  persist(id);
  rebuildEditor();
  schedulePreview();
  renderAll();
}

/** Reset the scene's working draft (client only, never a publish): to the look the show runs, or to approved (git). */
function resetDraft(to) {
  const id = L.scene;
  const target = to === "live" ? L.liveLooks[id] : L.approved[id];
  const what = to === "live" ? "the show’s look" : "the approved design (git)";
  if (!target) return;
  if (M.draftEquals(L.working[id], target)) {
    setNotice(`${sceneName(id)} is already ${what}.`);
    return;
  }
  if (!M.draftEquals(L.working[id], L.base[id]) && !window.confirm(`Reset ${sceneName(id)} to ${what}? Your unsaved changes to it are kept in Undo.`)) return;
  L.undo[id] = M.undoPush(L.undo[id], L.working[id]);
  L.working[id] = clone(target);
  setAlert("save", null);
  setNotice(`${sceneName(id)} reset to ${what} (Undo brings your edit back). Nothing was published.`);
  persist(id);
  rebuildEditor();
  schedulePreview();
  renderAll();
}

/** "Make this the scene" (D76): the option on screen's design into its base's working draft (client only, never a
 * request: Publish saves and publishes it, Undo on the base brings its draft back). Then the base is on screen. */
function promoteToBase() {
  const option = L.scene;
  const scene = sceneOf(option);
  const base = scene && scene.option_of;
  if (!option || !base || !L.working[base] || !L.working[option]) return;
  const { draft, notes } = M.promoteDraft(L.working[base], L.working[option], { baseStepped: !!(sceneOf(base) && sceneOf(base).stepped) });
  const optionName = sceneName(option);
  const baseName = sceneName(base);
  if (M.draftEquals(draft, L.working[base])) {
    setNotice(M.PROMOTE_COPY.same(optionName, baseName));
    return;
  }
  if (!window.confirm(M.PROMOTE_COPY.question(optionName, baseName))) return;
  L.undo[base] = M.undoPush(L.undo[base], L.working[base]);
  L.working[base] = draft;
  setAlert("save", null);
  persist(base);
  motionStop();
  L.scene = base;
  rememberUi();
  const lines = notes.map((n) => (n === "no-rung" ? M.PROMOTE_COPY.noRung(baseName) : M.PROMOTE_COPY.keptMotion(optionName, baseName)));
  setNotice([M.PROMOTE_COPY.done(optionName, baseName), ...lines].join(" "));
  rebuildEditor();
  schedulePreview();
  renderAll();
}

function selectScene(id) {
  if (!order().includes(id) || id === L.scene) return;
  motionStop();
  L.scene = id;
  rememberUi();
  rebuildEditor();
  renderAll();
  schedulePreview();
}

// ---- versions -----------------------------------------------------------------------------------------------------------
async function refreshVersions() {
  try {
    const answer = await api(`${API}/versions`);
    L.versions = answer.versions || [];
    setAlert("versions", null);
    return answer;
  } catch (err) {
    setAlert("versions", M.alertFor(err));
    return null;
  }
}

function guardUnsaved() {
  const names = M.unsavedScenes(L.working, L.base, order()).map(sceneName);
  const q = M.replaceQuestion(names);
  return !q || window.confirm(q);
}

/** Open a version (or "approved") into the working drafts: POST /restore sets the store's current and answers all six. */
async function openVersion(id) {
  if (!guardUnsaved()) return;
  try {
    const answer = await api(`${API}/restore`, { method: "POST", body: { version: id } });
    for (const sid of order()) {
      const d = answer.drafts && answer.drafts[sid];
      if (!d) continue;
      if (!M.draftEquals(L.working[sid], d)) L.undo[sid] = M.undoPush(L.undo[sid], L.working[sid]);
      L.base[sid] = clone(d);
      L.working[sid] = clone(d);
      persist(sid);
    }
    L.selected = answer.current || null;
    if (L.selected) L.versionDrafts[L.selected] = Object.fromEntries(Object.entries(answer.drafts || {}).filter(([sid, d]) => !M.draftEquals(d, L.approved[sid])));
    setAlert("versions", null);
    setAlert("save", null);
    setNotice(L.selected ? `Opened ${M.entryLabel(L.selected, L.versions)}.` : "Back to all approved.");
  } catch (err) {
    const problems = err && err.body && err.body.problems;
    const extra = problems && problems.length ? ` ${problems.map((p) => p.message).join(" ")}` : "";
    setAlert("versions", (M.alertFor(err) || "That version couldn't be opened.") + extra);
  }
  rebuildEditor();
  renderAll();
  schedulePreview();
}

async function backToPrevious() {
  const v = L.versions.find((x) => x.id === L.selected);
  await openVersion(v && v.parent ? v.parent : M.APPROVED);
}

/** Save the working drafts that differ from approved as a new version. Resolves the saved VersionJSON or null. One
 * save at a time: a second tap while one is on its way does nothing (Save and Export are disabled meanwhile). */
async function saveVersion(note) {
  if (L.saving) return null;
  L.saving = true;
  renderVersionsBar();
  try {
    return await saveVersionNow(note);
  } finally {
    L.saving = false;
    renderAll();
  }
}

async function saveVersionNow(note) {
  const drafted = M.draftedScenes(L.working, L.approved, order());
  const body = {
    drafts: Object.fromEntries(drafted.map((id) => [id, M.normaliseDraft(L.working[id])])),
    note: String(note || "").slice(0, 60),
    parent: L.selected,
  };
  let saved;
  try {
    saved = await api(`${API}/versions`, { method: "POST", body });
  } catch (err) {
    const problems = (err && err.body && err.body.problems) || [];
    const lines = M.saveProblemLines(problems, Object.fromEntries(order().map((sid) => [sid, sceneName(sid)])));
    setAlert("save", [M.alertFor(err) || (err.body && err.body.detail) || "The version wasn't saved.", ...lines].join(" "));
    if (problems.length && err.status === 422) L.problems[L.scene] = problems.filter((p) => !p.path || String(p.path).includes(L.scene));
    renderAll();
    return null;
  }
  setAlert("save", null);
  L.selected = saved.id;
  L.versionDrafts[saved.id] = saved.drafts || {};
  for (const id of order()) {
    L.base[id] = clone(L.working[id]);
    persist(id);
  }
  const answer = await refreshVersions();
  if (answer && answer.current !== saved.id) {
    // make the saved version the one the Lab opens on next time (restore sets the store's current)
    try {
      await api(`${API}/restore`, { method: "POST", body: { version: saved.id } });
    } catch {
      // the version is saved; only the "opens on" pointer is behind
    }
  }
  setNotice(`Saved as ${saved.name}`);
  renderAll();
  return saved;
}

async function deleteVersion(id) {
  const name = M.entryLabel(id, L.versions);
  if (!window.confirm(`Delete ${name}? This can’t be undone.`)) return;
  try {
    const answer = await api(`${API}/versions/${encodeURIComponent(id)}`, { method: "DELETE", body: {} });
    delete L.versionDrafts[id];
    if (L.selected === id) L.selected = answer.current || null;
    for (const side of ["a", "b"]) if (L.ab[side] === id) L.ab[side] = side === "a" ? M.APPROVED : M.WORKING;
    setNotice(`Deleted ${name}.`);
    setAlert("versions", null);
  } catch (err) {
    setAlert("versions", M.alertFor(err));
  }
  await refreshVersions();
  renderAll();
}

async function compareAs(side, id) {
  L.ab.on = true;
  L.ab[side] = id;
  if (id !== M.APPROVED && id !== M.WORKING) {
    try {
      await versionDrafts(id);
    } catch (err) {
      setAlert("versions", M.alertFor(err));
    }
  }
  renderAll();
}

// ---- export -------------------------------------------------------------------------------------------------------------
async function exportCurrent() {
  if (L.saving) return;
  const unsaved = M.unsavedScenes(L.working, L.base, order()).length > 0;
  const drafted = M.draftedScenes(L.working, L.approved, order()).length > 0;
  let id = L.selected;
  if (M.exportLabel({ selected: L.selected, unsaved, drafted }) === "Save & export") {
    const note = $("#lab-note") ? $("#lab-note").value : "";
    const saved = await saveVersion(note);
    if (!saved) return;
    if ($("#lab-note")) $("#lab-note").value = "";
    id = saved.id;
  }
  if (id) await openExport(id);
}

async function openExport(id) {
  const dialog = $("#lab-export");
  const body = $("#lab-export-body");
  body.replaceChildren(h("p", { class: "lab-muted", text: "Building the patch…" }));
  $("#lab-export-title").textContent = `Export · ${M.entryLabel(id, L.versions)}`;
  if (!dialog.open) dialog.showModal ? dialog.showModal() : dialog.setAttribute("open", "");
  try {
    const x = await api(`${API}/export?version=${encodeURIComponent(id)}&format=json`);
    renderExport(x);
  } catch (err) {
    const b = (err && err.body) || {};
    const items = [h("p", { class: "lab-export-refused", text: M.alertFor(err) || b.detail || "No patch is offered." })];
    // a D63 refusal's detail already says each warning with its scene's name: list them only when it doesn't
    if (!b.detail) for (const w of b.warnings || []) items.push(h("p", { class: "lab-warning", text: `${w.mode || ""} ${w.message || w.rule}`.trim() }));
    for (const p of b.problems || []) items.push(h("p", { class: "lab-warning", text: `${p.field}: ${p.message}` }));
    body.replaceChildren(...items);
  }
}

function renderExport(x) {
  const body = $("#lab-export-body");
  const parts = [];
  if (!x.patch) {
    parts.push(h("p", { class: "lab-muted", text: x.problem || "Nothing changed: this version is the approved config, so there is no patch." }));
  } else {
    const copyBtn = h("button", { class: "lab-btn", type: "button", text: "Copy patch" });
    copyBtn.addEventListener("click", async () => {
      const ok = await copyJSON(x.patch);
      copyBtn.textContent = ok ? "Copied" : "Copy failed — use Download";
    });
    const dl = h("button", { class: "lab-btn", type: "button", text: `Download ${x.filename}` });
    dl.addEventListener("click", () => {
      const url = URL.createObjectURL(new Blob([x.patch], { type: "text/x-diff" }));
      const a = h("a", { href: url, download: x.filename });
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    parts.push(h("div", { class: "lab-row" }, copyBtn, dl));
    parts.push(h("p", { class: "lab-muted", text: `Files: ${(x.files || []).join(", ")} · scenes: ${(x.scenes || []).join(", ")}` }));
  }
  const warn = [
    ...(x.approved_changes || []).map((t) => `D59 — needs the client's sign-off and a D59 update: ${t}`),
    ...(x.layout_changes || []).map((t) => `D63 — Paris reviews: ${t}`),
    ...(x.stepped_changes || []).map((t) => `D65 — stepped timing, ${M.steppedSectionClause(x, L.catalog.live)}: ${t}`),
  ];
  if (warn.length) parts.push(h("ul", { class: "lab-export-warnings" }, warn.map((t) => h("li", { text: t }))));
  if ((x.review_notes || []).length) parts.push(h("h3", { text: "Review notes" }), h("ul", {}, x.review_notes.map((t) => h("li", { text: t }))));
  if (x.decision) parts.push(h("h3", { text: "Decision draft" }), h("pre", { class: "lab-pre lab-decision", text: x.decision }));
  if (x.patch) parts.push(h("h3", { text: "Patch" }), h("pre", { class: "lab-pre", text: x.patch }));
  body.replaceChildren(...parts);
}

// ---- building the page ------------------------------------------------------------------------------------------------
function root() {
  return document.getElementById("lab");
}

/** Before the catalog answers (or when it can't): the header, the alert and one readable line. */
function renderShell(message, failed = false) {
  const el = root();
  if (L.built) return;
  el.replaceChildren(
    header(),
    h("div", { id: "lab-alert", class: "lab-alert", role: "alert", hidden: true }),
    h(
      "div",
      { class: "lab-empty" },
      h("p", { text: message }),
      failed ? h("button", { class: "lab-btn", type: "button", onclick: () => boot(), text: "Try again" }) : null,
    ),
  );
  setAlert("_", null);
}

function header() {
  return h(
    "header",
    { class: "lab-head" },
    h("div", { class: "lab-title" }, h("span", { id: "lab-eyebrow", class: "eyebrow", text: "DESIGN LAB · SCREEN ONLY" }), h("h1", { text: "Shape each scene." })),
    h(
      "div",
      { class: "lab-head-actions" },
      h("span", { id: "lab-sim", class: "lab-sim", role: "status", title: "Simulated: no real lights are driven from the Lab.", hidden: true, text: "Simulator" }),
      h("button", { id: "lab-trial-badge", class: "lab-trial-badge", type: "button", hidden: true, title: "Show the scene being tried on" }),
      h("button", { id: "lab-present-open", class: "lab-btn primary", type: "button", disabled: true, text: "Present" }),
      h("a", { class: "lab-close", href: "#", text: "Close Lab" }),
    ),
  );
}

function buildLab() {
  const el = root();
  const tabs = h("div", { class: "lab-tabs", role: "tablist", "aria-label": "What the stage shows" });
  el.replaceChildren(
    header(),
    showBarEl(),
    h("div", { id: "lab-alert", class: "lab-alert", role: "alert", hidden: true }),
    h(
      "section",
      { class: "lab-versions", "aria-label": "Versions" },
      h(
        "div",
        { class: "lab-versions-main" },
        h("div", { class: "lab-current" }, h("span", { class: "eyebrow", text: "VERSION" }), h("strong", { id: "lab-current-name" }), h("span", { id: "lab-unsaved", class: "lab-unsaved", hidden: true, text: "Unsaved changes" })),
        h(
          "form",
          { id: "lab-save", class: "lab-save" },
          h("label", { class: "sr-only", for: "lab-note", text: "Note for the version name" }),
          h("input", { id: "lab-note", class: "lab-input", maxlength: 60, placeholder: "Note, e.g. warmer sunset", autocomplete: "off" }),
          h("button", { class: "lab-btn primary", type: "submit", text: "Save version" }),
        ),
        h("button", { class: "lab-btn publish", type: "button", dataset: { publish: "bar" }, onclick: () => publishWorking(), text: M.PUBLISH }),
        h("button", { id: "lab-export-open", class: "lab-btn", type: "button", text: "Export" }),
        h("button", { id: "lab-back", class: "lab-btn quiet", type: "button", text: "Back to previous version" }),
        h("button", { id: "lab-all-approved", class: "lab-btn quiet", type: "button", text: "Back to all approved" }),
        h("button", { id: "lab-version-summary", class: "lab-btn", type: "button", "aria-expanded": "false", "aria-controls": "lab-versions-ol", text: "Saved versions" }),
      ),
      h(
        "div",
        { class: "lab-versions-files" },
        h("button", { id: "lab-download", class: "lab-btn small", type: "button", text: "Download version" }),
        h("button", { id: "lab-open-file", class: "lab-btn small", type: "button", "aria-controls": "lab-file", title: "Add a version downloaded from another Lab (laptop, staging, the show box); it is checked like any save", text: "Open a version file" }),
        h("label", { class: "sr-only", for: "lab-file", text: "A Lab version file (.json)" }),
        h("input", { id: "lab-file", class: "sr-only", type: "file", accept: ".json,application/json", tabindex: "-1" }),
        h("small", { class: "lab-muted", text: "A version file carries a version between the laptop, staging and the show box; it is checked like any save." }),
      ),
      h("p", { id: "lab-publish-why", class: "lab-publish-why" }),
      h("p", { id: "lab-notice", class: "lab-notice", role: "status", "aria-live": "polite" }),
      h("ol", { id: "lab-versions-ol", class: "lab-version-list", hidden: true }),
    ),
    h(
      "div",
      { class: "lab-grid" },
      h(
        "nav",
        { class: "lab-scenes", "aria-label": "Scenes" },
        h("ul", { id: "lab-scene-list" }),
        h(
          "section",
          { id: "lab-ipad", class: "lab-ipad", "aria-label": "What the iPad shows" },
          h("span", { class: "eyebrow", text: "ON THE IPAD NOW" }),
          h("ul", { id: "lab-ipad-cards", class: "lab-ipad-cards" }),
          h("p", { class: "lab-ipad-note", text: M.LIVE_COPY.ipad }),
        ),
      ),
      h(
        "section",
        { class: "lab-stage-col", "aria-label": "Preview" },
        h("div", { class: "lab-stage-bar" }, tabs),
        h("div", { id: "lab-stages", class: "lab-stages" }),
        h("p", { id: "lab-stage-moving", class: "lab-stage-moving", role: "status", "aria-live": "polite", hidden: true }),
        h(
          "div",
          { class: "lab-stage-tools" },
          h("button", { id: "lab-ab", class: "lab-chip", type: "button", "aria-pressed": "false", text: "Compare A/B" }),
          h("button", { id: "lab-angle", class: "lab-chip", type: "button", "aria-pressed": "false", text: "Ceiling plan" }),
          // the motion preview's controls sit by the picture they move (screen only; settings in the card below)
          h("button", { id: "lab-play", class: "lab-chip play", type: "button", "aria-pressed": "false", hidden: true, text: "▶ Play the steps" }),
          h("button", { id: "lab-motion-stop", class: "lab-chip", type: "button", hidden: true, text: "Stop" }),
          h("button", { id: "lab-ripple-play", class: "lab-chip", type: "button", text: "Play the ripple" }),
        ),
        motionPanel(),
        trialPanel(),
        h("ol", { id: "lab-tiers", class: "lab-tiers", "aria-label": "Each tier's sentence as the iPad shows it" }),
      ),
      h("aside", { id: "lab-editor", class: "lab-editor", "aria-label": "Edit the scene" }),
    ),
    presentOverlay(),
    exportDialog(),
    publishDialogEl(),
    h("div", { id: "lab-toast", class: "lab-toast", role: "status", "aria-live": "polite", hidden: true }),
  );
  $("#lab-sim").hidden = !L.catalog.simulated;
  wireLive();
  $("#lab-present-open").disabled = false;
  $("#lab-present-open").addEventListener("click", openPresent);
  $("#lab-save").addEventListener("submit", async (e) => {
    e.preventDefault();
    const saved = await saveVersion($("#lab-note").value);
    if (saved) $("#lab-note").value = "";
  });
  $("#lab-export-open").addEventListener("click", exportCurrent);
  $("#lab-back").addEventListener("click", backToPrevious);
  $("#lab-all-approved").addEventListener("click", () => openVersion(M.APPROVED));
  $("#lab-version-summary").addEventListener("click", () => {
    const list = $("#lab-versions-ol");
    list.hidden = !list.hidden;
    $("#lab-version-summary").setAttribute("aria-expanded", String(!list.hidden));
  });
  $("#lab-ab").addEventListener("click", () => {
    L.ab.on = !L.ab.on;
    renderAll();
  });
  $("#lab-angle").addEventListener("click", () => {
    L.angle = L.angle === "plan" ? "perspective" : "plan"; // the angle is in each stage's key: stageEl rebuilds in place
    rememberUi();
    renderStage();
  });
  $("#lab-trial-badge").addEventListener("click", () => {
    const t = trial();
    if (t) selectScene(t.scene_id);
  });
  $("#lab-trial-apply").addEventListener("click", () => applyTrial());
  $("#lab-trial-go").addEventListener("click", () => {
    const c = L.tryon.confirm;
    if (c && c.scene === L.scene) applyTrial({ confirm: c.sentence });
  });
  $("#lab-trial-cancel").addEventListener("click", () => {
    L.tryon.confirm = null;
    renderTrial();
  });
  $("#lab-trial-stop").addEventListener("click", stopTrial);
  wireMotion();
  wirePresentTouch();
  L.built = true;
  setAlert("_", null);
  rebuildEditor();
}

// ---- the scene list ---------------------------------------------------------------------------------------------------
/** The running try-on, as the server last said: GET /api/lab/trial (or a start/stop answer), else StateJSON's. */
function trial() {
  const api = L.tryon.api;
  if (api === "ready" || api === "unreachable" || api === "error") return L.tryon.trial; // the last known, until it answers
  if (api === "missing" || api === "no-engine") return null;
  return (L.state && L.state.trial) || null;
}

function renderSceneList() {
  const ul = $("#lab-scene-list");
  if (!ul) return;
  ul.replaceChildren(
    ...order().map((id) => {
      const d = L.working[id];
      // two badges: what the show runs for this scene (approved or published), and the working draft against it
      const state = M.workingState(d, L.liveLooks[id], trial());
      const live = M.liveBadge(sceneOf(id), liveScene(id));
      const held = !!(liveScene(id) && liveScene(id).held);
      const unsaved = !M.draftEquals(d, L.base[id]);
      const marked = !!L.publish.marks[id];
      const strip = h("span", { class: "lab-strip", "aria-hidden": "true" }, (d.palette || []).map((c) => h("i", { style: `background:${c.hex}` })));
      const option = !!(sceneOf(id) && sceneOf(id).option_of); // D76: an option sits under its base
      const offIpad = !!(liveScene(id) && liveScene(id).on_ipad === false); // /ops took its card off the iPad
      const btn = h(
        "button",
        { type: "button", class: `${marked ? "lab-scene is-refused" : "lab-scene"}${option ? " is-option" : ""}`, "aria-current": id === L.scene ? "true" : null, onclick: () => selectScene(id) },
        strip,
        h("span", { class: "lab-scene-text" }, h("strong", { text: d.name }), h("small", { text: d.mood })),
        h(
          "span",
          { class: "lab-badges" },
          h("span", { class: `lab-live-badge ${live.toLowerCase()}`, title: held ? "Shows at module level until its slots are repainted" : `The show runs the ${live.toLowerCase()} look`, text: `Live: ${live}${held ? " · held" : ""}` }),
          h("span", { class: `lab-badge ${state.replace(/\s/g, "-")}`, title: state === "draft" ? "The working draft differs from what the show runs" : null, text: M.WORKING_TEXT[state] }),
          offIpad ? h("span", { class: "lab-badge off-ipad", title: "Taken off the iPad on /ops: guests don’t see its card", text: "Off the iPad" }) : null,
        ),
        unsaved ? h("span", { class: "lab-dot", title: "Unsaved changes", "aria-label": "Unsaved changes" }) : null,
      );
      return h("li", {}, btn);
    }),
  );
}

/** LiveJSON's entry for a scene (what the iPad shows; `held`), or null. */
function liveScene(id) {
  return (L.live && Array.isArray(L.live.scenes) && L.live.scenes.find((s) => s.id === id)) || null;
}

/** "On the iPad now": the six cards as the live catalog has them (never a draft or a try-on). */
function renderIpadStrip() {
  const ul = $("#lab-ipad-cards");
  if (!ul) return;
  const cards = M.ipadCards(L.liveApi === "ready" ? L.live : null, L.catalog);
  const key = JSON.stringify(cards);
  if (ul.dataset.key === key) return;
  ul.dataset.key = key;
  ul.replaceChildren(
    ...cards.map((c) =>
      h(
        "li",
        { class: c.source === "published" ? "published" : null, title: c.source === "published" ? "Published look" : "Approved look" },
        h("span", { class: "lab-ipad-swatches", "aria-hidden": "true" }, c.palette.map((hex) => h("i", { style: `background:${hex}` }))),
        h("span", { text: c.name }),
      ),
    ),
  );
}

// ---- the versions bar ------------------------------------------------------------------------------------------------
function renderVersionsBar() {
  if (!$("#lab-current-name")) return;
  const unsavedIds = M.unsavedScenes(L.working, L.base, order());
  const drafted = M.draftedScenes(L.working, L.approved, order()).length > 0;
  $("#lab-current-name").textContent = L.selected ? M.entryLabel(L.selected, L.versions) : "Approved (git)";
  $("#lab-unsaved").hidden = !unsavedIds.length;
  $("#lab-unsaved").textContent = unsavedIds.length ? `Unsaved: ${unsavedIds.map(sceneName).join(", ")}` : "";
  const label = M.exportLabel({ selected: L.selected, unsaved: unsavedIds.length > 0, drafted });
  const exp = $("#lab-export-open");
  exp.hidden = !label;
  exp.textContent = label || "Export";
  $("#lab-back").hidden = !L.selected;
  $("#lab-all-approved").hidden = !L.selected && !drafted;
  const readOnly = !!(L.state && L.state.store && L.state.store.read_only);
  for (const b of document.querySelectorAll("#lab-back, #lab-all-approved")) b.disabled = readOnly;
  for (const b of document.querySelectorAll("#lab-save button")) b.disabled = readOnly || L.saving;
  exp.disabled = L.saving;
  renderPublishButtons();
  $("#lab-download").disabled = !L.selected;
  $("#lab-download").title = L.selected ? `Download ${M.entryLabel(L.selected, L.versions)} as a version file` : "Save or open a version first";
  $("#lab-open-file").disabled = readOnly || L.saving;
  $("#lab-version-summary").textContent = `Saved versions (${L.versions.length})`;
  const liveVersion = L.live && L.live.source === "published" && L.live.current ? L.live.current.version : null;
  const ol = $("#lab-versions-ol");
  ol.replaceChildren(
    ...(L.versions.length ? [] : [h("li", { class: "lab-muted", text: "No saved versions yet. Save one to compare it, present it, publish it or export it." })]),
    ...L.versions.map((v) => {
      const flags = [...(v.id === liveVersion ? ["Live on the show"] : []), ...M.versionFlags(v), ...(L.publish.stale[v.id] && !v.stale ? ["Stale"] : [])];
      const actions = v.unreadable
        ? [h("span", { class: "lab-muted", text: v.error || "This version couldn't be read; it is kept in the file as it is." })]
        : [
            h("button", { type: "button", class: "lab-btn small", onclick: () => openVersion(v.id), text: "Open" }),
            h("button", { type: "button", class: "lab-btn small", onclick: () => compareAs("a", v.id), text: "As A" }),
            h("button", { type: "button", class: "lab-btn small", onclick: () => compareAs("b", v.id), text: "As B" }),
            h("button", { type: "button", class: "lab-btn small", onclick: () => openExport(v.id), text: "Export" }),
            h("button", { type: "button", class: "lab-btn small", onclick: () => downloadVersion(v.id), text: "Download" }),
            h("button", { type: "button", class: "lab-btn small danger", disabled: readOnly, onclick: () => deleteVersion(v.id), text: "Delete" }),
          ];
      return h(
        "li",
        { class: v.id === L.selected ? "lab-version current" : "lab-version" },
        h(
          "div",
          { class: "lab-version-text" },
          h("strong", { text: v.name || v.id }),
          h("small", { text: [M.versionDate(v.created_at), (v.scenes || []).map((s) => (sceneOf(s) ? sceneOf(s).name : s)).join(", ") || "no scene changed"].filter(Boolean).join(" · ") }),
          flags.length ? h("span", { class: "lab-flags" }, flags.map((f) => h("span", { class: f === "Live on the show" ? "lab-flag live" : "lab-flag", text: f }))) : null,
          (v.problems || []).length ? h("small", { class: "lab-warning", text: v.problems.map((p) => p.message).join(" ") }) : null,
          L.publish.stale[v.id] && !(v.problems || []).length ? h("small", { class: "lab-warning", text: L.publish.stale[v.id] }) : null,
        ),
        h("div", { class: "lab-version-actions" }, actions),
      );
    }),
  );
}

/** The one Publish button (versions bar and editor header): its label, whether it can send, and why not. */
function publishState() {
  return M.publishAction({
    working: L.working,
    base: L.base,
    approved: L.approved,
    liveLooks: L.liveLooks,
    order: order(),
    selected: L.selected,
    api: L.liveApi,
    writable: !(L.live && L.live.writable === false),
    readOnly: !!(L.state && L.state.store && L.state.store.read_only),
    busy: L.publish.busy || L.saving,
    simulated: isSimulated(),
  });
}

function isSimulated() {
  return L.live && typeof L.live.simulated === "boolean" ? L.live.simulated : !!(L.catalog && L.catalog.simulated);
}

function renderPublishButtons() {
  const a = publishState();
  for (const b of document.querySelectorAll("[data-publish]")) {
    b.textContent = L.publish.busy ? "Publishing…" : a.label;
    b.disabled = !a.enabled;
    b.title = a.why || (a.save ? "Saves the working drafts as a version (with the note), then asks the server to publish it" : "The server says what changes and what it costs before anything is sent");
    b.classList.toggle("is-live", a.label === M.LIVE_NOW);
  }
  const why = $("#lab-publish-why");
  if (why) {
    // only the reasons a person can act on or should know (not "Live now", which the button itself says)
    why.textContent = a.enabled || a.label === M.LIVE_NOW ? "" : a.why;
  }
}

// ---- the stage ----------------------------------------------------------------------------------------------------------
function sideEntries() {
  return L.ab.on ? [["a", L.ab.a], ["b", L.ab.b]] : [["main", M.WORKING]];
}

function draftFor(entry, sceneId) {
  return M.presentDraft(entry, sceneId, { approved: L.approved, working: L.working, versionDrafts: L.versionDrafts, live: L.liveLooks });
}

function previewFor(entry, sceneId) {
  if (entry === M.WORKING) return workingPreview(sceneId);
  // (a saved or approved draft: `failed` is carried by the cached answer itself)
  const draft = draftFor(entry, sceneId);
  if (!draft) return { preview: null, fresh: false };
  const cached = L.cache.get(M.previewKey(draft));
  if (cached) return { preview: cached, fresh: true };
  requestPreview(draft).then(() => renderStage());
  return { preview: catalogPreview(draft), fresh: false };
}

function stageEl(side, entry) {
  const st = L.stages[side];
  const key = `${side}|${L.angle}|${L.versions.map((v) => v.id).join(",")}`;
  if (st && st.key === key && st.el.isConnected) return st;
  const canopy = h("div", { class: "lab-canopy" });
  const picker = L.ab.on ? sidePicker(side) : null;
  const el = h(
    "figure",
    { class: "lab-stage", dataset: { side } },
    h("figcaption", { class: "lab-stage-head" }, picker, h("span", { class: "lab-stage-label" }), h("span", { class: "lab-badge" })),
    canopy,
    h("p", { class: "lab-stage-note" }),
  );
  const view = buildCanopy(canopy, { scope: "canopy", device: "both", maps: {}, angle: L.angle });
  // a rebuilt stage (a new angle, a saved or deleted version for the picker) replaces the old one where it stands:
  // the old figure never stays on the stage, frozen above the live one
  if (st && st.el.isConnected) st.el.replaceWith(el);
  L.stages[side] = { key, el, view, entry };
  return L.stages[side];
}

function sidePicker(side) {
  const sel = h("select", { class: "lab-select", "aria-label": side === "a" ? "Side A shows" : "Side B shows" });
  const opts = [[M.APPROVED, "Approved"], [M.LIVE, publishLabelSim("Live on the show")], [M.WORKING, "Working draft"], ...L.versions.filter((v) => !v.unreadable).map((v) => [v.id, v.name])];
  for (const [value, text] of opts) sel.append(h("option", { value, text }));
  sel.value = L.ab[side];
  sel.addEventListener("change", () => compareAs(side, sel.value));
  return sel;
}

function paintStage(st, preview, view, { fresh = true, failed = false, notes = [], paint = {} } = {}) {
  const painter = M.viewPainter(preview, view, paint);
  const p = painter.error ? M.unlitPainter(painter.error) : painter;
  const level = Number.isFinite(Number(p.level)) ? Number(p.level) : 100;
  paintCanopy(st.view, p.head, p.spill, level);
  const note = $(".lab-stage-note", st.el);
  const parts = [];
  if (painter.error) parts.push(painter.error);
  else if (!fresh && failed) parts.push(preview && preview.fromCatalog ? "Showing the approved design from the catalog: the preview didn't answer." : "Showing the last valid draft.");
  else if (!fresh) parts.push("Updating…");
  if (preview && preview.fromCatalog && view !== "canvas" && view !== "modules" && view !== "strand") parts.push("Waiting for the server's preview…");
  if (!painter.error) parts.push(...notes.filter(Boolean));
  note.textContent = parts.join(" ");
  st.el.classList.toggle("is-error", !!painter.error);
  return painter;
}

function renderStage() {
  const box = $("#lab-stages");
  if (!box || !L.scene) return;
  const sides = sideEntries();
  const wanted = sides.map(([side]) => side).join(",");
  if (box.dataset.sides !== wanted) {
    box.dataset.sides = wanted;
    L.stages = {};
    box.replaceChildren();
  }
  const main = workingPreview();
  const views = M.availableViews(main.preview);
  const view = M.pickView(main.preview, L.view) || L.view;
  renderTabs(main.preview, views, view);
  for (const [side, entry] of sides) {
    const st = stageEl(side, entry);
    if (!st.el.isConnected) box.append(st.el);
    const { preview, fresh, failed } = previewFor(entry, L.scene);
    const shown = M.availableViews(preview).includes(view) ? view : M.pickView(preview, view);
    // the motion preview (screen only) paints the server's steps, or masks the modules a ripple hasn't reached yet
    const mo = motionPaint(preview, shown);
    const v = mo ? mo.view : shown;
    // under the picture: why "today" shows what it shows, and (for the working draft) a D63 warning the last edit
    // raised, wherever that edit was made
    const notes = [
      ...(mo ? mo.notes : []),
      M.todayNote(v, preview),
      M.trialStageNote(v, preview, trial(), L.scene),
      ...(entry === M.WORKING && fresh ? M.warningLines(preview) : []),
    ];
    paintStage(st, preview, v, { fresh, failed, notes, paint: mo ? mo.paint : {} });
    const picker = $("select", st.el);
    if (picker && document.activeElement !== picker) picker.value = entry;
    const draft = draftFor(entry, L.scene);
    const state = M.stageState(v, preview, stageDraftState(entry, draft));
    const caption = [M.tierCaption(v, preview), mo && mo.label].filter(Boolean).join(" · ");
    $(".lab-stage-label", st.el).textContent = L.ab.on ? `${side.toUpperCase()} · ${caption}` : caption;
    const badge = $(".lab-badge", st.el);
    badge.className = `lab-badge ${state.replace(/\s/g, "-")}`;
    badge.textContent = M.BADGE_TEXT[state];
  }
  renderTiers(main.preview, view);
  $("#lab-ab").setAttribute("aria-pressed", String(L.ab.on));
  $("#lab-angle").setAttribute("aria-pressed", String(L.angle === "plan"));
  $("#lab-angle").textContent = L.angle === "plan" ? "Perspective" : "Ceiling plan";
}

function renderStageBadges() {
  renderStage();
}

/** A stage side's badge: the working draft against what the show runs (Live / Draft / In try-on: never "Approved" for
 * a published look); the show's own look is Live; Approved and a saved version against approved (git). */
function stageDraftState(entry, draft) {
  if (!draft) return "approved";
  if (entry === M.WORKING) return M.workingState(draft, L.liveLooks[L.scene], trial());
  if (entry === M.LIVE) return "live";
  return M.draftState(L.approved[L.scene], draft, trial());
}

/** "…the show…" → "…this simulator…" on a simulator runtime. */
function publishLabelSim(text) {
  return M.publishLabel(isSimulated(), text);
}

function renderTabs(preview, views, current) {
  const tabs = $(".lab-tabs");
  if (!tabs) return;
  const list = views.length ? views : [L.view];
  tabs.replaceChildren(
    ...list.map((v) =>
      h("button", {
        type: "button",
        role: "tab",
        class: "lab-tab",
        "aria-selected": String(v === current),
        onclick: () => {
          L.view = v;
          rememberUi();
          renderStage();
        },
        text: M.viewLabel(v, preview),
      }),
    ),
  );
  // a row that scrolls (iPad): the selected tab is never left cut off at the edge
  const sel = tabs.querySelector('[aria-selected="true"]');
  if (sel && tabs.scrollWidth > tabs.clientWidth) {
    const left = sel.offsetLeft - tabs.offsetLeft;
    if (left < tabs.scrollLeft) tabs.scrollLeft = left;
    else if (left + sel.offsetWidth > tabs.scrollLeft + tabs.clientWidth) tabs.scrollLeft = left + sel.offsetWidth - tabs.clientWidth;
  }
}

function renderTiers(preview, view) {
  const ol = $("#lab-tiers");
  if (!ol) return;
  const items = [];
  // during a try-on of this scene the lights run the tier the engine installed for it, not today's
  const t0 = trial();
  const trying = !!(t0 && t0.scene_id === L.scene && t0.tier);
  const running = trying ? t0.tier : preview && preview.today ? preview.today.tier : null;
  // the moving sentence shows only while the steps play, as on the iPad (it is shown only while a run is going); at
  // the show box's pace each line says whose sentence it is (the row is the nine-module tier's, D62)
  const playing = L.motion.state === "playing" && !L.present;
  const motion = motionTimeline(preview);
  const at = playing && !motion.tl.error ? M.stepAt(motion.tl, motionElapsed()) : null;
  for (const t of (preview && preview.tiers) || []) {
    const isRunning = running && running === t.tier;
    items.push(
      h(
        "li",
        { class: isRunning ? "running" : null },
        h("span", { class: "lab-tier-label" }, t.label || t.tier, isRunning ? h("span", { class: "lab-tag", text: trying ? "Try-on running" : "Running here" }) : null),
        h("span", { class: "lab-tier-feature", text: t.feature || "" }),
        ...M.tierMovingLines(t, motion.st, L.motion.state, at, motion.tl.pace).map((text) => h("span", { class: "lab-tier-feature moving", text })),
        t.moving_feature && !playing ? h("small", { text: "Its moving sentence shows while the steps play (Motion preview below the picture)." }) : null,
        t.note ? h("small", { text: t.note }) : null,
      ),
    );
  }
  const today = view === "showBox" ? preview && preview.show_box_today : preview && preview.today;
  if (today && (view === "today" || view === "showBox")) {
    const lines = [today.feature ? `As the iPad shows it: ${today.feature}` : null, today.note, M.positionsLine(today.positions)].filter(Boolean);
    if (lines.length) items.unshift(h("li", { class: "lab-today" }, lines.map((t) => h("span", { text: t }))));
  }
  if (!items.length) items.push(h("li", { class: "lab-muted", text: preview && preview.fromCatalog ? "Each tier's sentence shows once the server answers the preview." : "" }));
  ol.replaceChildren(...items);
}

// ---- the editor ------------------------------------------------------------------------------------------------------
const field = (label, input, slot, extra = null) =>
  h("div", { class: "lab-field", dataset: slot ? { slot } : {} }, h("label", { class: "lab-label" }, label, input), extra, h("p", { class: "lab-problem", dataset: { for: slot || "" } }));

function lockOf(id = L.scene) {
  const main = workingPreview(id).preview;
  return (main && main.t1_lock) || (sceneOf(id) && sceneOf(id).t1_lock) || null;
}

function rebuildEditor() {
  const ed = $("#lab-editor");
  if (!ed || !L.scene) return;
  const id = L.scene;
  const scene = sceneOf(id);
  const d = L.working[id];
  const undoBtn = h("button", { type: "button", class: "lab-btn small", title: "Undo edit (⌘Z / Ctrl-Z)", onclick: undo, text: "↶ Undo edit" });
  // client only (never a publish): renamed from "Revert to approved" so it can't be read as the show-level Revert
  const resetMenu = h(
    "details",
    { class: "lab-menu wide" },
    h("summary", { "aria-label": "Reset this scene's working draft", text: "Reset draft ▾" }),
    h(
      "div",
      { class: "lab-menu-items" },
      h("button", { type: "button", onclick: (e) => menuReset(e, "live"), text: publishLabelSim("to the show’s look") }),
      h("button", { type: "button", onclick: (e) => menuReset(e, "approved"), text: "to approved (git)" }),
    ),
  );
  const publishBtn = h("button", { type: "button", class: "lab-btn small publish", dataset: { publish: "editor" }, onclick: () => publishWorking(), text: M.PUBLISH });
  // D76: an option's design into its base's working draft (no request; the base's Publish publishes it)
  const promoteBtn = scene && scene.option_of ? h("button", { type: "button", class: "lab-btn small", dataset: { promote: "editor" }, title: M.PROMOTE_COPY.title(d.name, sceneName(scene.option_of)), onclick: promoteToBase, text: M.PROMOTE }) : null;
  const kinds = h(
    "div",
    { class: "lab-seg", role: "group", "aria-label": "Layout kind" },
    ["linear", "radial", "sweep"].map((k) =>
      h("button", { type: "button", class: "lab-seg-btn", dataset: { kind: k }, onclick: () => edit((x) => M.setLayoutKind(x, k)), text: k[0].toUpperCase() + k.slice(1) }),
    ),
  );
  ed.replaceChildren(...[
    h(
      "div",
      { class: "lab-editor-head" },
      h("div", {}, h("span", { class: "eyebrow", text: scene && scene.painted ? `PAINTED · ${scene.slot}` : scene && scene.stepped ? "MODULE LEVEL · STEPPED" : "MODULE LEVEL" }), h("h2", { id: "lab-editor-title", text: d.name })),
      h("div", { class: "lab-row" }, undoBtn, resetMenu, promoteBtn, publishBtn),
    ),
    h("p", { class: "lab-reality", text: M.REALITY_LINE }),
    h("p", { id: "lab-lock", class: "lab-lock", hidden: true }),
    h("p", { id: "lab-repaint-note", class: "lab-lock", hidden: true }),
    h(
      "section",
      { class: "lab-section", "aria-label": "Palette" },
      h("h3", { text: "Palette" }),
      h("div", { id: "lab-palette" }),
      h("button", { id: "lab-add", type: "button", class: "lab-btn small", onclick: () => edit((x) => M.addColour(x, (L.catalog.limits.palette || [2, 6])[1])), text: "+ Add colour" }),
      h("p", { id: "lab-hints", class: "lab-hint" }),
      h("p", { class: "lab-problem", dataset: { for: "palette" } }),
      h("p", { class: "lab-problem", dataset: { for: "solid" } }),
    ),
    h(
      "section",
      { class: "lab-section", "aria-label": "Layout" },
      h("h3", { text: "Layout" }),
      kinds,
      h("div", { id: "lab-layout-controls" }),
      h(
        "div",
        { class: "lab-row lab-switches" },
        h("label", { class: "lab-switch" }, h("input", { type: "checkbox", id: "lab-mirror", onchange: (e) => edit((x) => M.setLayout(x, { transform: e.target.checked ? "mirror" : "linear" })) }), "Mirror"),
        h("label", { class: "lab-switch" }, h("input", { type: "checkbox", id: "lab-invert", onchange: (e) => edit((x) => M.setLayout(x, { invert: e.target.checked })) }), "Invert"),
      ),
      h("p", { class: "lab-words" }, "Reads as: ", h("strong", { id: "lab-words" })),
      h("ul", { class: "lab-locked" }, h("li", { text: M.D63_LOCKS.cycles }), h("li", { text: M.D63_LOCKS.sample_at })),
      h("p", { id: "lab-warnings", class: "lab-warning" }),
      h("p", { class: "lab-problem", dataset: { for: "layout" } }),
    ),
    h(
      "section",
      { class: "lab-section", "aria-label": "Brightness" },
      h("h3", { text: "Brightness" }),
      h("input", { id: "lab-brightness", type: "range", min: 1, max: 100, step: 1, "aria-label": "Brightness level", oninput: (e) => edit((x) => M.setField(x, "brightness", Number(e.target.value)), { coalesce: true }) }),
      h("p", { id: "lab-brightness-line", class: "lab-muted" }),
      h("p", { class: "lab-problem", dataset: { for: "brightness" } }),
    ),
    h(
      "section",
      { class: "lab-section", "aria-label": "Client copy" },
      h("h3", { text: "Client copy" }),
      field("Name", h("input", { id: "lab-name", class: "lab-input", maxlength: L.catalog.limits.name || 40, oninput: (e) => edit((x) => M.setField(x, "name", e.target.value), { coalesce: true }) }), "name"),
      field("Mood", h("input", { id: "lab-mood", class: "lab-input", maxlength: L.catalog.limits.mood || 60, oninput: (e) => edit((x) => M.setField(x, "mood", e.target.value), { coalesce: true }) }), "mood"),
      field("Description", h("textarea", { id: "lab-description", class: "lab-input", rows: 3, maxlength: L.catalog.limits.description || 200, oninput: (e) => edit((x) => M.setField(x, "description", e.target.value), { coalesce: true }) }), "description", h("p", { id: "lab-copy-notes", class: "lab-hint" })),
      h("div", { class: "lab-copy" }, h("span", { class: "eyebrow", text: "MODULE-LEVEL SENTENCE (READ ONLY)" }), h("p", { id: "lab-copy-modules" }), h("small", { id: "lab-copy-modules-why" })),
      scene && scene.stepped ? h("div", { class: "lab-copy" }, h("span", { class: "eyebrow", text: "MOVING SENTENCE (READ ONLY)" }), h("p", { id: "lab-copy-stepped" }), h("small", { id: "lab-copy-stepped-why" })) : null,
      h("p", { class: "lab-problem", dataset: { for: "copy" } }),
    ),
    motionSection(scene, d),
  ].filter(Boolean));
  ed.dataset.paletteKey = "";
  syncEditor();
}

function renderLayoutControls(d, locked) {
  const box = $("#lab-layout-controls");
  const c = M.controlsFor(d.layout);
  const key = `${c.kind}|${locked}`;
  if (box.dataset.key !== key) {
    box.dataset.key = key;
    const pair = (id, label, range, numberStep, onValue) => {
      const slider = h("input", { type: "range", id: `${id}-r`, min: range.min, max: range.max, step: range.step, disabled: locked, "aria-label": label });
      const num = h("input", { type: "number", id: `${id}-n`, class: "lab-input num", min: range.min, max: range.max, step: numberStep, disabled: locked, "aria-label": `${label} (number)`, inputmode: "decimal" });
      slider.addEventListener("input", () => onValue(Number(slider.value)));
      num.addEventListener("change", () => {
        const n = Number(num.value);
        if (Number.isFinite(n)) onValue(Math.min(range.max, Math.max(range.min, n)));
      });
      return h("div", { class: "lab-pair" }, h("span", { class: "lab-label-text", text: label }), slider, num);
    };
    const parts = [];
    if (c.angle) parts.push(pair("lab-angle-deg", "Angle (°)", c.angle, 1, (v) => edit((x) => M.setLayout(x, { angle_deg: v }), { coalesce: true })));
    if (c.centre) {
      const setCentre = (axis, v) =>
        edit((x) => {
          const cur = Array.isArray(x.layout.centre_in) ? [...x.layout.centre_in] : [...M.SWEEP_DEFAULT_CENTRE];
          cur[axis] = v;
          return M.setLayout(x, { centre_in: cur, centres_in: undefined });
        }, { coalesce: true });
      parts.push(pair("lab-cx", "Centre across (in, 0–225)", c.centre.x, c.centre.numberStep, (v) => setCentre(0, v)));
      parts.push(pair("lab-cy", "Centre down (in, 0–120)", c.centre.y, c.centre.numberStep, (v) => setCentre(1, v)));
    }
    if (c.metric) {
      const sel = h("select", { id: "lab-metric", class: "lab-select", disabled: locked, onchange: (e) => edit((x) => M.setLayout(x, { metric: e.target.value })) }, h("option", { value: "inches", text: "Inches (a round glow)" }), h("option", { value: "canopy", text: "Canopy proportions (stretched to the canopy)" }));
      parts.push(h("label", { class: "lab-label" }, "Distance", sel));
      parts.push(h("p", { class: "lab-muted", text: "Radius: auto (to the farthest corner)." }));
    }
    if (c.start) parts.push(pair("lab-start", "Start angle (°)", c.start, 1, (v) => edit((x) => M.setLayout(x, { start_deg: v }), { coalesce: true })));
    box.replaceChildren(...parts);
  }
  const setPair = (id, v) => {
    for (const suffix of ["-r", "-n"]) {
      const el = document.getElementById(id + suffix);
      if (el && document.activeElement !== el) el.value = String(v);
    }
  };
  const L0 = d.layout || {};
  if (c.angle) setPair("lab-angle-deg", L0.angle_deg ?? 0);
  if (c.centre) {
    const centre = Array.isArray(L0.centre_in) ? L0.centre_in : Array.isArray(L0.centres_in) ? L0.centres_in[0] : [0, 0];
    setPair("lab-cx", centre[0]);
    setPair("lab-cy", centre[1]);
  }
  if (c.start) setPair("lab-start", L0.start_deg ?? 0);
  const metric = $("#lab-metric");
  if (metric) metric.value = L0.metric || "inches";
  // the layout kind's buttons only (the Motion section's direction and the pace toggle are segmented buttons too, and
  // stay editable under the T1 lock)
  for (const b of document.querySelectorAll(".lab-seg-btn[data-kind]")) {
    b.setAttribute("aria-pressed", String(b.dataset.kind === c.kind));
    b.disabled = locked;
  }
  $("#lab-mirror").checked = L0.transform === "mirror";
  $("#lab-invert").checked = L0.invert === true;
  $("#lab-mirror").disabled = locked;
  $("#lab-invert").disabled = locked;
}

/** A ⋯ menu item: close its menu, then edit. */
function menuAction(e, fn) {
  const menu = e.target.closest("details");
  if (menu) menu.open = false;
  edit(fn);
}

function menuReset(e, to) {
  const menu = e.target.closest("details");
  if (menu) menu.open = false;
  resetDraft(to);
}

function renderPalette(d, locked) {
  const box = $("#lab-palette");
  const ed = $("#lab-editor");
  const key = `${L.scene}|${d.palette.length}|${locked}`;
  const approved = L.approved[L.scene];
  const max = (L.catalog.limits.palette || [2, 6])[1];
  const min = (L.catalog.limits.palette || [2, 6])[0];
  if (ed.dataset.paletteKey !== key) {
    ed.dataset.paletteKey = key;
    box.replaceChildren(
      ...d.palette.map((c, i) => {
        const swatch = h("input", { type: "color", class: "lab-swatch", dataset: { i, role: "swatch" }, disabled: locked, "aria-label": `Colour ${i + 1}` });
        swatch.addEventListener("input", () => edit((x) => M.setColour(x, i, { hex: swatch.value.toUpperCase() }), { coalesce: true }));
        // a name stays editable under the T1 lock: a rename keeps the frames and the slot hash (no repaint)
        const name = h("input", { class: "lab-input", dataset: { i, role: "name" }, maxlength: 40, "aria-label": `Colour ${i + 1} name (client copy)` });
        name.addEventListener("input", () => edit((x) => M.setColour(x, i, { name: name.value }), { coalesce: true }));
        const hex = h("input", { class: "lab-input hex", dataset: { i, role: "hex" }, maxlength: 7, spellcheck: "false", autocapitalize: "characters", disabled: locked, "aria-label": `Colour ${i + 1} hex` });
        hex.addEventListener("input", () => {
          const v = hex.value.trim();
          const norm = v.startsWith("#") ? v : `#${v}`;
          if (/^#[0-9a-f]{6}$/i.test(norm)) {
            hex.classList.remove("bad");
            edit((x) => M.setColour(x, i, { hex: norm.toUpperCase() }), { coalesce: true });
          } else hex.classList.add("bad");
        });
        const solid = h("label", { class: "lab-radio" }, h("input", { type: "radio", name: "lab-solid", dataset: { i, role: "solid" }, onchange: () => edit((x) => M.setField(x, "solid", i)) }), "Solid");
        const menu = h(
          "details",
          { class: "lab-menu" },
          h("summary", { "aria-label": `More for colour ${i + 1}`, text: "⋯" }),
          h(
            "div",
            { class: "lab-menu-items" },
            h("button", { type: "button", disabled: locked || i === 0, onclick: (e) => menuAction(e, (x) => M.moveColour(x, i, -1)), text: "Move up" }),
            h("button", { type: "button", disabled: locked || i === d.palette.length - 1, onclick: (e) => menuAction(e, (x) => M.moveColour(x, i, 1)), text: "Move down" }),
            h("button", { type: "button", disabled: locked || d.palette.length <= min, onclick: (e) => menuAction(e, (x) => M.removeColour(x, i, min)), text: "Remove" }),
          ),
        );
        return h(
          "div",
          { class: "lab-colour", dataset: { slot: `palette.${i}` } },
          h("div", { class: "lab-colour-line" }, swatch, name, h("span", { class: "lab-colour-hint", dataset: { i, role: "hint" } })),
          h("div", { class: "lab-colour-line" }, hex, solid, menu),
          h("p", { class: "lab-approved-change", dataset: { i, role: "approved" } }),
          h("p", { class: "lab-problem", dataset: { for: `palette.${i}` } }),
        );
      }),
    );
  }
  d.palette.forEach((c, i) => {
    const q = (role) => box.querySelector(`[data-role="${role}"][data-i="${i}"]`);
    const set = (el, v) => {
      if (el && document.activeElement !== el) el.value = v;
    };
    set(q("swatch"), String(c.hex).toLowerCase());
    set(q("name"), c.name);
    const hexEl = q("hex");
    if (hexEl && document.activeElement !== hexEl) {
      hexEl.value = c.hex;
      hexEl.classList.remove("bad");
    }
    q("solid").checked = d.solid === i;
    const hint = M.colourHint(c.hex);
    q("hint").textContent = hint ? `△ ${hint}` : "";
    q("approved").textContent = M.changesApprovedColour(approved, d, i) ? "Changes an approved colour — needs the client's sign-off" : "";
  });
  const add = $("#lab-add");
  add.disabled = locked || d.palette.length >= max;
  const hints = d.palette.filter((c) => M.colourHint(c.hex)).map((c) => c.name);
  $("#lab-hints").textContent = hints.length ? `Saturated orange, yellow and purple render weakly on these pucks: ${hints.join(", ")} — ${M.HINT}.` : "";
}

function syncEditor() {
  const ed = $("#lab-editor");
  if (!ed || !L.scene || !$("#lab-palette")) return;
  const id = L.scene;
  const d = L.working[id];
  const lock = lockOf(id);
  const locked = !!(lock && lock.locked);
  const lockEl = $("#lab-lock");
  const scene = sceneOf(id);
  if (locked) {
    lockEl.hidden = false;
    lockEl.className = "lab-lock on";
    // the server's sentence (the hand-painted "app" slot lock, D68; the T1 lock on an older server)
    const why = lock.sentence || `${scene ? scene.name : id}: ${M.T1_LOCKED}.`;
    lockEl.textContent = `${why} The colours’ hexes, their order and count, and the layout are read-only; colour names, brightness, solid, copy and stepped timing stay editable.`;
  } else if (lock && lock.sentence && scene && scene.painted) {
    lockEl.hidden = false;
    lockEl.className = "lab-lock";
    lockEl.textContent = lock.sentence;
  } else lockEl.hidden = true;
  // a painted scene's slot and what a publish of a new painted design does to it (RepaintInfo, from the server)
  const repaintEl = $("#lab-repaint-note");
  const repaintText = scene && scene.painted ? M.repaintNote((lock && lock.repaint) || scene.repaint) : "";
  repaintEl.textContent = repaintText;
  repaintEl.hidden = !repaintText || repaintText === lockEl.textContent;
  $("#lab-editor-title").textContent = d.name || (scene && scene.name) || id;
  renderPalette(d, locked);
  renderLayoutControls(d, locked);
  const bright = $("#lab-brightness");
  if (document.activeElement !== bright) bright.value = String(d.brightness);
  $("#lab-brightness-line").textContent = M.brightnessLine(d.brightness);
  for (const [k, sel] of [["name", "#lab-name"], ["mood", "#lab-mood"], ["description", "#lab-description"]]) {
    const el = $(sel);
    if (el && document.activeElement !== el) el.value = d[k] || "";
  }
  const { preview } = workingPreview(id);
  $("#lab-words").textContent = M.layoutWords(preview);
  const sweepOnCentre = d.layout && d.layout.kind === "sweep" && M.onModuleCentre(d.layout.centre_in);
  $("#lab-warnings").textContent = [
    ...M.warningLines(preview),
    sweepOnCentre ? "A sweep can't be centred on a module's centre while each strand shows the design at its own centre (D63): move it off." : null,
  ]
    .filter(Boolean)
    .join(" ");
  $("#lab-copy-notes").textContent = M.copyNotes(preview).join(" ");
  const copy = (preview && preview.copy) || {};
  const approvedCopy = (scene && scene.copy) || {};
  $("#lab-copy-modules").textContent = (copy.modules && copy.modules.sentence) || approvedCopy.modules || "";
  $("#lab-copy-modules-why").textContent = copy.modules ? M.copyLine(copy.modules) : "Approved wording";
  if ($("#lab-copy-stepped")) {
    $("#lab-copy-stepped").textContent = (copy.stepped && copy.stepped.sentence) || approvedCopy.stepped || "";
    $("#lab-copy-stepped-why").textContent = copy.stepped ? M.copyLine(copy.stepped) : "Approved wording";
  }
  syncMotionEditor(scene, d, preview);
  // problems: each at its field; `draft` ones go to the alert
  const slots = M.problemSlots(L.problems[id] || []);
  for (const p of ed.querySelectorAll(".lab-problem")) {
    const msgs = slots[p.dataset.for] || [];
    p.textContent = msgs.join(" ");
    p.hidden = !msgs.length;
    const host = p.closest("[data-slot]");
    if (host) host.classList.toggle("has-problem", msgs.length > 0);
  }
}

// ---- motion (Slice C, PLAN §7) --------------------------------------------------------------------------------------
// Screen only. The stepped playback paints the SERVER's compiled steps (PreviewJSON.stepped) at the server's pace (the
// ProgramRunner's step on nine modules, or on the show box's kits); the ripple preview lands the server's rings
// (PreviewJSON.rings) at a stagger chosen here. Nothing in this block sends anything: on the real lights stepped looks
// stay off until Gate R passes and Alon sets motion.stepped.live_enabled, and the ripple stays at
// kiosk.ripple_stagger_s: 0 until he signs it off (D64). The Lab shows both and has no control for either.

const perfNow = () => performance.now();

/** A slider and a number input for one value (the number input clamps to the slider's bounds). */
function rangePair(id, label, range, numberStep, onValue) {
  const slider = h("input", { type: "range", id: `${id}-r`, min: range.min, max: range.max, step: range.step, "aria-label": label });
  const num = h("input", { type: "number", id: `${id}-n`, class: "lab-input num", min: range.min, max: range.max, step: numberStep, "aria-label": `${label} (number)`, inputmode: "decimal" });
  slider.addEventListener("input", () => onValue(Number(slider.value)));
  num.addEventListener("change", () => {
    const n = Number(num.value);
    if (Number.isFinite(n) && num.value !== "") onValue(Math.min(Number(num.max), Math.max(Number(num.min), n)));
  });
  return h("div", { class: "lab-pair" }, h("span", { class: "lab-label-text", text: label }), slider, num);
}

function setPairValue(id, v) {
  for (const suffix of ["-r", "-n"]) {
    const el = document.getElementById(id + suffix);
    if (el && document.activeElement !== el) el.value = String(v);
  }
}

/** The editor's Motion section: the stepped timing for scenes with a stepped rung (read-only with the reason
 * otherwise), the live line, and the greyed effects card. */
function motionSection(scene, d) {
  const limits = L.catalog.limits;
  const c = M.steppedControls(scene, d, limits);
  const set = (patch, opts) => edit((x) => M.setStepped(x, patch, limits), opts);
  const parts = [h("h3", { text: "Motion" })];
  if (!c.editable) parts.push(h("p", { class: "lab-readonly", text: c.reason }));
  else {
    parts.push(
      h("p", { class: "lab-muted" }, `${M.MOTION_COPY.design} Reads as: `, h("strong", { id: "lab-stepped-words" })),
      h("span", { class: "lab-label-text", text: "Direction" }),
      h(
        "div",
        { id: "lab-direction", class: "lab-seg", role: "group", "aria-label": "Direction" },
        ["forward", "reverse"].map((k) =>
          h("button", { type: "button", class: "lab-seg-btn", dataset: { dir: k }, onclick: () => set({ direction: k }), text: k === "forward" ? "Forward" : "Reverse" }),
        ),
      ),
      h("p", { class: "lab-muted lab-tight", text: M.MOTION_COPY.direction }),
      rangePair("lab-steps", `Steps per cycle (${c.steps.min}–${c.steps.max})`, c.steps, 1, (v) => set({ steps_per_cycle: v }, { coalesce: true })),
      h("p", { class: "lab-muted lab-tight", text: M.MOTION_COPY.cycle }),
      rangePair("lab-step-s", `Step time (s, ${M.fmtS(c.step_s.min)}–${M.fmtS(c.step_s.max)}, in 0.5 s steps)`, c.step_s, M.STEP_S_STEP, (v) => set({ step_s: v }, { coalesce: true })),
      rangePair("lab-run-s", "Run length (s)", c.run_s, M.STEP_S_STEP, (v) => set({ run_s: v }, { coalesce: true })),
      h("p", { class: "lab-muted lab-tight", text: c.runLimit }),
      h("p", { id: "lab-stepped-run", class: "lab-words" }),
    );
  }
  if (scene && scene.stepped) parts.push(h("p", { id: "lab-stepped-when", class: "lab-muted lab-tight", hidden: true }), h("p", { id: "lab-stepped-live", class: "lab-live-line" }));
  parts.push(
    h("p", { class: "lab-problem", dataset: { for: "stepped" } }),
    h("div", { class: "lab-effects", role: "note", "aria-disabled": "true" }, h("strong", { text: M.effectsLine(L.catalog.live) }), h("small", { text: M.MOTION_COPY.effectsNote })),
  );
  return h("section", { class: "lab-section", "aria-label": "Motion", dataset: { slot: "stepped" } }, ...parts);
}

function syncMotionEditor(scene, d, preview) {
  const st = M.steppedOf(preview);
  const c = M.steppedControls(scene, d, L.catalog.limits);
  if (c.editable && document.getElementById("lab-steps-r")) {
    setPairValue("lab-steps", c.steps.value);
    setPairValue("lab-step-s", c.step_s.value);
    for (const suffix of ["-r", "-n"]) {
      const el = document.getElementById(`lab-run-s${suffix}`);
      if (el) el.min = String(c.run_s.min); // a run is never shorter than one step
    }
    setPairValue("lab-run-s", c.run_s.value);
    for (const b of document.querySelectorAll("#lab-direction .lab-seg-btn")) b.setAttribute("aria-pressed", String(b.dataset.dir === c.direction));
    $("#lab-stepped-words").textContent = M.layoutWords(preview);
    $("#lab-stepped-run").textContent = M.steppedRunLine(d.stepped, st);
  }
  const when = $("#lab-stepped-when");
  if (when) {
    when.textContent = st.error ? "" : M.sentence(st.stepsWhen);
    when.hidden = !when.textContent;
  }
  const live = $("#lab-stepped-live");
  if (live) live.textContent = M.sentence(M.steppedLiveLine(L.catalog.live, st));
}

/** The stage column's motion card: play the steps (pace, fast preview, counter, the moving sentence while it plays)
 * and the ripple preview. */
function motionPanel() {
  return h(
    "section",
    { id: "lab-motion", class: "lab-motion", "aria-label": "Motion preview (screen only)" },
    h("div", { class: "lab-motion-head" }, h("span", { class: "eyebrow light", text: "MOTION PREVIEW · SCREEN ONLY" })),
    h(
      "div",
      { id: "lab-motion-stepped", class: "lab-motion-block" },
      h("h3", { class: "lab-motion-title", text: "Stepped waves" }),
      h("p", { class: "lab-motion-how", text: "“Play the steps” under the picture plays the server’s compiled steps at the Conductor’s real pace." }),
      h("p", { id: "lab-steps-when", class: "lab-motion-note", hidden: true }),
      h("p", { id: "lab-step-counter", class: "lab-step-counter", role: "status", "aria-live": "polite" }),
      h("span", { class: "lab-label-text", text: "Pace (the Conductor's own step for that many modules on one account)" }),
      h("div", { id: "lab-pace", class: "lab-seg dark", role: "group", "aria-label": "Pace" }),
      h("label", { class: "lab-switch" }, h("input", { type: "checkbox", id: "lab-fast" }), `Fast preview (${M.FAST_FACTOR}× — sped up, not the real pace)`),
      h("p", { id: "lab-motion-note", class: "lab-motion-note" }),
      h("p", { id: "lab-motion-live", class: "lab-motion-live" }),
    ),
    h("p", { id: "lab-motion-none", class: "lab-motion-reason", hidden: true }),
    h(
      "div",
      { id: "lab-ripple", class: "lab-motion-block" },
      h("h3", { class: "lab-motion-title", text: "Ripple" }),
      h("p", { id: "lab-ripple-label", class: "lab-ripple-label" }),
      h(
        "div",
        { class: "lab-pair" },
        h("label", { class: "lab-label-text", for: "lab-ripple-r", text: `Stagger between rings (s, 0–${M.fmtS(M.RIPPLE_STAGGER_MAX_S)})` }),
        h("input", { id: "lab-ripple-r", type: "range", min: 0, max: M.RIPPLE_STAGGER_MAX_S, step: M.RIPPLE_STAGGER_STEP_S }),
        h("output", { id: "lab-ripple-v", class: "lab-ripple-v", for: "lab-ripple-r" }),
      ),
      h("p", { id: "lab-ripple-line", class: "lab-motion-note" }),
      h("p", { class: "lab-motion-how", text: "“Play the ripple” under the picture lands the server’s rings at this stagger, on this screen only." }),
    ),
  );
}

function wireMotion() {
  $("#lab-play").addEventListener("click", motionToggle);
  $("#lab-motion-stop").addEventListener("click", () => motionStop());
  $("#lab-fast").addEventListener("change", (e) => setFast(e.target.checked));
  const r = $("#lab-ripple-r");
  r.value = String(L.motion.ripple.stagger);
  r.addEventListener("input", () => {
    L.motion.ripple.stagger = M.clampStagger(r.value);
    renderMotion();
  });
  $("#lab-ripple-play").addEventListener("click", playRipple);
}

function motionElapsed() {
  const m = L.motion;
  if (m.state !== "playing") return m.elapsed;
  return m.elapsed + ((perfNow() - m.since) / 1000) * (m.fast ? M.FAST_FACTOR : 1);
}

function rippleElapsed() {
  const r = L.motion.ripple;
  return r.since === null ? null : (perfNow() - r.since) / 1000;
}

/** The previews on screen (A/B: both sides; Present: its sides), from the cache only (no request from the clock). */
function motionSidePreviews() {
  const cached = (entry, sceneId) => {
    const draft = draftFor(entry, sceneId);
    return (draft && L.cache.get(M.previewKey(draft))) || null;
  };
  const P = L.present;
  if (P) return (P.ab ? [M.APPROVED, P.version] : [P.version]).map((e) => cached(e, P.scene));
  return sideEntries().map(([, entry]) => (entry === M.WORKING ? workingPreview().preview : cached(entry, L.scene)));
}

/** The preview the motion controls play: Present's shown scene and version while presenting, else the working draft's. */
function motionPreview() {
  const P = L.present;
  if (P) {
    const draft = draftFor(P.version, P.scene);
    return (draft && L.cache.get(M.previewKey(draft))) || null;
  }
  return workingPreview().preview;
}

function motionTimeline(preview = motionPreview()) {
  const st = M.steppedOf(preview);
  return { st, tl: M.stepTimeline(st, L.motion.pace) };
}

/** What a stage paints while the motion preview runs: {view, paint (viewPainter's options), notes, label, moving}, or
 * null when it paints its own view. */
function motionPaint(preview, view) {
  const m = L.motion;
  const re = rippleElapsed();
  if (re !== null) {
    const schedule = M.rippleSchedule(preview && preview.rings, m.ripple.stagger);
    if (!schedule) return { view, paint: {}, notes: [M.MOTION_COPY.noRings], label: "", moving: "" };
    const landed = schedule.rings.filter((r) => r.at <= re + 1e-6).length;
    return {
      view,
      paint: { landed: M.rippleLanded(schedule, re) },
      notes: [`Ripple preview, screen only: ring ${landed} of ${schedule.rings.length} has landed.`],
      label: "ripple preview",
      moving: "",
    };
  }
  if (m.state === "idle") return null;
  const { st, tl } = motionTimeline(preview);
  if (tl.error) return { view, paint: {}, notes: [tl.error], label: "", moving: "" };
  const at = M.stepAt(tl, motionElapsed());
  const where = M.stepPositions(preview, tl.pace);
  return {
    view: "stepped",
    paint: { step: { colours: st.steps[at.index].colours, positions: where.positions } },
    notes: [m.fast ? `${M.MOTION_COPY.fast}.` : "", where.note],
    label: M.stepCounter(tl, at, { state: m.state, fast: m.fast }),
    moving: M.movingLine(st, m.state, at, tl.pace),
  };
}

function motionPlay() {
  const m = L.motion;
  const { tl } = motionTimeline();
  if (tl.error) {
    renderMotion();
    return;
  }
  m.ripple.since = null;
  if (m.state !== "paused") m.elapsed = 0; // idle or done: from the start
  m.state = "playing";
  m.since = perfNow();
  startMotionClock();
  renderMotionAll();
  showStage();
}

function motionPause() {
  const m = L.motion;
  if (m.state !== "playing") return;
  m.elapsed = motionElapsed();
  m.state = "paused";
  stopMotionClock();
  renderMotionAll();
}

function motionToggle() {
  if (L.motion.state === "playing") motionPause();
  else motionPlay();
}

/** Back to the stage's own view (a new scene, Present opening or closing, the Lab closing, or Stop). */
function motionStop({ render = true } = {}) {
  const m = L.motion;
  const was = m.state !== "idle" || m.ripple.since !== null;
  m.state = "idle";
  m.elapsed = 0;
  m.ripple.since = null;
  stopMotionClock();
  if (was && render) renderMotionAll();
}

function setPace(key) {
  const m = L.motion;
  if (m.pace === key) return;
  m.pace = key;
  if (m.state === "playing") {
    m.elapsed = 0; // a new pace is a new run: from the start
    m.since = perfNow();
  } else if (m.state !== "idle") {
    m.state = "idle";
    m.elapsed = 0;
  }
  renderMotionAll();
}

function setFast(on) {
  const m = L.motion;
  m.elapsed = motionElapsed(); // keep the place in the run
  m.since = perfNow();
  m.fast = !!on;
  renderMotionAll();
}

function playRipple() {
  const m = L.motion;
  if (m.state !== "idle") motionStop({ render: false });
  m.ripple.since = perfNow();
  startMotionClock();
  renderMotionAll();
  showStage();
}

function startMotionClock() {
  const m = L.motion;
  m.key = "";
  if (!m.clock) m.clock = setInterval(motionTick, 100);
}

function stopMotionClock() {
  clearInterval(L.motion.clock);
  L.motion.clock = null;
}

/** Repaint only when a step or a ring changes. */
function motionTick() {
  const m = L.motion;
  const re = rippleElapsed();
  let key;
  if (re !== null) {
    const preview = motionPreview();
    const schedule = M.rippleSchedule(preview && preview.rings, m.ripple.stagger);
    if (!schedule || re > schedule.spanS + M.RIPPLE_HOLD_S) {
      m.ripple.since = null;
      stopMotionClock();
      renderMotionAll();
      return;
    }
    key = `ripple|${M.rippleLanded(schedule, re).size}`;
  } else if (m.state === "playing") {
    const { tl } = motionTimeline();
    if (tl.error) {
      motionStop();
      return;
    }
    // A/B: every side on screen runs its own timeline; the run is over when the longest one is, so no side is left
    // frozen on a mid-run step (each lands on its own hold, as the Conductor sends it)
    const runs = M.runsAt([tl, ...motionSidePreviews().map((p) => motionTimeline(p).tl)], motionElapsed());
    if (runs.done) {
      m.state = "done";
      m.elapsed = runs.totalS;
      stopMotionClock();
      renderMotionAll();
      return;
    }
    key = `step|${runs.key}`;
  } else {
    stopMotionClock();
    return;
  }
  if (key !== m.key) {
    m.key = key;
    renderMotionAll();
  }
}

/** A play started from the Lab's own page: bring the picture it moves into view (the stage column scrolls). */
function showStage() {
  const stages = !L.present && $("#lab-stages");
  if (stages && stages.scrollIntoView) stages.scrollIntoView({ block: "nearest" });
}

function renderMotionAll() {
  if (!L.built) return;
  if (L.present) renderPresent();
  else renderStage();
  renderMotion();
}

function renderMotion() {
  const box = $("#lab-motion");
  if (!box || !L.scene || !L.catalog) return;
  const m = L.motion;
  const scene = sceneOf(L.scene);
  const preview = workingPreview().preview;
  const stepped = !!(scene && scene.stepped);
  $("#lab-motion-stepped").hidden = !stepped;
  const none = $("#lab-motion-none");
  none.hidden = stepped;
  none.textContent = stepped ? "" : M.steppedControls(scene, L.working[L.scene], L.catalog.limits).reason;
  const play = $("#lab-play");
  play.hidden = !stepped;
  $("#lab-motion-stop").hidden = !stepped || (m.state === "idle" && m.ripple.since === null);
  const stageMoving = $("#lab-stage-moving");
  stageMoving.hidden = true;
  if (stepped) {
    const st = M.steppedOf(preview);
    const tl = M.stepTimeline(st, m.pace);
    const at = m.state === "idle" ? null : M.stepAt(tl, motionElapsed());
    play.disabled = !!tl.error;
    play.title = tl.error || "";
    play.textContent = m.state === "playing" ? "❚❚ Pause" : m.state === "paused" ? "▶ Resume" : m.state === "done" ? "▶ Play again" : "▶ Play the steps";
    play.setAttribute("aria-pressed", String(m.state === "playing"));
    $("#lab-step-counter").textContent = M.stepCounter(tl, at, { state: m.state, fast: m.fast });
    const movingNow = M.movingLine(st, m.state, at, tl.pace);
    stageMoving.textContent = movingNow ? `While it moves, the iPad says: ${movingNow}` : "";
    stageMoving.hidden = !movingNow;
    const options = M.paceOptions(st);
    const pace = $("#lab-pace");
    const key = options.map((o) => `${o.key}:${o.label}`).join("|");
    if (pace.dataset.key !== key) {
      pace.dataset.key = key;
      pace.replaceChildren(...options.map((o) => h("button", { type: "button", class: "lab-seg-btn", dataset: { pace: o.key }, onclick: () => setPace(o.key), text: o.label })));
    }
    for (const b of pace.querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.pace === (tl.pace || m.pace)));
    pace.hidden = !options.length;
    pace.previousElementSibling.hidden = !options.length;
    $("#lab-fast").checked = m.fast;
    $("#lab-fast").disabled = !!tl.error;
    $("#lab-motion-note").textContent = m.fast ? `${M.MOTION_COPY.fast}.` : "";
    $("#lab-motion-live").textContent = M.sentence(M.steppedLiveLine(L.catalog.live, st));
    const when = $("#lab-steps-when");
    when.textContent = st.error ? "" : M.sentence(st.stepsWhen);
    when.hidden = !when.textContent;
  }
  const schedule = M.rippleSchedule(preview && preview.rings, m.ripple.stagger);
  $("#lab-ripple-label").textContent = M.rippleLabel(L.catalog.live);
  const r = $("#lab-ripple-r");
  if (document.activeElement !== r) r.value = String(m.ripple.stagger);
  $("#lab-ripple-v").textContent = `${M.fmtS(m.ripple.stagger)} s`;
  const ripple = preview && preview.ripple;
  const liveStagger = ripple && ripple.live_stagger_s !== undefined ? ripple.live_stagger_s : L.catalog.live && L.catalog.live.ripple_stagger_s;
  $("#lab-ripple-line").textContent = M.rippleLine(schedule, liveStagger);
  const rp = $("#lab-ripple-play");
  rp.disabled = !schedule;
  rp.textContent = m.ripple.since !== null ? "Playing the ripple…" : "Play the ripple";
}

// ---- the whole panel ----------------------------------------------------------------------------------------------------
function renderAll() {
  if (!L.built) return;
  renderShowBar();
  renderSceneList();
  renderIpadStrip();
  renderVersionsBar();
  renderStage();
  syncEditor();
  renderMotion();
  renderTrial();
  if (L.present) renderPresent();
}

// ---- the live show (docs/live-lab/PLAN.md §3) -------------------------------------------------------------------------
// GET /api/lab/live every 5 s while the Lab is open and visible (every 1 s while a slot repaint runs), and on focus.
// Publish, Undo and Revert POST /api/lab/publish: without `confirm` the server answers 409 with its sentence, the cost,
// the changes and the repaint; "Publish now" sends that sentence back with the live id the dialog was opened against
// (`expected_live`), so a look someone else published meanwhile is a conflict, never silently replaced.

function showBarEl() {
  return h(
    "section",
    { id: "lab-show", class: "lab-show", "aria-label": "What the show runs" },
    h(
      "div",
      { class: "lab-show-main" },
      h("div", { id: "lab-show-state", class: "lab-show-state", role: "status", "aria-live": "polite" }, h("p", { id: "lab-show-text", class: "lab-show-text" }), h("ul", { id: "lab-show-lines", class: "lab-show-lines" })),
      h("span", { id: "lab-show-chip", class: "lab-show-chip" }),
      h(
        "div",
        { class: "lab-show-actions" },
        h("button", { id: "lab-show-undo", class: "lab-btn small", type: "button", hidden: true, text: M.UNDO_PUBLISH }),
        h("button", { id: "lab-show-revert", class: "lab-btn small danger", type: "button", hidden: true, text: M.REVERT }),
        h("button", { id: "lab-show-history", class: "lab-btn small", type: "button", hidden: true, "aria-expanded": "false", "aria-controls": "lab-history", text: "History" }),
      ),
    ),
    h("details", { id: "lab-repaint-rows", class: "lab-repaint-rows", hidden: true }, h("summary", { text: "Each module" }), h("ul", {})),
    h("div", { id: "lab-history", class: "lab-history", hidden: true }, h("h3", { text: "Publish history" }), h("ol", { id: "lab-history-ol", class: "lab-history-list" })),
  );
}

function publishDialogEl() {
  return h(
    "dialog",
    { id: "lab-publish", class: "lab-dialog lab-publish-dialog", "aria-labelledby": "lab-publish-title" },
    h("div", { class: "lab-dialog-head" }, h("h2", { id: "lab-publish-title", text: M.PUBLISH }), h("button", { id: "lab-publish-close", type: "button", class: "lab-btn small", text: "Close" })),
    h(
      "div",
      { class: "lab-publish-scroll" },
      h("p", { id: "lab-publish-conflict", class: "lab-publish-conflict", hidden: true }),
      h("p", { id: "lab-publish-sentence", class: "lab-publish-sentence" }),
      h("p", { id: "lab-publish-cost", class: "lab-publish-cost" }),
      h("ul", { id: "lab-publish-changes", class: "lab-publish-list" }),
      h("ul", { id: "lab-publish-repaint", class: "lab-publish-list" }),
      h("ul", { id: "lab-publish-warnings", class: "lab-publish-list warn" }),
      h("ul", { id: "lab-publish-notes", class: "lab-publish-list note" }),
      h("label", { id: "lab-publish-show-row", class: "lab-switch", hidden: true }, h("input", { type: "checkbox", id: "lab-publish-show", checked: true }), h("span", { id: "lab-publish-show-label" })),
      h(
        "label",
        { class: "lab-label lab-publish-who" },
        "Your name, for the show’s history",
        h("input", { id: "lab-publish-who", class: "lab-input", maxlength: M.WHO_MAX, autocomplete: "name", placeholder: "e.g. Alon" }),
      ),
      h("p", { id: "lab-publish-wait", class: "lab-publish-wait", role: "status", "aria-live": "polite" }),
    ),
    h(
      "div",
      { class: "lab-row lab-publish-actions" },
      h("button", { id: "lab-publish-go", class: "lab-btn publish", type: "button", text: "Publish now" }),
      h("button", { id: "lab-publish-cancel", class: "lab-btn", type: "button", text: "Cancel" }),
    ),
  );
}

function wireLive() {
  $("#lab-show-undo").addEventListener("click", () => askPublish("previous", "previous", (L.live && L.live.previous && L.live.previous.name) || ""));
  $("#lab-show-revert").addEventListener("click", () => askPublish(M.APPROVED, "approved", "the approved looks"));
  $("#lab-show-history").addEventListener("click", () => {
    L.historyOpen = !L.historyOpen;
    renderShowBar();
  });
  $("#lab-publish-go").addEventListener("click", confirmPublish);
  // the box is part of what ops confirms: the server's sentence, notes and cost follow it, so a toggle asks again
  $("#lab-publish-show").addEventListener("change", (e) => {
    const d = L.publish.dialog;
    if (d) askPublish(d.version, d.kind, d.name, { show: !!e.target.checked, reask: true });
  });
  $("#lab-publish-cancel").addEventListener("click", () => closePublishDialog());
  $("#lab-publish-close").addEventListener("click", () => closePublishDialog());
  $("#lab-publish").addEventListener("cancel", () => closePublishDialog({ fromEvent: true })); // Esc
  $("#lab-download").addEventListener("click", () => L.selected && downloadVersion(L.selected));
  $("#lab-open-file").addEventListener("click", () => $("#lab-file").click());
  $("#lab-file").addEventListener("change", openVersionFile);
}

/** GET /api/lab/live. Never throws: a missing route (an older server) or an unreachable Conductor is a state. */
async function refreshLive({ render = true } = {}) {
  const before = L.live;
  const beforeKey = L.liveKey;
  try {
    const live = await api(`${API}/live`);
    L.liveApi = "ready";
    applyLive(live, { before });
  } catch (err) {
    L.liveApi = M.liveApiFromError(err);
    if (L.liveApi === "error") setAlert("live", M.alertFor(err));
  }
  L.liveReadAt = Date.now();
  if (L.liveApi !== "error") setAlert("live", null);
  L.liveKey = `${L.liveApi}|${JSON.stringify(L.live)}`;
  if (!render || !L.built) return;
  // the whole panel only when something changed (a poll every 5 s must not rebuild the lists under a finger)
  if (L.liveKey !== beforeKey) renderAll();
  else renderShowBar();
}

/** A new LiveJSON (a poll, or a publish's answer): when the live publication changed, say so, read the scenes' live
 * looks again (the badges compare with them) and what the lights show today. Working drafts are never touched. */
function applyLive(live, { before = L.live, ownPublish = false } = {}) {
  if (!live || typeof live !== "object") return;
  L.live = live;
  L.liveApi = "ready";
  L.liveReadAt = Date.now();
  const changed = !!before && M.expectedLive(before) !== M.expectedLive(live);
  if (!changed) return;
  if (!ownPublish) {
    const notice = M.liveNotice(before, live);
    if (notice) setNotice(notice);
  }
  refreshLiveLooks();
  invalidateToday();
}

/** The catalog's per-scene live looks (live_look, live_source, repaint) after the show's look changed. */
async function refreshLiveLooks() {
  try {
    const catalog = await api(`${API}/catalog`);
    if (!L.catalog || catalog.config_sha !== L.catalog.config_sha) return; // another config: the reload alert says so
    const byId = Object.fromEntries((catalog.scenes || []).map((s) => [s.id, s]));
    for (const s of L.catalog.scenes) {
      const next = byId[s.id];
      if (!next) continue;
      for (const k of ["live_look", "live_source", "repaint", "t1_lock"]) if (k in next) s[k] = next[k];
    }
    if ("show_looks" in catalog) L.catalog.show_looks = catalog.show_looks;
    setLiveLooks(L.catalog);
    prefetchApproved();
    renderAll();
  } catch {
    // the next change (or a reload) reads them again; the badges keep the last known looks meanwhile
  }
}

function liveDelay() {
  const r = L.live && L.live.repaint;
  return r && (r.running || (r.queued_until !== null && r.queued_until !== undefined && !r.finished_at)) ? M.LIVE_REPAINT_POLL_MS : M.LIVE_POLL_MS;
}

function scheduleLivePoll() {
  clearTimeout(L.livePoll);
  if (!L.liveWatching || L.liveApi === "missing") return; // an older server: a reload finds publishing once it's there
  L.livePoll = setTimeout(async () => {
    if (!document.hidden) await refreshLive();
    scheduleLivePoll();
  }, liveDelay());
}

function onVisible() {
  if (!L.liveWatching || document.hidden) return;
  refreshLive().then(scheduleLivePoll);
}

/** `fresh`: read now (the Lab reopened); boot has just read it. */
function startLiveWatch({ fresh = true } = {}) {
  if (L.liveWatching) return;
  L.liveWatching = true;
  document.addEventListener("visibilitychange", onVisible);
  window.addEventListener("focus", onVisible);
  if (fresh) refreshLive().then(scheduleLivePoll);
  else scheduleLivePoll();
}

function stopLiveWatch() {
  L.liveWatching = false;
  clearTimeout(L.livePoll);
  document.removeEventListener("visibilitychange", onVisible);
  window.removeEventListener("focus", onVisible);
}

function renderShowBar() {
  const box = $("#lab-show");
  if (!box) return;
  const bar = M.showBar(L.live, L.catalog, { api: L.liveApi, host: location.host, nowMs: Date.now(), readAtMs: L.liveReadAt });
  box.dataset.tone = bar.tone;
  const text = $("#lab-show-text");
  if (text.textContent !== bar.text) text.textContent = bar.text;
  const lines = $("#lab-show-lines");
  const key = JSON.stringify(bar.lines);
  if (lines.dataset.key !== key) {
    lines.dataset.key = key;
    lines.replaceChildren(...bar.lines.map((l) => h("li", { class: `lab-show-line ${l.tone}`, text: l.text })));
  }
  lines.hidden = !bar.lines.length;
  // "Live hardware" here; on a simulator the line itself and the header's Simulator badge say so
  const chip = $("#lab-show-chip");
  chip.textContent = bar.chip;
  chip.hidden = bar.chip === "Simulator";
  const busy = L.publish.busy;
  const undo = $("#lab-show-undo");
  undo.hidden = !bar.actions.undo;
  if (bar.actions.undo) undo.title = bar.actions.undo.title;
  undo.disabled = busy;
  const revert = $("#lab-show-revert");
  revert.hidden = !bar.actions.revert;
  if (bar.actions.revert) revert.textContent = bar.actions.revert.label;
  revert.disabled = busy;
  const hist = $("#lab-show-history");
  hist.hidden = !bar.actions.history;
  if (bar.actions.history) hist.textContent = bar.actions.history.label;
  hist.setAttribute("aria-expanded", String(L.historyOpen && !!bar.actions.history));
  // the repaint's per-module list (B2), on tap
  const rows = $("#lab-repaint-rows");
  const progress = bar.repaint;
  rows.hidden = !(progress && progress.rows.length);
  if (progress && progress.rows.length) {
    const ul = $("ul", rows);
    const rkey = JSON.stringify(progress.rows);
    if (ul.dataset.key !== rkey) {
      ul.dataset.key = rkey;
      ul.replaceChildren(...progress.rows.map((r) => h("li", { class: `lab-repaint-row ${r.status}`, text: r.text })));
    }
  }
  renderHistory(!!bar.actions.history);
  const eyebrow = $("#lab-eyebrow");
  if (eyebrow) eyebrow.textContent = M.labEyebrow(L.tryon.api, trial(), L.liveApi === "ready");
}

function renderHistory(available) {
  const box = $("#lab-history");
  const open = L.historyOpen && available;
  box.hidden = !open;
  if (!open) return;
  const names = Object.fromEntries(order().map((id) => [id, (sceneOf(id) && sceneOf(id).name) || id]));
  const rows = M.historyRows(L.live && L.live.history, { scenes: names });
  const busy = L.publish.busy;
  $("#lab-history-ol").replaceChildren(
    ...rows.map((r) =>
      h(
        "li",
        { class: r.live ? "lab-history-row live" : "lab-history-row" },
        h(
          "div",
          { class: "lab-history-text" },
          h("strong", { text: r.name }),
          h("small", { text: [r.when, r.by ? `by ${r.by}` : "", r.scenes].filter(Boolean).join(" · ") }),
          r.repaint ? h("small", { text: r.repaint }) : null,
          r.gone ? h("small", { class: "lab-muted", text: r.gone }) : null,
        ),
        h("span", { class: `lab-outcome ${r.outcome.replace(/\s/g, "-")}`, text: r.outcomeText }),
        h(
          "div",
          { class: "lab-version-actions" },
          r.canOpen ? h("button", { type: "button", class: "lab-btn small", onclick: () => openVersion(r.version), text: "Open in editor" }) : null,
          r.canRepublish ? h("button", { type: "button", class: "lab-btn small", disabled: busy, onclick: () => askPublish(r.version, r.kind === "approved" ? "approved" : "version", r.name), text: "Publish again" }) : null,
          r.canExport ? h("button", { type: "button", class: "lab-btn small", onclick: () => openExport(r.version), text: "Export to git" }) : null,
        ),
      ),
    ),
  );
}

/** The Publish button: publish the selected version, or save the working drafts first (with the note) and publish the
 * version just saved, or (every working draft approved, nothing selected) revert the show to approved. */
async function publishWorking() {
  const a = publishState();
  if (!a.enabled) return;
  if (a.save) {
    const note = $("#lab-note") ? $("#lab-note").value : "";
    const saved = await saveVersion(note);
    if (!saved) return;
    if ($("#lab-note")) $("#lab-note").value = "";
    await askPublish(saved.id, "version", saved.name);
    return;
  }
  await askPublish(a.version, a.version === M.APPROVED ? "approved" : "version", M.entryLabel(a.version, L.versions));
}

/** POST /api/lab/publish without `confirm`: the server answers with its confirm sentence (409 confirm_required), or
 * refuses. `conflict`: the sentence of a conflict that brought the dialog back (shown on top). `show`: the
 * "Show <scene> after publishing" box (M.publishShowDefault: on for a version, off for Undo and Revert), asked with
 * so the sentence and the cost say what it sends; `reask`: the box was toggled in an open dialog. */
async function askPublish(version, kind, name = "", { conflict = "", show = M.publishShowDefault(kind), reask = false } = {}) {
  if (L.publish.busy || !version) return;
  L.publish.busy = true;
  renderPublishBusy();
  const expected = M.expectedLive(L.live);
  try {
    const answer = await api(`${API}/publish`, { method: "POST", body: M.publishBody({ version, expected, show }) });
    onPublished(answer); // a server that publishes without asking first (not this one: it always asks)
  } catch (err) {
    const out = M.publishOutcome(err);
    if (out.kind === "confirm") openPublishDialog({ version, kind, name, expected, view: out.dialog, conflict, show }, { reask });
    else if (out.kind === "conflict") {
      // the show changed between our read and the ask: read it and ask again (once per click)
      if (out.live) applyLive(out.live);
      L.publish.busy = false;
      if (reask) closePublishDialog();
      if (!conflict) return askPublish(version, kind, name, { conflict: out.text, show });
      publishRefused(out, version);
    } else {
      if (reask) closePublishDialog();
      publishRefused(out, version);
    }
  } finally {
    L.publish.busy = false;
    renderPublishBusy();
  }
}

function openPublishDialog(d, { reask = false } = {}) {
  const P = L.publish;
  const dlg = $("#lab-publish");
  if (reask && P.dialog && dlg.open) {
    // the same dialog, the server's sentence for the box as it is now (the name typed so far stays)
    P.dialog = { ...P.dialog, ...d, changed: "" };
    $("#lab-publish-show").checked = !!d.show;
    renderPublishDialog();
    return;
  }
  P.dialog = { ...d, waitS: 0, waitAt: 0 };
  $("#lab-publish-who").value = L.who || "";
  $("#lab-publish-show").checked = !!d.show;
  renderPublishDialog();
  if (!dlg.open) dlg.showModal ? dlg.showModal() : dlg.setAttribute("open", "");
  const scroll = $(".lab-publish-scroll", dlg);
  if (scroll) scroll.scrollTop = 0;
  // the name is asked once per browser: focus it the first time (not on a touch screen: no keyboard popping up over
  // the sentence), else the confirm button
  const touch = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
  const first = L.who ? $("#lab-publish-go") : touch ? null : $("#lab-publish-who");
  if (first) setTimeout(() => first.focus(), 0);
}

function closePublishDialog({ fromEvent = false } = {}) {
  const P = L.publish;
  P.dialog = null;
  clearInterval(P.clock);
  P.clock = null;
  const dlg = $("#lab-publish");
  if (!fromEvent && dlg && dlg.open) dlg.close ? dlg.close() : dlg.removeAttribute("open");
  renderPublishBusy();
}

function renderPublishDialog() {
  const P = L.publish;
  const d = P.dialog;
  if (!d) return;
  const v = d.view;
  const sim = isSimulated();
  $("#lab-publish-title").textContent = M.publishTitle(d.kind, sim);
  const conflict = $("#lab-publish-conflict");
  conflict.textContent = [d.conflict, d.changed].filter(Boolean).join(" ");
  conflict.hidden = !conflict.textContent;
  $("#lab-publish-sentence").textContent = v.sentence;
  $("#lab-publish-cost").textContent = v.cost ? `Cost: ${v.cost}.` : "";
  const list = (sel, items) => {
    const ul = $(sel);
    ul.replaceChildren(...items.map((x) => (x instanceof Node ? x : h("li", { text: x }))));
    ul.hidden = !items.length;
  };
  list(
    "#lab-publish-changes",
    v.changes.map((c) => h("li", {}, c.text, c.d59 ? h("span", { class: "lab-d59", text: ` — ${M.LIVE_COPY.d59}` }) : null)),
  );
  list("#lab-publish-repaint", v.repaint);
  list("#lab-publish-warnings", v.warnings.map((w) => `Not blocking: ${w}`));
  list("#lab-publish-notes", [v.trialLine, ...v.notes].filter(Boolean));
  const showRow = $("#lab-publish-show-row");
  showRow.hidden = !v.showOffer;
  $("#lab-publish-show-label").textContent = v.showLabel;
  renderPublishWait();
}

/** The confirm button and the server's wait (retry_after_s), counted down once a second. */
function renderPublishWait() {
  const P = L.publish;
  const d = P.dialog;
  const go = $("#lab-publish-go");
  if (!d || !go) return;
  const left = M.gateCountdown(d.waitS, Date.now(), d.waitAt);
  go.disabled = P.busy || left > 0;
  const box = $("#lab-publish-show");
  if (box) box.disabled = P.busy;
  go.textContent = P.busy ? "Publishing…" : left > 0 ? `Publish now (in ${left} s)` : "Publish now";
  const wait = $("#lab-publish-wait");
  wait.textContent = d.waitText && left > 0 ? d.waitText : d.waitText && !left ? `${d.waitText} You can try again now.` : "";
  if (!left && P.clock) {
    clearInterval(P.clock);
    P.clock = null;
  }
}

function renderPublishBusy() {
  renderPublishButtons();
  renderShowBar();
  if (L.historyOpen) renderHistory(true);
  renderPublishWait();
}

/** "Publish now": the server's sentence back, with the live id the dialog was opened against. */
async function confirmPublish() {
  const P = L.publish;
  const d = P.dialog;
  if (!d || P.busy) return;
  if (M.gateCountdown(d.waitS, Date.now(), d.waitAt) > 0) return;
  L.who = M.whoValue($("#lab-publish-who").value);
  rememberUi();
  // the `show` the dialog's sentence was asked with (a toggle asks again before this is possible)
  const show = d.view.showOffer ? !!d.show : null;
  P.busy = true;
  renderPublishBusy();
  try {
    const answer = await api(`${API}/publish`, { method: "POST", body: M.publishBody({ version: d.version, confirm: d.view.sentence, expected: d.expected, who: L.who, show }) });
    closePublishDialog();
    // what the show runs now, as far as this page knows it, until the catalog's live looks answer (no Draft flash)
    if (d.kind === "approved") for (const id of order()) L.liveLooks[id] = L.approved[id];
    else if (d.kind === "version" && L.versionDrafts[d.version]) for (const id of order()) L.liveLooks[id] = L.versionDrafts[d.version][id] || L.approved[id];
    onPublished(answer);
  } catch (err) {
    const out = M.publishOutcome(err);
    if (out.kind === "conflict") {
      // someone else published while this dialog was open: show what's live now and the server's new sentence
      if (out.live) applyLive(out.live);
      P.busy = false;
      closePublishDialog();
      await askPublish(d.version, d.kind, d.name, { conflict: out.text, show: !!d.show });
      return;
    }
    if (out.kind === "confirm") {
      // the server's sentence changed (another try-on, a pause, a repaint): read the new one before publishing
      P.dialog = { ...d, view: out.dialog, changed: "What publishing does changed since this opened; read it again." };
      renderPublishDialog();
    } else if (out.kind === "wait") {
      d.waitS = out.retryAfterS;
      d.waitAt = Date.now();
      d.waitText = out.text;
      clearInterval(P.clock);
      P.clock = setInterval(renderPublishWait, 1000);
    } else {
      closePublishDialog();
      publishRefused(out, d.version);
    }
  } finally {
    P.busy = false;
    renderPublishBusy();
  }
}

/** A refusal outside the dialog: its sentence in #lab-alert; a `field` one marks the scene, a stale one the version. */
function publishRefused(out, version) {
  if (out.kind === "nothing") {
    setAlert("publish", null);
    setNotice(out.text);
    refreshLive();
    return;
  }
  if (out.kind === "missing") L.liveApi = "missing";
  if (out.kind === "field" && out.scene) {
    L.publish.marks[out.scene] = out.text;
    L.problems[out.scene] = [{ field: out.slot, message: out.text }];
  }
  let text = out.text;
  if (out.kind === "stale") {
    if (version) L.publish.stale[version] = out.text;
    const names = Object.fromEntries(order().map((sid) => [sid, sceneName(sid)]));
    text = [out.text, ...M.saveProblemLines(out.problems || [], names)].join(" ");
    refreshVersions().then(renderAll);
  }
  setAlert("publish", text);
  renderAll();
}

/** A publish went live: the show bar and badges from the answer's `live`, the toast, and the stage's "today". */
function onPublished(answer) {
  const live = answer && answer.live;
  if (answer && answer.state && answer.state.engine) L.state = answer.state;
  L.publish.marks = {};
  setAlert("publish", null);
  if (live) {
    const before = L.live;
    applyLive(live, { before, ownPublish: true });
    if (before && M.expectedLive(before) === M.expectedLive(live)) refreshLiveLooks();
  } else refreshLive();
  const text = M.publishedToast(live, isSimulated());
  const held = ((answer && answer.report && answer.report.held) || []).map((id) => (sceneOf(id) ? sceneOf(id).name : id));
  toast(held.length ? `${text} · ${held.join(", ")} at module level until its slots are repainted` : text);
  setNotice(text);
  invalidateToday();
  refreshTrial(); // a publish ends a running try-on
  renderAll();
}

function toast(text) {
  const el = $("#lab-toast");
  if (!el) return;
  el.textContent = text;
  el.hidden = false;
  clearTimeout(L.toastTimer);
  L.toastTimer = setTimeout(() => {
    el.hidden = true;
  }, 5000);
}

// ---- version files (one system across the laptop, staging and the show box) -------------------------------------------
async function downloadVersion(id) {
  try {
    const v = await api(`${API}/versions/${encodeURIComponent(id)}`);
    const file = M.versionFile(v, L.catalog && L.catalog.config_sha);
    const url = URL.createObjectURL(new Blob([JSON.stringify(file, null, 2) + "\n"], { type: "application/json" }));
    const a = h("a", { href: url, download: M.versionFileName(v) });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setNotice(`Downloaded ${v.name || id} as a version file.`);
  } catch (err) {
    setAlert("versions", M.alertFor(err) || "That version couldn't be downloaded.");
  }
}

/** "Open a version file": its drafts are saved here as a new version (the server checks them like any save: the
 * stale, app-lock and copy rules), then opened into the working drafts (the unsaved-changes question first). */
async function openVersionFile(e) {
  const input = e.target;
  const file = input.files && input.files[0];
  input.value = "";
  if (!file) return;
  if (file.size > M.VERSION_FILE_MAX_BYTES) {
    setAlert("file", "That file is too large to be a Lab version.");
    return;
  }
  let text;
  try {
    text = await file.text();
  } catch {
    setAlert("file", "That file couldn't be read.");
    return;
  }
  const parsed = M.readVersionFile(text);
  if (parsed.error) {
    setAlert("file", parsed.error);
    return;
  }
  const body = { drafts: Object.fromEntries(Object.entries(parsed.drafts).map(([sid, d]) => [sid, M.normaliseDraft(d)])), note: parsed.note };
  if (parsed.name) body.name = parsed.name;
  let saved;
  try {
    saved = await api(`${API}/versions`, { method: "POST", body });
  } catch (err) {
    const problems = (err && err.body && err.body.problems) || [];
    const lines = M.saveProblemLines(problems, Object.fromEntries(order().map((sid) => [sid, sceneName(sid)])));
    setAlert("file", [`${parsed.name || file.name} wasn't added:`, M.alertFor(err) || (err.body && err.body.detail) || "the server refused it.", ...lines].join(" "));
    return;
  }
  setAlert("file", null);
  L.versionDrafts[saved.id] = saved.drafts || {};
  await refreshVersions();
  setNotice(`Added ${saved.name} from ${file.name}.`);
  renderAll();
  await openVersion(saved.id);
}

// ---- the try-on (Slice B, PLAN §4/§6.4) -----------------------------------------------------------------------------
// The panel sends nothing on its own: Apply → POST /trial/start (the server answers 409 with the confirm sentence
// naming the modules; "Try it on" sends it back) and Back to the show's look → POST /trial/stop. The server keeps the
// gap between applies, the bound and every refusal; this page shows its sentences and debounces taps.

function trialPanel() {
  return h(
    "section",
    { id: "lab-trial", class: "lab-trial", "aria-label": "Try on the lights" },
    h(
      "div",
      { class: "lab-trial-head" },
      h("span", { class: "eyebrow light", text: "TRY ON THE LIGHTS" }),
      h("span", { id: "lab-trial-left", class: "lab-trial-left", role: "timer", "aria-live": "off", hidden: true }),
    ),
    h("p", { id: "lab-trial-reason", class: "lab-trial-reason" }),
    h("div", { id: "lab-trial-now", class: "lab-trial-now", role: "status", "aria-live": "polite" }),
    h("p", { id: "lab-trial-edited", class: "lab-trial-edited" }),
    h("ul", { id: "lab-trial-scope", class: "lab-trial-scope" }),
    h("p", { id: "lab-trial-cost", class: "lab-trial-cost" }),
    h(
      "div",
      { id: "lab-trial-confirm", class: "lab-trial-confirm", hidden: true, role: "group", "aria-label": "Confirm the try-on" },
      h("p", { id: "lab-trial-sentence", class: "lab-trial-sentence" }),
      h("p", { id: "lab-trial-confirm-cost", class: "lab-trial-cost" }),
      h(
        "div",
        { class: "lab-row" },
        h("button", { id: "lab-trial-go", class: "lab-btn tryon", type: "button", text: "Try it on" }),
        h("button", { id: "lab-trial-cancel", class: "lab-btn quiet-dark", type: "button", text: "Cancel" }),
      ),
    ),
    h(
      "div",
      { id: "lab-trial-actions", class: "lab-row lab-trial-actions" },
      h("button", { id: "lab-trial-apply", class: "lab-btn tryon", type: "button", text: "Apply to the lights" }),
      h("button", { id: "lab-trial-stop", class: "lab-btn quiet-dark", type: "button", hidden: true, text: "Back to the show’s look" }),
    ),
  );
}

function trialAvailability() {
  const T = L.tryon;
  return M.trialAvailability({
    api: T.api,
    engine: L.state && L.state.engine,
    trial: trial(),
    problems: (L.problems[L.scene] || []).length,
    error: T.error,
  });
}

function trialGate(now = Date.now()) {
  const T = L.tryon;
  return M.applyReady({ busy: T.busy, lastTapMs: null, acceptedAtMs: T.acceptedAt, nextApplyInS: T.nextApplyInS, nowMs: now });
}

function renderTrial() {
  const box = $("#lab-trial");
  if (!box || !L.scene) return;
  const T = L.tryon;
  const t = trial();
  const scene = sceneOf(L.scene);
  const avail = trialAvailability();
  const ours = !!(t && t.scene_id === L.scene);
  box.classList.toggle("is-on", !!t);
  box.classList.toggle("is-note", !avail.controls);
  const reason = $("#lab-trial-reason");
  reason.textContent = avail.reason || "";
  reason.hidden = !avail.reason;
  const now = $("#lab-trial-now");
  const lines = M.trialStatusLines(t);
  if (t && !ours) lines.push(`Applying ${sceneName(L.scene)} replaces it (the time limit stays the running one's).`);
  now.replaceChildren(...lines.map((text, i) => h("p", { class: i === 0 ? "lab-trial-what" : null, text })));
  now.hidden = !lines.length;
  const edited = $("#lab-trial-edited");
  edited.textContent = M.editedSinceApply(t, L.scene, L.working[L.scene], T.applied[L.scene]);
  // what a try-on of this scene can do, and what it costs: only where the controls are
  const scope = $("#lab-trial-scope");
  scope.replaceChildren(...(avail.controls && !ours ? M.trialScopeLines(scene).map((text) => h("li", { text })) : []));
  scope.hidden = !scope.childElementCount;
  const cost = $("#lab-trial-cost");
  const estimate = M.trialEstimate(workingPreview().preview);
  cost.textContent = !avail.controls
    ? ""
    : ours && t.cost
      ? `Each apply ${t.cost}.`
      : estimate
        ? `Apply ${estimate.cost}, to ${estimate.modules}; the server confirms the modules first.`
        : "Apply asks the server which modules it writes to before anything is sent.";
  const c = T.confirm && T.confirm.scene === L.scene && avail.controls ? T.confirm : null;
  $("#lab-trial-confirm").hidden = !c;
  if (c) {
    $("#lab-trial-sentence").textContent = c.sentence;
    $("#lab-trial-confirm-cost").textContent = c.cost ? `It ${c.cost}.` : "";
    $("#lab-trial-go").disabled = T.busy || !avail.canApply;
  }
  $("#lab-trial-actions").hidden = !avail.controls;
  const stop = $("#lab-trial-stop");
  stop.hidden = !t;
  stop.disabled = T.busy;
  renderTrialClock();
}

/** The parts that change every second: the time left, the header badge, the Apply button's wait. */
function renderTrialClock() {
  const T = L.tryon;
  const t = trial();
  const now = Date.now();
  const left = M.trialLeft(t, T.readAt, now);
  const leftEl = $("#lab-trial-left");
  if (leftEl) {
    leftEl.hidden = !t;
    leftEl.textContent = t ? M.formatLeft(left) : "";
  }
  const badge = $("#lab-trial-badge");
  if (badge) {
    badge.hidden = !t;
    badge.textContent = M.trialBadge(t, left);
  }
  const eyebrow = $("#lab-eyebrow");
  if (eyebrow) eyebrow.textContent = M.labEyebrow(T.api, t, L.liveApi === "ready");
  const apply = $("#lab-trial-apply");
  if (apply) {
    const avail = trialAvailability();
    const gate = trialGate(now);
    apply.disabled = !avail.canApply || !gate.ok || !!(T.confirm && T.confirm.scene === L.scene);
    apply.textContent = M.applyLabel({ busy: T.busy, waitS: gate.waitS, reapply: !!(t && t.scene_id === L.scene) });
  }
  if (t && left === 0 && !T.ending) {
    T.ending = true; // the engine ends it at the bound: read the end soon after
    setTimeout(refreshTrial, 1500);
  }
}

/** GET /api/lab/trial: the running try-on (or none), and whether the API is on this server at all. */
async function refreshTrial() {
  const T = L.tryon;
  const before = T.trial;
  try {
    const answer = await api(`${API}/trial`);
    T.api = "ready";
    T.error = "";
    T.trial = M.trialFromAnswer(answer);
  } catch (err) {
    const kind = M.trialApiFromError(err);
    T.api = kind === "none" ? "ready" : kind;
    T.error = kind === "error" ? M.alertFor(err) || "" : "";
    if (kind === "none" || kind === "missing" || kind === "no-engine") T.trial = null;
  }
  T.readAt = Date.now();
  T.ending = false;
  if (before && !T.trial && T.api === "ready") setNotice(`The try-on of ${before.label || before.scene_id} ended.`);
  const changed = !before !== !T.trial || (before && T.trial && (before.scene_id !== T.trial.scene_id || before.applies !== T.trial.applies || before.tier !== T.trial.tier));
  if (changed) {
    invalidateToday();
    renderAll();
  } else renderTrial();
}

/** GET /api/lab/state: the engine's flags (running, paused, commissioning, outage) and the store, for the panel. */
async function refreshLabState() {
  try {
    L.state = await api(`${API}/state`);
    setAlert("store", M.alertFor(L.state));
  } catch {
    // the trial's own read says whether the Conductor answers; the last state stands
  }
}

function scheduleTrialPoll() {
  const T = L.tryon;
  clearTimeout(T.pollTimer);
  if (!T.watching || T.api === "missing") return;
  T.pollTimer = setTimeout(
    async () => {
      if (!document.hidden) {
        if (!trial()) await refreshLabState();
        await refreshTrial();
      }
      scheduleTrialPoll();
    },
    trial() ? M.TRIAL_POLL_MS : M.TRIAL_IDLE_POLL_MS,
  );
}

/** A "canopy" event (any look change, a pause, the bound's end): read the try-on and the engine flags soon, once. */
function nudgeTrial() {
  const T = L.tryon;
  if (T.nudgeTimer) return;
  T.nudgeTimer = setTimeout(async () => {
    T.nudgeTimer = null;
    await refreshLabState();
    await refreshTrial();
  }, 600);
}

async function startTrialWatch() {
  const T = L.tryon;
  if (T.watching) return;
  T.watching = true;
  await refreshTrial();
  if (!T.watching) return;
  scheduleTrialPoll();
  if (T.api !== "missing" && !T.live) {
    try {
      T.live = connectLive({ topics: ["canopy"], onEvent: (topic) => topic === "canopy" && nudgeTrial() });
    } catch {
      T.live = null; // polling still keeps the panel current
    }
  }
  clearInterval(T.clock);
  T.clock = setInterval(() => {
    if (trial() || L.tryon.acceptedAt) renderTrialClock();
  }, 1000);
}

function stopTrialWatch() {
  const T = L.tryon;
  T.watching = false;
  clearTimeout(T.pollTimer);
  clearTimeout(T.nudgeTimer);
  T.nudgeTimer = null;
  clearInterval(T.clock);
  if (T.live) {
    try {
      T.live.close();
    } catch {
      // already closed
    }
    T.live = null;
  }
}

/** Apply: the working draft of the scene on screen, to the lights. A double tap, a tap while one is on its way and a
 * tap inside the server's gap send nothing. Without `confirm` the server answers with its confirm sentence first
 * (except a re-apply of the scene already on, which sends back the sentence confirmed for it). */
async function applyTrial({ confirm = null } = {}) {
  const T = L.tryon;
  const now = Date.now();
  // "Try it on" answers the server's question, so it never counts as Apply's second tap
  const lastTapMs = confirm ? null : T.lastTapAt;
  const gate = M.applyReady({ busy: T.busy, lastTapMs, acceptedAtMs: T.acceptedAt, nextApplyInS: T.nextApplyInS, nowMs: now });
  if (!gate.ok || !trialAvailability().canApply) return;
  T.lastTapAt = now;
  const scene = L.scene;
  const draft = clone(L.working[scene]);
  const t = trial();
  const sentence = confirm || (t && t.scene_id === scene ? T.confirmed[scene] : null) || null;
  T.busy = true;
  renderTrial();
  try {
    const answer = await api(`${API}/trial/start`, { method: "POST", body: M.trialStartBody(scene, draft, sentence) });
    T.api = "ready";
    T.trial = M.trialFromAnswer(answer);
    T.readAt = Date.now();
    T.acceptedAt = Date.now();
    T.nextApplyInS = Number((T.trial && T.trial.next_apply_in_s) || 0);
    T.confirm = null;
    if (sentence) T.confirmed[scene] = sentence;
    T.applied[scene] = draft;
    if (answer && answer.state && answer.state.engine) L.state = answer.state;
    setAlert("trial", null);
    setNotice(T.trial ? `Applied to the lights: ${T.trial.draft_name || T.trial.label || sceneName(scene)}.` : "Applied.");
    invalidateToday();
  } catch (err) {
    const out = M.startOutcome(err);
    if (out.kind === "confirm") {
      T.confirm = { scene, sentence: out.sentence, cost: out.cost };
      delete T.confirmed[scene];
      setAlert("trial", null);
    } else {
      T.confirm = null;
      if (out.kind === "problems") L.problems[scene] = out.problems;
      if (out.kind === "missing") T.api = "missing";
      setAlert("trial", out.kind === "problems" ? M.alertFor(err) || out.text : out.text);
      if (out.retryAfterS) {
        T.acceptedAt = Date.now(); // the button counts the server's wait down
        T.nextApplyInS = out.retryAfterS;
      }
    }
  } finally {
    T.busy = false;
  }
  renderAll();
  if (T.confirm && T.confirm.scene === scene) {
    const box = $("#lab-trial-confirm");
    if (box && box.scrollIntoView) box.scrollIntoView({ block: "nearest" }); // the question is never left off-screen
  }
  refreshLabState().then(renderTrial);
}

/** Back to the show's look: the server ends the try-on and the engine puts the scene's live look back (one burst). */
async function stopTrial() {
  const T = L.tryon;
  if (T.busy) return;
  const was = trial();
  T.busy = true;
  renderTrial();
  try {
    const answer = await api(`${API}/trial/stop`, { method: "POST", body: {} });
    T.trial = M.trialFromAnswer(answer);
    T.readAt = Date.now();
    T.confirm = null;
    if (answer && answer.state && answer.state.engine) L.state = answer.state;
    setAlert("trial", null);
    setNotice(was ? `Back to the show’s look: the lights show ${was.label || sceneName(was.scene_id)} as the show runs it.` : "No try-on was running.");
    invalidateToday();
  } catch (err) {
    const out = M.startOutcome(err);
    if (out.kind === "missing") T.api = "missing";
    setAlert("trial", out.text);
  } finally {
    T.busy = false;
  }
  renderAll();
}

// ---- Present ------------------------------------------------------------------------------------------------------------
function presentOverlay() {
  return h(
    "div",
    { id: "lab-present", class: "lab-present", hidden: true, role: "dialog", "aria-modal": "true", "aria-label": "Present" },
    h("button", { id: "lab-present-exit", class: "lab-present-exit", type: "button", "aria-label": "Exit Present", text: "✕" }),
    h("div", { id: "lab-present-stages", class: "lab-present-stages" }),
    h(
      "div",
      { class: "lab-present-text" },
      h("h2", { id: "lab-present-name" }),
      h("p", { id: "lab-present-sub" }),
      h("p", { class: "lab-present-caption" }, h("span", { id: "lab-present-caption" }), h("span", { id: "lab-present-badge", class: "lab-badge" })),
      h("p", { id: "lab-present-moving", class: "lab-present-moving", hidden: true }),
      h(
        "div",
        { id: "lab-present-motion", class: "lab-present-motion", hidden: true },
        h("button", { id: "lab-present-play", class: "lab-present-play", type: "button", "aria-pressed": "false", text: "▶ Play the steps" }),
        h("span", { id: "lab-present-pace", class: "lab-present-pace" }),
      ),
    ),
    h("p", { id: "lab-present-hint", class: "lab-present-hint", text: "← → scenes · ↑ ↓ versions · V view · B compare · Space plays the steps · Esc exits — or swipe" }),
  );
}

function exportDialog() {
  return h(
    "dialog",
    { id: "lab-export", class: "lab-dialog" },
    h("div", { class: "lab-dialog-head" }, h("h2", { id: "lab-export-title", text: "Export" }), h("button", { type: "button", class: "lab-btn small", onclick: () => $("#lab-export").close(), text: "Close" })),
    h("div", { id: "lab-export-body" }),
  );
}

function openPresent() {
  const unsaved = M.unsavedScenes(L.working, L.base, order()).length > 0 || (!L.selected && M.draftedScenes(L.working, L.approved, order()).length > 0);
  const compare = M.compareSet(L.versions, { unsaved });
  const version = unsaved ? M.WORKING : L.selected && compare.includes(L.selected) ? L.selected : M.APPROVED;
  motionStop({ render: false });
  L.present = { scenes: order(), compare, scene: L.scene, version, ab: false, fs: false, stages: {}, touch: null };
  const box = $("#lab-present-stages");
  box.replaceChildren(); // a new Present has new stages: never one left from the last time it opened
  delete box.dataset.sides;
  delete box.dataset.angle;
  const el = $("#lab-present");
  el.hidden = false;
  document.documentElement.classList.add("lab-presenting");
  try {
    if (el.requestFullscreen) {
      el.requestFullscreen()
        .then(() => {
          if (L.present) L.present.fs = true;
        })
        .catch(() => {});
    }
  } catch {
    // no element fullscreen (older iPadOS): the fixed overlay covers the viewport anyway
  }
  const hint = $("#lab-present-hint");
  hint.classList.remove("fade");
  setTimeout(() => hint.classList.add("fade"), 3000);
  showExit();
  renderPresent();
  prefetchPresent();
}

function closePresent() {
  if (!L.present) return;
  const wasFs = L.present.fs;
  motionStop({ render: false });
  L.present = null;
  $("#lab-present").hidden = true;
  document.documentElement.classList.remove("lab-presenting");
  if (wasFs && document.fullscreenElement) document.exitFullscreen().catch(() => {});
  renderAll();
}

function showExit() {
  const x = $("#lab-present-exit");
  x.classList.add("shown");
  clearTimeout(showExit.t);
  showExit.t = setTimeout(() => x.classList.remove("shown"), 3000);
}

async function prefetchPresent() {
  const P = L.present;
  if (!P) return;
  if (P.version !== M.APPROVED && P.version !== M.WORKING) {
    try {
      await versionDrafts(P.version);
    } catch (err) {
      setAlert("versions", M.alertFor(err));
    }
  }
  if (!L.present) return;
  await Promise.all(P.scenes.map((id) => requestPreview(draftFor(P.version, id))));
  if (L.present) renderPresent();
}

function presentStage(side) {
  const P = L.present;
  const key = `${side}|${L.angle}`;
  const st = P.stages[side];
  if (st && st.key === key && st.el.isConnected) return st;
  const canopy = h("div", { class: "lab-canopy" });
  const el = h("figure", { class: "lab-present-stage" }, h("figcaption", { class: "lab-present-side" }), canopy, h("p", { class: "lab-stage-note" }));
  const view = buildCanopy(canopy, { scope: "canopy", device: "both", maps: {}, angle: L.angle });
  P.stages[side] = { key, el, view };
  return P.stages[side];
}

function renderPresent() {
  const P = L.present;
  if (!P) return;
  const box = $("#lab-present-stages");
  const sides = P.ab ? [["a", M.APPROVED], ["b", P.version]] : [["main", P.version]];
  const wanted = sides.map(([s]) => s).join(",");
  if (box.dataset.sides !== wanted || box.dataset.angle !== L.angle) {
    box.dataset.sides = wanted;
    box.dataset.angle = L.angle;
    P.stages = {};
    box.replaceChildren();
  }
  box.classList.toggle("two", P.ab);
  let shownDraft = null;
  let shownPreview = null;
  let shownView = null;
  let shownMotion = null;
  for (const [side, entry] of sides) {
    const st = presentStage(side);
    if (!st.el.isConnected) box.append(st.el);
    const draft = draftFor(entry, P.scene);
    const key = draft ? M.previewKey(draft) : "";
    let preview = L.cache.get(key) || null;
    if (!preview && draft) {
      preview = catalogPreview(draft);
      requestPreview(draft).then(() => L.present && renderPresent());
    }
    const picked = M.pickView(preview, L.view);
    const mo = motionPaint(preview, picked); // Present plays the steps too (Space or the play button)
    const view = mo ? mo.view : picked;
    paintStage(st, preview, view, { fresh: true, notes: [...(mo ? mo.notes : []), M.todayNote(view, preview)], paint: mo ? mo.paint : {} });
    $(".lab-present-side", st.el).textContent = P.ab ? M.entryLabel(entry, L.versions, { unsaved: true }) : "";
    if (entry === P.version) {
      shownDraft = draft;
      shownPreview = preview;
      shownView = view;
      shownMotion = mo;
    }
  }
  const scene = sceneOf(P.scene);
  $("#lab-present-name").textContent = (shownDraft && shownDraft.name) || (scene && scene.name) || P.scene;
  $("#lab-present-sub").textContent = [M.entryLabel(P.version, L.versions, { unsaved: true }), shownDraft && shownDraft.mood].filter(Boolean).join(" · ");
  $("#lab-present-caption").textContent = [M.tierCaption(shownView, shownPreview) || "Loading…", shownMotion && shownMotion.label].filter(Boolean).join(" · ");
  const moving = $("#lab-present-moving");
  moving.textContent = (shownMotion && shownMotion.moving) || "";
  moving.hidden = !moving.textContent;
  const stepped = !!(scene && scene.stepped);
  const { st: shownSt, tl } = motionTimeline(shownPreview);
  $("#lab-present-motion").hidden = !stepped;
  $("#lab-present").classList.toggle("has-motion", stepped);
  const play = $("#lab-present-play");
  const m = L.motion;
  play.disabled = !!tl.error;
  play.textContent = m.state === "playing" ? "❚❚ Pause" : m.state === "paused" ? "▶ Resume" : m.state === "done" ? "▶ Play again" : "▶ Play the steps";
  play.setAttribute("aria-pressed", String(m.state === "playing"));
  $("#lab-present-pace").textContent = tl.error ? tl.error : [tl.label, m.fast ? `sped up ${M.FAST_FACTOR}×, not the real pace` : "", shownSt.error ? "" : M.sentence(shownSt.stepsWhen)].filter(Boolean).join(" · ");
  const state = M.stageState(shownView, shownPreview, shownDraft ? M.draftState(L.approved[P.scene], shownDraft, trial()) : "approved");
  const badge = $("#lab-present-badge");
  badge.className = `lab-badge ${state.replace(/\s/g, "-")}`;
  badge.textContent = M.BADGE_TEXT[state];
}

function presentInput(input) {
  const P = L.present;
  if (!P) return;
  const next = M.presentStep(P, input);
  const versionChanged = next.version !== P.version;
  if (versionChanged || next.scene !== P.scene) motionStop({ render: false }); // another scene or version: its own steps
  Object.assign(P, { scene: next.scene, version: next.version });
  renderPresent();
  if (versionChanged) prefetchPresent();
}

function wireKeys() {
  document.addEventListener("keydown", (e) => {
    if (L.present) {
      if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) {
        e.preventDefault();
        presentInput(e.key);
      } else if (e.key === "Escape") {
        e.preventDefault();
        closePresent();
      } else if (e.key === "v" || e.key === "V") {
        L.angle = L.angle === "plan" ? "perspective" : "plan"; // in every stage's key: each rebuilds in place
        renderPresent();
      } else if (e.key === "b" || e.key === "B") {
        L.present.ab = !L.present.ab;
        renderPresent();
      } else if (e.key === " " || e.key === "p" || e.key === "P") {
        e.preventDefault();
        motionToggle();
      }
      return;
    }
    if (!document.body.classList.contains("lab-open") || !L.built) return;
    const t = e.target;
    const typing = t && (t.tagName === "TEXTAREA" || (t.tagName === "INPUT" && !["range", "radio", "checkbox", "color"].includes(t.type)));
    if ((e.metaKey || e.ctrlKey) && !e.shiftKey && (e.key === "z" || e.key === "Z") && !typing) {
      e.preventDefault();
      undo();
    }
  });
  document.addEventListener("fullscreenchange", () => {
    if (L.present && L.present.fs && !document.fullscreenElement) closePresent();
  });
}

function wirePresentTouch() {
  const el = $("#lab-present");
  el.addEventListener(
    "touchstart",
    (e) => {
      const t = e.touches[0];
      if (L.present && t) L.present.touch = { x: t.clientX, y: t.clientY, at: performance.now() };
    },
    { passive: true },
  );
  el.addEventListener(
    "touchmove",
    (e) => {
      if (L.present) e.preventDefault(); // no elastic scroll or pull-to-refresh eating a swipe
    },
    { passive: false },
  );
  el.addEventListener("touchend", (e) => {
    const P = L.present;
    const t = e.changedTouches[0];
    if (!P || !P.touch || !t) return;
    const swipe = M.swipeOf(t.clientX - P.touch.x, t.clientY - P.touch.y, performance.now() - P.touch.at);
    P.touch = null;
    if (swipe) presentInput(swipe);
    else showExit();
  });
  el.addEventListener("click", (e) => {
    if (e.target && e.target.id === "lab-present-exit") closePresent();
    else if (e.target && e.target.closest && e.target.closest("#lab-present-play")) motionToggle();
    else showExit();
  });
}

// ---- open / close -------------------------------------------------------------------------------------------------------
function route() {
  const open = location.hash === "#lab";
  const el = root();
  if (!el) return;
  document.body.classList.toggle("lab-open", open);
  el.hidden = !open;
  if (!open) {
    if (L.present) closePresent();
    motionStop({ render: false }); // no motion clock while the Lab is closed
    stopTrialWatch(); // no polling or socket while the Lab is closed
    stopLiveWatch();
    return;
  }
  window.scrollTo(0, 0);
  if (!L.booting) L.booting = boot();
  else if (L.built) {
    startTrialWatch();
    startLiveWatch();
  }
}

wireKeys();
window.addEventListener("hashchange", route);
route();
