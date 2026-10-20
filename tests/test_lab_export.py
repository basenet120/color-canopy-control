"""The Design Lab's export (conductor/design_lab/export.py, PLAN §3.6): a version as a git patch of exactly four files.

The patched trios here are built by hand (`edit_scene`: model_validate of edited model_dump dicts, every rung in
lockstep), so the export is tested independently of S1's `apply_drafts`; one test runs through `apply_drafts` and is
skipped while that is still a stub. Every patch is proven the way the PLAN asks: it applies with `git apply` to a
copy of the working tree's config/ and fixture, and the files it leaves re-read (through the real loaders) as the
edited trio.

The edits are written against a frozen baseline (tests/fixtures_lab_baseline/: config/{scenes,modes,recipes,canopy}.yaml
as approved on 2026-10-16), exported from a scratch root holding that config and its fixture, so these tests keep
passing after an edited export changes config/ itself. The tests marked "live" run on the working tree's config/."""

from __future__ import annotations

import atexit
import difflib
import importlib.util
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from conductor.config import CONFIG_DIR, RecipesFile
from conductor.design_lab import export as X
from conductor.design_lab.draft import ConfigTrio, load_trio
from conductor.design_lab.store import Version
from conductor.geometry import load_canopy_config
from conductor.looks import field as F
from conductor.looks import scene_rules as R
from conductor.looks.schema import FieldCanvas, motion_words, parse_modes
from conductor.scenes import SceneCatalog, load_scenes

REPO = CONFIG_DIR.parent
BASELINE = Path(__file__).with_name("fixtures_lab_baseline")
CANOPY = load_canopy_config()
APPROVED = load_trio(BASELINE)  # the baseline every hand edit below is written against
LIVE = load_trio(CONFIG_DIR)  # the working tree's approved config


def _baseline_root() -> Path:
    """A repo-shaped root: the baseline as config/, and the fixture it gives as tests/fixtures_scene_frames.json."""
    root = Path(tempfile.mkdtemp(prefix="lab-baseline-"))
    atexit.register(shutil.rmtree, root, True)
    shutil.copytree(BASELINE, root / "config")
    (root / "tests").mkdir()
    (root / X.FIXTURE_FILE).write_text(X.fixture_for(APPROVED, CANOPY), encoding="utf-8")
    return root


ROOT = _baseline_root()
NOW = 1_792_270_800.0  # 2026-10-17
GOLDEN = [
    ("Soft gold", "#FFDB9B"),
    ("Peach", "#FFB69D"),
    ("Sunset pink", "#FF9BC4"),
    ("Rose", "#E779B0"),
    ("Lavender", "#B59AE2"),
]
DESIGN = ("techniques", "modules", "sample", "canvas")


# ---- hand-built patched trios ----------------------------------------------------------------------------------------
def _rgb(hex_: str) -> list[int]:
    return [int(hex_[i : i + 2], 16) for i in (1, 3, 5)]


def recipe_entry(name: str, hex_: str) -> dict:
    """A scene colour's recipe in the house style (recipes.yaml: the hex at full strength, no white)."""
    r, g, b = _rgb(hex_)
    entry = {"r": r, "g": g, "b": b, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0}
    return {
        "label": name,
        "hex": hex_.upper(),
        "rgb_only": entry,
        "blend": dict(entry),
        "strand": {"rgb": [r, g, b]},
    }


def _phrase(names: list[str]) -> str:
    low = [n.lower() for n in names]
    rest = ", ".join(low[1:-1]) + " and " + low[-1] if len(low) > 2 else low[1]
    return f"{low[0]}, then {rest}"


def edit_scene(
    trio: ConfigTrio,
    scene_id: str,
    *,
    palette: list[tuple[str, str]] | None = None,
    keys: list[str] | None = None,
    layout: dict | None = None,
    brightness: int | None = None,
    solid: int | None = None,
    name: str | None = None,
    mood: str | None = None,
    description: str | None = None,
    stepped: dict | None = None,
) -> ConfigTrio:
    """`trio` with one scene edited across every rung in lockstep (a stand-in for apply_drafts): palette (with its
    recipe keys; the old keys it no longer names are removed), layout, brightness, solid, client copy, stepped timing.
    The module-level and moving copy are regenerated when the layout or the colour names change."""
    scenes = trio.scenes.model_dump(mode="json")
    modes = trio.modes.model_dump(mode="json")
    recipes = trio.recipes.model_dump(mode="json")
    scene = next(s for s in scenes["scenes"] if s["id"] == scene_id)
    mode = modes["modes"][scene_id]
    t = mode["techniques"]
    design = t["modules"]["sample"]["canvas"]
    old_keys = [s["color"] for s in design["stops"]]
    old_names = [c["name"] for c in scene["palette"]]
    if palette is not None:
        keys = keys or old_keys[: len(palette)]
        assert len(keys) == len(palette)
        scene["palette"] = [{"name": n, "hex": h.upper()} for n, h in palette]
        for key in old_keys:
            if key not in keys:
                recipes["colors"].pop(key, None)
        for key, (n, h) in zip(keys, palette, strict=True):
            recipes["colors"][key] = recipe_entry(n, h)
        count = len(palette)
        design["stops"] = [{"at": i / (count - 1), "color": k} for i, k in enumerate(keys)]
        mode["preview"] = [h.upper() for _, h in palette]
    if layout is not None:
        design["field"] = layout
    if solid is not None:
        scene["default_solid"] = solid
    if brightness is not None:
        scene["brightness_draft"] = brightness
    if name is not None:
        scene["name"] = mode["label"] = name
    if mood is not None:
        scene["mood"] = mode["blurb"] = mood
    if description is not None:
        scene["description"] = description
    level = scene["brightness_draft"]
    for rung in ("canvas", "stepped"):
        if t.get(rung) is not None:
            t[rung]["canvas"] = design
            t[rung]["brightness"] = level
    if t.get("slot") is not None:
        t["slot"]["brightness"] = level
    t["modules"]["sample"]["brightness"] = level
    if stepped is not None:
        t["stepped"].update(stepped)
    solid_colour = scene["palette"][scene["default_solid"]]
    t["strand"]["look"] = {**t["strand"]["look"], "rgb": _rgb(solid_colour["hex"]), "brightness": level}
    t["strand"]["feature"] = (
        f"Colour and dimming: all nine Hubspace strands set to {solid_colour['name'].lower()}."
    )
    names = [c["name"] for c in scene["palette"]]
    canvas = FieldCanvas.model_validate(design)
    if layout is not None or names != old_names or (stepped and "direction" in stepped):
        words = F.layout_words(canvas.field, CANOPY)
        t["modules"]["feature"] = (
            "Colour and dimming: each Hubspace strand set to a single colour, so the palette is laid out "
            f"{words}: {_phrase(names)}."
        )
        if t.get("stepped") is not None:
            flow = motion_words(canvas, CANOPY, t["stepped"]["direction"])
            t["stepped"]["moving_feature"] = (
                "Colour and dimming through the Hubspace cloud: each Hubspace strand set to a single colour and "
                f"sent its next colour every few seconds, so the palette's colours move {flow} across the "
                f"canopy: {_phrase(names)}."
            )
    new_recipes = RecipesFile.model_validate(recipes)
    return ConfigTrio(
        scenes=SceneCatalog.model_validate(scenes),
        modes=parse_modes(modes, recipes=new_recipes, canopy=CANOPY, path="a hand edit"),
        recipes=new_recipes,
    )


def golden_edit(trio: ConfigTrio = APPROVED) -> ConfigTrio:
    """The Done-means edit: Golden Hour's Sunset pink → #FF8FBF, layout → linear 30°, brightness 90."""
    palette = [*GOLDEN[:2], ("Sunset pink", "#FF8FBF"), *GOLDEN[3:]]
    return edit_scene(
        trio, "golden-hour", palette=palette, layout={"kind": "linear", "angle_deg": 30}, brightness=90
    )


def canonical_edits(trio: ConfigTrio = APPROVED) -> ConfigTrio:
    """The three canonical edits (PLAN §8): Golden Hour as above; Coastal Tides at 60° with Aqua #6FD0D8; Lowcountry
    Calm's Sea glass #CDEBE6."""
    trio = golden_edit(trio)
    trio = edit_scene(
        trio,
        "coastal-tides",
        palette=[
            ("Ocean blue", "#659BD5"),
            ("Aqua", "#6FD0D8"),
            ("Seafoam", "#B2E8D2"),
            ("Foam white", "#E9F3E7"),
        ],
        layout={"kind": "linear", "angle_deg": 60},
    )
    return edit_scene(trio, "lowcountry-calm", palette=[("Soft ivory", "#F7F2E7"), ("Sea glass", "#CDEBE6")])


# rename / add / remove / reorder: each changes recipes.yaml's keys (PLAN §3.7: "export a rename" must stay green)
STRUCTURAL: dict[str, tuple[str, dict]] = {
    "rename": (
        "golden-hour",
        {
            "palette": [GOLDEN[0], ("Apricot", "#FFB69D"), *GOLDEN[2:]],
            "keys": ["soft-gold", "apricot", "sunset-pink", "rose", "lavender"],
        },
    ),
    "add": (
        "goodnight-glow",
        {
            "palette": [
                ("Periwinkle", "#8B8BD1"),
                ("Lavender", "#BEA9E6"),
                ("Moonlight blue", "#C4D6F3"),
                ("Dusk", "#A0A0E0"),
            ],
            "keys": ["periwinkle", "goodnight-glow-lavender", "moonlight-blue", "dusk"],
        },
    ),
    "remove": (
        "coastal-tides",
        {
            "palette": [("Ocean blue", "#659BD5"), ("Aqua", "#79CED4"), ("Foam white", "#E9F3E7")],
            "keys": ["ocean-blue", "aqua", "foam-white"],
        },
    ),
    "reorder": (
        "rainbow-row-sunset",
        {
            "palette": [("Lilac", "#C4B3EC"), ("Blush pink", "#FFD2E0"), ("Pastel coral", "#FF9E96")],
            "keys": ["lilac", "blush-pink", "pastel-coral"],
            "solid": 2,
        },
    ),
}


def structural(case: str, trio: ConfigTrio = APPROVED) -> ConfigTrio:
    scene_id, kw = STRUCTURAL[case]
    return edit_scene(trio, scene_id, **kw)


# ---- helpers ---------------------------------------------------------------------------------------------------------
def export(patched: ConfigTrio, *, root: Path = ROOT, version: Version | None = None) -> X.LabExport:
    return X.export_patch(APPROVED, patched, root=root, canopy=CANOPY, version=version, now=NOW)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def scratch_repo(tmp: Path, *, with_tests: bool = True) -> Path:
    """A git repo holding the root's config/ (and the fixture), committed: what a reviewer applies to."""
    repo = tmp / "repo"
    shutil.copytree(ROOT / "config", repo / "config")
    if with_tests:
        (repo / "tests").mkdir()
        shutil.copy(ROOT / X.FIXTURE_FILE, repo / X.FIXTURE_FILE)
    assert _git(repo, "init", "-q").returncode == 0
    assert _git(repo, "add", "-A").returncode == 0
    commit = _git(repo, "-c", "user.name=lab", "-c", "user.email=lab@localhost", "commit", "-qm", "base")
    assert commit.returncode == 0, commit.stderr
    return repo


def applied(repo: Path, patch: str) -> dict[str, str]:
    """`git apply --check`, then `git apply`; the four files as they are afterwards."""
    (repo / "lab.patch").write_text(patch, encoding="utf-8")
    check = _git(repo, "apply", "--check", "lab.patch")
    assert check.returncode == 0, check.stderr
    assert _git(repo, "apply", "lab.patch").returncode == 0
    return {path: (repo / path).read_text(encoding="utf-8") for path in X.EXPORT_FILES}


def after_texts(result: X.LabExport, tmp_path: Path) -> dict[str, str]:
    return applied(scratch_repo(tmp_path), result.patch)


def _block(lines: list[str], mode_id: str) -> range:
    start = lines.index(f"  {mode_id}:")
    end = start + 1
    while end < len(lines) and not re.match(r"^  \S", lines[end]):
        end += 1
    return range(start, end)


def _changed_lines(before: str, after: str) -> set[int]:
    """Indices of `before`'s lines that the edit replaced or removed (and where insertions landed)."""
    out: set[int] = set()
    matcher = difflib.SequenceMatcher(None, before.splitlines(), after.splitlines(), autojunk=False)
    for tag, i1, i2, _, _ in matcher.get_opcodes():
        if tag != "equal":
            out.update(range(i1, max(i2, i1 + 1)))
    return out


def _version(name: str = "Client Oct 17 v2 – warmer sunset") -> Version:
    return Version(id="v0002", name=name, created_at=NOW, config_sha="0123456789ab")


# ---- an unchanged version ----------------------------------------------------------------------------------------------
def test_an_unchanged_version_exports_nothing_and_rereads_equal():
    result = export(APPROVED)
    assert (result.patch, result.files, result.scenes, result.problem, result.decision) == (
        "",
        [],
        [],
        "",
        "",
    )
    texts = {path: (ROOT / path).read_text(encoding="utf-8") for path in X.EXPORT_FILES[:3]}
    assert X.reread(texts, CANOPY).dump() == APPROVED.dump()
    # and a rewrite that has nothing to change leaves every byte
    assert (
        X.rewrite_scenes_yaml(texts[X.SCENES_FILE], APPROVED, [s.id for s in APPROVED.scenes.scenes])
        == (texts[X.SCENES_FILE])
    )
    ids = list(APPROVED.modes.modes)
    assert X.rewrite_modes_yaml(texts[X.MODES_FILE], APPROVED, APPROVED, ids) == (texts[X.MODES_FILE], [])
    assert X.rewrite_recipes_yaml(texts[X.RECIPES_FILE], APPROVED, APPROVED) == texts[X.RECIPES_FILE]


def test_live_an_unchanged_export_of_the_working_tree_is_empty():
    result = X.export_patch(LIVE, LIVE, root=REPO, canopy=CANOPY, version=None, now=NOW)
    assert (result.patch, result.files, result.problem) == ("", [], "")
    texts = {path: (REPO / path).read_text(encoding="utf-8") for path in X.EXPORT_FILES[:3]}
    assert X.reread(texts, CANOPY).dump() == LIVE.dump()


def test_the_fixture_is_regenerated_byte_for_byte_on_no_change():
    committed = (REPO / X.FIXTURE_FILE).read_text(encoding="utf-8")  # live: the committed fixture
    assert X.fixture_for(LIVE, CANOPY) == committed
    assert not committed.endswith("\n")  # json.dumps(…, indent=0): no trailing newline
    frames = json.loads(committed)
    assert [k for k in frames if k != "_pins"] == [s.id for s in LIVE.scenes.scenes]


# ---- an edit -----------------------------------------------------------------------------------------------------------
def test_an_edit_rewrites_only_that_scenes_blocks_keeping_comments_and_anchors(tmp_path):
    patched = golden_edit()
    result = export(patched)
    assert result.problem == "" and result.scenes == ["golden-hour"]
    # the YAML hunks hold only the lines that changed: no line is removed and re-added as itself (difflib's
    # autojunk would do that to `strand:`, which every mode repeats)
    yaml_diff = result.patch[: result.patch.index(f"diff --git a/{X.FIXTURE_FILE}")]
    removed = {
        line[1:] for line in yaml_diff.splitlines() if line.startswith("-") and not line.startswith("---")
    }
    added = {
        line[1:] for line in yaml_diff.splitlines() if line.startswith("+") and not line.startswith("+++")
    }
    assert removed and not removed & added
    after = after_texts(result, tmp_path)
    before_modes = (ROOT / X.MODES_FILE).read_text(encoding="utf-8")
    lines = before_modes.splitlines()
    assert _changed_lines(before_modes, after[X.MODES_FILE]) <= set(_block(lines, "golden-hour"))
    # every other mode's lines, comments included, byte for byte
    for mode_id in APPROVED.modes.modes:
        if mode_id != "golden-hour":
            block = _block(lines, mode_id)
            new_lines = after[X.MODES_FILE].splitlines()
            assert (
                new_lines[_block(new_lines, mode_id).start : _block(new_lines, mode_id).stop]
                == lines[block.start : block.stop]
            )
    # the file header and the anchors stay: one design, shared by every rung
    assert after[X.MODES_FILE].startswith("\n".join(lines[:40]))
    assert (
        after[X.MODES_FILE].count("&golden-design") == 1 and after[X.MODES_FILE].count("*golden-design") == 2
    )
    for anchor in ("sunset-design", "garden-design", "tides-design"):
        assert after[X.MODES_FILE].count(f"&{anchor}") == before_modes.count(f"&{anchor}") == 1
        assert after[X.MODES_FILE].count(f"*{anchor}") == before_modes.count(f"*{anchor}")
    # scenes.yaml: only Golden Hour's lines; recipes.yaml: only sunset-pink's block
    before_scenes = (ROOT / X.SCENES_FILE).read_text(encoding="utf-8")
    scene_lines = before_scenes.splitlines()
    start = scene_lines.index("  - id: golden-hour")
    end = scene_lines.index("  - id: goodnight-glow")
    assert _changed_lines(before_scenes, after[X.SCENES_FILE]) <= set(range(start, end))
    before_recipes = (ROOT / X.RECIPES_FILE).read_text(encoding="utf-8")
    recipe_lines = before_recipes.splitlines()
    pink = recipe_lines.index("  sunset-pink:")
    assert _changed_lines(before_recipes, after[X.RECIPES_FILE]) <= set(range(pink, pink + 6))
    assert X.reread(after, CANOPY).dump() == patched.dump()


def test_the_patch_applies_with_git_apply(tmp_path):
    patched = canonical_edits()
    result = export(patched, version=_version())
    assert result.problem == ""
    assert result.scenes == ["lowcountry-calm", "coastal-tides", "golden-hour"]
    assert result.files == list(X.EXPORT_FILES)
    after = after_texts(result, tmp_path)
    got = X.reread(after, CANOPY)
    assert got.dump() == patched.dump() and list(got.modes.modes) == list(APPROVED.modes.modes)
    assert after[X.FIXTURE_FILE] == X.fixture_for(patched, CANOPY)  # the frames and pins of the edited trio
    # the edited config passes the validator the suite runs, warnings included
    assert R.scene_mode_problems(got.scenes, got.modes, got.recipes, CANOPY, approved=APPROVED.modes) == []


@pytest.mark.parametrize("case", list(STRUCTURAL))
def test_rename_add_remove_and_reorder_export_and_reread(case, tmp_path):
    patched = structural(case)
    result = export(patched)
    assert result.problem == "", result.problem
    after = after_texts(result, tmp_path)
    assert X.reread(after, CANOPY).dump() == patched.dump()
    keys_before, keys_after = set(APPROVED.recipes.colors), set(patched.recipes.colors)
    recipes_after = after[X.RECIPES_FILE]
    for key in keys_before - keys_after:  # an orphan key's block is gone
        assert f"\n  {key}:\n" not in recipes_after
    for key in (
        keys_after - keys_before
    ):  # a new key sits with its scene's colours, before the colour-study ones
        assert recipes_after.index(f"\n  {key}:\n") < recipes_after.index("colour-study colours (module page")
    if case == "rename":
        assert "  apricot:\n" in recipes_after and "golden-hour-peach" not in recipes_after
        assert X.FIXTURE_FILE not in result.files  # same hexes, same frames, same pins


def test_only_the_four_files_are_touched():
    result = export(canonical_edits())
    touched = re.findall(r"^diff --git a/(\S+) b/(\S+)$", result.patch, flags=re.MULTILINE)
    assert [a for a, b in touched] == [b for a, b in touched] == result.files
    assert set(result.files) <= set(X.EXPORT_FILES)
    assert "demo-model.mjs" not in "".join(a for a, _ in touched)
    headers = re.findall(r"^(?:---|\+\+\+) (\S+)$", result.patch, flags=re.MULTILINE)
    assert headers == [p for path in result.files for p in (f"a/{path}", f"b/{path}")]


def test_an_export_without_a_tests_dir_still_patches_the_fixture(tmp_path):
    """Docker/staging images have no tests/ (.dockerignore): the fixture is built in memory, never skipped."""
    root = tmp_path / "image"
    shutil.copytree(ROOT / "config", root / "config")
    result = export(golden_edit(), root=root)
    assert result.problem == "" and X.FIXTURE_FILE in result.files
    after = applied(scratch_repo(tmp_path), result.patch)
    assert after[X.FIXTURE_FILE] == X.fixture_for(golden_edit(), CANOPY)


@pytest.mark.parametrize(
    ("before", "after", "marks"),
    [
        ("{\n1,\n2\n}", "{\n1,\n3\n}", 1),  # the fixture's shape: no trailing newline either side
        ("{\n1,\n2", "{\n1,\n3", 2),  # the last line itself changes: marked on both sides
        ("a\nb\nc\n", "a\nb\nd", 1),
        ("a\nb\nc", "a\nb\nc\n", 1),
    ],
)
def test_a_missing_final_newline_is_marked_and_applies(before, after, marks, tmp_path):
    """tests/fixtures_scene_frames.json has no trailing newline (json.dumps(…, indent=0)): a hunk that reaches the
    last line says so, and git applies it to exactly the new text."""
    diff = X.unified_diff("tests/f.json", before, after)
    assert diff.count(X.NO_NEWLINE) == marks
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "f.json").write_text(before, encoding="utf-8")
    (repo / "f.patch").write_text(diff, encoding="utf-8")
    assert _git(repo, "init", "-q").returncode == 0
    check = _git(repo, "apply", "f.patch")
    assert check.returncode == 0, check.stderr
    assert (repo / "tests" / "f.json").read_text(encoding="utf-8") == after
    assert X.unified_diff("tests/f.json", before, before) == ""


def test_a_stale_fixture_on_disk_offers_no_patch(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "config", root / "config")
    (root / "tests").mkdir()
    (root / X.FIXTURE_FILE).write_text((ROOT / X.FIXTURE_FILE).read_text() + "\n", encoding="utf-8")
    result = export(golden_edit(), root=root)
    assert result.problem == X.FIXTURE_STALE and result.patch == "" and result.files == []


def test_a_reread_mismatch_offers_no_patch(monkeypatch):
    # a change the rewriter doesn't place (the kiosk copy block isn't a scene's): the re-read catches it
    modes = APPROVED.modes.model_dump(mode="json")
    modes["kiosk"]["title"] = "The Colour Canopy"
    kiosk_edit = ConfigTrio(
        golden_edit().scenes,
        parse_modes(
            {**modes, "modes": golden_edit().modes.model_dump(mode="json")["modes"]},
            recipes=golden_edit().recipes,
            canopy=CANOPY,
        ),
        golden_edit().recipes,
    )
    result = export(kiosk_edit)
    assert result.patch == "" and result.files == [] and "re-read differently" in result.problem
    assert "config/modes.yaml" in result.problem
    # a rewriter that drops an edit: no patch either
    monkeypatch.setattr(X, "rewrite_recipes_yaml", lambda text, approved, patched: text)
    result = export(golden_edit())
    assert result.patch == "" and "config/recipes.yaml" in result.problem


def test_a_trio_missing_a_scene_offers_no_patch():
    scenes = APPROVED.scenes.model_dump(mode="json")
    scenes["scenes"] = [s for s in scenes["scenes"] if s["id"] != "goodnight-glow"]
    modes = APPROVED.modes.model_dump(mode="json")
    del modes["modes"]["goodnight-glow"]
    dropped = ConfigTrio(
        SceneCatalog.model_validate(scenes),
        parse_modes(modes, recipes=APPROVED.recipes, canopy=CANOPY),
        APPROVED.recipes,
    )
    result = export(dropped)
    assert result.patch == "" and result.problem and result.scenes == ["goodnight-glow"]


def test_config_on_disk_that_isnt_the_approved_trio_offers_no_patch(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "config", root / "config")
    path = root / X.SCENES_FILE
    path.write_text(
        path.read_text().replace("brightness_draft: 28", "brightness_draft: 30"), encoding="utf-8"
    )
    result = export(golden_edit(), root=root)
    assert result.patch == "" and "isn't the approved config" in result.problem


# ---- what the export says ---------------------------------------------------------------------------------------------
def test_the_decision_draft_lists_d59_and_d63_changes():
    result = export(canonical_edits(), version=_version())
    assert "Golden Hour: Sunset pink #FF9BC4 → #FF8FBF" in result.approved_changes
    assert "Coastal Tides: Aqua #79CED4 → #6FD0D8" in result.approved_changes
    assert "Lowcountry Calm: Sea glass #D1EAE3 → #CDEBE6" in result.approved_changes
    assert result.layout_changes == [
        "Coastal Tides: layout linear at 78° ('diagonally') → linear at 60° ('diagonally')",
        (
            "Golden Hour: layout radial from (0, 60) in the canopy's proportions ('from one edge') → linear at 30° "
            "('diagonally')"
        ),
    ]
    decision = result.decision
    assert decision.startswith("**D")
    assert 'Design Lab version "Client Oct 17 v2 – warmer sunset" (2026-10-1' in decision.splitlines()[0]
    assert "Lowcountry Calm, Coastal Tides, Golden Hour" in decision.splitlines()[0]
    assert "Approved colour changes (D59; needs the client's sign-off and a D59 update): " in decision
    assert "Layout changes (D63; Paris reviews): " in decision
    assert "Brightness (drafts): Golden Hour 95 → 90." in decision
    assert "Copy regenerated: " in decision
    assert decision.endswith(
        "Exported from the Design Lab (D66); verified: re-read equal, suite to run in the PR."
    )
    # the Studio's own SCENES palette goes stale for every scene whose colours changed
    for scene, old in (
        ("Golden Hour", "#FF9BC4"),
        ("Coastal Tides", "#79CED4"),
        ("Lowcountry Calm", "#D1EAE3"),
    ):
        note = X.STUDIO_SCENES.format(hexes=old, scene=scene)
        assert note in result.review_notes and note in decision


# ---- D76 "Make this the scene": the promotion line --------------------------------------------------------------------------
def _with_scene(trio: ConfigTrio, scene_id: str, **fields: Any) -> ConfigTrio:
    """`trio` with scene `scene_id`'s fields replaced (palette as [(name, hex)])."""
    dump = trio.scenes.model_dump(mode="json")
    for scene in dump["scenes"]:
        if scene["id"] == scene_id:
            for key, value in fields.items():
                scene[key] = [{"name": n, "hex": h} for n, h in value] if key == "palette" else value
    return ConfigTrio(scenes=SceneCatalog.model_validate(dump), modes=trio.modes, recipes=trio.recipes)


def _promoted(trio: ConfigTrio, base: str, option: str) -> ConfigTrio:
    """`trio` with `base` given `option`'s palette and its module-level, stepped and painted design (what promoting
    the option in the Lab and publishing leaves)."""
    from conductor.looks.schema import Mode

    o_scene = trio.scenes.get(option)
    trio = _with_scene(trio, base, palette=[(c.name, c.hex) for c in o_scene.palette])
    modes = dict(trio.modes.modes)
    b = modes[base].model_dump(mode="json", exclude_unset=True)
    o = modes[option].model_dump(mode="json", exclude_unset=True)
    b["techniques"]["modules"] = o["techniques"]["modules"]
    if "stepped" in o["techniques"] and "stepped" in b["techniques"]:
        b["techniques"]["stepped"] = o["techniques"]["stepped"]
    if "canvas" in b["techniques"]:
        b["techniques"]["canvas"]["canvas"]["field"] = o["techniques"]["modules"]["sample"]["canvas"]["field"]
    modes[base] = Mode.model_validate(b)
    return ConfigTrio(
        scenes=trio.scenes, modes=trio.modes.model_copy(update={"modes": modes}), recipes=trio.recipes
    )


def test_a_promotion_is_recorded_with_a_sign_off_prompt():
    from tests.review_fixture import REVIEW_IDS, review_trio

    review = review_trio(APPROVED)
    patched = _promoted(review, "golden-hour", "golden-hour-2")
    assert X.promotions(patched, ["golden-hour"]) == [
        ("golden-hour", "golden-hour-2")
    ]  # one pair, never #3 too
    assert X.promotions(patched, REVIEW_IDS) == [("golden-hour", "golden-hour-2")]  # options themselves: none
    assert X.promotions(patched, ["golden-hour"], approved=patched) == []  # already the base's design in git
    # negatives: the review catalog as built (each option's design differs from its base's) ...
    assert X.promotions(review, REVIEW_IDS) == []
    # ... the option's field with the base's palette changed to another colour ...
    recoloured = [(c.name, c.hex) for c in patched.scenes.get("golden-hour").palette]
    recoloured[0] = (recoloured[0][0], "#123456")
    assert X.promotions(_with_scene(patched, "golden-hour", palette=recoloured), ["golden-hour"]) == []
    # ... and the option's palette with the base keeping its own field
    same_palette = _with_scene(review, "golden-hour-2", palette=recoloured)
    same_palette = _with_scene(same_palette, "golden-hour", palette=recoloured)
    assert X.promotions(same_palette, ["golden-hour"]) == []

    scene_ids = X.changed_scenes(review, patched)
    assert scene_ids == ["golden-hour"]
    changes = X._changes(review, patched, scene_ids, CANOPY)
    decision = X.decision_draft(review, patched, scene_ids, changes, root=ROOT, version=_version(), now=NOW)
    assert "Made the scene on site (D76" in decision
    assert "Golden Hour now has Golden Hour #2's palette and layout (golden-hour-2 → golden-hour)" in decision
    assert "If the client chose it at the review, add who chose it and when before merging" in decision
    for line in decision.splitlines():
        if "sign-off" in line:
            assert "needs the client's sign-off" in line or "If the client chose it" in line, line
    # with a D59 colour change too, the promotion line follows the D59 line, and still asserts no approval
    both = _with_scene(patched, "golden-hour-2", palette=recoloured)
    both = _with_scene(both, "golden-hour", palette=recoloured)
    changes = X._changes(review, both, X.changed_scenes(review, both), CANOPY)
    lines = X.decision_draft(
        review, both, ["golden-hour"], changes, root=ROOT, version=None, now=NOW
    ).splitlines()
    d59 = next(i for i, line in enumerate(lines) if line.startswith("Approved colour changes (D59"))
    assert lines[d59 + 1].startswith("Made the scene on site (D76")
    # a later tweak of the base drops the line (the D59 line, which asks for the sign-off, stays)
    tweaked = _with_scene(both, "golden-hour", palette=[*recoloured[:-1], (recoloured[-1][0], "#654321")])
    changes = X._changes(review, tweaked, ["golden-hour"], CANOPY)
    decision = X.decision_draft(review, tweaked, ["golden-hour"], changes, root=ROOT, version=None, now=NOW)
    assert "Made the scene on site" not in decision and "needs the client's sign-off" in decision


def test_a_brightness_only_edit_changes_no_approved_colour_and_no_layout():
    result = export(edit_scene(APPROVED, "goodnight-glow", brightness=30))
    assert result.problem == "" and result.approved_changes == [] and result.layout_changes == []
    assert (
        "Approved colour changes" not in result.decision
        and "Brightness (drafts): The Goodnight Glow 28 → 30." in (result.decision)
    )
    assert not any("Deploy only in a commissioning window" in n for n in result.review_notes)


def test_a_painted_layout_change_carries_the_slot_repaint_sentence():
    painted = export(golden_edit())
    sentence = "Golden Hour: " + X.SLOT_REPAINT.format(slot="custom-3")
    assert sentence in painted.review_notes and sentence in painted.decision
    # a hex change alone on a painted scene changes the slot's content too
    hex_only = edit_scene(
        APPROVED,
        "rainbow-row-sunset",
        palette=[
            ("Pastel coral", "#FF9E97"),
            *[(c.name, c.hex) for c in APPROVED.scenes.get("rainbow-row-sunset").palette[1:]],
        ],
    )
    assert any("custom-1 on every module" in n for n in export(hex_only).review_notes)
    # a module-level scene has no slot to repaint; brightness isn't slot content
    assert not any(
        "commissioning window" in n for n in export(canonical_edits()).review_notes if "Golden" not in n
    )
    assert not any(
        "commissioning window" in n
        for n in export(edit_scene(APPROVED, "golden-hour", brightness=90)).review_notes
    )


def test_the_export_and_draft_changes_agree_on_a_slot_repaint():
    """A rename changes only recipe keys, so the painted frames, the slot hash and the slot stay as they are: neither
    the preview's badge (draft_changes) nor the export asks for a commissioning window. A hex edit asks in both."""
    from conductor.config import ConductorSettings
    from conductor.design_lab import draft as D

    def both(changes: dict) -> tuple[bool, bool]:
        data = D.seed_draft(APPROVED, "golden-hour").model_dump()
        for colour in data["palette"]:
            if colour["name"] == "Sunset pink":
                colour.update(changes)
        draft = D.SceneDraft.model_validate(data)
        patched = D.apply_drafts(
            APPROVED, {"golden-hour": draft}, canopy=CANOPY, settings=ConductorSettings()
        )
        flagged = D.draft_changes(APPROVED, draft, canopy=CANOPY)["needs_slot_repaint"]
        return flagged, any("commissioning window" in n for n in export(patched).review_notes)

    assert both({"name": "Sunset rose"}) == (False, False)
    assert both({"hex": "#FF8FBF"}) == (True, True)


def test_the_file_header_lines_naming_a_rewritten_scene_are_listed_for_review():
    notes = export(golden_edit()).review_notes
    headers = [n for n in notes if "(file header)" in n]
    assert headers and all(n.startswith("config/modes.yaml:") and "Golden Hour" in n for n in headers)


def test_a_redesigned_block_keeps_no_stale_comment(tmp_path):
    after = after_texts(export(golden_edit(), version=_version("Client Oct 17 v2")), tmp_path)[X.MODES_FILE]
    lines = after.splitlines()
    golden = "\n".join(lines[_block(lines, "golden-hour").start : _block(lines, "golden-hour").stop])
    comments = " ".join(X.split_comment(line)[1] for line in golden.splitlines()).lower()
    assert "#" in comments  # the comments that still hold are kept
    for stale in ("metric: canopy", "a sun", "the sun", "rays", "outwards", "left edge"):
        assert stale not in comments, stale
    assert "canvas: &golden-design   # diagonally (Design Lab: Client Oct 17 v2)" in golden
    assert "look: {kind: rgb, rgb: [255, 143, 191], brightness: 90}   # sunset pink" in golden
    assert "step_s: 6               # design pace" in golden  # a timing comment still true is kept
    # stops moved (a colour removed): their "1/3 exactly" comments go
    tides = after_texts(export(structural("remove")), tmp_path / "remove")[X.MODES_FILE]
    assert "1/3 exactly" not in tides and "2/3 exactly" not in tides
    assert "&tides-design   # diagonally (Design Lab)" in tides


def test_client_copy_with_yaml_specials_round_trips(tmp_path):
    special = 'Dusk: rose — Charleston’s "best" #1 trailing #'
    patched = edit_scene(
        APPROVED,
        "goodnight-glow",
        name="Off",
        mood="No",
        description=special,
        palette=[("On", "#8B8BD1"), ("Null", "#BEA9E6"), ("Moonlight blue", "#C4D6F3")],
        keys=["on", "null", "moonlight-blue"],
    )
    result = export(patched)
    assert result.problem == ""
    after = after_texts(result, tmp_path)
    got = X.reread(after, CANOPY)
    assert got.dump() == patched.dump()
    scene = got.scenes.get("goodnight-glow")
    assert (scene.name, scene.mood, scene.description) == ("Off", "No", special)
    assert got.modes.mode("goodnight-glow").label == "Off" and set(got.recipes.colors) >= {"on", "null"}
    assert 'name: "Off"' in after[X.SCENES_FILE] and 'label: "Off"' in after[X.MODES_FILE]
    assert '  "on":' in after[X.RECIPES_FILE] and 'color: "null"}' in after[X.MODES_FILE]


def test_the_flow_writers_read_back_as_what_they_wrote():
    import yaml

    for field in (
        {"kind": "linear", "angle_deg": 30.0, "transform": "mirror", "invert": True, "cycles": 1},
        {"kind": "radial", "centre_in": [0.0, 60.0], "metric": "canopy", "radius_in": 90.5, "cycles": 1},
        {"kind": "sweep", "centre_in": [12.5, 60.0], "start_deg": 90.0},
        {"kind": "fins", "invert": True},
    ):
        spec = {
            "linear": F.LinearField,
            "radial": F.RadialField,
            "sweep": F.SweepField,
            "fins": F.FinsField,
        }[field["kind"]]
        model = spec.model_validate(field)
        line = X.field_flow(model.model_dump(mode="json"))
        assert line.startswith(f"{{kind: {field['kind']}") and ".0," not in line and ".0]" not in line
        assert spec.model_validate(yaml.safe_load(line)) == model
    assert X.split_comment('feature: "a # inside"   # and a comment') == (
        'feature: "a # inside"',
        "   # and a comment",
    )
    assert X.split_comment("look: {kind: rgb}") == ("look: {kind: rgb}", "")
    assert (
        X.field_flow(F.FinsField(kind="fins", invert=True).model_dump(mode="json"))
        == "{kind: fins, invert: true}"
    )
    assert X.field_flow(F.FinsField(kind="fins").model_dump(mode="json")) == "{kind: fins}"


def test_describe_field_names_the_fin_rings():
    assert X.describe_field(F.FinsField(kind="fins")) == "fin rings (the drawing's drops, deep centre first)"
    assert (
        X.describe_field(F.FinsField(kind="fins", invert=True))
        == "fin rings (the drawing's drops, deep centre first), inverted"
    )


def test_export_never_mutates_its_inputs_or_the_cached_catalog():
    patched = canonical_edits()
    before = (APPROVED.dump(), patched.dump(), load_scenes().model_dump())
    export(patched, version=_version())
    assert (APPROVED.dump(), patched.dump(), load_scenes().model_dump()) == before


# ---- the route --------------------------------------------------------------------------------------------------------
def _route_export(app_client, monkeypatch, patched: ConfigTrio, *, format: str = "json") -> Any:
    """GET /api/lab/export for a saved version whose drafts give `patched` (apply_drafts and the store stubbed, so
    this runs before S1 and S2 land)."""
    from conductor.design_lab import draft as D
    from conductor.design_lab import store as S

    version = _version()
    monkeypatch.setattr(S.LookStore, "get", lambda self, version_id: version)
    monkeypatch.setattr(D, "apply_drafts", lambda trio, drafts, **kw: patched)
    monkeypatch.setattr(D, "draft_changes", lambda approved, draft, **kw: {})
    return app_client.get(f"/api/lab/export?version={version.id}&format={format}")


def test_an_export_with_a_d63_warning_is_refused(app_client, monkeypatch):
    # live: the app's Lab holds the working tree's config, so the draft is made from it
    stripes = edit_scene(LIVE, "rainbow-row-sunset", layout={"kind": "linear", "angle_deg": 0})
    r = _route_export(app_client, monkeypatch, stripes)
    assert r.status_code == 409, r.text
    body = r.json()
    assert [w["rule"] for w in body["warnings"]] == ["two-d"]
    assert body["detail"].startswith(LIVE.scenes.get("rainbow-row-sunset").name + ": " + R.STRIPES)
    assert (
        "tests/test_modes_match_scenes.py::test_no_scene_reads_as_three_columns_at_module_level would fail"
        in (body["detail"])
    )


def test_the_route_exports_json_and_a_patch_file(app_client, monkeypatch):
    # live: a brightness edit of the working tree's Golden Hour (whatever its approved value)
    level = LIVE.scenes.get("golden-hour").brightness_draft
    patched = edit_scene(LIVE, "golden-hour", brightness=level + 1 if level < 100 else level - 1)
    r = _route_export(app_client, monkeypatch, patched)
    assert r.status_code == 200, r.text
    body = r.json()
    # a brightness edit: scenes, modes and the pinned brightness; no recipe changes
    assert body["problem"] == "" and body["files"] == [X.SCENES_FILE, X.MODES_FILE, X.FIXTURE_FILE]
    assert re.fullmatch(r"design-lab-v0002-\d{8}T\d{6}Z\.patch", body["filename"])
    direct = X.export_patch(LIVE, patched, root=REPO, canopy=CANOPY, version=_version(), now=NOW)
    assert body["patch"] == direct.patch
    r = _route_export(app_client, monkeypatch, patched, format="patch")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/x-diff")
    assert "attachment" in r.headers["content-disposition"] and r.text.startswith(
        "diff --git a/config/scenes.yaml"
    )


# ---- through S1's apply_drafts (skipped while it is a stub) -------------------------------------------------------------
def test_an_export_of_applied_drafts_rereads_as_the_patched_trio(tmp_path):
    from conductor.config import ConductorSettings
    from conductor.design_lab.draft import DraftColour, SceneDraft, apply_drafts

    scene = APPROVED.scenes.get("golden-hour")
    mode = APPROVED.modes.mode("golden-hour")
    stepped = mode.techniques.stepped
    draft = SceneDraft(
        scene_id="golden-hour",
        name=scene.name,
        mood=scene.mood,
        description=scene.description,
        palette=[
            DraftColour(name=c.name, hex="#FF8FBF" if c.name == "Sunset pink" else c.hex)
            for c in scene.palette
        ],
        layout=F.LinearField(kind="linear", angle_deg=30),
        brightness=90,
        solid=scene.default_solid,
        stepped={
            "direction": stepped.direction,
            "steps_per_cycle": stepped.steps_per_cycle,
            "step_s": stepped.step_s,
            "run_s": stepped.run_s,
        },
    )
    patched = apply_drafts(APPROVED, {"golden-hour": draft}, canopy=CANOPY, settings=ConductorSettings())
    result = export(patched, version=_version())
    assert result.problem == ""
    assert X.reread(after_texts(result, tmp_path), CANOPY).dump() == patched.dump()


def _breathe_golden(trio: ConfigTrio) -> ConfigTrio:
    """Golden Hour → breathe (swing 5, lag 2) through S1's apply_drafts, copy auto (the generated breath sentence)."""
    from conductor.config import ConductorSettings
    from conductor.design_lab.draft import SceneDraft, apply_drafts, seed_draft

    data = seed_draft(trio, "golden-hour").model_dump()
    data["stepped"].update(mode="breathe", brightness_swing=5, lag_steps=2)
    draft = SceneDraft.model_validate(data)
    return apply_drafts(trio, {"golden-hour": draft}, canopy=CANOPY, settings=ConductorSettings())


def test_a_change_of_motion_kind_exports_inserted_lines_that_re_read_equal(tmp_path):
    """D75: the baseline's stepped block has no mode / brightness_swing / lag_steps lines (travel never needs them), so
    the export adds them under techniques → stepped, at its children's indent, and the re-read trio is the patched
    one. Going back to travel then rewrites those lines in place (explicit zeros load on a travel rung, C6)."""
    patched = _breathe_golden(APPROVED)
    result = export(patched, version=_version())
    assert result.problem == ""
    added = [x[1:] for x in result.patch.splitlines() if x.startswith("+") and not x.startswith("+++")]
    for line in ("        mode: breathe", "        brightness_swing: 5", "        lag_steps: 2"):
        assert line in added
    after = after_texts(result, tmp_path)
    assert X.reread(after, CANOPY).dump() == patched.dump()
    lines = after[X.MODES_FILE].splitlines()
    block = [lines[i] for i in _block(lines, "golden-hour")]
    stepped = block[block.index("      stepped:") :]
    assert stepped[1:9] == [
        "        canvas: *golden-design",
        "        direction: forward",  # the old "outwards from the sun" no longer describes the run
        "        steps_per_cycle: 6",
        stepped[4],  # step_s and its pace comment, unchanged
        "        run_s: 90",
        "        brightness: 95",
        f"        moving_feature: {json.dumps(patched.modes.mode('golden-hour').techniques.stepped.moving_feature)}",
        "        mode: breathe",
    ]
    assert stepped[9:11] == ["        brightness_swing: 5", "        lag_steps: 2"]
    assert stepped[4].startswith("        step_s: 6")
    assert any("Golden Hour: stepped run mode travel → breathe" in c for c in result.stepped_changes)
    assert "mode travel → breathe, brightness_swing 0 → 5, lag_steps 0 → 2" in result.decision
    # and back: the lines are there now, so they are rewritten where they stand
    root = tmp_path / "breathing"
    shutil.copytree(ROOT, root)
    for path, text in after.items():
        (root / path).write_text(text, encoding="utf-8")
    breathing = X.reread(after, CANOPY)
    travel = X.export_patch(breathing, APPROVED, root=root, canopy=CANOPY, version=None, now=NOW)
    assert travel.problem == "", travel.problem
    back = applied(scratch_repo_of(root, tmp_path / "back"), travel.patch)
    assert X.reread(back, CANOPY).dump() == APPROVED.dump()
    block = back[X.MODES_FILE].splitlines()
    for line in ("        mode: travel", "        brightness_swing: 0", "        lag_steps: 0"):
        assert line in block


def scratch_repo_of(root: Path, repo: Path) -> Path:
    """A git repo holding `root`'s config/ and fixture, committed."""
    shutil.copytree(root / "config", repo / "config")
    (repo / "tests").mkdir()
    shutil.copy(root / X.FIXTURE_FILE, repo / X.FIXTURE_FILE)
    assert _git(repo, "init", "-q").returncode == 0
    assert _git(repo, "add", "-A").returncode == 0
    commit = _git(repo, "-c", "user.name=lab", "-c", "user.email=lab@localhost", "commit", "-qm", "base")
    assert commit.returncode == 0, commit.stderr
    return repo


def test_a_stepped_rung_written_as_a_flow_mapping_offers_no_patch_for_new_keys():
    """The breathe keys need a line each under techniques → stepped; a flow-mapped stepped rung has no place for
    them, so the export refuses rather than guessing (LocateError → no patch)."""
    lines = ["    techniques:", "      stepped: {canvas: *golden-design, direction: forward}"]
    walked = list(X._walk(lines, 0, len(lines)))
    travel = {"mode": "travel", "shift": 0.0, "brightness_swing": 0, "lag_steps": 0, "seed": None}
    before = {"techniques": {"stepped": travel}}
    after = {"techniques": {"stepped": {**travel, "mode": "breathe", "brightness_swing": 5}}}
    with pytest.raises(
        X.LocateError, match="isn't a block mapping, so mode, brightness_swing can't be added"
    ):
        X._add_stepped_keys(lines, walked, {}, before, after, "golden-hour")
    edits: dict[int, list[str]] = {}
    X._add_stepped_keys(lines, walked, edits, before, before, "golden-hour")  # nothing new: nothing to place
    assert edits == {}


# ---- make scene-pins ----------------------------------------------------------------------------------------------------
def _regen_script():
    spec = importlib.util.spec_from_file_location(
        "regen_scene_pins", REPO / "scripts" / "regen_scene_pins.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_regen_scene_pins_matches_the_committed_fixture(tmp_path, capsys):
    regen = _regen_script()
    committed = (REPO / X.FIXTURE_FILE).read_text(encoding="utf-8")  # live: the committed fixture
    assert regen.fixture_text() == committed
    assert regen.main(["--check"]) == 0
    out = tmp_path / "frames.json"
    assert regen.main(["--out", str(out)]) == 0 and out.read_text(encoding="utf-8") == committed
    out.write_text("{}", encoding="utf-8")
    assert regen.main(["--check", "--out", str(out)]) == 1
    # after a hand edit of config/, it writes what the export would have
    config = tmp_path / "config"
    shutil.copytree(ROOT / "config", config)
    patched = golden_edit()
    texts = after_texts(export(patched), tmp_path)
    for path in X.EXPORT_FILES[:3]:
        (tmp_path / path).write_text(texts[path], encoding="utf-8")
    assert regen.main(["--config", str(config), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == texts[X.FIXTURE_FILE]


# ---- the fins' rings (D74) ----------------------------------------------------------------------------------------
def test_a_fins_layout_exports_rereads_and_pins(tmp_path):
    """A Lab draft on the fins kind exports as `field: {kind: fins}`, applies with git apply, re-reads through the
    real loaders over the fin-mapped canopy, and pins the drawing's rings (r0c1 and r2c1 are one ring)."""
    from conductor.config import ConductorSettings
    from conductor.design_lab.draft import apply_drafts, seed_draft

    draft = seed_draft(APPROVED, "golden-hour").model_copy(update={"layout": F.FinsField(kind="fins")})
    patched = apply_drafts(APPROVED, {"golden-hour": draft}, canopy=CANOPY, settings=ConductorSettings())
    result = export(patched)
    assert result.problem == ""
    assert [ln for ln in result.patch.splitlines() if ln.startswith("+") and "field: {kind: fins}" in ln]
    after = after_texts(result, tmp_path)
    assert X.reread(after, CANOPY).dump() == patched.dump()
    pin = json.loads(after[X.FIXTURE_FILE])["_pins"]["golden-hour"]
    assert pin["layout"] == {"kind": "fins", "transform": "linear", "invert": False, "cycles": 1}
    assert pin["layout_words"] == "from the centre out"
    assert pin["t3"]["r0c1"] == pin["t3"]["r2c1"]
