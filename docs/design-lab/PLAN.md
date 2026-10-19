# The Design Lab — build plan

_Written Fri 2026-10-16 by the planner, from `docs/design-lab/PROMPT.md` (binding, except its "Base branch" section),
CLAUDE.md, `docs/context/07-engineering-contracts.md`, `docs/DECISIONS.md` D59–D65 and the code on `claude/design-lab`
(based on PR #24 `claude/stack-to-main`, which carries #20–#23 to main; this branch targets main). Every symbol and
line number below was read on this branch at `1d736e7`; line numbers drift, so search by name. Revised the same day
after three critics (contracts-engine, client-UX, data-tests); each finding's disposition is in the Review log at
the end._

The three "Alon decides" questions take their defaults: (1) an approved palette colour **can** be changed, as a
clearly labelled draft whose export says "changes an approved colour — needs the client's sign-off and a D59 update";
(2) Present runs in **Alon's laptop browser** as ops, and the Test iPad stays the guest panel on the show Conductor;
(3) version names default to `Client <Mon D> v<N> – <note>` ("Client Oct 17 v3 – warmer sunset").

Hard rules for every agent (repeat them in each agent prompt): this Mac mini is the show box. Never bind port 8000
(`make run` and the `conductor` launch entry do), never touch `~/canopy-show` or
`~/Library/Application Support/ColorCanopy`, never run `canopy-mac`, never set `HUBSPACE_LIVE`, no network in tests,
`uv run --offline` for Python, never edit `.github/workflows/`, never merge/approve/auto-merge.

---

## 1. Scope, slices and schedule

| Slice | What ships | Show-path edits | Due | PR |
|---|---|---|---|---|
| **A** screen-only Lab | drafts over the parsed config, `/api/lab/*` (catalog, state, preview, versions, restore, export), the corrupt-store safety, the Lab panel in the Studio with tier views, A/B, version list, Present, export UI, the extracted validator, the pin fixture, the dev server on 8200 | **none** (only new files, one import + one `include_router` in `app.py`, the `/ops` and Studio link edits) | **Sat Oct 17, night** | #25 `claude/design-lab` → main |
| **B** try-on | the trial seam in `canopy.py`/`show.py`, `lab.trial_max_s` in `config.py`/`conductor.yaml`, `/api/lab/trial*`, the Lab's Try-on panel, the kiosk card key | `canopy.py`, `show.py` (+ `config.py` settings) — **not** `reconciler.py`, `executor.py`, drivers | **Sun Oct 18** | #26 `claude/design-lab-trial` → stacked on #25 |
| **C** motion editor | stepped editor + paced step preview, ripple preview (screen only), greyed effects, polish, soak report | none | Mon Oct 19 (after A and B merge; screen-only, useful for install-week reviews with Paris) | #27 `claude/design-lab-motion` → stacked on #26 |

Each PR body says: "retarget to main before merging (stacked-PR trap); merge commit, not squash". Never leave a slice
half built: a slice that can't finish ships behind its cut line below, not half-wired.

**Timeline against today (Fri Oct 16):**
- **Fri evening:** this plan; then the **contracts commit** (§2.1) on `claude/design-lab`: stubs with final
  signatures and models, the router with every Slice A route, `app.py`/link/launch/Makefile edits, the dev server.
- **Sat morning → afternoon:** fan out the four Slice A streams (S1–S4, §2). S1 lands `apply_draft` first (S3's
  export and S4's live preview depend on it); S3 builds the YAML editor against hand-built patched trios meanwhile.
- **Sat late afternoon:** sequential integration by the lead; adversarial verify (contracts / truth / resilience /
  client UX / mutation lenses); fix; full suite, lint, node; `LAB_ROUNDTRIP=1 make lab-roundtrip` (≈ 10 min a
  canonical edit; §8); browser check on 8200 at 1440×900 and 1180×820 touch.
- **Where the client session runs (§9.1):** Alon's laptop with `make lab-dev` (laptop only), or Railway staging once
  #24/#25 are on main (laptop + an ops-signed-in iPad, not the kiosk-enrolled Test iPad); the show box only after the
  deploy block.
- **Sat night:** PR #25. STATUS + D66 committed with it.
- **Sun:** Slice B (B1 engine seam, B2 UI), verify, PR #26 with the deploy block. If the stack (#24 → #26) isn't
  merged by Sat night, STATUS says plainly: kit try-on happens in the Hubspace app or on `/ops/module`, the Lab stays
  screen-only until after Oct 19.
- **Mon Oct 19+:** Slice C, `make soak`, commit `docs/reports/sim-acceptance.md` in #27 only.

**Cut lines (in this order, only if time runs short):**
- Slice A: (1) the refactor of `tests/test_modes_match_scenes.py` onto the extracted validator (the validator itself
  still ships and `/api/lab/preview` uses it; the brief allows this cut); (2) pins conversion beyond the tests the
  Done-means Golden Hour edit breaks (§3.7; the rest are listed in STATUS as "export tripwires"); (3) any-vs-any A/B
  (keep approved-vs-draft); (4) vertical swipe in Present (keep keys + horizontal swipe). **Never cut:** the corrupt
  store safety, ops-only + role tests, preview truth (real compiler with the draft's recipes, today view, no
  `SCENES` fallback), unchanged-export round trip, the compiler tests' frozen recipes (§3.7, or a rename can't
  export), the input-purity test, the Simulator badge, the T1 lock.
- Slice B: cut the remaining-time ring in the UI; never cut a refusal, the T1 isolation test or the writer path.
- Slice C: cut the ripple preview first, then step timeline scrubbing (keep play/pause).

---

## 2. Streams and file ownership

**Streams.** L = lead (contracts commit, integration, docs). Slice A: S1 draft + patch + preview, S2 versions +
store + access, S3 export + extracted validator + pins, S4 Studio Lab panel + Present. Slice B: B1 trial seam +
API + tests, B2 trial UI + kiosk key. Slice C: C1 server, C2 UI. Agents edit only files they own; anything else goes
to L as a note in their final message.

### 2.1 The contracts commit (L, before fan-out)
Creates every new Python module below with the exact signatures and pydantic models of §3 (bodies
`raise NotImplementedError` except where noted), `conductor/routes/design_lab.py` complete (thin handlers that call
the module functions), `conductor/design_lab/service.py` complete, empty placeholders `web/ops/showcase/lab.js`
(`export {}`), `lab.css` (empty) and `lab-model.mjs` (`export {}`), and the shared-file edits below.
`scene_mode_problems` is stubbed to return `[]` and `scene_pins` to `{}` so S1 can integrate before S3 lands.

### 2.2 Ownership table

| File | New/changed | Owner | Slice |
|---|---|---|---|
| `conductor/design_lab/__init__.py` | new (re-exports) | L | A |
| `conductor/design_lab/service.py` (`LabService`, `lab_of`) | new | L | A |
| `conductor/routes/design_lab.py` (router, all `/api/lab/*`) | new | L (A routes); B1 adds trial handlers | A, B |
| `conductor/design_lab/draft.py` | new | S1 | A |
| `conductor/design_lab/preview.py` | new | S1 (C1 adds `stepped`/`rings`) | A, C |
| `conductor/design_lab/store.py` | new | S2 | A |
| `conductor/design_lab/export.py` | new | S3 | A |
| `conductor/looks/scene_rules.py` | new | S3 | A |
| `conductor/design_lab/trial.py` | new | B1 | B |
| `conductor/design_lab/stepped.py` | new | C1 | C |
| `web/ops/showcase/lab.js`, `lab.css`, `lab-model.mjs` | new | S4 (B2, C2 later) | A, B, C |
| `scripts/dev_design_lab.py` | new | L | A |
| `tests/test_lab_draft.py`, `tests/test_lab_preview.py`, `tests/test_lab_routes_preview.py` | new | S1 | A |
| `tests/test_lab_store.py`, `tests/test_lab_routes_versions.py`, `tests/test_lab_access.py` | new | S2 | A |
| `tests/test_lab_export.py`, `tests/test_scene_rules.py`, `tests/test_lab_roundtrip.py` | new | S3 | A |
| `tests/lab-model.test.mjs`, `tests/test_lab_static.py` | new | S4 | A |
| `tests/test_dev_design_lab.py` | new | L | A |
| `tests/test_lab_router.py` (mounted, guarded, every Slice A route answers; 501 while a stream's stub stands) | new | L | A |
| `tests/test_lab_trial.py` | new | B1 | B |
| `tests/test_lab_stepped.py` | new | C1 | C |
| `conductor/app.py` | **shared**: line 19 import gains `design_lab`; `app.include_router(design_lab.router)` right after `app.include_router(design.router)` (line 176), before the StaticFiles mounts | L | A |
| `web/ops/index.html` | **shared**: Pages card (`#links-card`, ~line 170) gains `<a class="btn secondary" href="/ops/showcase/#lab">Design Lab</a>` after the Visualizer link | L | A |
| `web/ops/showcase/index.html` | **shared**: 4 hooks (§5.1) | L | A |
| `web/ops/showcase/studio.js`, `demo-model.mjs` | **unchanged** (the Lab imports demo-model's painter; nobody edits either) | — | — |
| `tests/test_roles.py` | **shared**: Lab paths added to the negative lists of `test_tester_is_kept_out_of_everything_else` (parametrize list), `test_kiosk_paths`, `test_vignette_paths` | S2 | A |
| `tests/test_writer_paths.py` | **shared**: `WRITER_PATHS["lab try-on (module level)"]`, `COVERED_MODULES["conductor/routes/design_lab.py"]` | B1 | B |
| `.claude/launch.json` | **shared**: add the `design-lab` entry (§9); keep `conductor` untouched | L | A |
| `Makefile` | **shared**: `lab-dev`, `lab-roundtrip`, `scene-pins` targets + `.PHONY` | L | A |
| `scripts/regen_scene_pins.py` | new (§3.7) | S3 | A |
| `tests/test_modes_match_scenes.py`, `tests/test_scenes_catalog.py`, `tests/fixtures_scene_frames.json` | changed (validator alongside + `_pins`) | S3 | A |
| `tests/fixtures_recipes_frozen.yaml` | new: today's 20 scene-palette recipe entries, verbatim (§3.7) | S3 | A |
| compiler tests `tests/test_compile_field.py`, `test_compile_stepped.py`, `test_engine_program.py` | changed: recipes = config ∪ frozen (§3.7) | S3 | A |
| pin tests in `tests/test_compile.py`, `test_kiosk_routes.py`, `test_canopy_view.py`, `test_soak.py`, `test_d65_modes.py`, `test_module_api.py`, `test_ops_routes.py`, `test_engine_ripple.py` | changed (read config/pins, §3.7) | S3 | A |
| `conductor/canopy.py`, `conductor/show.py` | changed (trial seam, §6) | B1 | B |
| `conductor/routes/kiosk.py` | changed: one `trial_active` branch in `refusal_for_state` (§6.5) | B1 | B |
| `conductor/config.py`, `config/conductor.yaml` | changed (`LabSettings`) | B1 | B |
| `web/kiosk/kiosk.js`, `web/kiosk/panel-model.mjs`, `tests/guest-panel.test.mjs` | changed (`cardsKey`) | B2 | B |
| `docs/STATUS.md`, `docs/DECISIONS.md` (D66), PR bodies, `docs/reports/sim-acceptance.md` (C only) | changed | L | A, B, C |

### 2.3 The contracts commit as built (deviations from §3–§5, L, Fri Oct 16)
Everything below is in the contracts commit; streams build against it. Where this list and §3–§5 differ, this list
wins.
- **Package re-exports:** `conductor/design_lab/__init__.py` does **not** re-export the `preview()` function: the
  name would shadow the submodule, so `from conductor.design_lab import preview` would give the function, not the
  module. Import `conductor.design_lab.preview` as a module (`from conductor.design_lab import preview as P`).
- **`lab_of`'s lock** is an `asyncio.Lock` kept per Services in `extras["design_lab:lock"]` (`service.LOCK_KEY`),
  not a module-level `_LOCK`: a module-level lock binds to the first event loop that contends for it, and each
  `TestClient` runs its own loop. Same get-or-create, same threadpool construction, same alert on the loop thread.
- **`LabService`** also carries `config_sha` (of the config `approved` was read from, computed once at
  construction; the catalog, the store and every save use it), and `settings` / `simulated` properties.
  `ENGINE_KEY`/`SHOW_KEY` are `"canopy"`/`"show"`.
- **`load_trio` lives in `draft.py`** (as §3.2 lists it) and is implemented in this commit; `service.py` imports it.
- **`draft.py`:** the `SceneDraft` field is named `copy` as the contract says; it shadows pydantic's deprecated
  `BaseModel.copy()`, so the class is created under a `warnings` filter for that one message (use `model_copy`).
  The §3.1 validators are implemented here as **field** validators (`solid`, palette name uniqueness, `mood`,
  `DraftColour.name`/`hex`, `DraftStepped.run_s`), so a refused body names its field (`palette.1.name`); the D63
  locks (`layout.cycles == 1`, `sample_at == "centre"`) are **not** in the model: S1 refuses them in `apply_drafts`
  as `Problem`s. `Problem.to_json()` and `DraftError(problems, message=None)` (`.problems`) added. `FIELD_FOR` is
  the §3.2.3 table as a constant (`stepped.<name>` rows map to `"stepped"`; S1's `problems_from_config_error`
  refines it). `NUMBER_NAMES` uses `re.IGNORECASE` (lint).
- **`store.py`** adds what the routes need from S2: `LookStore.read_only` and `.pending_alert` (named in §3.5's
  text), `unreadable() -> list[dict]`, `version_summaries(*, approved, canopy, settings, config_sha) -> list[dict]`
  (VersionSummary, newest first, then the unreadable ones) and `version_json(version_id, *, …) -> dict`
  (VersionJSON; KeyError), the exception `StoreReadOnly(RuntimeError)` (→ 409) and its sentence `READ_ONLY`.
  Implemented now: `__init__`, `file()`, `get()` (KeyError), `unreadable()`, `default_name`, `config_sha` (sha256
  over `scenes.yaml`, `modes.yaml`, `recipes.yaml` bytes in that order, `[:12]`), and `load()` **only for a missing
  file** (an empty store, so the Lab runs before S2 lands); an existing file is S2's (`NotImplementedError`).
- **`preview.py`** holds the T1 lock (§3.8 named no module): `T1_LOCKED`, `t1_lock(lab, scene_id)`, the exception
  `T1Locked(detail, field)` (→ 409 `{detail, field}`) and a new S1 function `check_t1_lock(lab, draft)` that
  preview and the save route both call. `design_registry()` and `design_caps()` are implemented.
- **`scene_rules.py`:** `WARNING_TESTS` (rule → the test the export's 409 names: `two-d` →
  `test_no_scene_reads_as_three_columns_at_module_level`, `kits-differ` →
  `test_on_the_partial_live_canopy_the_two_kits_show_different_colours`; S3 may refine) and
  `SceneProblem.to_json()` added; `delta_e76` is implemented (moved verbatim from the test's `_lab`/`_delta_e`).
- **`export.py`:** `LabExport` as §3.6 (only `problem` has a default).
- **Routes (`routes/design_lab.py`):**
  - The router uses a router-scoped route class `LabRoute` (FastAPI exception handlers are app-wide): every answer,
    errors included, is `Cache-Control: no-store`; a body pydantic refuses is a **422 in the Lab's shape**
    `{detail, problems: [{path, field, message}]}` (`field` = the SceneDraft path past `draft` / `drafts.<scene>`,
    e.g. `palette.2.hex`), not FastAPI's default list; a stream's stub (`NotImplementedError`) answers **501**
    `{detail: "Not built yet: S1: seed_draft.", not_built: true}` so the page degrades readably.
  - `catalog_json(lab)` and `state_json(lab)` (the CatalogJSON/StateJSON builders) live in the routes module (L).
    Catalog: `copy` holds each rung's approved sentence (`stepped` = the moving sentence); `idle` is true for
    `kiosk.idle_mode` or `kiosk.idle_scene`; `state` is `draft` when the current version drafts the scene and
    `is_approved` is false. State: `engine.running` = the engine exists and isn't `stopped`.
  - Error bodies beyond §4: stale → 409 `{detail, stale: true, problems}`; read-only store → 409
    `{detail, read_only: true}` (save, delete, restore check it first); T1 lock → 409 `{detail, field}`; a draft
    whose `scene_id` differs from its key → 422 on field `scene_id`; an unknown parent on save → 404.
  - Export checks staleness (`apply_drafts`) and the D63 warnings (`scene_mode_problems(rules=WARNINGS)`, 409
    `{detail, warnings}`) **before** calling `export_patch`; ExportJSON is `asdict(LabExport) + filename`.
  - Restore re-applies the version (stale → 409) before `set_current`; "approved" sets `current` to null.
  - FastAPI 0.141 nests included routers (`_IncludedRouter` in `app.routes`), so dependant walks use
    `design_lab.router.routes`, and "is it mounted" reads `app.openapi()["paths"]` (S2's
    `test_every_lab_route_requires_ops_past_the_path_table`).
- **`scripts/dev_design_lab.py`** also drops `CONDUCTOR_ENGINE` (the engine runs, as on the show box) and any
  variable whose name looks secret (`SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY|CREDENTIAL`); a `--data` dir
  is resolved and kept; the refusals are one `refusal(port, host, data, home)` helper (`parser.error`, exit 2).
- **Makefile:** the three Lab targets use `uv run --offline`.
- **Lint:** ruff 0.16 formats Python blocks inside Markdown, and this file at `9b73765` failed `make lint`; it is
  now `ruff format`ted (code blocks only; pseudo-code that doesn't parse is left as written).

---

## 3. Python contracts (Slice A)

### 3.0 Package layout and the service
```python
# conductor/design_lab/service.py
KEY = "design_lab"  # services.extras key

class LabService:
    """Per-process Lab state: the approved (git) trio, the canopy, the store. Built on first use; never touches the
    engine except to read it (Slice A)."""
    def __init__(self, services: Any) -> None:
        self.services = services
        self.approved: ConfigTrio = load_trio(Path(services.config_dir))
        self.canopy: dict = load_canopy_config(Path(services.config_dir) / "canopy.yaml")
        self.store = LookStore(Path(services.data), clock=services.clock, telemetry=services.telemetry)
        self.store.load()  # never raises (§3.5)
    def show(self) -> Any | None: return self.services.extras.get("show")          # ShowWiring
    def engine(self) -> Any | None: return self.services.extras.get("canopy")      # CanopyEngine (ENGINE_KEY)

# (as built, §2.3) the lock is per Services — extras["design_lab:lock"] — not a module-level _LOCK
async def lab_of(request: Request) -> LabService:
    # get-or-create, never `setdefault(KEY, LabService(...))` (that builds a throwaway service — ~37 ms of YAML parsing
    # on the engine's loop, measured on the Mac mini — on every request):
    #   lab = extras.get(KEY); if lab is None: async with _LOCK: re-check, then
    #   lab = extras[KEY] = await run_in_threadpool(LabService, services)
    # The corrupt-store alert is recorded on the loop thread after construction returns (store.pending_alert).
```
`load_trio` reads `config/scenes.yaml` through `conductor.scenes.load_scenes(config_dir)`, `config/modes.yaml` through
`conductor.looks.load_modes(config_dir)` and recipes through `conductor.config.load_recipes(config_dir)` — the
show's own loaders, so the Lab's "approved" is exactly what the show compiles — and **deep-copies each**
(`.model_copy(deep=True)`): `load_scenes` is `lru_cache`d and is the very `SceneCatalog` the guest panel serves
(guest_panel.py:1023), and the pydantic models are mutable (`_Strict` isn't frozen).

**Purity rule (all Lab code):** never assign into a trio object, the cached catalog or `wiring.modes_file`. Outputs
are built only by `Model.model_validate(<edited dict from model_dump()>)`. Pinned by
`test_apply_never_mutates_its_inputs_or_the_show` (§8): `model_dump()` of the input trio, of `load_scenes(config_dir)`
and (B) of `wiring.modes_file` are equal before and after a preview, a save, an export and a trial start.

CPU work (preview, export, save validation, the stale check) runs via `starlette.concurrency.run_in_threadpool`
inside `async` handlers so a preview never stalls the engine's event loop on the show box; telemetry is recorded back
on the loop thread after the call returns. Version staleness is computed lazily in the threadpool and cached by
`(version id, config_sha)`; constructing the service doesn't re-apply every saved version.

### 3.1 `SceneDraft` (`conductor/design_lab/draft.py`)
```python
from conductor.looks import field as F
from conductor.looks.schema import MOTION_WORDS, STEPPED_FALSE_CLAIMS, MOTION_PHRASES
from conductor.scenes import HEX, SceneCatalog
from conductor.config import RecipesFile, ConductorSettings

GRID: tuple[str, ...] = tuple(f"r{r}c{c}" for r in range(3) for c in range(3))
PALETTE_MIN, PALETTE_MAX = 2, 6  # Mode.preview caps at 6 (schema.py Mode.preview max_length=6)
NUMBER_NAMES = re.compile(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|both)\b", re.I)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftColour(_Strict):
    name: str = Field(min_length=1, max_length=40)  # = PaletteColor.name bounds
    hex: str  # HEX (#RRGGBB), upper-cased by a validator
    # validators: name has no MOTION_WORDS match, no STEPPED_FALSE_CLAIMS match, no LAYOUT/MOTION phrase
    # (MOTION_PHRASES), and no numeral/number word (NUMBER_NAMES) — names go into generated copy, which `_counted`
    # rewrites by number words (D62), so a colour called "Nine" would be miscounted.


class DraftStepped(_Strict):  # = SteppedTechnique's bounds (schema.py:265)
    direction: Literal["forward", "reverse"]
    steps_per_cycle: int = Field(ge=3, le=12)
    step_s: float = Field(ge=4.0, le=12.0)
    run_s: float = Field(gt=0, le=180.0)  # ≥ step_s (validator); ≤ kiosk.idle_return_s in apply


class DraftCopy(_Strict):
    # "auto" (the default): the approved sentence while §3.3 says it is still true, else the generated one; the
    # server resolves it and reports {chosen, why}. An explicit "approved" that is no longer true → 422 on save only.
    modules: Literal["auto", "approved", "generated"] = "auto"  # the T3 sentence
    stepped: Literal["auto", "approved", "generated"] = (
        "auto"  # the moving sentence (scenes with a stepped rung)
    )


class SceneDraft(_Strict):
    scene_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,40}$")  # must be an existing scene (checked in apply)
    name: str = Field(min_length=1, max_length=40)  # client copy → Scene.name and Mode.label
    mood: str = Field(min_length=1, max_length=60)  # client copy → Scene.mood and Mode.blurb
    description: str = Field(min_length=1, max_length=200)  # client copy → Scene.description
    palette: list[DraftColour] = Field(min_length=PALETTE_MIN, max_length=PALETTE_MAX)
    layout: F.FieldSpec  # LinearField | RadialField | SweepField, reused
    sample_at: Literal["centre", "mean"] = "centre"
    brightness: int = Field(ge=1, le=100)  # brightness_draft and every rung's brightness
    solid: int = Field(ge=0)  # index into palette (default_solid)
    stepped: DraftStepped | None = None  # present iff the approved ladder has "stepped"
    copy: DraftCopy = Field(default_factory=DraftCopy)
    # model validators: solid < len(palette); palette names unique (case-insensitive); name, mood and description (client copy, shown at every tier) have no MOTION_WORDS and no "270"/"nine" (scene_rules.copy_problem, also checked by the catalog rule; review 2026-10-16)
    # (it is the blurb, shown at every tier: schema.py Mode._ladder).
```
Ids, ladders and slots are **not** fields: a draft can't create a scene, rename an id, add/drop a rung or move a slot.
`lowcountry-calm` can be drafted (colours, layout, brightness, copy) but its id never changes. *(Superseded by
D75/D77: "and it never steps". Calm #1 has a breathe rung, which the Lab edits like any other rung; the show never
starts its run on its own while `motion.idle` is off.)*

**Locked to D63 in v1** (carried in the draft, refused by the validator if changed, shown read-only in the UI with
the reason): `layout.cycles` must be 1 and `sample_at` must be `centre` — `tests/test_modes_match_scenes.py` pins both
("one static design, no repeats"; every sample `at: centre`). Unlocking them is a D63 change for Alon, recorded in
D66 as an option.

Seed and equality:
```python
def seed_draft(trio: ConfigTrio, scene_id: str) -> SceneDraft
    # from Scene (name, mood, description, palette, brightness_draft, default_solid) and the mode's design
    # (modules.sample.canvas.field, .at), its stepped technique (if any); copy "auto"/"auto".
    # Hexes upper-case (the server's validator does it; the client's draftEquals normalises too, §5.2).
def is_approved(trio: ConfigTrio, draft: SceneDraft) -> bool   # draft == seed_draft(trio, draft.scene_id)
```

### 3.2 The patch function
```python
@dataclass(frozen=True)
class ConfigTrio:
    scenes: SceneCatalog
    modes: ModesFile
    recipes: RecipesFile
    def dump(self) -> dict  # {"scenes": scenes.model_dump(mode="json"), "modes": ..., "recipes": ...}

@dataclass(frozen=True)
class Problem:
    path: str     # the config path as ConfigError/scene_rules say it: "mode 'golden-hour' → techniques → modules → feature"
    field: str    # the SceneDraft field: "palette", "palette.2.hex", "layout", "brightness", "copy.modules", ...
    message: str

class DraftError(ValueError):
    problems: list[Problem]

def load_trio(config_dir: Path) -> ConfigTrio
def apply_draft(trio: ConfigTrio, draft: SceneDraft, *, canopy: dict, settings: ConductorSettings) -> ConfigTrio
def apply_drafts(trio: ConfigTrio, drafts: Mapping[str, SceneDraft], *, canopy: dict,
                 settings: ConductorSettings) -> ConfigTrio   # in catalog order; validates once at the end
def draft_changes(approved: ConfigTrio, draft: SceneDraft, *, canopy: dict) -> dict   # §3.2.5
```
Pure: no I/O, no clock, no engine. `apply_draft` = `apply_drafts(trio, {draft.scene_id: draft}, …)`.

**3.2.1 Recipe keys** (recipes.yaml's rule: "A key is the colour's name, or `<scene id>-<name>` when that name is
already taken by another colour"):
```python
def slug(name: str) -> str        # lower, [^a-z0-9]+ → "-", strip "-"; "" → "colour"
def recipe_keys(approved_mode: Mode, draft: SceneDraft, recipes: RecipesFile,
                owners: Mapping[str, str]) -> list[str]
```
- `owners` = every recipe key named by any mode's design stops → owning mode id (built from `_colour_refs`-style
  walk of the **approved** modes); keys no mode names (the colour-study colours `hd-orange`, `amber`, … `peach`) count
  as taken by "the module page".
- **Exact allocation** (deterministic; scenes processed in catalog order):
  1. Draft slugs must be unique within the draft ("Sea glass" and "sea-glass" → `Problem` on the second
     `palette.<i>.name`).
  2. **Stable keys first:** draft colour *i* whose name equals approved colour *i*'s name keeps approved stop *i*'s
     key (Golden Hour's "Peach" stays `golden-hour-peach` with a new hex). A colour whose name equals approved colour
     *j*'s name (a reorder) keeps stop *j*'s key. Every key assigned here is "assigned in this pass".
  3. Each remaining colour: candidate `slug(name)`; it is **taken** if another scene or the module page owns it in
     `owners` (computed from the approved modes **minus the keys this scene owns**), or it was assigned in this pass.
     If taken, try `<scene-id>-<slug>` under the same test; if that is taken too →
     `Problem(field=f"palette.{i}.name", "the colour name … is already used by another scene's colour …")`.
  Tests: reordering Golden Hour keeps every key; a renamed colour whose slug collides with a stable key is refused;
  "Sea glass" + "sea-glass" is refused.
- Each key's entry is rewritten in the house style: `label=name, hex, rgb_only={r,g,b,colorBrightness:100}`,
  `blend` = `rgb_only` (per-puck white unverified, gate 6), `strand={rgb:[r,g,b]}`.
- Keys this scene owned and no mode names any more are **removed** from `RecipesFile.colors`.

**3.2.2 Each rung, in lockstep** (built as a dict from `approved.modes.mode(scene_id).model_dump()` and validated
back — the purity rule of §3.0; its `ladder`, `slot` and `motion` are never touched):
- `design = FieldCanvas(kind="field", field=draft.layout, stops=[ColorStop(at=i / (n - 1), color=key_i)],
  recipe="rgb_only")` — `i / (n - 1)` as a Python float, which equals the approved `0.3333333333333333` exactly.
- `techniques.canvas` (painted three only): `canvas=design`, `brightness=draft.brightness`; `feature`,
  `motion_effect`, `motion_feature` copied.
- `techniques.slot` (painted three only): `brightness=draft.brightness`; `feature`, `speed` copied (slot copy unchanged).
- `techniques.modules`: `sample=ModuleSample(canvas=design, at=draft.sample_at, brightness=draft.brightness)`;
  `feature` per §3.3.
- `techniques.stepped` **only where the approved mode has one**: `canvas=design` (so the "stepped canvas == modules
  sample canvas" rule in `Mode._stepped_holds` holds), `direction/steps_per_cycle/step_s/run_s` from
  `draft.stepped`, `brightness=draft.brightness`, `moving_feature` per §3.3.
- `techniques.strand`: `look=LookSpec(kind="rgb", rgb=palette[solid].rgb, brightness=draft.brightness)`, no
  `fallback_look`; `feature` per §3.3.
- `label=draft.name`, `blurb=draft.mood`, `preview=[c.hex for c in draft.palette]`.
- Scene: `name, mood, description, brightness_draft=brightness, default_solid=solid,
  palette=[PaletteColor(name, hex)]`; `id` and `transform` (the 30-light strand gradient rule, D59) copied.

**3.2.3 Re-validation** (all of it, every time; first failure set wins and is returned whole):
1. `SceneCatalog.model_validate(scenes_dump)` → `scenes.yaml` problems.
2. `RecipesFile.model_validate(recipes_dump)`.
3. `parse_modes(modes_dump, recipes=…, canopy=…, path=f"the draft of {scene_id}")` (schema.py:599) — the real
   schema + `cross_check` (copy honesty, layout words, one-colour, sweep centre, slot uniqueness).
4. `conductor.show._check_stepped(settings, patched.modes)` and
   `_check_idle_scene(settings.kiosk.idle_scene, list(patched.modes.modes))` (show.py:77, :59; imported, not edited;
   **`.modes`** — `list()` of a `ModesFile` yields pydantic field tuples, so with any `idle_scene` set every draft
   would be refused; test with `settings_with(kiosk={"idle_scene": "golden-hour"})`): no run outlasts
   `idle_return_s`, the idle mode never steps. *(Superseded by D75/D77: the idle mode may carry a stepped rung, and
   `_check_stepped` refuses `motion.idle` only when it has none; the engine never starts a run on start-up or an
   idle return unless `motion.idle` is on.)*
5. `scene_mode_problems(scenes, modes, recipes, canopy, approved=trio.modes, rules=BLOCKING)` (§3.4) → problems;
   then `rules=WARNINGS` → `warnings` (returned on a 200, never a 422; §3.4).
6. Copy resolution (§3.3).
`ConfigError` text is split into `Problem`s by `problems_from_config_error(text)`: each `  • <path>: <message>` bullet
becomes one; `field` comes from `FIELD_FOR`, an ordered regex table:

| path contains | field |
|---|---|
| `→ label` / `scenes → [i] → name` | `name` |
| `→ blurb` / `→ mood` | `mood` |
| `→ description` | `description` |
| `→ preview`, `→ stops`, `palette`, `recipes` | `palette` (`palette.<i>` when an index is known) |
| `→ field` (any depth) | `layout` |
| `→ sample → at` | `sample_at` |
| `brightness` | `brightness` |
| `→ modules → feature` | `copy.modules` |
| `→ stepped → moving_feature` | `copy.stepped` |
| `→ stepped → (direction\|steps_per_cycle\|step_s\|run_s)` | `stepped.<that>` |
| `→ strand` | `solid` |
| anything else | `draft` |

**3.2.4 Ids, slots, ladders.** `apply_drafts` raises `DraftError(field="scene_id")` for an unknown scene and refuses
any result where `list(modes.modes) != list(approved.modes.modes)`, a ladder or slot differs, or `scenes` ids differ
(belt and braces; `scene_rules` "slots" checks it too).

**3.2.5 `draft_changes`** (feeds the badges, the export's decision draft and the T1 lock):
`{"approved_colours": [{"index", "from": {name, hex} | None, "to": {name, hex} | None}], "name": {from,to} | None,
"mood", "description", "layout": {"from": field_json, "to": field_json, "words_from", "words_to"} | None,
"brightness": {from,to} | None, "solid", "sample_at", "stepped", "copy": [rungs regenerated]}`. Any entry in
`approved_colours` or `name` sets `"needs_d59": true`; a `layout` entry sets `"needs_d63": true`. For a painted
scene (approved mode has a `slot`), any change to its T1 frames (the palette's hexes in stop order — a hex edit, a reorder, an added or removed colour — or the layout; not a rename, which keeps the slot hash: `draft.painted_change`) sets
`"needs_slot_repaint": true`: deployed, the restart makes `stale_slot`/`check_slots` rewrite `custom-N` on every
module (canopy.py:1962, reconciler.py:615), so the export says so (§3.6) wherever it was made (sim or show).

### 3.3 Copy: kept true, never free text
The kiosk copy for every rung comes from either the approved sentence (verbatim) or a generated one; the client
never types a feature sentence.
```python
T3_TEMPLATE = ("Colour and dimming: each Hubspace strand set to a single colour, so the palette is laid out "
               "{words}: {colours}.")
STEPPED_TEMPLATE = ("Colour and dimming through the Hubspace cloud: each Hubspace strand set to a single colour and "
                    "sent its next colour every few seconds, so the palette's colours move {flow} across the "
                    "canopy: {colours}.")
STRAND_TEMPLATE = "Colour and dimming: all nine Hubspace strands set to {solid}."
def colour_phrase(names: list[str], *, invert: bool, transform: str) -> str
    # lower-cased names in the order they sit from where the layout starts: reversed when invert; with
    # transform "mirror" → "<first> at both ends through <last> in the middle"; otherwise
    # "<a>, then <b>, <c> and <d>" (two colours: "<a>, then <b>").
def generated_copy(mode: Mode, draft: SceneDraft, canopy: dict) -> dict[str, str]
    # {"modules": T3_TEMPLATE(words=layout_words(design, canopy)), "strand": STRAND_TEMPLATE(...),
    #  "stepped": STEPPED_TEMPLATE(flow=motion_words(design, canopy, direction))}  (schema.py:447, :468)
    # The T3 sentence names only the stops the module level reaches (`reached_stops`: t at some module centre
    # within half a stop spacing), so an edge or corner sweep never names a colour no strand shows; the moving
    # sentence names every colour (each strand steps through the whole palette over a run). Review 2026-10-16.
```
Rules:
- **Strand:** approved sentence if the solid colour's name is unchanged, else `STRAND_TEMPLATE` (keeps "nine", so
  `compile._counted` rewrites it on a partial canopy, D62; ends "set to <solid name lower>." as
  `test_every_ladder_ends_on_the_solid_colour` requires).
- **T3 (`copy.modules`)**: the approved sentence is still true only while `draft.layout == seed.layout` (**model
  equality** of the field, not equal `layout_words`: the approved sentences describe D63's exact geometry, e.g.
  Golden Hour's "at the middle of one short side" stays "from one edge" at centre (0, 100) but is then false) **and**
  the palette names and their order are unchanged. `"auto"` resolves to approved when that holds, else generated;
  preview and save return `copy: {modules: {chosen: "approved"|"generated", why, sentence}, stepped: {…}}` on a 200.
  An explicit `"approved"` that no longer holds → 422 on **save** (`copy.modules: "the approved sentence no longer
  describes this design (…)"`); preview resolves it as auto and says why, so the client never sees a refusal.
- **Stepped (`copy.stepped`)**: same rule plus `draft.stepped.direction` unchanged.
- **T1/T2 sentences** are copied unchanged (they name no colour or layout; "270"/"nine" stay for `_counted`).
- The generated sentences carry no `MOTION_WORDS` outside the moving one, the layout phrase from `layout_words`
  (and no other `LAYOUT_PHRASES`), `STEPPED_PACE` ("every few seconds") and no `STEPPED_FALSE_CLAIMS` — and
  `parse_modes` checks them anyway (step 3 above).

### 3.4 The extracted validator (`conductor/looks/scene_rules.py`)
```python
BLOCKING = ("catalog", "brightness", "static", "ladder-end", "slots", "one-design", "recipes", "stepped")
WARNINGS = ("two-d", "kits-differ")   # D63 design-quality: a 200 preview with warnings; save OK; export 409
RULES = BLOCKING + WARNINGS
LIVE_KITS = ("r0c0", "r0c1")    # the Mac box's two shop kits (D62)

@dataclass(frozen=True)
class SceneProblem:
    rule: str; mode: str; path: str; message: str
    def __str__(self) -> str: return f"mode {self.mode!r} → {self.path}: {self.message}"

def scene_mode_problems(scenes: SceneCatalog, modes: ModesFile, recipes: RecipesFile, canopy: Mapping[str, Any], *,
                        approved: ModesFile | None = None, rules: Iterable[str] = RULES) -> list[SceneProblem]
def module_colours(mode: Mode, recipes: RecipesFile, canopy: Mapping[str, Any]) -> dict[str, tuple[int, int, int]]
    # position → T3 RGB via compile.module_look over DeviceEntry(id=f"rules-{pos}", role="canopy_module",
    # account="rules", module=pos) — module_look needs no capabilities
def delta_e76(a: tuple[int, int, int], b: tuple[int, int, int]) -> float   # moved from the test's _lab/_delta_e
def scene_pins(scenes: SceneCatalog, modes: ModesFile, recipes: RecipesFile, canopy: Mapping[str, Any]) -> dict
```
What moves out of `tests/test_modes_match_scenes.py` (the invariants; each rule = one old test):

| rule | old test | invariant |
|---|---|---|
| catalog | `test_the_modes_are_the_scenes_ids_labels_blurbs_and_previews_in_order` | mode ids == scene ids in order; label == name; blurb == mood; preview (upper) == palette hexes |
| brightness | `test_every_tier_uses_the_scenes_brightness_and_nothing_moves` (levels) | canvas/slot/modules/stepped/strand brightness == `brightness_draft` |
| static | same test (motion part) | no `motion_effect`/`motion_feature`, slot `speed == 0`, `mode.motion is False`, design `cycles == 1` |
| ladder-end | `test_every_ladder_ends_on_the_solid_colour` | ladder ends `[modules, strand]`; strand rgb == solid; no `fallback_look`; feature ends `set to <solid lower>.` |
| slots | `test_three_modes_are_painted_one_per_slot_and_every_mode_steps_before_module_level` | with `approved`: same mode ids, same ladder, same slot per mode; canvas technique ⇔ slot |
| one-design | `test_each_scene_is_one_design_whose_stops_are_its_palette` (not its layout pin) | `FieldCanvas`, `rgb_only`, `at == "centre"`, stops at i/(n−1), stop recipes' hex/label == palette, canvas == sample design |
| recipes | `test_every_design_recipe_is_rgb_only_with_no_white` | each design key: `rgb_only` == hex at 100, no white; `blend == rgb_only`; `strand.rgb` == hex; one key per palette colour |
| two-d | `test_module_level_is_the_design_at_each_modules_centre` (count), `test_the_module_level_copy_names_the_designs_layout` (uniform rows/cols), `test_no_scene_reads_as_three_columns_at_module_level` | 4–9 distinct T3 colours; no uniform rows/columns; max column ΔE76 ≥ max row ΔE76 / 3 |
| kits-differ | `test_on_the_partial_live_canopy_the_two_kits_show_different_colours` | T3 at r0c0 ≠ r0c1 for every scene |
| stepped | `tests/test_d65_modes.py:70-90` | stepped hold == T3 rows; `steps_per_cycle` distinct frames; each frame > 2 colours; the moving copy names `motion_words` and `STEPPED_PACE` |

**Why two rules only warn:** `two-d` and `kits-differ` refuse 65 of 360 Golden Hour linear angles (0–15°, 165–195°,
345–359° stripe; 90–91° and 270° make the kits equal; client-UX scan). Blocking them would freeze the stage on the
two most natural asks (left-to-right, top-to-bottom). They come back as `warnings: [{rule, field: "layout",
message}]` with one line each ("Reads as stripes at module level (D63)" / "Modules 1 and 2 would show the same
colour"); save stores them; **export answers 409 while any warning stands**, naming the failing test, because the
suite would fail.

**How the existing tests use it — every existing assertion stays verbatim** (only pinned values move, §3.7): each
test above gains one extra line `assert scene_mode_problems(SCENES, MODES, RECIPES, CANOPY, rules={rule}) == []`
alongside its old assertions. The `slots` rule is called there with `approved=None` (ids, ladders and slots never
move), so the literal slot map and ladders in `test_three_modes_are_painted_one_per_slot…` stay, and so does the
per-module compiler oracle `colours[pos] == _palette_at(...)` in `test_module_level_is_the_design_at_each_modules_centre`.
The D63 design pins (LAYOUTS, the words dict, Calm's centre, Goodnight's moon, Golden Hour's sun) read the `_pins`
fixture (§3.7). `tests/test_scene_rules.py` proves the move: for each rule a crafted mutation of the shipped trio
(e.g. a preview hex off by one, brightness 94 on one rung, `cycles: 2`, ladder without `strand`, slot moved, stop at
0.4, `blend` with white, linear 0° design, a design giving r0c0 == r0c1, `steps_per_cycle: 12` on a design whose
frames repeat) yields exactly that rule's problem, and the shipped config yields none. Mutation targets: delete the
slots check from `scene_rules` → `test_scene_rules` fails; move a slot in `modes.yaml` → the old literal test fails.
`/api/lab/preview` runs all rules via `apply_drafts` step 5.

### 3.5 The store: `<data>/overrides/looks.yaml` (`conductor/design_lab/store.py`)
```yaml
format: 1
seq: 7                       # last version number issued
current: v0007               # the version the Lab opens on (null = approved)
versions:
  - id: v0007                # f"v{seq:04d}"
    name: "Client Oct 17 v3 – warmer sunset"
    note: "warmer sunset"
    created_at: 1792270800.0 # services.clock.now()
    parent: v0006            # the version it was edited from (null = approved); "undo" restores it
    config_sha: 3f9a1c0e2b7d # sha256[:12] of config/{scenes,modes,recipes}.yaml bytes at save
    drafts:                  # only the scenes that differ from approved at save time
      golden-hour: {scene_id: golden-hour, name: …, palette: [...], layout: {...}, …}   # SceneDraft.model_dump(mode="json")
```
```python
LOOKS_FILE = "looks.yaml"; MAX_VERSIONS = 200; MAX_BYTES = 1_000_000
class Version(_Strict):
    id: str = Field(pattern=r"^v\d{4,}$"); name: str = Field(min_length=1, max_length=80)
    note: str = Field("", max_length=60); created_at: float; parent: str | None = None
    config_sha: str = Field(pattern=r"^[0-9a-f]{12}$"); drafts: dict[str, SceneDraft] = Field(default_factory=dict)
class LooksFile(_Strict):
    format: Literal[1] = 1; seq: int = Field(0, ge=0); current: str | None = None
    versions: list[Version] = Field(default_factory=list, max_length=MAX_VERSIONS)

class LookStore:
    def __init__(self, data: Path, *, clock: Clock, telemetry: Telemetry) -> None
    path: Path            # data / "overrides" / "looks.yaml"
    error: str | None     # why the last load failed (None = fine)
    set_aside: str | None # the name the bad file was moved to
    def load(self) -> LooksFile                       # never raises (below)
    def file(self) -> LooksFile                       # the loaded state
    def get(self, version_id: str) -> Version         # KeyError
    def save_version(self, *, drafts: Mapping[str, SceneDraft], note: str, name: str | None, parent: str | None,
                     approved: ConfigTrio, canopy: dict, settings: ConductorSettings, config_sha: str) -> Version
    def delete_version(self, version_id: str) -> None # KeyError; clears `current` if it pointed there
    def set_current(self, version_id: str | None) -> None
def default_name(note: str, now: float, existing: Iterable[str]) -> str
    # local date of the box: f"Client {dt:%b} {dt.day} v{N}" + (f" – {note}" if note else ""); N = 1 + the largest
    # number captured by ^Client <Mon> <D> v(\d+) among existing names (0 when none) — not a count, so deleting v1
    # of v1, v2 doesn't reissue "v2" (test: save v1, v2, delete v1 → next is v3)
def config_sha(config_dir: Path) -> str
```
- **Writes follow `save_overlay_patch`** (config.py:455): build the whole proposed `LooksFile`, validate it (pydantic +
  `apply_drafts` of the *new* version against `approved` — older versions are not re-validated, they may be stale),
  `yaml.safe_dump(sort_keys=False, allow_unicode=True)`, refuse > `MAX_BYTES`, write the history copy
  `overrides/history/looks-<UTC %Y%m%dT%H%M%SZ>.yaml` (suffix `-2`, `-3` … if that name exists), then
  `conductor.tokens.atomic_write(path, text, mode=0o600)`. One `threading.Lock` around read-modify-write. Never
  writes into `config/`. The show never reads this file.
- **Per-version parsing:** the top level (`format`, `seq`, `current`, `versions` as a list of raw dicts) is
  validated first; each `Version` is then validated **on its own**. One unreadable version (a field added in Slice C
  and rolled back, a tightened bound) is listed as `{"id", "unreadable": true, "error"}`, kept in the file verbatim on
  the next save, and never fatal; only a bad top level counts as a corrupt file.
- **Size first:** `stat` the file and treat > `MAX_BYTES` as corrupt **without reading it**.
- **Corrupt file** (unreadable YAML, bad top level, unknown `format`, oversize): `os.replace` it to
  `overrides/looks.corrupt-<UTC>.yaml` (`-2`, `-3` … on a name collision; chmod 0600), start with an empty
  `LooksFile` in memory (nothing written until the next save), set `error`/`set_aside`, log a warning, and raise the
  /ops attention item (below). **If the read fails with an `OSError`, or the `os.replace` fails**, the file is left
  where it is and the store goes **read-only** (`store.read_only = True`; save/delete/set-current answer 409 "The
  Lab's store couldn't be set aside: <reason>; saved versions are kept as they are.") so the next save never
  overwrites the evidence. Tests: one bad version among good ones; an unwritable `overrides/` (chmod 0500); an
  oversize file is never read (patch `Path.read_text` to fail); a set-aside name collision. The alert goes through the
  **existing alerts mechanism**: `telemetry.record("alert", level="warn", message=f"Design Lab: overrides/looks.yaml
  couldn't be read ({reason}), so it was set aside as {set_aside} and the Lab started with no saved versions. The
  show doesn't use this file.")` — `AlertBoard` (conductor/alerts.py) keeps warn alerts and `/api/devices`
  (routes/devices.py:336, `AlertBoard.of(services).active()`) lists them under "Needs attention"; ops can ack it.
  No change to `alerts.py`. `reason` is ≤ 120 chars (first line of the error). `load()` only stores the alert as
  `pending_alert`; `lab_of` records it on the loop thread once the threadpool construction returns. The read-only
  case alerts too ("… couldn't be read or set aside ({reason}); the Lab won't save until it is moved by hand.").
- **Stale versions:** lazily (in the threadpool, cached by `(version id, config_sha)`, §3.0), each version's drafts
  are re-applied against the current approved trio; a failure marks it `stale: true` with its problems in API answers
  (not a load failure). Its `config_sha` differing from the current one is reported as "saved against an older
  config/".
- Per-scene state is **computed**, never stored: `approved` (git) when `is_approved`, `draft` otherwise, `in try-on`
  when the engine's trial is that scene (Slice B).

### 3.6 Export (`conductor/design_lab/export.py`)
```python
EXPORT_FILES = ("config/scenes.yaml", "config/modes.yaml", "config/recipes.yaml", "tests/fixtures_scene_frames.json")
@dataclass
class LabExport:
    patch: str                  # unified diff, "a/<path>" / "b/<path>" headers, git-apply-able ("" = nothing changed)
    files: list[str]            # which of EXPORT_FILES changed
    scenes: list[str]           # scene ids rewritten
    decision: str               # the drafted DECISIONS entry (markdown)
    approved_changes: list[str] # D59 sentences ("Golden Hour: Sunset pink #FF9BC4 → #FF8FBF")
    layout_changes: list[str]   # D63 sentences
    review_notes: list[str]     # e.g. comments kept in a rewritten block that may now be stale
    problem: str = ""           # set when the edited files didn't re-read as the patched trio: then patch == ""

def export_patch(approved: ConfigTrio, patched: ConfigTrio, *, root: Path, canopy: dict, version: Version | None,
                 now: float) -> LabExport
def rewrite_scenes_yaml(text: str, patched: ConfigTrio, scene_ids: list[str]) -> str
def rewrite_modes_yaml(text: str, approved: ConfigTrio, patched: ConfigTrio, scene_ids: list[str]) -> tuple[str, list[str]]
def rewrite_recipes_yaml(text: str, approved: ConfigTrio, patched: ConfigTrio) -> str
def frames_fixture(scenes: SceneCatalog, pins: dict) -> str
def reread(texts: Mapping[str, str], canopy: dict) -> ConfigTrio
```
- **In-place block rewrite**, comments and anchors kept (the `overlay_diff` approach, config.py:508): a small
  line-oriented locator walks the file, tracks the indent stack of keys to give each line its key path, and edits
  only lines whose value changed, keeping each line's trailing `# comment` (except the comment on a design's anchor
  line, `canvas: &golden-design   # …`, which is replaced with `# <layout words> (Design Lab: <version name>)` when
  the layout or palette changed). Specifically:
  - `scenes.yaml`: in the `  - id: <scene>` block, the `name`/`mood`/`description` (JSON-style double-quoted, as
    today), `brightness_draft`, `default_solid` lines, and the `palette:` item lines (re-emitted
    `      - {name: "…", hex: "#…"}`).
  - `modes.yaml`: in the `  <scene>:` block, `label`, `blurb`, `preview` lines; the design's `field: {…}` flow line
    (key order `kind` first, numbers without a trailing `.0` when integral) and its `stops:` item lines under the
    anchored (`&…-design`) or inline canvas; every `brightness:` value in the block, including inside the flow lines
    `sample: {canvas: *x, at: centre, brightness: 95}` and `look: {kind: rgb, rgb: [...], brightness: 95}   # <solid>`
    (that comment becomes the solid name); `at:`; the `feature:`/`moving_feature:` lines of the modules, strand and
    stepped techniques; the stepped `direction`, `steps_per_cycle`, `step_s`, `run_s` lines. Aliases (`*tides-design`)
    are untouched, so every rung still shares one design.
  - `recipes.yaml`: changed key blocks rewritten (6 lines, house style); new keys inserted after the last key the
    scene owns; removed keys' blocks deleted.
  - `tests/fixtures_scene_frames.json`: **built in memory, never skipped**: `before = frames_fixture(approved.scenes,
    scene_pins(approved…))`, `after = frames_fixture(patched.scenes, scene_pins(patched…))`, each
    `json.dumps({**{s.id: [list(c) for c in s.frame(30)] for s in scenes}, "_pins": pins}, indent=0)` with **no**
    trailing newline (the current file is exactly `json.dumps(…, indent=0)`; verified byte-equal on an unchanged
    export). The file on disk is read only to cross-check when it exists (Docker/staging images have no `tests/`,
    .dockerignore): if it differs from `before`, `problem` is set ("tests/fixtures_scene_frames.json isn't what the
    approved config gives; run make scene-pins first") and no patch is offered.
  - **Scalar emission:** every string scalar the rewrite emits (scenes `name`/`mood`/`description`/palette `name`,
    modes `label`/`blurb`/`feature`/`moving_feature`, recipes `label`) is double-quoted via `json.dumps(s,
    ensure_ascii=False)` (valid YAML), so client copy like "Off", "No", "Null", "Dusk: rose" or a trailing " #" can't
    become a bool, null, mapping or comment. Flow collections (`field`, stop items, palette items, `sample`, `look`,
    `preview`) go through `conductor.config._flow` (config.py:502). Test
    `test_client_copy_with_yaml_specials_round_trips` ("Off", "No", "Dusk: rose", "Charleston’s", a `"`).
  - **Comments:** when a design's field changes, full-line comments **inside** that design mapping are dropped (Golden
    Hour's `metric: canopy` / sun lines) and the anchor-line comment is replaced (above); stop lines are re-emitted
    **without** trailing comments whenever any stop's `at` or the stop count changes (Coastal Tides' "1/3 exactly");
    the trailing comments on the stepped `stepped:` and `direction:` lines are cleared when the layout or direction
    changes ("rays move out from the left edge", "outwards from the sun"); each file-header line that names the
    scene is listed in `review_notes` with its line number. Test: Golden Hour → linear 30° leaves no "metric: canopy"
    or "sun" comment in its block.
- **Round-trip proof, every export:** `reread(after_texts)` parses the edited texts through the real loaders
  (`SceneCatalog.model_validate(yaml.safe_load(...))`, `RecipesFile.model_validate`, `parse_modes(…, canopy=…)`) and
  must give `model_dump()` equality with `patched` for all three files, and every *other* scene must dump equal to
  `approved`; otherwise `problem` is set and no patch is offered. An unchanged version exports `patch == ""` and
  `reread(before) == approved` (model_dump equality, not bytes).
- **Unified diff:** `difflib.unified_diff(before, after, f"a/{path}", f"b/{path}")` per file, concatenated in
  `EXPORT_FILES` order, with `\ No newline at end of file` handled for the JSON fixture. `root` =
  `Path(services.config_dir).parent`. Test `test_an_export_without_a_tests_dir_still_patches_the_fixture` (a root
  holding only `config/`).
- **Refused while warnings stand:** a version whose drafts carry a `two-d`/`kits-differ` warning exports 409
  `{detail: "<Scene>: <warning> — tests/test_modes_match_scenes.py::<test> would fail", warnings}`.
- **Slot repaint:** when `needs_slot_repaint`, the decision draft, `review_notes` and the PR text say "Deploy only in
  a commissioning window: on restart the reconciler rewrites custom-N on every module (stale slot)." (test: an
  exported Golden Hour layout change carries the flag and the sentence).
- **The Studio's own `SCENES`:** for each scene whose palette changed (hexes, names, order or length), `review_notes` and
  the decision draft say "demo-model.mjs SCENES still has <old hex> for <Scene>; the Studio's shop-kit Apply buttons
  (studio.js:314, :340) send it until SCENES is updated by hand." (or, with no approved hex changed, that SCENES still
  has the approved N-colour palette); and when the name, solid or brightness changed, that SCENES still opens the
  scene on the old ones (review 2026-10-16).
- **Decision draft** (`decision`): "**D<next> — <scene names>: Design Lab version "<name>" (<date>).** Approved
  colour changes (D59; needs the client's sign-off and a D59 update): … Strand solid (a draft default, not a D59
  colour): … Layout changes (D63; Paris reviews): … Stepped timing (D65; live off until Gate R | live on
  (motion.stepped.live_enabled), from the settings; `stepped_live_clause`): … Brightness
  (drafts): … Client copy edited (check it is true at every tier): … Copy regenerated: … Exported from the Design Lab
  (D66); verified: re-read equal, suite to run in the PR." The D59 list is exactly what sets `needs_d59` (a scene's
  name, its palette colours); stepped changes are `stepped_changes`, never `layout_changes` (review 2026-10-16).
- Only those four files; `demo-model.mjs` is untouched (its `SCENES` stay Paris's handoff; the Lab never reads them).

### 3.7 Pins: making edited exports pass the suite
Tests that pin an **approved value** (not an invariant) would fail on any edited export. They are moved onto the
pinned fixture the export regenerates: `tests/fixtures_scene_frames.json` gains a top-level `"_pins"` key (the scene
keys and their 30-light frames stay as they are, so `test_each_scene_reproduces_the_handoff_frame_exactly` is
unchanged):
```json
"_pins": {"golden-hour": {"layout": {"kind": "radial", "centre_in": [0.0, 60.0], ...}, "layout_words": "from one edge",
          "sample_at": "centre", "solid": "Sunset pink", "brightness": 95,
          "t3": {"r0c0": [r, g, b], ... 9},
          "stepped": {"direction": "forward", "steps_per_cycle": 6, "step_s": 6.0, "run_s": 90.0},  // stepped scenes only
          "flow_words": "from one edge"}, ...}                                                       // stepped scenes only
```
`scene_pins()` computes it (`layout` = the design field's `model_dump(mode="json")`, `t3` = `module_colours`,
`stepped` = the stepped technique's four values, `flow_words` = `motion_words(design, canopy, direction)`). There is
**no** `d59_columns` pin: those tests pin compiler behaviour, not approved values (below). Pin assertion messages say
"approved value changed: run make scene-pins and review the fixture diff".

**Decoupling the compiler tests from the approved config (the rename blocker).** `tests/fixtures_modes_field.yaml`
and `tests/fixtures_modes_stepped.yaml` name the scenes' recipe keys (`golden-hour-peach`, `sea-glass`, `aqua`, …),
and `test_compile_field.py`, `test_compile_stepped.py`, `test_engine_program.py` compile them against
`load_recipes()` from `config/`. An export that renames, adds or removes a colour (orphan keys are removed, §3.2.1)
would then fail those modules at collection. So: `tests/fixtures_recipes_frozen.yaml` holds today's 20 scene-palette
recipe entries verbatim, and those three modules use
`RecipesFile.model_validate({**load_recipes().model_dump(), "colors": {**load_recipes().model_dump()["colors"],
**frozen["colors"]}})` (the colour-study keys still come from config; frozen wins for the scene keys). `D59_COLUMNS` stays **literal**; `test_d59s_columns_are_each_scenes_x_gradient_at_the_column_centres`
builds its palettes from the fixture YAML's own `preview` hexes instead of `load_scenes()`.

S3 converts, reading `_pins` (or the scene catalog / `RECIPES`, or the live mode, where the value is just a palette
colour, a recipe or plumbing):
- `test_modes_match_scenes.py`: `LAYOUTS`; the words dict in `test_the_layouts_name_where_each_design_sits`;
  Calm's `(209, 234, 227)` and Goodnight's `(196, 214, 243)`; `test_golden_hours_sun_shows_at_module_level` runs
  only while `_pins["golden-hour"]["layout"]` is D63's sun, else `pytest.skip("Golden Hour's sun was redesigned in
  the Design Lab; see DECISIONS")`.
- `test_compile_field.py`: pastel coral / lilac literals → `RECIPES.colors[...]` (frozen recipes above).
- `test_compile.py::test_module_level_looks_follow_canopy_position` (sunset/calm literals → `_pins` t3),
  `::test_the_strand_fallback_is_the_scenes_solid_colour_on_every_module` (aqua → the scene's solid).
- `test_kiosk_routes.py` (Calm `#D1EAE3`), `test_canopy_view.py` (`#F7F2E7`, `#6FB5D5`), `test_soak.py`
  (periwinkle), `test_compile_stepped.py` (`#FF9E96`), `test_scenes_catalog.py::test_solid_fallbacks_match_the_handoff_defaults`.
- `test_d65_modes.py`: `STEPPING` → `_pins[*]["flow_words"]`; the `("forward", 6, 6.0, 90.0)` defaults →
  `_pins[*]["stepped"]`; `len(steps) == 6` / `len(set(frames)) == 6` → `== stepped.steps_per_cycle`;
  `test_golden_hours_rays_move_out_from_the_middle_of_the_left_edge` guarded like the sun test.
- `test_module_api.py:377/:393` (Goodnight r1c1 `{191,178,233}`, Golden Hour r1c2 `{219,116,167}`) → `module_look`
  of the live mode at that position (plumbing, not a pin); `test_ops_routes.py:178` (Golden Hour's literal T3
  sentence) → `load_modes().mode("golden-hour").techniques.modules.feature`.
- `test_engine_ripple.py:216-230` (each scene's first ring, Coastal Tides row by row) → run only while
  `_pins[id]["layout"]` equals the D63 layout, else `pytest.skip` (as the sun test).
- **Before fan-out**, L runs the harness once (`LAB_ROUNDTRIP=1`, §8) on the three canonical edits **plus** a rename,
  an added colour, a removed colour and a reorder, and pastes the full failure list here; S3 converts exactly that
  list. (Critic's run on HEAD: Golden Hour edit alone 11 failed — 2 of them `test_workshop`'s `git ls-files`, an
  artefact of the extract, fixed in §8; all three edits 22 failed.)

**Regenerating pins outside the Lab:** `scripts/regen_scene_pins.py` (`make scene-pins`) writes
`frames_fixture(load_scenes(), scene_pins(...))` to `tests/fixtures_scene_frames.json`, so a hand edit of
`modes.yaml` (or Paris's D63 review merged as YAML) has a path; a test pins its output byte-equal to the committed
fixture.

### 3.8 The T1 lock after commissioning
```python
T1_LOCKED = "changing this needs a slot repaint at commissioning"
def t1_lock(lab: LabService, scene_id: str) -> dict
    # {"locked": bool, "simulated": bool, "slot": "custom-3" | None, "positions": [module ids], "sentence": str | None}
```
- Applies only to scenes whose approved mode has a `slot` (the painted three).
- **Commissioned** = some registry canopy module has a record of origin `"api"` for that slot: through the engine
  when it runs, `engine.recorded_slot(entry.id, slot)` (canopy.py:1872 — hand-painted record first, then this
  process's commissioning, then the registry overlay), else `entry.slot_record(slot)` (`DeviceEntry`, config.py) over
  `services.registry.canopy_modules()`. (`ShowWiring._record` does exactly this; it is private, so the Lab repeats
  the two lines rather than calling it.)
- **Enforced only on a non-simulated driver** (`services.engine_driver.simulated is False`); an export made anywhere
  (8200 sim, staging) still carries `needs_slot_repaint` and its deploy sentence (§3.2.5, §3.6). In sim the slots are
  commissioned automatically at start-up (show.py `_sim_commission`), so the lock would always be on; there the
  answer is `{"locked": false, "simulated": true, "sentence": "In the simulator the slots are commissioned at
  start-up; on the show box this scene's painted design is read-only once commissioned."}`.
- When locked, a draft that changes the **frames** of that scene (palette hexes, their order and count, layout,
  sample_at: `draft.painted_change`, the same predicate as `needs_slot_repaint`; a colour rename keeps the slot hash,
  so it stays editable — review 2026-10-16) is refused
  by preview and save with **409** `{detail: "<Scene>: " + T1_LOCKED + ".", field: "palette"|"layout"}`; brightness
  (a control row, not slot content), the strand solid, client copy and stepped timing stay editable. The UI disables
  the locked controls and shows the sentence.

---

## 4. HTTP API (`conductor/routes/design_lab.py`)

```python
from conductor.routes.ops import _no_cross_site  # reused: same-origin + JSON-only for non-GET

router = APIRouter(prefix="/api/lab", dependencies=[Depends(require_ops), Depends(_no_cross_site)])
```
`require_ops` first (a kiosk/tester/vignette gets 403/401 before anything else; the auth middleware's path table
already denies `/api/lab/*` to every non-ops role). Every answer has `Cache-Control: no-store`. Non-GET requests must
send `Content-Type: application/json` — including `DELETE` (the client sends `{}`). Errors use FastAPI's
`{"detail": …}`, extended as below.

| Method & path | Request | 200/201 response | Errors | Telemetry (`kind="lab"`) |
|---|---|---|---|---|
| `GET /api/lab/catalog` | — | `CatalogJSON` | — | — |
| `GET /api/lab/state` | — | `StateJSON` | — | — |
| `POST /api/lab/preview` | `{scene_id, draft: SceneDraft}` | `PreviewJSON` | 404 unknown scene; 409 T1 lock; 422 problems | — |
| `GET /api/lab/versions` | — | `{versions: [VersionSummary], current}` | — | — |
| `GET /api/lab/versions/{id}` | — | `VersionJSON` | 404 | — |
| `POST /api/lab/versions` | `{drafts: {scene_id: SceneDraft}, note?: str ≤60, name?: str ≤80, parent?: id}` | 201 `VersionJSON` | 409 T1 lock / store unwritable; 422 problems | `action="save", version, name, scenes, needs_d59` |
| `DELETE /api/lab/versions/{id}` | `{}` | `{deleted: id, current}` | 404 | `action="delete", version` |
| `POST /api/lab/restore` | `{version: id \| "approved"}` | `{current, drafts: {scene_id: SceneDraft}}` (all six, seeded where not drafted) | 404; 409 stale (with problems) | `action="restore", version` |
| `GET /api/lab/export?version=<id>&format=json\|patch` | — | json: `ExportJSON`; patch: `text/x-diff` attachment `design-lab-<id>-<UTC>.patch` | 404; 409 `{detail, problem}` when the re-read check fails; 409 stale; 409 `{detail, warnings}` while a D63 warning stands | `action="export", version, files, needs_d59, needs_slot_repaint` |
| **B** `GET /api/lab/trial` | — | `TrialJSON` | 503 no engine | — |
| **B** `POST /api/lab/trial/start` | `{scene_id, draft, confirm?: str}` | `{trial: TrialJSON, state}` | 409 `{detail: confirm sentence, confirm_required: true, confirm, cost}`; 409 engine refusal sentence (+ `retry_after_s`); 409 apply gap (+ `retry_after_s`, `Retry-After`); 422 problems; 503 no engine | `action="trial_start"`/`"trial_apply"`/`"trial_refused", scene, modules, bound_s` |
| **B** `POST /api/lab/trial/stop` | `{}` | `{trial: null, state}` | 503 | `action="trial_stop"` (the bound's end: `"trial_end"`, from the engine) |

Response shapes (field names are the contract; S4 builds against them):
```jsonc
// CatalogJSON
{"simulated": true, "driver": "sim", "config_sha": "3f9a1c0e2b7d",   // keys the browser's working drafts (§5.3)
 "limits": {"palette": [2, 6], "brightness": [1, 100], "name": 40, "mood": 60, "description": 200,
            "stepped": {"steps_per_cycle": [3, 12], "step_s": [4, 12], "run_s_max": 180},   // run_s_max = min(180, idle_return_s)
            "canopy": {"width_in": 225, "height_in": 120}},
 "live": {"stepped_live_enabled": false, "stepped_live_sentence": "<STEPPED_LIVE_OFF>", "ripple_stagger_s": 0,
          "effects_enabled": false},
 "scenes": [{"id": "golden-hour", "name": "Golden Hour", "mood": "...", "description": "...",
             "ladder": ["canvas","slot","stepped","modules","strand"], "slot": "custom-3", "painted": true,
             "stepped": true, "idle": false,
             "approved": SceneDraft,                 // seed_draft: what the Lab starts from
             "design": DesignJSON,                   // mode_design() of the approved mode (routes/design.py:98 shape)
             "copy": {"canvas": "...", "slot": "...", "modules": "...", "stepped": "...", "strand": "..."},
             "state": "approved" | "draft" | "in try-on",   // vs the current version
             "t1_lock": T1LockJSON,
             "option_of": "golden-hour" | null,      // D76: the base scene this one is an option of (git's)
             "group": "golden-hour",                 // its base's id, or its own
             "on_ipad": true}]}                      // D76: false = /ops took its card off the iPad (read only here)
// StateJSON
{"store": {"ok": true, "error": null, "set_aside": null, "read_only": false, "versions": 7, "unreadable": 0},
 "current": "v0007",
 "engine": {"running": true, "simulated": true, "commissioning": false, "paused": false, "outage": false},
 "trial": TrialJSON | null}                          // null in Slice A
// PreviewJSON
{"scene_id": "golden-hour", "label": "Golden Hour", "approved": false,
 "design": DesignJSON,                               // mode_design(scene, CompileContext(registry=[], capabilities={},
                                                     //   modes=<patched>, recipes=<patched>, canopy=…)): heads ×270, modules ×9
 "brightness": 95,
 "tiers": [{"tier": "canvas", "label": "Painted (all 270 lights)", "draws": "heads", "feature": "..."},
           {"tier": "slot", "label": "Painted in the app", "draws": "heads", "feature": "...",
            "note": "hand-painted in the Hubspace app to match; only after it is recorded"},
           {"tier": "stepped", "label": "Module level, stepped", "draws": "modules", "feature": "<still>",
            "moving_feature": "..."},
           {"tier": "modules", "label": "Module level (one colour per strand)", "draws": "modules", "feature": "..."},
           {"tier": "strand", "label": "Strand (one colour)", "draws": "solid", "solid": [255,155,196], "feature": "..."}],
 "today": TodayJSON,                                 // "what this runtime's lights would show" (title below)
 "show_box_today": TodayJSON,                        // pinned until the Oct 19 undo: Modules 1–2, module level (§4)
 "warnings": [{"rule": "two-d", "field": "layout", "message": "Reads as stripes at module level (D63)"}],
 "copy": {"modules": {"chosen": "approved" | "generated", "why": "...", "sentence": "..."},
          "stepped": {...} | null},
 "changes": <draft_changes>, "t1_lock": T1LockJSON,
 "stepped": null,                                    // Slice C (§7)
 "rings": null,                                      // Slice C (§7)
 "elapsed_ms": 140}
// TodayJSON
{"title": "What this simulator shows" | "What the lights can show today" | "Show box today (Modules 1–2, module level)",
 "tier": "modules" | null, "label": "...", "feature": "<as the iPad shows it on this runtime>",
 "lit": {"level": 95, "modules": [{"pos": "r0c0", "state": "lit" | "dark", "color": "#RRGGBB" | null,
                                   "colors": ["#RRGGBB" × 30] | null} × 9]},   // lit_state's module shape
 "heads": "approved" | "draft" | null,               // canvas/slot today: the slots hold the APPROVED design
 "positions": {"driven": ["r0c0","r0c1"], "left_out": ["r0c2", ...]}, "note": "..."}
// 422
{"detail": "The draft has 2 problem(s).", "problems": [{"path": "...", "field": "copy.modules", "message": "..."}]}
// VersionSummary
{"id": "v0007", "name": "...", "note": "...", "created_at": 1792270800.0, "parent": "v0006",
 "scenes": ["golden-hour"], "needs_d59": true, "stale": false, "problems": [], "older_config": false}
// VersionJSON = VersionSummary + {"drafts": {scene_id: SceneDraft}}
// ExportJSON = LabExport fields + {"filename": "design-lab-v0007-20261017T153000Z.patch"}
```
**Preview details (S1, `conductor/design_lab/preview.py`):**
```python
def design_registry() -> list[DeviceEntry]   # nine DeviceEntry(id=f"design-{pos}", role="canopy_module",
                                             #   account="design", module=pos, expected_heads=30)
def design_caps() -> Capabilities           # detect(*profile_parts("sqhdk03-sim")), cached
def preview(lab: LabService, draft: SceneDraft) -> dict          # PreviewJSON; raises DraftError / T1Locked
def tiers_json(patched: ConfigTrio, scene_id: str, canopy: dict) -> list[dict]
def today_view(lab: LabService, patched: ConfigTrio, scene_id: str) -> dict
```
- The design goes through `mode_design` (routes/design.py:98) — the real composer/`render.head_colors`/`module_look`.
- `tiers_json`: per ladder rung, `compile_mode(scene, tier, CompileContext(registry=design_registry(),
  capabilities={d.id: design_caps() …}, modes=patched.modes, recipes=patched.recipes, canopy=canopy))` for
  canvas/modules/stepped/strand (the full canopy's sentence; `flags=None`, `addresses=None`); the slot rung's
  sentence is `techniques.slot.feature` (T2 compiles only with an app-recorded hash).
- `today_view` (read-only use of the running show): `wiring = lab.show()`; none → `{"tier": null, "note": "The
  canopy engine isn't running here."}`. Else `ctx = dataclasses.replace(wiring.context(modes=patched.modes),
  recipes=patched.recipes)` (show.py:349 passes no `recipes`, so without the replace the draft's T3 colours would come
  from the git recipes: a hex edit would show the approved colour, and a renamed colour fails modules and drops to
  strand; Slice A doesn't edit show.py, so it replaces the field on the returned `CompileContext` dataclass). The
  show's registry, capabilities, flags, addresses and D62 left-outs apply. Walk `ladder(scene, patched.modes)`, skip
  any tier where `wiring.tier_allowed(scene, tier)[0]` is False (show.py:445: T1 needs commissioned records,
  stepped needs sim or `live_enabled`), `compile_mode(scene, tier, ctx)`, first that compiles wins (floors ignored:
  a temporary downgrade isn't "today"). `lit` is in **`lit_state`'s module shape** (canopy_view.py:204), built
  from the plan's `ModuleTarget.colors` through `canopy_view.module_colours(cfg, entry, target.colors)` (the same
  per-head expansion the guest panel gets), every driven module `state: "lit"`, left-out positions `"dark"`; the Lab
  paints it with `paintFor` imported from `/kiosk/canopy-preview.mjs`, so T1/T2 draw heads exactly as the iPad does
  and nothing is drawn lit that the running tier wouldn't light. If the tier is canvas or slot, `heads: "approved"`
  and `lit` comes from compiling the **approved** trio instead, with note "The slots hold the approved painted
  design; your draft's painted design reaches the lights only after export, merge and commissioning."
  `title`: **"What this simulator shows"** when `simulated` (there every tier is allowed and all nine positions are
  addressed, so it is not the show box), else "What the lights can show today".
- `show_box_today` (pinned until the Oct 19 undo; S1 deletes it in the post-Oct-19 cleanup noted in STATUS):
  compiled like `test_on_the_partial_live_canopy_*` (test_modes_match_scenes.py:258): the shipped registry with
  addresses only for r0c0/r0c1, non-sim flags with no bench gates (so T1 refused, stepped refused as live), the
  patched trio → lands on **modules, r0c0 and r0c1 only**, with the D62 sentence; r0c2..r2c2 `"dark"`. Test:
  `test_the_show_box_view_lights_only_r0c0_and_r0c1_with_the_d62_sentence`; and
  `test_today_in_sim_is_titled_what_this_simulator_shows`.
- Brightness is a separate level (`brightness`), never multiplied into any colour (D59).
- Budget: < 300 ms server time for Golden Hour on the Mac mini; `elapsed_ms` reports it.

**Trial JSON (B):** `{"scene_id", "label", "version": id | null, "draft_name", "started_at", "ends_at",
"left_s", "bound_s", "modules": ["canopy-r0c0", …], "positions": ["r0c0", …], "applies": 1,
"tier": "modules" | "strand"  // the INSTALLED plan's real tier: engine.current_plan().tier, never assumed
"message": "Trying on at module level: one colour per strand; the painted design isn't on the lights." (painted
scenes at modules) | "Trying on at module level." | "Trying on as one colour on every strand (module level
couldn't be compiled: <why>)." (strand), "cost": "sends 2 writes now, up to ~8 over the next 10 s",
"next_apply_in_s": 0.0}`.

---

## 5. Front end (Slice A, S4)

### 5.1 Where the Lab lives: the Studio, `web/ops/showcase/`
Hooks in `web/ops/showcase/index.html` (L, contracts commit; four edits, nothing else):
1. `<head>`: `<link rel="stylesheet" href="./lab.css?v=lab-1">` after the `studio.css` link.
2. `.header-actions`: `<a class="text-link" href="#lab" id="lab-link">Design Lab</a>` before `#guest-panel-link`.
3. Before `</main>`: `<section id="lab" class="lab" aria-label="Design Lab" hidden></section>`.
4. After the `studio.js` script: `<script type="module" src="./lab.js?v=lab-1"></script>`.

`lab.js` owns everything inside `#lab`. On load and `hashchange`: `#lab` → `body.classList.add('lab-open')`,
`#lab` unhidden, built once; any other hash → closed. `lab.css`: `body.lab-open main > :not(#lab) {display:none
!important}` (the masthead stays). **`studio.js` and `demo-model.mjs` are not edited**: the Lab imports
`buildCanopy` (demo-model.mjs:185), `paintCanopy` (:222), `designLevels` (:47), `designPaint` (:54),
`defaultDesignLevel` and `CANOPY` from `./demo-model.mjs?v=show-design-3` (the same URL studio.js uses), `paintFor`
and `UNLIT` from `/kiosk/canopy-preview.mjs`, and `api`, `ApiError`, `copyJSON` from `/static/app.js`. The canopy
SVG is built with `buildCanopy(el, {scope: 'canopy', device: 'both', maps: {}, angle})` (`displayLayout('canopy')`,
:176) and painted from the **server's** `design` through **`lab-model.viewPainter` only**, which returns
`designPaint(design, level)` (or the today/strand painters) or `{error}`; on `{error}` or while loading, every head is
painted `UNLIT` and the reason shows. The Lab **never imports `scenePainters`, `sceneDesignView`, `sceneColorAt` or
`SCENES`** (`scenePainters` falls back to `SCENES` — Paris's handoff palette — whenever `view.paint` is null,
demo-model.mjs:81-84; under a Draft label that would be untrue). `test_lab_static.py` greps `lab.js` and
`lab-model.mjs` for those four names. Brightness drives opacity only, as in the Studio. Scene tiles, swatches and
names come from `/api/lab/catalog`. **Cut, recorded in D66 and STATUS:** the Studio's own page keeps its `SCENES`
tiles and shop-kit Apply buttons; routing them through the server design is a follow-up (brief A2 asked for it; the
Lab never uses them, and each export's review notes flag a stale `SCENES` colour, §3.6).

### 5.2 `lab-model.mjs` (pure, no DOM; `tests/lab-model.test.mjs` under `node --test`)
```js
export const VIEWS = ['canvas', 'modules', 'strand', 'today', 'showBox'];   // tabs
export const UNLIT = '#D3D2D8';                                           // = canopy-preview.mjs UNLIT
export const REALITY_LINE = 'Colours are RGB only; per-puck white isn’t verified yet.';
export function viewPainter(preview, view)       // → {head(h), spill(p), level, caption} | {error}
   // the ONLY painter entry point. canvas: designPaint(preview.design,'heads'); modules: designPaint(...,'modules');
   // strand: every head preview.tiers[strand].solid; today/showBox: paintFor(lit.modules[k], k) per module
   // (canopy-preview.mjs), dark → UNLIT; no design / design.error → {error} (painted UNLIT by lab.js)
export function availableViews(preview)          // canvas only when the ladder has canvas/slot; stepped scenes add none
export function tierCaption(view, preview)       // "Painted · all 270 lights" | "Module level · one colour per strand" | …
export function colourHint(hex)                  // 'check on the real strand' | null (rule below)
export function draftState(approvedDraft, draft, trial)   // 'approved' | 'draft' | 'in try-on'
export function draftEquals(a, b)                // upper-cases hexes (<input type=color> gives lower case) and
                                                 //   compares numbers numerically; copy "auto" == "auto" only
export function controlsFor(layout)              // which layout controls show: linear → angle; radial → centre
                                                 //   (step 2.5), metric, invert; sweep → centre, start; all → transform, invert
export function withLayoutKind(layout, kind)     // switch kind keeping invert/transform; linear defaults to 45°
                                                 //   (0° stripes, D63 warning); sweep centre_in [75, 60] (inside, so
                                                 //   the sweep goes all the way round; never a module centre:
                                                 //   schema.py:544 refuses those while at: centre)
export function problemsByField(problems)        // {field: [message]}
export function nextPreviewAt(lastSentMs, nowMs, minGapMs = 150)   // debounce: latest-wins, ≥150 ms apart
export function presentStep(state, input)        // {scene, version} after ArrowLeft/Right/Up/Down, swipe L/R/U/D;
                                                 //   compare set = [Approved, ...versions newest first]
export function swipeOf(dx, dy, dtMs)            // 'left'|'right'|'up'|'down'|null: ≥ 60 px, ≤ 600 ms, dominant axis
export function draftStoreKey(configSha, sceneId) // `lab:${configSha}:${sceneId}`; staleDraftKeys(keys, sha) → to drop
export function undoPush(stack, state) / undoPop(stack)   // ≤ 50 working states, in memory
export function alertFor(err | state)            // the #lab-alert sentence for store errors, field-`draft` problems,
                                                 //   the T1 lock, status 0 ("Can't reach the Conductor — showing the
                                                 //   last valid draft") and 5xx
export const PROMOTE = 'Make this the scene';    // D76: the editor's button on an option (scene.option_of set)
export const PROMOTE_COPY                        // {title, question, done, tookMotion, noRung, keptMotion, same}
                                                 //   (option, base names); title and question take {motion}: then
                                                 //   they list the motion (stepped direction, steps, timing) too
export function promoteTakesMotion(option, {baseStepped})  // the base has a stepped rung and the option has timing
export function promoteNotes(notes, option, base)          // the done notice's line for each note, in order
export function promoteDraft(base, option, {baseStepped})  // → {draft, notes}: the base's draft with the option's
                                                 //   palette, layout, sample_at, brightness, solid, description; id,
                                                 //   name, mood the base's; stepped (direction, steps per cycle,
                                                 //   step_s, run_s) the option's only when the base has a stepped
                                                 //   rung ("took-motion" when copied, else "no-rung" /
                                                 //   "kept-motion"); copy auto.
                                                 //   lab.js promoteToBase swaps the base's WORKING draft after a
                                                 //   confirm (Undo brings it back); no request: Publish publishes it
export function ipadCards(live, catalog)         // "On the iPad now": LiveJSON's scenes less on_ipad === false
```
**The export's promotion line (D76, `export.promotions`):** a base scene in the export whose palette and module-level
field equal one of its options' (and differ from its git design) gets "Made the scene on site (D76 “Make this the
scene”): <Base> now has <Option #n>'s palette and layout (<option id> → <base id>). If the client chose it at the
review, add who chose it and when before merging: that choice is the D59 sign-off for its colours." right after the
D59 line. It never asserts the sign-off; a later tweak of the base drops the line.
(No `defaultVersionName` — the UI shows "Saved as <name>" from the POST answer; no `copyChoice` — the server
resolves copy, §3.3.)
**Colour-reality hint rule (04: "orange, yellow and purple render weakly"):** HSV of the hex; hint when saturation
≥ 0.45 **and** value ≥ 0.40 **and** hue in [15°, 70°) (orange–yellow) or [255°, 320°) (purple). The six approved
palettes produce no hint (Soft gold s = 0.39, Lavender s = 0.32; pinned in the test); HD orange `#F96302` does. One
line under the palette, never a dialog. `REALITY_LINE` always shows once in the editor.

### 5.3 Layout (laptop 1440×900 and iPad 1180×820)
- ≥ 1280 px: three columns `minmax(220px,260px) 1fr minmax(320px,360px)`: scene list (the six, palette strip drawn
  from catalog hexes, name, mood, badge **Approved** / **Draft** / **In try-on**) · canopy stage · editor.
  < 1280 px (iPad landscape): scene list becomes a horizontal strip above; stage + editor side by side (editor
  340 px, scrolls). All targets ≥ 44 px; colour inputs 44 px; no hover-only affordances; `touch-action:
  manipulation`; no zoom on double tap in the editor; **every Lab text/number input `font-size: 16px`** (iPad Safari
  zooms the page on focusing a smaller input; app.js `copyJSON` does the same for its textarea).
- **`#lab-alert`** (`role=alert`), one element under the Lab header (the Studio's `#alert` sits inside `<main>`, which
  `lab-open` hides): the store error ("Saved versions couldn't be read (<error>); the file was set aside as
  <set_aside>. The show doesn't use this file."), the read-only store, field-`draft` problems, the T1-lock sentence,
  network failures (`ApiError` status 0: "Can't reach the Conductor — showing the last valid draft") and 5xx answers.
- **Stage:** tabs "Painted (all 270)", "Module level (one per strand)", "Strand (one colour)", `today.title` ("What
  this simulator shows" in sim) and, until the Oct 19 undo, "Show box today (Modules 1–2, module level)" (only the
  tabs `availableViews` allows; `VIEWS` gains `'showBox'`), an A/B toggle, the view angle toggle (Perspective / Ceiling
  plan), the **Simulator** badge when `catalog.simulated` (rendered by lab.js in the Lab header, styled in lab.css;
  base.css isn't loaded on this page), a **Draft** / **Approved** badge for what's on the stage, and below it every
  tier's sentence "as the iPad shows it" with the running tier's marked.
- **Editor controls** (each change → debounced `POST /api/lab/preview`, latest wins via `AbortController`):
  - Palette rows, **two lines per colour** (one line of seven 44 px controls is ~450 px and can't fit a 340 px
    editor): line 1 swatch `<input type=color>` (44 px) + colour name (client copy) + hint; line 2 hex text + Solid
    radio + a ⋯ menu (Move up / Move down / Remove). "Add colour" up to 6; at least 2. Renaming/rehexing an approved
    colour shows "Changes an approved colour — needs the client's sign-off".
  - Layout: kind (Linear | Radial | Sweep); Linear: angle slider 0–359 + number (default 45° on switching); Radial:
    centre x 0–225 / y 0–120 sliders at **`step="2.5"`** (every approved centre — 112.5, 187.5 — module centre, edge
    mid and corner is a multiple of 2.5; HTML's default step 1 can't land on them) each paired with a number input at
    step 0.5, metric (inches | canopy proportions), radius auto; Sweep: centre (default [75, 60]) + start angle 0–359;
    all: Mirror (transform) and Invert switches; the live `layout_words` beside the controls; **Cycles: 1 (locked,
    D63)** and **Sample at: centre (locked, D63)** read-only with their reason. A D63 warning (§3.4) shows as one line
    under the layout, the stage still draws the draft. (Tap-to-place on the Ceiling plan moves to Slice C:
    `buildCanopy`'s projection is local, demo-model.mjs:193-195.)
  - Brightness: slider 1–100, shown as "Level 95% — a device setting; colours stay full strength".
  - Copy: name, mood, description inputs labelled "client copy"; the T3 and moving sentences shown **read-only** as
    the server resolved them (`preview.copy.*.sentence`) with "Approved wording" or "Generated: <why>" — no toggle
    in Slice A.
  - Locked (T1 lock): the palette and layout controls disabled with `T1_LOCKED`.
  - Stepped (scenes with a stepped rung): read-only summary in A; editor in C.
- **Editor header:** "Revert to approved" (client only: copies `catalog.scenes[i].approved` into this scene's working
  draft; confirms when it has unsaved edits) and ↶ Undo edit (Cmd/Ctrl-Z; `undoPush`/`undoPop`, ≤ 50 working states
  in memory).
- **Versions bar:** "Save version" (note input → `POST /versions`; shows "Saved as <server name>"), version list
  (name, date, scenes changed, Stale / older-config / unreadable flags), Open (restore into the working draft),
  Compare as A / as B, Back to previous version (restore `parent`), Delete (confirm), Export. Open, Back to previous
  version and "Back to all approved" first ask "Replace your unsaved changes to <Scene>?" when the working draft
  differs from the last saved/opened state. **Export becomes "Save & export"** whenever the working draft differs
  from the selected version, so what is exported is what is on screen. The working draft is kept in `localStorage`
  under `draftStoreKey(catalog.config_sha, scene)` (try/catch, a per-viewer convenience) and survives a reload; keys
  for another `config_sha` are dropped with one `#lab-alert` line ("Unsaved drafts from before the last config change
  were cleared"); the store is the record.
- **A/B:** two stages side by side (each its own `buildCanopy`), A defaults to Approved and B to the working draft;
  each side picks Approved, Working draft or any version; same view tab on both; labels on each side.
- **Export dialog:** the patch in a `<pre>`, "Copy patch" (`copyJSON(patch)` from app.js, which already handles
  plain http and iOS), "Download" (Blob + `a[download]` with `ExportJSON.filename`), the decision draft, the
  D59/D63/slot-repaint warnings, review notes, or the `problem` / D63-warning sentence when no patch is offered.
- **Errors:** a 422 marks the fields (`problemsByField`) with the server's sentence; the last good preview stays on
  the stage, labelled "showing the last valid draft".

### 5.4 Present mode
"Present" button → `#lab-present` overlay: `requestFullscreen()` where supported (iPadOS ≥ 16.4), else a fixed
full-viewport overlay. No chrome: the canopy as large as fits, the scene name large (Studio `--serif`, `clamp(48px,
7vw, 96px)`), the version name and mood small under it, **always a tier caption** (`tierCaption`: "Painted · all 270
lights" / "Module level · one colour per strand") and the Draft/Approved badge, so a T1 design is never read as what
the lights do at module level. **View per scene:** the stage's view when the scene has it, else
`defaultDesignLevel(design)`. A/B in Present shows both canopies with "Approved" / version labels. Input: ← →
previous/next scene, ↑ ↓ previous/next version in the **compare set `[Approved, ...versions newest first]`**, `V`
perspective/plan, `B` toggle A/B, `Esc` exits; and because the browser takes Esc to leave fullscreen, Present also
closes on `fullscreenchange` when `document.fullscreenElement` becomes null. Touch: horizontal swipe = scene,
vertical swipe = version; while presenting `html {overflow: hidden; overscroll-behavior: none}`, `#lab-present
{touch-action: none}` and `touchmove` listeners `{passive: false}`, so iPad Safari's elastic scroll and
pull-to-refresh can't eat a swipe or reload the page. **Exit:** a corner ✕ (≥ 44 px) that fades in on any tap (no
double-tap exit). A one-line hint fades after 3 s. Previews for the six scenes of the shown version are prefetched
(six `POST /preview` calls) so stepping is instant. Works at 1440×900 and 1180×820 (checked in the Chromium browser
pane, §9); STATUS lists "needs a real iPad: the colour picker popover, element fullscreen, swipe without reload".

### 5.5 Assets
Vendored, no CDN, no `http(s)://` in `lab.js`, `lab.css`, `lab-model.mjs` (`tests/test_lab_static.py`, the same
check `tests/test_static.py` makes for `web/static`), no `@import`, no `@font-face`; fonts are the Studio's system
stacks (`var(--serif)` = Georgia…, body `'Segoe UI', Arial, sans-serif`).

---

## 6. Slice B: the try-on seam (B1 server, B2 UI)

### 6.1 Settings (`conductor/config.py`, `config/conductor.yaml`)
```python
class LabSettings(_Strict):
    trial_max_s: float | None = Field(None, gt=0, le=600)  # None = kiosk.idle_return_s
class ConductorSettings(_Strict):
    ...; lab: LabSettings = Field(default_factory=LabSettings)
    @model_validator(mode="after")
    def _trial_within_idle(self):  # "lab.trial_max_s (200 s) must be at most kiosk.idle_return_s (180 s), so the
        ...                        #  idle hold never swaps the look under a try-on"
def trial_bound_s(settings: ConductorSettings) -> float:   # min(lab.trial_max_s or idle_return_s, idle_return_s)
```
`config/conductor.yaml` gains `lab: {trial_max_s: 150}` with a comment. `None` as the default keeps every existing
`settings_with(kiosk={"idle_return_s": 30})` test valid (they dump `lab.trial_max_s: None`).

### 6.2 Engine (`conductor/canopy.py`)
```python
TRIAL_SOURCE = "lab"
TRIAL_MESSAGE = "Staff are trying a new look on the canopy. Try again in a few minutes."
TRIAL_SKIP = frozenset({Tier.T1, Tier.T2, Tier.STEPPED})  # a try-on is module level only, and never steps
ONE_BURST_SOURCES = RECOMPILE_SOURCES | {TRIAL_SOURCE}  # ripple seam; RECOMPILE_SOURCES itself unchanged


@dataclass
class Trial:
    mode_id: str
    label: str  # the draft's name / version name (telemetry, Lab)
    modes: Any  # the patched ModesFile — opaque to the engine; ShowWiring compiles with it
    recipes: Any  # the patched RecipesFile — T3/strand colours come from recipes, so without it a hex
    #   edit would show the APPROVED colour and a renamed colour would fail modules → strand
    started_mono: float
    ends_mono: float
    started_at: float
    version: str | None = None
    applies: int = 1
```
`CanopyEngine` additions: `self._trial: Trial | None = None`, `self._trial_task: asyncio.Task | None = None` (a
loop task like `_settle_task` (canopy.py:1157), **not** in `self._tasks`, so `wait_idle` never waits 150 s);
properties `trial` and **`trial_active`** (`self._trial is not None`, read by the kiosk's `refusal_for_state`,
§6.5); methods:
- `async def start_trial(self, trial: Trial) -> CanopyState` — refusals first, each a `_refused(sentence, retry)`:
  stopped (`STOPPED_MESSAGE`), commissioning (`COMMISSIONING_MESSAGE`, 30), guest input paused ("Guest input is
  paused by staff, so a try-on can't start; resume guest input on /ops first."), outage (`RESTING_MESSAGE`,
  `outage_probe_interval_s`), stepped rehearsal running ("A stepped rehearsal is running; stop it first."). While a
  trial already runs, the new one keeps the running session's `ends_mono` (re-applies never extend the bound),
  `applies += 1`, and **keeps the existing `_trial_task`** (the bound doesn't change). Then `previous = self._trial;
  self._trial = trial; st = await self._request(trial.mode_id, source=TRIAL_SOURCE, ripple=False)`; if `st` is
  refused, a **first** start clears it (`_clear_trial(…)` + `_restore_approved`, below) and a failed **re-apply
  restores `previous`** (the running try-on stays as it was). On a first start, arms `_trial_task` to call
  `_end_trial_at_bound(session=trial)` at `ends_mono`. Telemetry `lab` `trial_start`/`trial_apply`.
  *(Review fix, Oct 17.)* Keyword `expect_session` (the session the route's confirm described, None for none):
  another one running now → refused `TRIAL_CHANGED_MESSAGE`; `min_left_s` (the route passes `kiosk.cooldown_s`): a
  re-apply with less of the bound left → refused `TRIAL_ENDING_MESSAGE`, retry after the time left. Any **exception**
  from `_request` once the override is set clears it (and the bound task) first, puts the approved look back
  (`_recover_from_trial_error`: a re-apply's running draft plan too; the safe look if the approved compile raises
  again), alerts, and re-raises; `end_trial` does the same, and the bound task logs it and ends cleanly.
- `async def end_trial(self, *, source: str) -> CanopyState` — "Back to approved": `_clear_trial("stopped by …")`
  then `await self._request(mode_id, source=TRIAL_SOURCE, ripple=False)` with no trial set (the approved look, one
  burst; counts as a change for the idle hold); if that is refused, `_restore_approved(trial)`.
- `async def _end_trial_at_bound(self, session: Trial)` — acts only `if self._trial is session` (a stale task from an
  ended session does nothing); then as `end_trial(source="the try-on's time limit")` (`trial_end`).
- `def _clear_trial(self, why: str, *, repick: bool = False, skip: frozenset[Tier] = frozenset()) -> Trial | None` —
  cancels `_trial_task` **only `if self._trial_task is not asyncio.current_task()`** (the bound's own task must not
  cancel itself mid-way), sets `_trial = None`, records `lab` `trial_end` with `why`, and **returns the trial it
  cleared** (None if none). With `repick`, calls `_restore_approved(cleared, skip=skip)`.
- `def _restore_approved(self, trial: Trial | None, *, skip=frozenset()) -> None` — when `trial` is not None and
  `self._plan.mode_id == trial.mode_id`: `plan, _ = self.compile_best(trial.mode_id, skip=skip)` →
  `self._show(plan, "reconciler")` (one burst). This is the **transactional fallback**: any caller that cleared a
  trial and then fails to install its own look calls it, so a trial plan can never stay installed with no override
  and no bound (the reconciler re-asserts `self._plan` forever, reconciler.py:256-284, and the idle hold skips a
  Calm trial, reconciler.py:556).
- `def _trial_left(self) -> float | None`.

Edits to existing methods (all small, each with a test). **Placements are pinned**:
- `request_mode` (canopy.py:479): `ripple=source not in ONE_BURST_SOURCES`.
- `_request` (:485): **after `_refusal` returns None, before the rehearsal line (:490)**: `cleared = None; if
  self._trial and source not in RECOMPILE_SOURCES and source != TRIAL_SOURCE: cleared =
  self._clear_trial(f"{source} chose {mode_id}")` — ops, idle, the safe look's fallback, `idle_scene` all clear it in
  the same step. If `compile_best` then returns None: `self._restore_approved(cleared)` before returning refused.
- `_refusal` (:512), under `source == "kiosk"` after the rehearsal check: `if self._trial is not None: return
  self._refused(TRIAL_MESSAGE, self._trial_left())` (engine-level; the guest's sentence comes from §6.5).
- `compile_best` (:1232): `if self._trial is not None and mode_id == self._trial.mode_id: skip = skip |
  TRIAL_SKIP` — covers request, upgrade, `_replan_soon`, `_settle` and ShowWiring's `_apply_new_caps` (show.py:314), so the trial mode can only
  land on `modules`/`strand` while the override stands. (`downgrade` (:1647) doesn't call `compile_best`; it walks
  **down** from the current tier and skips STEPPED itself, so from a trial at modules it can only reach strand,
  which the override also covers — no edit.)
- *(Review fix, Oct 17)* ShowWiring's `_apply_new_caps`: before its `reconciler` re-pick, `_joining_trial(plan)`
  compiles the try-on's plan again (`compile_best`, pure); when it would ADD a module (registered, or newly read,
  mid-try-on: the confirm never named it) it calls `engine.end_trial(…)` instead (why: "a canopy module joined the
  look mid-try-on (Module 3; …)"), so the approved look goes to every module. A re-pick that keeps or shrinks the set
  keeps the try-on.
- `stop` (:386): `_clear_trial("the canopy engine stopped")` first; then, beside the `_settle_task` block, cancel and
  `await` `_trial_task` (suppressing `CancelledError`).
- `pause_guest_input` (:674): when pausing, `_clear_trial("guest input was paused", repick=True)`.
- `safe_look` (:690): `cleared = self._clear_trial("the safe look")` as the **first statement, before
  `self.deps.compile`** (with the trial still set, a Calm trial's override covers STRAND and the safe look would show
  the draft's solid); if it returns refused, `_restore_approved(cleared)`.
- `rehearse` (:1054): `cleared = self._clear_trial("a stepped rehearsal started")` **right after `preview =
  self.rehearsal_preview(...)` returns, before `self._rehearsal = rehearsal`** (with the trial still set,
  `compile_best` skips STEPPED and rehearsing the trial's own scene is refused); on the `RehearsalRefused` path after
  that, `_restore_approved(cleared)` before raising.
- `commission` (:704): **after the two early returns (:706-709)**, `self._clear_trial("commissioning started",
  repick=True, skip=TRIAL_SKIP)`. ShowWiring's wrapper has already set `self.commissioning = True` (show.py:501),
  under which `tier_allowed` passes T1 whether or not slots are recorded (show.py:448), so an unrestricted re-pick
  would select uncommissioned `custom-N`; excluding T1/T2/STEPPED puts the approved look on modules/strand, and the
  existing `self._replan_soon()` at the end of `commission` (:767) lifts it to T1 once the records exist (`fire()`
  sees a different tier and calls `_show`, :1952-1958).
Never persisted: `_trial` is memory only; a restart has none (and no "in try-on" badge).

### 6.3 Show wiring (`conductor/show.py`) — the only other show-path edit
```python
def _trial_for(self, mode_id: str) -> Any | None:
    trial = getattr(self.engine, "trial", None) if self.engine is not None else None
    return trial if trial is not None and trial.mode_id == mode_id else None


def context(self, modes=None, recipes=None):  # show.py:349 — new `recipes` param forwarded to
    ...  # CompileContext.from_settings(recipes=recipes); None keeps today's default


def compile(self, mode_id, tier):  # show.py:374
    if tier is Tier.STEPPED and self._rehearsing(mode_id):
        return self.rehearsal_plan(mode_id)
    trial = self._trial_for(mode_id)
    if trial is not None and tier in (Tier.T3, Tier.STRAND):
        return compile_mode(mode_id, tier, self.context(trial.modes, trial.recipes))
    return compile_mode(mode_id, tier, self.context())
```
Tests: a trial of the Done-means hex edit (Sunset pink → `#FF8FBF`, key `sunset-pink` kept) gives plan module colours
equal to `module_colours(patched)` and different from approved; a draft with a **renamed** colour lands on
`modules`, not `strand`; `TrialJSON.tier` reports the installed plan's tier.
`ladder` (:383), `tier_allowed` (:445), `_record`, and therefore the engine's `t1_target` (canopy.py:2072, which
calls `deps.compile(mode, Tier.T1)` and `deps.ladder`), `slot_expectations` (:2040), `stale_slot` (:1962) and every
T1 compile keep reading the effective (git) `self.modes_file`. The reconciler (`check_slots`, reconciler.py:593)
therefore never sees a trial as a stale slot and never rewrites `custom-N`. **`reconciler.py` is not edited**: the
idle hold (`_idle_hold`, :548) calls `request_mode(idle_mode, source="idle")`, which clears the override (6.2), and
skips when the current mode is the idle mode — so a `lowcountry-calm` trial ends only at its bound, which is
≤ `idle_return_s`. No parallel `preview_plan`, no direct driver/executor calls, no module-page leases.

### 6.4 The Lab side (`conductor/design_lab/trial.py`, routes)
```python
def build_trial(lab: LabService, draft: SceneDraft, *, version: str | None, now_mono: float, now: float) -> Trial
    # patched = apply_draft(<the show's own trio, DEEP-COPIED: wiring.modes_file.model_copy(deep=True) + load_recipes
    #   + load_scenes(...).model_copy(deep=True)>, draft, ...); Trial(modes=patched.modes, recipes=patched.recipes, …).
    # wiring.modes_file is the live object the ladder, T1 compile, t1_target and stale_slot read: one in-place write
    # would reach a custom-N rewrite (purity rule, §3.0).
def confirm_sentence(lab, scene_id, positions, bound_s, painted: bool) -> str
    # "Try on <draft name> for <Scene> at module level on 2 modules (Module 1, Module 2) for up to 150 s.
    #  The painted design isn't on the lights."  (positions = registered, driven modules: wiring.context().addresses)
    # A draft that keeps the scene's name: "Try on your draft of <Scene> at module level on …" (never "Golden Hour
    #  for Golden Hour"). TrialJSON's `label` is the approved scene's name, `draft_name` the draft's.
    # (Review fix, Oct 17) A re-apply while a try-on runs keeps its bound, so it names the time left instead:
    #  "… for the 110 s left of the running try-on (a re-apply never extends it)."; `confirm_matches` accepts one
    #  shown up to CONFIRM_SLACK_S (30 s) earlier. With less than kiosk.cooldown_s left the route refuses it (409,
    #  retry_after_s = the time left, nothing sent, the gap kept): "This try-on ends in 6 s, too soon for another
    #  apply; apply again once it has ended."
def cost_line(n: int) -> str   # f"sends {n} writes now, up to ~{n * (1 + EARLY_REASSERT_LIMIT)} over the next 10 s"
                               # a re-apply's adds "; the running try-on still ends in N s"
class ApplyGate:               # the server-side minimum gap between accepted applies (kiosk.cooldown_s, 8 s)
    def reserve(self, now_mono: float) -> float  # on the LOOP THREAD, before any await: seconds left (> 0 = refuse);
                                                 #   0 = stamped now, so a double-tapped second POST is refused even
                                                 #   while the first is still in run_in_threadpool(build_trial)
    def release(self) -> None                    # undo the stamp when validation or the engine refuses
```
Test: two concurrent `POST /api/lab/trial/start` (`asyncio.gather` on the app) → one 200, one 409 with
`retry_after_s`.
`POST /trial/start` without `confirm` equal to the current sentence → 409 `confirm_required` (like
`/api/canopy/stepped-rehearsal`, ops.py:571). Accepted applies are ≥ `settings.kiosk.cooldown_s` apart (409 with
`retry_after_s` otherwise); the UI also debounces double taps. The UI has an **Apply** button (never a streaming
slider) with the cost line, a **Back to approved** button, the bound countdown, and the "in try-on" badge. A painted
scene's message: "Trying on at module level: one colour per strand; the painted design isn't on the lights."

### 6.5 What the iPad shows
The guest panel already draws `state.lit` from the plan the engine shows (canopy_view.py `lit_state`), so a trial is
shown truthfully at module level with its sentence. **Guest refusals:** the kiosk and the guest panel never show the
engine's sentence — `refusal_for_state` (routes/kiosk.py:401) maps engine flags to kiosk copy and otherwise falls
through to `409 UNAVAILABLE` ("That mood isn't available right now. Choose another."), which during a trial would be
untrue and invite repeated taps. B1 adds, right after the `rehearsal_active` branch: `if _flag(engine,
"trial_active") is True: return 503, TRYING_ON, _retry(st.retry_after_s)` with `TRYING_ON = "Staff are trying a new
look. It will be back shortly."` (the guest panel shares this mapping for scenes, guest_panel.py:808, and
brightness/power, :845). Tests through `POST /api/kiosk/select` and `GuestPanel.scene/brightness/power`: status 503
and the sentence everywhere; `Retry-After` on select and scene (brightness/power drop `extra` today,
guest_panel.py:846 — left as is). `kiosk.js` `buildCards`
(web/kiosk/kiosk.js:200) keys only on ids (`scenes.map((s) => s.id).join(",")`); B2 replaces that key with
`cardsKey(scenes)` from `panel-model.mjs` = `JSON.stringify(scenes.map(s => [s.id, s.name, s.mood, s.solid,
(s.palette || []).map(p => p.hex)]))`, so a catalog that changes after a deploy rebuilds the cards (node test in
`tests/guest-panel.test.mjs`).

### 6.6 Slice B tests (`tests/test_lab_trial.py`, virtual clock)
Engine-level with `tests/fake_compiler.py` (`make_world`, `settings_with`, `FakeCompiler` — the `sunset` mode has
canvas/slot/modules, so a trial must land on modules), and show-level with the `Show` harness pattern of
`tests/test_stepped_rehearsal.py` (`build_services` over the shipped `config/` on a `VirtualClock`, `ShowWiring`,
`QUIET_S` = 70 s for start-up commissioning; `monkeypatch` `engine_driver.simulated=False` for live tier rules):
- `test_a_golden_hour_trial_makes_no_slot_write_and_no_stale_slot_alert_over_20_virtual_minutes` — Show harness;
  sim start-up commissions custom-1..3 (records origin "api"); trial on golden-hour with a draft that changes **both a
  hex and the layout** (radial → linear 30°), so a T1 wrongly compiled from the trial would hash differently (a
  hex-only draft keeps the stable recipe keys and the same T1 hash, hiding the mutation); run 20 virtual min across
  the bound and a "Back to approved", count driver writes containing a slot object (`R.FRAME_CLASS`) after QUIET_S
  == 0, `engine.slot_written` unchanged, `engine.t1_target(dev, "custom-3")[0].modules[dev].slot_hash` unchanged
  during and after, no `alert` whose message matches "pattern in custom-" and no `AlertBoard` key starting `slot:`;
  before and after the revert.
- `test_the_trial_shows_the_drafts_colours_not_the_approved_ones` and
  `test_a_renamed_colour_trial_lands_on_modules_not_strand` (§6.3).
- `test_commissioning_during_a_trial_never_installs_t1_before_the_records_exist` — Show harness, `simulated=False`,
  Gate C flags, no slot records, a golden-hour trial, then `commission`: no T1 plan installed before every module's
  custom-3 record exists; afterwards `_replan_soon` lifts it.
- `test_a_calm_trial_then_the_safe_look_shows_approved_calm` (strand rows = approved Calm);
  `test_rehearsing_the_trials_own_scene_starts_the_stepped_runner`;
  `test_a_refused_request_after_clearing_restores_the_approved_look` (parametrized: `_request` plan None, refused
  rehearse, refused safe look → approved look installed, `engine.trial is None`);
  `test_a_failed_reapply_keeps_the_running_trial`.
- `test_the_bound_task_never_cancels_itself_and_stop_awaits_it`; `test_a_stale_bound_task_ignores_a_new_session`.
- `test_apply_never_mutates_its_inputs_or_the_show` (B part: `wiring.modes_file` and `load_scenes()` dumps unchanged
  after a trial start and end).
- `test_the_trial_ladder_is_modules_then_strand_and_t1_keeps_the_git_design` — during the trial
  `engine.t1_target(dev, "custom-3")[0].modules[dev].slot_hash` equals the pre-trial value; plan tier `modules`.
- `test_the_idle_return_clears_the_override` (idle_return_s = trial_max_s; idle source clears in the same step).
- `test_a_lowcountry_calm_trial_ends_at_its_bound` (idle hold skips the idle mode; bound ends it, approved Calm back).
- `test_a_restart_mid_trial_comes_up_clean` (close + rebuild over the same data dir: `engine.trial is None`, idle
  look, `GET /api/lab/state` trial null, looks.yaml untouched).
- `test_the_kiosk_is_refused_during_a_trial_with_retry_after` (mood, brightness and power — through the routes and
  `GuestPanel`, asserting the guest sentence `TRYING_ON`, not only the engine state; §6.5).
- `test_the_server_side_apply_gap_holds` (two applies 3 s apart → second 409 with retry_after ≈ 5; at 8 s accepted)
  and `test_two_concurrent_starts_give_one_200_and_one_409` (§6.4).
- `test_a_trial_lands_as_one_burst_with_the_ripple_at_1_2_s` (`settings_with(kiosk={"ripple_stagger_s": 1.2})`:
  every module's command in one burst, `engine._ripple is None`).
- `test_start_is_refused_in_outage_pause_commissioning_stopped_and_rehearsal` (parametrized; engine sentences).
- `test_any_other_source_clears_the_override_before_compiling` (ops, idle, safe look, commission, rehearse, pause,
  stop; parametrized).
- `test_a_trial_never_steps` (coastal-tides trial in sim lands on modules, `engine.runner is None`).
- `test_trial_max_s_is_validated_against_idle_return_s` and `test_the_bound_is_the_smaller_of_the_two`.
- `test_reapplying_keeps_the_sessions_bound`.
- `test_the_confirm_sentence_names_the_driven_modules_and_the_cost_line` (2 modules → "sends 2 writes now, up to ~8").
- Writer path (`tests/test_writer_paths.py`): `WRITER_PATHS["lab try-on (module level)"] = _lab_trial` built like
  `_canopy_engine` (FakeCompiler, the world's `address`): `engine.start_trial(Trial("sunset", …, modes=None,
  recipes=None, …))` then `wait_idle`, ending with `await engine.stop()` like the other entries
  (test_writer_paths.py:461); denied for `SPARE_MD`, reaches the wire for `BENCH_MD`. `COVERED_MODULES["conductor/routes/
  design_lab.py"] = "lab try-on (module level)"`.

---

## 7. Slice C: stepped editor, ripple preview, greyed effects

`conductor/design_lab/stepped.py` (C1):
```python
def effective_step_s(design_step_s: float, n_modules: int, settings: ConductorSettings) -> float
    # = ProgramRunner._effective_step's rule (sequence.py:127) with no ripple and one account:
    #   max(design_step_s, n * 60 / planned_writes_per_min(settings.motion.stepped), 0 + min_step_s), rounded up to ms
def stepped_preview(patched: ConfigTrio, scene_id: str, canopy: dict, settings, today_modules: int) -> dict | None
    # compile_mode(scene, Tier.STEPPED, <design context>) → {"steps": [{"index", "colours": {pos: "#RRGGBB"}}],
    #  "design_step_s", "steps_per_cycle", "run_s", "direction", "moving_feature",
    #  "pace": {"nine": 7.5, "today": 6.0, "today_modules": 2}, "live_sentence": STEPPED_LIVE_OFF | null}
def ripple_rings(patched: ConfigTrio, scene_id: str, canopy: dict) -> list[list[str]] | None
    # F.bands(design_field(mode, Tier.T3), canopy, GRID, RIPPLE_MAX_WAVES)  (field.py:271, compile.py:453)
```
- **Editable** (scenes with a stepped rung only: Coastal Tides, Golden Hour): direction (forward | reverse),
  `steps_per_cycle` 3–12, `step_s` 4–12 (0.5 steps), `run_s` from `step_s` to `min(180, kiosk.idle_return_s)`,
  and the design they move (the scene's layout — the same `design` as every rung). Bounds are `SteppedTechnique`'s
  (schema.py:265) plus `_check_stepped` (show.py:77).
- **Preview:** plays the exact compiled steps at real pacing: `pace.nine` (7.5 s at the 6 s design, 72 planned
  writes/min = 90 cap less the 20 % reserve) and `pace.today` (6.0 s on the Mac's two kits); play/pause and a step
  counter; the moving sentence shows only while it plays. `effective_step_s` is pinned equal to a real
  `ProgramRunner.step_s` for 9 and 2 modules (`test_lab_stepped.py`).
- **Live:** the Lab shows `STEPPED_LIVE_OFF` (show.py:70) + "Alon sets motion.stepped.live_enabled after Gate R"
  and has no control for it. A trial never steps (6.2). *(Superseded by D75/D77: "The idle scene has no stepped
  rung (no editor)". Calm #1's breathe rung has the stepped editor like every other option.)*
- **Ripple (D64):** screen-only animation of `ripple_rings` at a stagger slider 0–1.2 s
  (`RIPPLE_SPAN_MAX_S / (RIPPLE_MAX_WAVES − 1)`), labelled "Screen preview only — the canopy's ripple is off
  (kiosk.ripple_stagger_s: 0) until Alon signs off (D64)". Never sent anywhere.
- **Custom-slot effects:** a greyed card "Effects — not enabled: no code has passed the bench (06 #4)".
- **Copy:** moving copy only from the schema-checked `moving_feature` (approved or `STEPPED_TEMPLATE`).
- `make soak` run on the final branch; `docs/reports/sim-acceptance.md` committed in #27 only.
- **As built (C1, Oct 17; deviations):** `stepped_preview(patched, scene_id, canopy, settings, today_positions)`
  takes the show box's driven positions (the preview passes `show_box_today`'s, else `LIVE_KITS`), not a count, so
  the ripple term of the runner's rule can be computed over them; `effective_step_s(…, *, rings=1)` carries that
  term (0 while `ripple_stagger_s` is 0). The stepped JSON adds `feature` (the hold's sentence),
  `today_moving_feature` (D62's two-strand copy), `runs.{nine,today}` (`step_s`, `hold_at` = the runner's
  `count`, `moves_s`, `still` = why it would hold still), `live_enabled`, `live_note`, `trial_note`, `error`;
  `pace` adds `today_positions`, `planned_writes_per_min`, `account_writes_per_min`. PreviewJSON keeps `rings`
  (positions per ring) and adds `ripple` {rings, stagger_s [0, 1.2], max_rings, live_stagger_s, note}.
  `export_patch(…, settings=)` (optional; the shipped defaults without) gives the D65 line its pace and keeps the
  modes.yaml `step_s` comment's "step every N s" true.
- **As built (C2 + integration, Oct 17):** the page reads PLAN §7's shape and C1's additions: the live line is
  `stepped.live_sentence` + `live_note` word for word (C2's own sentence only before the server answers), the
  ripple label prefers `CatalogJSON.live.ripple_note`, a pace whose `runs.<pace>.still` is set is not played (its
  reason is shown), and at the show box's pace the moving sentence is `today_moving_feature` (D62). Assets `lab-4`.
  Not cut: the ripple preview and pause/stop both shipped; step-timeline scrubbing was not built (play/pause/stop
  and the counter instead).
- **As built (review fixes, Oct 17):** `DraftStepped` keeps exactly `SteppedTechnique`'s bounds, so any valid
  approved `step_s` seeds (a modes.yaml at 6.25 s no longer stops the whole Lab); the half-second rule applies to an
  EDIT only (`apply_drafts`, field `stepped.step_s`), and the page snaps only the field being edited. A run that
  fits one step is still (step 0 IS the still design: `runs.<pace>.still` says so, the page doesn't play it; the
  runner itself still counts it as running, a show-path gap left for Alon). The stepped JSON adds `steps_when`
  (Golden Hour: it steps only where its higher rungs can't run), shown on the Motion card, the editor and Present.
  With `motion.stepped.live_enabled` set, every live line is the server's (`STEPPED_LIVE_ON`; the catalog's
  `stepped_live_sentence` is null), the show-box pace note drops "(stepped stays off …)", and the export's D65
  section says "live on (motion.stepped.live_enabled)". The ripple line says "as the canopy does today" only at
  the canopy's own `live_stagger_s`. The export's `step_s` comment comes from the D65 line's own `canopy_pace`
  (ripple rings, max_step_s, a too-short run: "hold the still design (…)") whenever the timing or that pace moves.
  In A/B (and Present A/B) the run ends when the longest side's does; each side lands on its own hold. At the show
  box's pace the tier list labels its moving lines "on all nine modules" and "on the show box today". Assets
  `lab-5`.

---

## 8. Test plan

**Suite commands** (every stream before handing back; L before each PR):
```
uv run --offline pytest -q            # tests/test_deploy.py fails on macOS: known, not ours
make lint                             # ruff
node --test tests/*.test.mjs
make soak                             # B and C; report committed only in the last PR (#27)
LAB_ROUNDTRIP=1 make lab-roundtrip    # L, before each PR (gated: CI skips it)
```

| Stream | Test file | Key named tests | Mutation targets (break it → that test fails) |
|---|---|---|---|
| S1 | `test_lab_draft.py` | `test_the_seed_of_every_scene_applies_to_the_approved_trio_exactly` (model_dump equality); `test_golden_hours_pink_changes_every_rung_in_lockstep`; `test_brightness_reaches_every_rung_and_the_scene`; `test_recipe_keys_follow_the_name_or_scene_id_rule` (Peach → `golden-hour-peach` kept; new "Lavender" in goodnight → `goodnight-glow-lavender`; orphan removed); `test_ladder_slot_and_ids_never_move`; `test_any_layout_change_forces_generated_copy_even_with_the_same_words` (parametrized: Coastal Tides 78°→30°, Golden Hour (0,60)→(0,100), Goodnight Glow (187.5,20)→(100,50)); `test_auto_copy_resolves_without_a_422` (layout change, `copy` omitted → 200, `chosen: "generated"`); `test_an_explicit_approved_copy_that_is_false_is_refused_on_save`; `test_generated_copy_passes_parse_modes_for_every_layout_kind_invert_and_direction`; `test_colour_names_with_motion_or_number_words_are_refused`; `test_cycles_and_sample_at_are_locked_to_d63`; `test_config_error_paths_map_to_draft_fields`; `test_recipe_key_allocation_is_exact` (reorder keeps keys; slug collision with a stable key refused; "Sea glass"+"sea-glass" refused); `test_a_draft_validates_with_an_idle_scene_set`; `test_apply_never_mutates_its_inputs_or_the_show` (A part: trio, `load_scenes()` after preview/save/export) | drop the stepped canvas update; skip `slot.brightness`; reuse a taken key; compare `layout_words` instead of the layout (the same-words test fails); `list(modes)` for the idle check; assign into the input mode |
| S1 | `test_lab_preview.py`, `test_lab_routes_preview.py` | `test_preview_heads_are_mode_design_of_the_patched_modes` (equals `mode_design` head for head); `test_each_tier_sentence_is_compile_modes`; `test_today_on_a_partial_live_canopy_lights_only_the_driven_modules` (fake wiring, addresses r0c0/r0c1 → tier modules, 7 dark, D62 sentence); `test_todays_lit_colours_are_the_drafts_recipes` (hex edit: today's driven colours equal the T3 tab's, not approved); `test_today_at_t1_shows_the_approved_heads_with_the_note`; `test_today_in_sim_is_titled_what_this_simulator_shows`; `test_the_show_box_view_lights_only_r0c0_and_r0c1_with_the_d62_sentence`; `test_golden_hour_at_linear_0_is_a_200_with_a_two_d_warning`; `test_brightness_is_a_level_never_multiplied_into_colours`; `test_t1_lock_refuses_design_edits_after_commissioning_on_live_only` (409 + sentence; brightness still OK); `test_preview_writes_nothing` (no executor command, no file); `test_preview_is_fast` (< 1.0 s on CI, `elapsed_ms` reported); `test_lab_service_is_built_once` (two requests, one construction); `test_no_yaml_is_parsed_on_the_loop_thread_after_construction` (patch `load_modes` to assert it runs off the main thread) | multiply brightness in; forget left-out positions; lock in sim; drop the `recipes` replace in today_view; `setdefault(KEY, LabService(...))` |
| S2 | `test_lab_store.py` | `test_save_validates_writes_history_then_atomic_0600`; `test_default_names_count_per_day` ("Client Oct 17 v3 – warmer sunset"); `test_a_deleted_version_never_reissues_its_name` (v1, v2, delete v1 → v3); `test_a_corrupt_looks_yaml_is_set_aside_and_alerted` (YAML error, bad top level, format 2, oversize; file moved, 0600, empty store, one warn alert on the AlertBoard); `test_one_bad_version_is_listed_unreadable_and_kept`; `test_an_oversize_file_is_never_read`; `test_an_unwritable_overrides_dir_makes_the_store_read_only` (file left in place, save 409); `test_a_set_aside_name_collision_gets_a_suffix`; `test_a_stale_version_is_flagged_not_fatal`; `test_delete_and_restore_and_undo`; `test_the_store_never_writes_into_config` | write without history; raise on corrupt; skip the alert; count names instead of max; overwrite after a failed set-aside |
| S2 | `test_lab_routes_versions.py`, `test_lab_access.py`, `tests/test_roles.py` edits | `test_a_corrupt_looks_yaml_leaves_the_studio_and_conductor_working` (app_client: `/ops/showcase/` 200, `/api/lab/state` 200 with `store.error`, `/api/devices` attention lists it, `/healthz` OK); `test_every_lab_route_is_ops_only` (kiosk/tester/vignette enrolled → 401/403 on every path×method); `test_every_lab_route_requires_ops_past_the_path_table` (dependant walk, as `test_the_design_route_requires_ops_even_past_the_path_table`); `test_cross_site_and_non_json_writes_are_refused`; `test_save_export_and_restore_are_recorded_in_telemetry`; `test_no_lab_route_changes_live_enabled_or_ripple` | drop `require_ops`; drop `_no_cross_site` |
| S3 | `test_scene_rules.py` | `test_the_shipped_config_has_no_problem`; `test_each_rule_catches_its_mutation` (parametrized, one per rule, incl. `stepped`); `test_the_validator_and_the_old_assertions_agree`; `test_two_d_and_kits_differ_are_warnings_not_problems` | remove a rule's check (incl. slots → `test_scene_rules` fails; a slot moved in modes.yaml → the old literal test fails) |
| S3 | `test_lab_export.py` | `test_an_unchanged_version_exports_nothing_and_rereads_equal`; `test_an_edit_rewrites_only_that_scenes_blocks_keeping_comments_and_anchors` (other lines byte-identical; `&golden-design`/`*golden-design` present); `test_the_patch_applies_with_git_apply` (copies the **working-tree** `config/` and `tests/fixtures_scene_frames.json` into a `git init` temp repo, commits, then `git apply --check` — not `git archive HEAD`, which would miss uncommitted config); `test_the_fixture_is_regenerated_byte_for_byte_on_no_change`; `test_an_export_without_a_tests_dir_still_patches_the_fixture`; `test_a_reread_mismatch_offers_no_patch`; `test_only_the_four_files_are_touched`; `test_the_decision_draft_lists_d59_and_d63_changes`; `test_a_painted_layout_change_carries_the_slot_repaint_sentence`; `test_an_export_with_a_d63_warning_is_refused`; `test_client_copy_with_yaml_specials_round_trips`; `test_a_redesigned_block_keeps_no_stale_comment`; `test_rename_add_remove_and_reorder_export_and_reread` ("export a rename" must stay green); `test_regen_scene_pins_matches_the_committed_fixture` | edit a line in another scene; drop the anchor; trailing newline on the JSON; skip the fixture when `tests/` is absent; emit an unquoted scalar |
| S3 | `test_lab_roundtrip.py` — **gated**: `pytest.mark.skipif(os.environ.get("LAB_ROUNDTRIP") != "1", reason="slow: make lab-roundtrip")`, so CI's plain `uv run pytest -q` (ci.yml:23, not editable) skips it; `make lab-roundtrip` sets it | `test_an_edited_export_passes_the_whole_suite` — exports the canonical edits (Golden Hour: Sunset pink → `#FF8FBF`, layout → linear 30°, brightness 90; Coastal Tides: angle 60°, Aqua → `#6FD0D8`; Lowcountry Calm: Sea glass → `#CDEBE6`) **and** a rename, an added colour, a removed colour and a reorder; applies each in a scratch copy of the working tree in which it first runs `git init -q && git add -A && git -c user.name=lab -c user.email=lab@localhost commit -qm base` (so `git apply` and `test_workshop`'s `git ls-files` work), then `uv run --offline pytest -q --ignore=tests/test_deploy.py -p no:cacheprovider` there (≈ 530 s a run); every failure is a pin to convert (§3.7). L runs it before each PR and records the timing in STATUS | — |
| S4 | `tests/lab-model.test.mjs`, `test_lab_static.py` | `viewPainter paints today's dark modules UNLIT`; `viewPainter({design: null}) returns {error}`; `colourHint flags HD orange and none of the six palettes`; `controlsFor shows angle for linear and centre for radial` (`centre.step === 2.5`); `withLayoutKind linear defaults to 45 and sweep never sits on a module centre`; `draftEquals ignores hex case and number formatting`; `draftStoreKey and staleDraftKeys`; `undo stack caps at 50`; `alertFor the store error, status 0 and 5xx`; `presentStep wraps scenes and the compare set`; `swipeOf needs 60 px within 600 ms`; `draftState shows in try-on over draft`; `tierCaption`; static: no `http(s)://`, `@import`, `@font-face` in `lab.*`; `lab.js`/`lab-model.mjs` contain none of `scenePainters`, `sceneDesignView`, `sceneColorAt`, `SCENES`; `#lab-alert` exists; the Studio index has the 4 hooks and still loads `studio.js` | import `scenePainters`; drop the hex upper-casing |
| L | `test_dev_design_lab.py` | `test_the_environment_strips_hubspace_and_secrets`; `test_port_8000_is_refused`; `test_a_non_loopback_host_is_refused_with_auth_off`; `test_the_launch_entry_is_design_lab_on_8200`; `test_the_data_dir_is_never_the_show_box` | — |
| B1 | `test_lab_trial.py`, `test_writer_paths.py`, kiosk/guest-panel refusal tests | §6.6 | remove the `_refusal` trial check; remove the `refusal_for_state` trial branch; drop `TRIAL_SKIP`; compile T1 with the trial modes (caught only because the 20-minute test's draft changes the layout); drop `trial.recipes`; clear without re-pick on pause; re-pick inside `commission` without `skip`; drop `_restore_approved`; place the safe-look clear after `deps.compile`. The PR body records, for each mutation, the named test that failed |
| B2 | `tests/guest-panel.test.mjs`, `lab-model.test.mjs` | `cardsKey changes when a palette changes`; `trialCountdown`; `apply button waits the gap` | — |
| C1/C2 | `test_lab_stepped.py`, `lab-model.test.mjs` | `test_effective_step_matches_the_program_runner_for_nine_and_two`; `test_the_preview_steps_are_compile_modes_steps`; `test_run_s_is_bounded_by_idle_return`; `test_the_lab_never_flips_live_enabled_or_ripple`; `rippleSchedule`, `stepTimeline` | — |

**Hard limits, each with a named test:** no Lab path writes to a device outside the engine
(`test_lab_access.py::test_no_lab_module_imports_a_writer` — grep: `conductor/design_lab/*.py` and
`routes/design_lab.py` import no `executor`/`drivers`/`locks`, call no `.submit(`/`.burst(`/`driver.write(`/
`engine.send(`; plus B's writer path); no Lab path flips `live_enabled` or `ripple_stagger_s`
(`test_no_lab_route_changes_live_enabled_or_ripple`: every route, settings object and `config/conductor.yaml`
unchanged; `SceneDraft.model_fields` has neither); no Lab path takes or rewrites a show slot
(`test_ladder_slot_and_ids_never_move`, `scene_rules` "slots", and B's zero-slot-write test); no Lab path binds 8000
(`test_port_8000_is_refused`, `test_the_launch_entry_is_design_lab_on_8200`).

---

## 9. Dev server (port 8200)

`.claude/launch.json` after the contracts commit (the `conductor` entry is kept and never started):
```json
{
  "version": "0.0.1",
  "configurations": [
    {
      "name": "conductor",
      "runtimeExecutable": "uv",
      "runtimeArgs": ["run", "uvicorn", "conductor.app:app", "--port", "8000"],
      "port": 8000
    },
    {
      "name": "design-lab",
      "runtimeExecutable": "uv",
      "runtimeArgs": ["run", "--offline", "python", "scripts/dev_design_lab.py", "--port", "8200"],
      "port": 8200
    }
  ]
}
```
`scripts/dev_design_lab.py` (modelled on `scripts/dev_guest_panel.py`; `make lab-dev`):
- `UNSAFE_ENV` imported from `scripts/dev_guest_panel.py` (`sys.path` insert of `scripts/`), and additionally every
  variable starting `HUBSPACE_` or `RAILWAY_` is dropped; then `CONDUCTOR_MODE=sim`, `CONDUCTOR_AUTH=off`,
  `CONDUCTOR_DATA_DIR=<data>`; `CONDUCTOR_ENV` removed (not workshop: the engine target, as on the show box).
- `--port` (default 8200; **8000 refused**: "port 8000 belongs to the show Conductor"), `--host` 127.0.0.1 (any
  non-loopback host is **refused** while `CONDUCTOR_AUTH=off`: "auth is off, so this server is for this machine
  only"; `test_a_non_loopback_host_is_refused_with_auth_off`),
  `--data <dir>` (default `tempfile.mkdtemp(prefix="design-lab-")`; agents pass their scratchpad), `--keep`;
  refuses a data dir under `~/canopy-show` or `~/Library/Application Support/ColorCanopy`.
- Prints `http://127.0.0.1:8200/ops/showcase/#lab`, runs `uvicorn.run("conductor.app:app", …)`, deletes the data dir
  on exit unless `--keep` or `--data` was given.
- UI checks: Browser preview (`preview_start name=design-lab`) at 1440×900 and 1180×820 with touch; look at the
  screenshots; anything not screenshotted goes in STATUS under "needs a look on the Mac"; iPad Safari specifics go
  under "needs a real iPad" (§5.4).

### 9.1 Running the Lab with the client (STATUS and D66 carry this block)
The dev server is loopback-only with auth off, the show Conductor on :8000 is held, and the enrolled Test iPad is
kiosk-only (refused on `/api/lab/*`). So:
- **(a) Laptop only (works Sat Oct 17):** on Alon's laptop, a clone of `claude/design-lab`, `make lab-dev`, then
  `http://127.0.0.1:8200/ops/showcase/#lab`. Present on the laptop (default 2).
- **(b) Laptop + iPad:** Railway staging, once #24 and #25 are merged to main (it follows main, runs sim, never holds
  show accounts, and always has ops basic auth — auth.py:84). The iPad signs in **as ops in Safari**; it is a second
  iPad or Safari on Alon's own, **not** the kiosk-enrolled Test iPad. Exports from staging are complete (the fixture
  is built in memory, §3.6).
- **(c) Show box:** only after the §11 deploy block, coordinated with Paris.

---

## 10. Risks, and the D66 draft

| Risk | Mitigation |
|---|---|
| Edited exports fail tests that pin approved values | `_pins` in the pinned fixture (§3.7), compiler tests on frozen recipes, the gated round-trip harness incl. rename/add/remove/reorder; cut line keeps the Done-means edit green |
| A trial shows the approved colours, or drops to strand | `Trial.recipes` + `ShowWiring.context(recipes=)`; `TrialJSON.tier` is the installed plan's; two named tests |
| A cleared trial stays installed with no override | pinned clear placements + `_restore_approved` fallback; parametrized test |
| Lab code mutates the show's cached config | deep copies at the boundary, build-by-validate only, the purity test |
| No host for the client session | §9.1: laptop `make lab-dev`, staging after merge, show box after deploy |
| The stack isn't merged by Sat night → no kit try-on before Oct 19 | STATUS says so plainly; kit try-on in the Hubspace app / `/ops/module`; Lab stays screen-only |
| A trial leaks into T1 (stale-slot rewrite of `custom-3`) | trial compiles only T3/strand; `t1_target`/`slot_expectations`/`stale_slot`/`ladder` read git; 20-minute zero-slot-write test |
| A trial outlives its welcome | bound ≤ idle_return_s, any other source clears it, pause/stop/commission clear it, never persisted |
| YAML in-place rewrite corrupts anchors/comments | re-read through the real loaders + model_dump equality, fail closed with a reason (as `overlay_diff`) |
| Preview slow on the show box / blocks the engine loop | threadpool; < 300 ms budget; perf test |
| Generated copy untrue | templates name only colours and layout words the validator checks; `parse_modes` re-validates; partial canopy keeps D62's sentence |
| Client changes approved colours casually | badge + export warning "needs the client's sign-off and a D59 update" |
| T1 lock always on in sim | enforced only on a non-simulated driver; sim says what would happen |
| Studio main page tiles and shop-kit Apply buttons still from `demo-model.mjs` `SCENES` | explicit cut in D66/STATUS; Lab never imports them; each export's review notes name a stale `SCENES` colour |
| Clock/timezone of version names | box local date; server-authoritative name |

**D66 draft (L commits it with #25; B and C append their lines):**

> **D66 — The Design Lab: edit the scenes on screen, try them on at module level, approve through git (a freeze
> exception).** We are past the Oct 12 feature freeze (`docs/runbooks/release-and-freeze.md`); Alon grants this
> exception so Alon and the client (Home Depot / Hearst, with Paris from VWBD) can iterate each Color Canopy scene's
> colours, 2-D layout, brightness and stepped motion without a YAML-PR-restart loop per change.
> - **Slice A (screen only, no show-path change).** A Lab panel in the Studio (`/ops/showcase/#lab`, ops only,
>   `/api/lab/*`). A draft is a patch over the parsed `scenes.yaml`/`modes.yaml`/`recipes.yaml` that moves every rung
>   of that scene's mode in lockstep; ids, ladders and slot ownership never move. The preview goes through the real
>   compiler (`mode_design`, `compile_mode`) and shows each tier and "what the lights can show today" from the
>   running show's registry and bench flags. Versions are named (`Client <Mon D> v<N> – <note>`) and kept in
>   `<data>/overrides/looks.yaml` (history copies, atomic 0600; a corrupt file is set aside and flagged on /ops; the
>   show never reads it). Export is a reviewed git patch of the four files (`config/scenes.yaml`, `modes.yaml`,
>   `recipes.yaml`, `tests/fixtures_scene_frames.json`) with a drafted DECISIONS entry; approved looks reach the show
>   only through git → merge → `canopy-mac install`.
> - **Defaults taken** (Alon hadn't answered): an approved palette colour (D59) can change, as a labelled draft whose
>   export says it needs the client's sign-off and a D59 update; Present runs in Alon's laptop browser as ops (the
>   Test iPad stays the guest panel); version names as above.
> - **Locked to D63 in v1:** a design's `cycles` (1) and the module sample (`at: centre`), which the suite pins. After
>   commissioning, a painted scene's design (palette, layout) is read-only in the Lab on live hardware: "changing
>   this needs a slot repaint at commissioning" (07 §6); its brightness, solid, copy and stepped timing stay
>   editable. Copy is never free text: each sentence is the approved one while the design is unchanged, else
>   generated from the layout's own words. D63's design-quality tests (`two-d`, `kits-differ`) warn in the preview
>   and block only the export. Unlocking `at: mean` (so a sweep can sit "around the centre") is an option for Alon.
>   An export that changes a painted scene's design says "Deploy only in a commissioning window" (slot repaint).
> - **Approved-value pins** in the tests move to `tests/fixtures_scene_frames.json` (`_pins`), which the export
>   regenerates (or `make scene-pins` after a hand edit), so an approved edit passes the suite with only that fixture
>   changed; the compiler tests compile against frozen copies of today's scene recipes.
> - **Where the client session runs:** §9.1 of the plan (laptop `make lab-dev`; staging with an ops-signed-in iPad
>   after merge; the show box only after the deploy). **Cut:** the Studio's own tiles and shop-kit buttons keep
>   `demo-model.mjs` `SCENES` (a follow-up).
> - **Slice B (try-on):** a one-tap, module-level trial of a draft on the real lights through the engine
>   (`request_mode` source `"lab"`, one burst), bounded by `min(lab.trial_max_s, kiosk.idle_return_s)`, cleared by
>   any other look, a pause, commissioning or a stop, never persisted; T1/T2/stepped are never used for a trial and
>   every slot path keeps the git config, so no slot is written or judged stale; commissioning during a trial
>   re-picks without T1 until the records exist. Guests are refused with a plain sentence while it runs ("Staff are
>   trying a new look. It will be back shortly."). Writes: one per module per apply, plus the engine's usual re-sends; applies ≥ 8 s apart.
> - **Slice C:** the stepped editor plays the exact compiled steps at the real pace (7.5 s on nine modules, 6 s on
>   two); the ripple is a screen preview; neither `motion.stepped.live_enabled` nor `kiosk.ripple_stagger_s` is ever
>   changed by the Lab; effect codes stay greyed (06 #4).
> - **Not done, option for Alon:** a read-only "client viewer" role for Present on the client's own device. It would
>   change the binding 07 §2 role table, so it is not built; Present runs signed in as ops on Alon's laptop.
> - **Out of scope for v1:** hot-applying config to the running show, slot repaints and firmware presets from the Lab.

---

## 11. Deploy block for STATUS (Slice B; Alon runs it, coordinated with Paris)
```
# after merging #24 → #26 (retarget each to main first; merge commits):
git -C ~/canopy-show switch main && git -C ~/canopy-show pull --ff-only && canopy-mac install && canopy-mac start
# hand the box back to Paris (break-glass hold):
canopy-mac stop --yes
```
This ends the hold Paris's demos rely on. If the stack isn't merged by Sat night, the kit try-on happens in the
Hubspace app or on `/ops/module`, and the Lab stays screen-only until after the Oct 19 undo.

---

## Review log (Fri Oct 16; each finding checked against the code on `claude/design-lab` before deciding)

**Contracts-engine**
- [blocker] Trial/today compile with git recipes → **accepted**: show.py:349-372 passes no `recipes`, compile.py:187 loads config's. `Trial.recipes`, `ShowWiring.context(recipes=)`, `dataclasses.replace` in today_view, `TrialJSON.tier` (§4, §6.2, §6.3).
- [major] Guests get an untrue "isn't available" → **accepted**: routes/kiosk.py:421 falls through to `UNAVAILABLE`. `trial_active` + `TRYING_ON` branch; kiosk.py added to B1 (§6.5).
- [major] Re-pick inside `commission` passes T1 → **accepted**: show.py:501 sets `commissioning` first, :448 then allows T1. Clear after the early returns with `skip=TRIAL_SKIP` (§6.2).
- [major] `_clear_trial` placement / no fallback → **accepted**: safe_look compiles at :694, rehearse sets `_rehearsal` before `compile_best`. Placements pinned; `_clear_trial` returns the trial; `_restore_approved`; a failed re-apply restores the previous trial (§6.2).
- [major] Lab holds show-shared mutable config → **accepted**: `load_scenes` is `lru_cache`d (scenes.py:104) and served by guest_panel.py:1023; `_Strict` isn't frozen. Deep copies + build-by-validate + purity test (§3.0, §6.4).
- [major] `lab_of` rebuilds per request on the loop → **accepted**: `setdefault` evaluates its argument. Get-or-create under an `asyncio.Lock` in the threadpool; lazy cached stale check (§3.0).
- [minor] One bad version sets aside the whole store; OSError/replace failure → **accepted**: per-version validation, stat-first, read-only on failure, suffixes (§3.5).
- [minor] T1 lock only on live; sim exports can force slot repaints → **accepted**: `needs_slot_repaint` + deploy sentence (§3.2.5, §3.6, §3.8).
- [minor] Trial task lifecycle → **accepted**: no self-cancel, session check, re-apply keeps the task, `stop()` awaits it, writer path ends with `engine.stop()` (§6.2, §6.6).
- [minor] ApplyGate check/accept race → **accepted**: `reserve()`/`release()` on the loop thread + concurrency test (§6.4).
- [minor] 20-minute test can't catch the T1 mutation → **accepted**: draft changes hex and layout; `slot_hash` asserted; PR records the test each mutation trips (§6.6, §8).
- (verdict note) `downgrade` doesn't call `compile_best` → **accepted** as a wording fix; it walks down and skips STEPPED, so no edit (§6.2).

**Client-UX**
- [major] No host for the client session → **accepted**: §9.1 (laptop / staging after merge / show box after deploy); non-loopback host refused with auth off (§9).
- [major] Export from staging skips the fixture → **accepted**: .dockerignore/Dockerfile copy no `tests/`. Fixture built in memory, never skipped (§3.6).
- [major] Preview refuses 65/360 angles → **accepted**: `two-d`/`kits-differ` become warnings; export 409; linear defaults to 45° (§3.4, §5.2).
- [major] Painter chain falls back to `SCENES` → **accepted**: demo-model.mjs:81-84. `viewPainter` only; static grep (§5.1).
- [major] "Today" untrue in sim → **accepted**: show.py:358 flags None in sim. `lit_state` shape + `paintFor`, sim title, pinned show-box view (§4).
- [major] Copy choice needs a 422 round trip → **accepted in part**: `"auto"` default resolved by the server, toggle dropped; an explicit `"approved"` stays for save-time validation (§3.1, §3.3).
- [major] No revert/undo/unsaved guard; Export ≠ screen; storage keys; hex case → **accepted**: all six fixes (§5.2, §5.3).
- [minor] Radial sliders can't reach 112.5/187.5 → **accepted**: step 2.5 + number input + live words (§5.3).
- [minor] Present underspecified → **accepted**: view rule, tier caption, `fullscreenchange`, touch CSS, ✕ exit, compare set, "needs a real iPad" (§5.4).
- [minor] No Lab alert region → **accepted**: `#lab-alert` + `alertFor` (§5.2, §5.3).
- [minor] Editor row too wide; iOS zoom → **accepted**: two lines per colour, 16 px inputs (§5.3).
- [minor] Over-built pieces → **accepted**: tap-to-place → Slice C, `defaultVersionName` dropped, `copyJSON` reused (§5.2, §5.3).
- [minor] `lab_of` → **accepted** (duplicate of contracts-engine; `test_lab_service_is_built_once`).
- [minor] Default names repeat after delete → **accepted**: max captured N + 1 (§3.5).
- [minor] Studio `SCENES` go stale after export → **accepted**: review note + explicit cut in D66/STATUS (§3.6, §5.1, §10).

**Data-tests**
- [blocker] Rename/add/remove breaks compiler fixtures → **accepted**: fixtures name scene keys; those tests use `load_recipes()`. Frozen recipes, `D59_COLUMNS` literal, no `d59_columns` pin, harness edits added (§3.7, §8).
- [major] Pin list misses three files → **accepted**: test_module_api.py, test_ops_routes.py, test_engine_ripple.py added to S3 and §3.7; L runs the harness before fan-out.
- [major] Round-trip harness fails alone, slow, runs in CI → **accepted**: env-gated, `git init` in the scratch copy, working-tree `git apply --check` (§8).
- [major] Approved-copy rule lets false sentences ship → **accepted**: approved only while the layout is model-equal (§3.3, §8).
- [major] Validator extraction weakens tests → **accepted**: old assertions kept verbatim, validator added alongside, slots with `approved=None` (§3.4).
- [major] No stepped pins or rule → **accepted**: `_pins.stepped`/`flow_words`, a `stepped` rule, guarded rays test (§3.4, §3.7).
- [minor] Recipe-key allocation ambiguous → **accepted**: exact algorithm + tests (§3.2.1).
- [minor] Scalar quoting unspecified → **accepted**: `json.dumps` scalars, `_flow` collections, specials test (§3.6).
- [minor] Stale comments survive → **accepted**: the comment rules + test (§3.6).
- [minor] `list(modes)` in the idle check → **accepted**: verified, `list()` of a pydantic model gives field tuples; `list(patched.modes.modes)` + test (§3.2.3).
- [minor] Sweep at a module centre refused → **accepted**: sweep default [0, 60] (moved to [75, 60] on review 2026-10-16: an edge sweep reaches only half the palette); "around the centre" needs `at: mean`, a D66 option (§5.2, D66).
- [minor] Purity of the cached catalog → **accepted** (merged with contracts-engine's purity finding, §3.0).
- [minor] No pin regeneration path → **accepted**: `scripts/regen_scene_pins.py`, `make scene-pins`, byte-equal test (§3.7).

None rejected: every finding's evidence held against the code.
