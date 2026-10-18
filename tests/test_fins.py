"""The canopy's 650 fins (D74): config/fins.json as validated data, the fin map, and the canopy loader that carries it.

Acceptance data: config/fins.json and tests/fixtures_fins_lights.json are byte copies of the plan's extracts of VWBD
drawing A-20-1 (docs/plans/preset-options/fins.json and lights.json; lights.json lists every light with the
weighted fin drop around it). The fixture is copied so the suite survives archiving the plan folder. The weight is
reimplemented here by brute force, never imported.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import os
import random
import shutil
from collections import Counter
from pathlib import Path

import pytest
import yaml

from conductor import preflight
from conductor.config import CONFIG_DIR, ConfigError
from conductor.fins import (
    FinsFile,
    FinsSection,
    build_fin_map,
    fin_problems,
    head_problems,
    parse_vwbd_panels,
)
from conductor.geometry import build_layout, lattice_heads, load_canopy_config
from conductor.looks import field as F

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "docs" / "plans" / "preset-options"
LIGHTS_FIXTURE = Path(__file__).resolve().parent / "fixtures_fins_lights.json"
BASELINE = Path(__file__).resolve().parent / "fixtures_lab_baseline"
CANOPY = load_canopy_config()
LIGHTS = json.loads(LIGHTS_FIXTURE.read_text())
FINS_BYTES = (CONFIG_DIR / "fins.json").read_bytes()
FINS_RAW = json.loads(FINS_BYTES)
CANOPY_TEXT = (CONFIG_DIR / "canopy.yaml").read_text()
POSITIONS = [f"r{r}c{c}" for r in range(3) for c in range(3)]
PANELS = {
    "r0c0": "P-03",
    "r0c1": "P-06",
    "r0c2": "P-09",
    "r1c0": "P-02",
    "r1c1": "P-05",
    "r1c2": "P-08",
    "r2c0": "P-01",
    "r2c1": "P-04",
    "r2c2": "P-07",
}
MISSING = object()  # _config: no fins.json at all


def _brute_drop(x: float, y: float, weight=lambda d: math.exp(-((d / 6) ** 2))) -> float | None:
    """The drawing's drop around (x, y): every fin within 12 in, weighted (default exp(−(d/6)²)), in file order."""
    total = weighted = 0.0
    for f in FINS_RAW["fins"]:
        d = math.hypot(f["x"] - x, f["y"] - y)
        if d <= 12:
            w = weight(d)
            total += w
            weighted += w * f["drop_in"]
    return weighted / total if total else None


def _config(tmp_path: Path, mutate=None, *, fins=None, canopy: str | None = None) -> Path:
    """A config folder with canopy.yaml (the shipped text, or `canopy`) and fins.json (the shipped file through
    `mutate`, or the text `fins`, or MISSING). Returns canopy.yaml's path."""
    folder = tmp_path / "config"
    folder.mkdir(exist_ok=True)
    (folder / "canopy.yaml").write_text(CANOPY_TEXT if canopy is None else canopy)
    if fins is MISSING:
        return folder / "canopy.yaml"
    if fins is None:
        data = copy.deepcopy(FINS_RAW)
        if mutate is not None:
            mutate(data)
        fins = json.dumps(data)
    (folder / "fins.json").write_text(fins)
    return folder / "canopy.yaml"


def _refused(path: Path) -> str:
    with pytest.raises(ConfigError) as e:
        load_canopy_config(path)
    return str(e.value)


# ---- the data ------------------------------------------------------------------------------------------------------
def test_config_fins_json_is_the_drawings_extract():
    assert CANOPY["fin_map"].digest == "7a1754886f97"
    assert (ROOT / "config" / "fins.json").stat().st_size == 34541
    if (PLAN / "fins.json").exists():
        assert (ROOT / "config" / "fins.json").read_bytes() == (
            PLAN / "fins.json"
        ).read_bytes()  # a byte copy
    assert CANOPY["fins"] == {"file": "fins.json", "apron_in": 6.0, "fin_width_in": 5.0}


def test_the_lights_fixture_is_the_plans_extract():
    raw = LIGHTS_FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest()[:12] == "0326af5363b5"
    if (PLAN / "lights.json").exists():
        assert raw == (PLAN / "lights.json").read_bytes()


def test_650_fins_match_the_drop_key_and_hang_inside_the_apron():
    fin_map = CANOPY["fin_map"]
    fins = fin_map.fins
    assert len(fins) == 650
    assert Counter(f.drop_in for f in fins) == {
        14: 160,
        15: 88,
        16: 52,
        17: 44,
        18: 48,
        19: 40,
        20: 38,
        21: 42,
        22: 38,
        23: 48,
        24.5: 52,
    }
    assert (fin_map.deep_in, fin_map.shallow_in) == (24.5, 14.0)
    assert all(-6 <= f.x <= 231 and -6 <= f.y <= 126 for f in fins)
    apron = [f for f in fins if not (0 <= f.x <= 225 and 0 <= f.y <= 120)]
    assert len(apron) == 104 and {f.drop_in for f in apron} == {14}  # spill only: a built-in vignette
    assert all(0 <= f.angle_deg <= 180 for f in fins)
    assert sum(f.angle_deg == 0.0 for f in fins) == 1 and sum(f.angle_deg == 180.0 for f in fins) == 5
    assert len({(f.x, f.y) for f in fins}) == 650


def test_every_lights_fin_drop_matches_lights_json():
    fin_map = CANOPY["fin_map"]
    fins = F.FinsField(kind="fins")
    assert len(LIGHTS["lights"]) == 270
    for light in LIGHTS["lights"]:
        x, y, drop = light["x"], light["y"], light["fin_drop_in"]
        assert abs(fin_map.drop_at(x, y) - drop) <= 0.05  # 0.0102 measured
        expected = min(1.0, max(0.0, (24.5 - drop) / 10.5))
        assert abs(F.value(fins, x, y, CANOPY) - expected) <= 0.05 / 10.5 + 1e-9


def test_the_weight_is_exp_minus_d_over_6_squared_cut_at_12_in():
    fin_map = CANOPY["fin_map"]
    rng = random.Random(74)
    points = [*lattice_heads(CANOPY), *((rng.uniform(-6, 231), rng.uniform(-6, 126)) for _ in range(200))]
    for x, y in points:
        brute, got = _brute_drop(x, y), fin_map.drop_at(x, y)
        assert (brute is None) == (got is None), (x, y)
        if brute is not None:
            assert abs(got - brute) <= 1e-9, (x, y)
    # the textbook Gaussian with σ 6 is not what lights.json was made with: it misses at 119 of the 270 heads
    textbook = [
        abs(_brute_drop(lt["x"], lt["y"], weight=lambda d: math.exp(-(d * d) / 72)) - lt["fin_drop_in"])
        for lt in LIGHTS["lights"]
    ]
    assert sum(e > 0.05 for e in textbook) >= 100


def test_lights_json_is_the_canopy_lattice():
    lights = sorted((lt["module"], float(lt["x"]), float(lt["y"])) for lt in LIGHTS["lights"])
    assert lights == sorted((h.module, h.x, h.y) for h in build_layout(CANOPY))
    assert set(lattice_heads(CANOPY)) == {(h.x, h.y) for h in build_layout(CANOPY)}
    # p14 is what is built: under p18 only 150 of the drawing's 270 lights would be heads (canopy.yaml's comment)
    p18 = {(h.x, h.y) for h in build_layout(CANOPY, "p18")}
    assert sum((lt["x"], lt["y"]) in p18 for lt in LIGHTS["lights"]) == 150


def test_vwbd_panels_are_the_drawings():
    assert CANOPY["vwbd_panels"] == PANELS
    assert {panel: pos for pos, panel in CANOPY["vwbd_panels"].items()} == LIGHTS["vwbd_panel_to_module"]
    assert all(lt["vwbd_panel"] == CANOPY["vwbd_panels"][lt["module"]] for lt in LIGHTS["lights"])


def test_a_canopy_without_the_sections_loads_as_before(tmp_path):
    baseline = load_canopy_config(BASELINE / "canopy.yaml")
    assert not {"fin_map", "fins", "vwbd_panels"} & set(baseline)
    assert set(baseline) == set(yaml.safe_load((BASELINE / "canopy.yaml").read_text())["canopy"])
    raw = yaml.safe_load(CANOPY_TEXT)
    del raw["canopy"]["fins"]
    path = _config(tmp_path, canopy=yaml.safe_dump(raw), fins=MISSING)
    panels_only = load_canopy_config(path)
    assert panels_only["vwbd_panels"] == PANELS and "fin_map" not in panels_only


def test_the_fin_map_is_loaded_once_and_is_cheap_to_copy_and_key(tmp_path):
    first, again = load_canopy_config(), load_canopy_config()
    assert first["fin_map"] is again["fin_map"]
    assert copy.deepcopy(first)["fin_map"] is first["fin_map"]
    text = repr(first["fin_map"])
    assert text.startswith("FinMap(650 fins, sha256 ") and len(text) < 60
    path = _config(tmp_path, fins=FINS_BYTES.decode())
    before = load_canopy_config(path)["fin_map"]
    fins = path.parent / "fins.json"
    edited = fins.read_text().replace('"angle_deg":26.4,', '"angle_deg":26.45,', 1)
    assert edited != fins.read_text()
    fins.write_text(edited)  # a different size …
    st = fins.stat()
    os.utime(fins, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))  # … and a later mtime (coarse clocks)
    after = load_canopy_config(path)["fin_map"]
    assert after is not before and after.fins[0].angle_deg == 26.45


# ---- refusals ------------------------------------------------------------------------------------------------------
def _fin0(**over):
    return lambda d: d["fins"][0].update(over)


def _drop_one_fin(d):
    d["fins"].pop(0)  # a 14 in fin; count and key adjusted to match, so only the 650 rule is broken
    d["count"] = 649
    d["drop_key"][0]["count"] -= 1


def _same_centre(d):
    d["fins"][1].update(x=d["fins"][0]["x"], y=d["fins"][0]["y"])


@pytest.mark.parametrize(
    ("make", "needles"),
    [
        (lambda p: _config(p, fins=MISSING), ["fins.json is missing", "canopy → fins → file names it"]),
        (lambda p: _config(p, fins="{not json"), ["is not valid JSON"]),
        (lambda p: _config(p, _fin0(x=float("nan"))), ["fins → [0] → x", "finite"]),
        (lambda p: _config(p, _fin0(angle_deg=181)), ["fins → [0] → angle_deg", "less than or equal to 180"]),
        (lambda p: _config(p, _fin0(angle_deg=-1)), ["greater than or equal to 0"]),
        (lambda p: _config(p, _fin0(drop_in=0)), ["fins → [0] → drop_in"]),
        (lambda p: _config(p, _fin0(width=5)), ["Extra inputs are not permitted"]),
        (lambda p: _config(p, _fin0(drop_in=13)), ["the drop key has 0 fins of 13 in but fins lists 1"]),
        (lambda p: _config(p, lambda d: d.update(count=649)), ["count says 649 fins but fins lists 650"]),
        (lambda p: _config(p, _drop_one_fin), ["fins lists 649 fins but drawing A-20-1 has 650"]),
        (lambda p: _config(p, _fin0(x=-7)), ["hangs outside the canopy and its 6 in apron"]),
        (lambda p: _config(p, _same_centre), ["has the same centre as fins → [0]"]),
        (lambda p: _config(p, lambda d: d.update(apron_in=5)), ["apron_in is 5 but"]),
        (lambda p: _config(p, lambda d: d.update(fin_plan_width_in=4)), ["fin_plan_width_in is 4 but"]),
        (lambda p: _config(p, lambda d: d["outer_in"].update(x=[-6, 230])), ["outer_in is x"]),
        (
            lambda p: _config(p, canopy=CANOPY_TEXT.replace("file: fins.json", "file: ../fins.json")),
            ["must be a file name next to canopy.yaml"],
        ),
        (
            lambda p: _config(p, canopy=CANOPY_TEXT.replace("    fin_width_in: 5\n", "")),
            ["fin_width_in", "Field required"],
        ),
    ],
)
def test_bad_fins_files_name_the_file_the_entry_and_the_fix(tmp_path, make, needles):
    assert "    fin_width_in: 5\n" in CANOPY_TEXT and "file: fins.json" in CANOPY_TEXT
    msg = _refused(make(tmp_path))
    for needle in needles:
        assert needle in msg, msg
    assert "Restore it from git" in msg or "Fix the file and restart." in msg, msg


def test_the_problem_list_stops_at_ten(tmp_path):
    def twelve(d):
        for f in d["fins"][:12]:
            f["angle_deg"] = 200

    lines = _refused(_config(tmp_path, twelve)).splitlines()
    assert lines[0].endswith("fins.json has 12 problem(s):")
    assert sum(line.startswith("  • fins → [") for line in lines) == 10
    assert lines[-2:] == [
        "  … and 2 more",
        "Restore it from git (VWBD drawing A-20-1, extracted) and restart.",
    ]


def _one_drop(d):
    for f in d["fins"]:
        f["drop_in"] = 14
    d["drop_key"] = [{"drawing_colour": "light-blue", "drop_in": 14, "count": 650}]


def test_a_one_drop_key_is_refused_at_load(tmp_path):
    data = copy.deepcopy(FINS_RAW)
    _one_drop(data)
    section = FinsSection(file="fins.json", apron_in=6, fin_width_in=5)
    problems = fin_problems(FinsFile.model_validate(data), section, 225, 120, lattice_heads(CANOPY))
    assert any("the drop key needs at least two different drops (it has only 14 in)" in p for p in problems)
    assert "the fins field has no depth to read" in _refused(
        _config(tmp_path, _one_drop)
    )  # never ZeroDivisionError


def test_a_light_with_no_fin_within_12_in_is_refused():
    data = FinsFile.model_validate(
        {
            **{
                k: FINS_RAW[k]
                for k in ("source", "frame", "apron_in", "outer_in", "fin_plan_width_in", "angle")
            },
            "drop_key": [
                {"drawing_colour": "light-blue", "drop_in": 14, "count": 1},
                {"drawing_colour": "purple", "drop_in": 24.5, "count": 1},
            ],
            "count": 2,
            "fins": [
                {"x": 200, "y": 60, "angle_deg": 30, "drop_in": 14},
                {"x": 210, "y": 60, "angle_deg": 30, "drop_in": 24.5},
            ],
        }
    )
    fin_map = build_fin_map(data, digest="test", apron_in=6, fin_width_in=5, heads=[(2.5, 5.0)])
    assert head_problems(fin_map, [(2.5, 5.0)]) == [
        "1 light(s) have no fin within 12 in, e.g. (2.5, 5): the fins field has no value there"
    ]
    assert fin_map.t_at(2.5, 5.0) == 1.0
    assert head_problems(fin_map, [(205.0, 60.0)]) == []


@pytest.mark.parametrize(
    ("raw", "needle"),
    [
        ({k: v for k, v in PANELS.items() if k != "r2c2"}, "no panel for r2c2"),
        ({**PANELS, "r0c0": "P-10"}, "panel numbers must read P-01 … P-09"),
        ({**PANELS, "r0c1": "P-03"}, "P-03 is given to more than one position"),
        ({**PANELS, "r3c0": "P-01"}, "r3c0 isn't a canopy position"),
        (list(PANELS.values()), "must map each canopy position"),
    ],
)
def test_bad_vwbd_panels_are_refused_plainly(raw, needle):
    with pytest.raises(ConfigError) as e:
        parse_vwbd_panels(Path("config/canopy.yaml"), raw, POSITIONS)
    assert needle in str(e.value) and "config/canopy.yaml → canopy → vwbd_panels" in str(e.value)


def test_a_broken_canopy_yaml_is_a_config_error_naming_the_file(tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "canopy.yaml").write_text("canopy: [\n")
    with pytest.raises(ConfigError, match="canopy.yaml is not valid YAML at line"):
        load_canopy_config(bad / "canopy.yaml")
    (tmp_path / "none.yaml").write_text("name: The Color Canopy\n")
    with pytest.raises(ConfigError, match="has no canopy: section"):
        load_canopy_config(tmp_path / "none.yaml")
    with pytest.raises(ConfigError, match="is missing"):
        load_canopy_config(tmp_path / "nowhere" / "canopy.yaml")
    raw = yaml.safe_load(CANOPY_TEXT)
    raw["canopy"]["orientation_scheme"] = "p99"
    path = _config(tmp_path, canopy=yaml.safe_dump(raw))
    assert "orientation_scheme 'p99' isn't one of ['p14', 'p18']" in _refused(path)


# ---- the deploy preflight ------------------------------------------------------------------------------------------
def _snapshot(folder: Path) -> dict[str, str]:
    return {
        p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def _preflight(config: Path, data: Path) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    env = {"CONDUCTOR_DATA_DIR": str(data), "CONDUCTOR_MODE": "sim"}
    code = preflight.run(env=env, config_dir=config, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def _angle_181(fins: Path) -> None:
    data = json.loads(fins.read_text())
    data["fins"][0]["angle_deg"] = 181
    fins.write_text(json.dumps(data))


@pytest.mark.parametrize(
    ("break_it", "needle"),
    [(lambda fins: fins.unlink(), "fins.json is missing"), (_angle_181, "angle_deg")],
    ids=["missing", "angle-181"],
)
def test_a_broken_fins_file_fails_the_deploy_preflight(tmp_path, break_it, needle):
    """A checkout whose canopy can't load would refuse the start (build_live_looks loads it), so the preflight that
    `canopy-mac install --check` runs fails before any restart."""
    config, data = tmp_path / "config", tmp_path / "data"
    shutil.copytree(CONFIG_DIR, config)
    data.mkdir()
    code, out, err = _preflight(config, data)
    assert code == 0, err  # the unmodified copy passes
    break_it(config / "fins.json")
    before = _snapshot(data)
    code, out, err = _preflight(config, data)
    assert code == 1
    assert "FAILED in looks" in err and needle in err, err
    assert "preflight  ok" not in out
    assert _snapshot(data) == before
