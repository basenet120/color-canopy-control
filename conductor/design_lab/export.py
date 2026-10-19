"""Export: a saved version as a reviewed git patch of four files, with a drafted DECISIONS entry (PLAN §3.6).

The edited texts are rewritten in place (comments and anchors kept, as `config.overlay_diff`), re-read through the
real loaders and compared with the patched trio; any mismatch sets `problem` and offers no patch. The JSON fixture
(`tests/fixtures_scene_frames.json`, frames + `_pins`) is built in memory, so an export from a runtime without a
`tests/` directory (Docker/staging) is still complete.

How the rewrite works: a small line-oriented locator walks each file, tracks the indent stack of keys to give every
line its key path, and compares the approved and the patched model dumps at that path. Only lines whose value changed
are re-emitted; each keeps its trailing `# comment` except where the comment would now describe the old design (the
design's anchor-line comment, full-line comments inside a redesigned design, stop comments when the stops move, the
stepped rung's direction comments, the strand look's colour comment). Aliases (`*golden-design`) are never touched,
so every rung still shares one design. Every string the rewrite emits is a JSON string (valid YAML), so client copy
like "Off", "No" or "Dusk: rose" can't turn into a bool, null, mapping or comment.

Owners: `EXPORT_FILES` and `LabExport` are the contracts commit (L); the bodies are stream S3's.
"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from conductor.config import ConductorSettings, RecipesFile
from conductor.design_lab import draft as D
from conductor.design_lab import stepped as ST
from conductor.design_lab.draft import ConfigTrio
from conductor.design_lab.store import Version
from conductor.looks import field as F
from conductor.looks.scene_rules import scene_pins
from conductor.looks.schema import FieldCanvas, Mode, parse_modes
from conductor.scenes import SceneCatalog

EXPORT_FILES = (
    "config/scenes.yaml",
    "config/modes.yaml",
    "config/recipes.yaml",
    "tests/fixtures_scene_frames.json",
)
SCENES_FILE, MODES_FILE, RECIPES_FILE, FIXTURE_FILE = EXPORT_FILES
NO_NEWLINE = "\\ No newline at end of file\n"
SLOT_REPAINT = (
    "Deploy only in a commissioning window — or publish it from the Lab: on restart the reconciler rewrites {slot} "
    "on every module (stale slot)."
)
# The Studio reads each scene's palette, name, mood, description and brightness from the show's live looks
# (GET /api/canopy/scenes, D68); demo-model.mjs SCENES is only its fallback copy, shown when that read fails.
STUDIO_SCENES = (
    "demo-model.mjs SCENES (the Studio's fallback copy, shown only when the show's live looks can't be read) still has "
    "{hexes} for {scene}; update it by hand."
)
# A palette that changed with no approved hex changed (a colour added, or renamed): SCENES still has the old list.
STUDIO_SCENES_PALETTE = (
    "demo-model.mjs SCENES (the Studio's fallback copy) still has {scene}'s approved {n}-colour palette ({palette}); "
    "update it by hand."
)
# SCENES also holds the scene's name, its default colour and its brightness (the fallback copy's).
STUDIO_SCENES_OPENS = (
    'demo-model.mjs SCENES (the Studio\'s fallback copy) still opens {scene} as "{name}" on {solid} at {brightness}% '
    "until SCENES is updated by hand."
)
# D65: what an edited stepped timing means on the canopy (the runner's own pace, `stepped.effective_step_s`)
STEPPED_PACE = (
    "nine strands on one account step every {nine:g} s ({hold_at} steps, then the still design holds)"
)
STEPPED_STILL = "nine strands on one account would hold the still design: {why}"
# the modes.yaml step_s comment's pace clause: the runner's step, or the still design when the runner wouldn't step
_STEP_COMMENT = re.compile(r"step every [0-9.]+ s|hold the still design \([^)]*\)")
STEP_COMMENT_STEP = "step every {step:g} s"
STEP_COMMENT_STILL = "hold the still design ({why})"
# D76 "Make this the scene": a fact plus a prompt. It never asserts the client's sign-off (07 §9): only the person
# exporting knows whether the client chose it, so the line asks them to write who and when.
PROMOTION_LINE = (
    "Made the scene on site (D76 “Make this the scene”): {base} now has {option}'s palette and layout "
    "({option_id} → {base_id}). If the client chose it at the review, add who chose it and when before merging: "
    "that choice is the D59 sign-off for its colours."
)
# the D65 section's live state (the Lab never sets motion.stepped.live_enabled; it says where it stands)
STEPPED_SECTION_OFF = "live off until Gate R"
STEPPED_SECTION_ON = "live on (motion.stepped.live_enabled)"
FIXTURE_STALE = (
    "tests/fixtures_scene_frames.json isn't what the approved config gives; run make scene-pins first"
)


@dataclass
class LabExport:
    patch: str  # unified diff, "a/<path>" / "b/<path>" headers, git-apply-able ("" = nothing changed)
    files: list[str]  # which of EXPORT_FILES changed
    scenes: list[str]  # scene ids rewritten
    decision: str  # the drafted DECISIONS entry (markdown)
    approved_changes: list[str]  # D59 sentences ("Golden Hour: Sunset pink #FF9BC4 → #FF8FBF")
    layout_changes: list[str]  # D63 sentences
    review_notes: list[str]  # e.g. comments kept in a rewritten block that may now be stale
    problem: str = ""  # set when the edited files didn't re-read as the patched trio: then patch == ""
    stepped_changes: list[str] = field(default_factory=list)  # D65 sentences (stepped timing)
    stepped_live_clause: str = STEPPED_SECTION_OFF  # the D65 section's live state, from the settings


class LocateError(ValueError):
    """The rewrite couldn't find a line it needed (the file's layout isn't the one this rewriter knows)."""


# ---- scalars and flow collections ----------------------------------------------------------------------------------
def _number(v: float) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return repr(v)


def _plain(s: str) -> str:
    """A key or enum word as a plain scalar when YAML reads it back as that same string, else a JSON string."""
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", s):
        try:
            if yaml.safe_load(s) == s:
                return s
        except yaml.YAMLError:
            pass
    return json.dumps(s, ensure_ascii=False)


def _scalar(v: Any, *, plain: bool = False) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return _number(v)
    if isinstance(v, str):
        return _plain(v) if plain else json.dumps(v, ensure_ascii=False)
    raise LocateError(f"can't write {v!r} as a scalar")


def _flow(v: Any, *, plain: bool = True) -> str:
    """A flow collection in the files' house style: `{k: v, …}`, `[a, b]`, plain words where they read back as
    themselves, numbers without a trailing `.0`."""
    if isinstance(v, Mapping):
        return "{" + ", ".join(f"{_plain(str(k))}: {_flow(x, plain=plain)}" for k, x in v.items()) + "}"
    if isinstance(v, list | tuple):
        return "[" + ", ".join(_flow(x, plain=plain) for x in v) + "]"
    return _scalar(v, plain=plain)


_FIELD_DEFAULTS = {
    "transform": "linear",
    "invert": False,
    "cycles": 1,
    "centres_in": None,
    "radius_in": "auto",
}


def field_flow(field: Mapping[str, Any]) -> str:
    """A design field as its one flow line: `kind` first, then only what isn't a default (a linear field always
    names its angle)."""
    out: dict[str, Any] = {"kind": field["kind"]}
    for key, value in field.items():
        if key == "kind" or value is None:
            continue
        if key == "angle_deg" or _FIELD_DEFAULTS.get(key, object()) != value:
            if key == "metric" and value == "inches":
                continue
            if key == "start_deg" and value == 0:
                continue
            out[key] = value
    return _flow(out)


def look_flow(look: Mapping[str, Any]) -> str:
    out: dict[str, Any] = {"kind": look["kind"]}
    for key in ("cct", "rgb", "preset", "program"):
        if look.get(key) is not None:
            out[key] = look[key]
    out["brightness"] = look.get("brightness", 100)
    if look.get("speed"):
        out["speed"] = look["speed"]
    return _flow(out)


def _entry_flow(entry: Mapping[str, Any]) -> str:
    out = {k: entry[k] for k in ("r", "g", "b")}
    out["colorBrightness"] = entry.get("colorBrightness", 100)
    for key in ("cct", "whiteBrightness"):
        if entry.get(key):
            out[key] = entry[key]
    return _flow(out)


def _strand_flow(strand: Mapping[str, Any]) -> str:
    return _flow({k: v for k, v in strand.items() if v is not None})


# ---- the line locator ----------------------------------------------------------------------------------------------
_KEY = re.compile(r"^(?P<indent> *)(?P<key>[A-Za-z_][\w-]*|\"[^\"]*\")\s*:(?:\s+(?P<rest>.*?))?\s*$")
_ITEM = re.compile(r"^(?P<indent> *)-\s+(?P<rest>.*?)\s*$")


def split_comment(line: str) -> tuple[str, str]:
    """(content, trailing comment with the whitespace before it). A `#` starts a comment only outside quotes and
    after whitespace (or at the start of the line)."""
    single = double = False
    i = 0
    while i < len(line):
        ch = line[i]
        if double:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                double = False
        elif single:
            if ch == "'":
                single = False
        elif ch == '"':
            double = True
        elif ch == "'":
            single = True
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            content = line[:i].rstrip()
            return content, line[len(content) :]
        i += 1
    return line.rstrip(), ""


def _indent(text: str) -> int:
    return len(text) - len(text.lstrip(" "))


def _is_comment_or_blank(line: str) -> bool:
    s = line.strip()
    return not s or s.startswith("#")


@dataclass
class _Line:
    index: int
    path: tuple[str, ...]  # keys from the block's root; a list item's path ends in "[]"
    indent: int
    key: str | None  # None for a list item
    rest: str  # the value text (no comment)
    comment: str


def _walk(lines: Sequence[str], start: int, end: int) -> Iterator[_Line]:
    """Every key or list-item line in lines[start:end] with its key path (comments and blanks skipped)."""
    stack: list[tuple[int, str]] = []
    for i in range(start, end):
        if _is_comment_or_blank(lines[i]):
            continue
        content, comment = split_comment(lines[i])
        indent = _indent(content)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if m := _KEY.match(content):
            key = m.group("key").strip('"')
            yield _Line(i, (*(k for _, k in stack), key), indent, key, m.group("rest") or "", comment)
            stack.append((indent, key))
        elif m := _ITEM.match(content):
            yield _Line(i, (*(k for _, k in stack), "[]"), indent, None, m.group("rest"), comment)
        else:
            raise LocateError(f"line {i + 1} isn't a key or a list item: {lines[i]!r}")


def _block_end(lines: Sequence[str], start: int, indent: int) -> int:
    """The first line after `start` (a key line at `indent`) that isn't inside its block."""
    end = start + 1
    while end < len(lines):
        if not _is_comment_or_blank(lines[end]) and _indent(lines[end]) <= indent:
            break
        end += 1
    # trailing blanks and comments at the block's own indent or less belong to what follows
    while (
        end > start + 1
        and _is_comment_or_blank(lines[end - 1])
        and (not lines[end - 1].strip() or _indent(lines[end - 1]) <= indent)
    ):
        end -= 1
    return end


def _get(tree: Any, path: Sequence[str]) -> Any:
    for key in path:
        if not isinstance(tree, Mapping) or key not in tree:
            raise KeyError("/".join(path))
        tree = tree[key]
    return tree


def _split_lines(text: str) -> tuple[list[str], bool]:
    return text.split("\n")[:-1] if text.endswith("\n") else text.split("\n"), text.endswith("\n")


def _join(lines: Sequence[str], newline: bool) -> str:
    return "\n".join(lines) + ("\n" if newline else "")


def _header(lines: Sequence[str]) -> list[tuple[int, str]]:
    out = []
    for i, line in enumerate(lines):
        if not _is_comment_or_blank(line):
            break
        out.append((i, line))
    return out


def header_notes(path: str, text: str, names: Mapping[str, Sequence[str]]) -> list[str]:
    """Each file-header comment line that names a rewritten scene, with its line number, for review."""
    lines, _ = _split_lines(text)
    out = []
    for i, line in _header(lines):
        lower = line.lower()
        for scene_name, words in names.items():
            if any(w and w.lower() in lower for w in words):
                out.append(
                    f"{path}:{i + 1} (file header) mentions {scene_name}; check it still holds: {line.strip()}"
                )
                break
    return out


# ---- config/scenes.yaml --------------------------------------------------------------------------------------------
def _palette_item(colour: Mapping[str, Any]) -> str:
    return f"{{name: {json.dumps(colour['name'], ensure_ascii=False)}, hex: {json.dumps(colour['hex'])}}}"


def _scene_block(lines: Sequence[str], scene_id: str) -> tuple[int, int, int]:
    pattern = re.compile(rf"^(?P<indent> *)-\s+id:\s*\"?{re.escape(scene_id)}\"?\s*(#.*)?$")
    for i, line in enumerate(lines):
        if m := pattern.match(line):
            indent = len(m.group("indent"))
            return i, _block_end(lines, i, indent), indent
    raise LocateError(f"couldn't find scene {scene_id!r} in {SCENES_FILE}")


def rewrite_scenes_yaml(
    text: str, patched: ConfigTrio, scene_ids: list[str], *, approved: ConfigTrio | None = None
) -> str:
    """scenes.yaml with each listed scene's changed lines rewritten in its `- id:` block. Without `approved`, the
    values are compared with what the text itself says."""
    lines, newline = _split_lines(text)
    if approved is None:
        approved = ConfigTrio(
            SceneCatalog.model_validate(yaml.safe_load(text)), patched.modes, patched.recipes
        )
    for scene_id in scene_ids:
        before = approved.scenes.get(scene_id).model_dump(mode="json")
        after = patched.scenes.get(scene_id).model_dump(mode="json")
        if before == after:
            continue
        start, end, _ = _scene_block(lines, scene_id)
        edits: dict[int, list[str]] = {}
        seen: set[str] = {"id"}
        palette_key: _Line | None = None
        items: list[_Line] = []
        for ln in _walk(lines, start + 1, end):
            if ln.path == ("palette",):
                palette_key = ln
                continue
            if ln.path == ("palette", "[]"):
                items.append(ln)
                continue
            if ln.key is None or len(ln.path) != 1:
                raise LocateError(f"{SCENES_FILE}:{ln.index + 1}: unexpected line in scene {scene_id!r}")
            seen.add(ln.key)
            if ln.key not in after:
                raise LocateError(f"{SCENES_FILE}:{ln.index + 1}: unknown scene field {ln.key!r}")
            if before.get(ln.key) != after[ln.key]:
                edits[ln.index] = [f"{' ' * ln.indent}{ln.key}: {_scalar(after[ln.key])}{ln.comment}"]
        missing = [k for k in after if k not in seen and k != "palette" and before.get(k) != after[k]]
        if missing:
            raise LocateError(f"scene {scene_id!r} has no line for {', '.join(missing)} to rewrite")
        if before["palette"] != after["palette"]:
            if palette_key is None or not items:
                raise LocateError(f"scene {scene_id!r} has no palette list to rewrite")
            if palette_key.rest:
                raise LocateError(
                    f"scene {scene_id!r}'s palette is a flow list; this rewriter needs a block list"
                )
            indent = items[0].indent
            new = []
            for n, colour in enumerate(after["palette"]):
                comment = items[n].comment if n < len(items) and n < len(before["palette"]) else ""
                if n < len(before["palette"]) and before["palette"][n] == colour and n < len(items):
                    new.append(lines[items[n].index])
                else:
                    new.append(f"{' ' * indent}- {_palette_item(colour)}{comment}")
            edits[items[0].index] = new
            for it in items[1:]:
                edits[it.index] = []
        lines = _apply_edits(lines, edits)
    return _join(lines, newline)


def _apply_edits(lines: Sequence[str], edits: Mapping[int, list[str]]) -> list[str]:
    out: list[str] = []
    for i, line in enumerate(lines):
        if i in edits:
            out.extend(edits[i])
        else:
            out.append(line)
    return out


# ---- config/modes.yaml ---------------------------------------------------------------------------------------------
def _mode_block(lines: Sequence[str], mode_id: str) -> tuple[int, int]:
    in_modes = False
    pattern = re.compile(rf"^  \"?{re.escape(mode_id)}\"?\s*:\s*(#.*)?$")
    for i, line in enumerate(lines):
        if re.match(r"^modes\s*:", line):
            in_modes = True
            continue
        if in_modes and line and not line.startswith(" ") and not line.startswith("#"):
            in_modes = False
        if in_modes and pattern.match(line):
            return i, _block_end(lines, i, 2)
    raise LocateError(f"couldn't find mode {mode_id!r} in {MODES_FILE}")


def _design_paths(mode: Mode) -> list[tuple[str, ...]]:
    paths = []
    if mode.techniques.canvas is not None:
        paths.append(("techniques", "canvas", "canvas"))
    if mode.techniques.modules is not None and mode.techniques.modules.sample is not None:
        paths.append(("techniques", "modules", "sample", "canvas"))
    if mode.techniques.stepped is not None:
        paths.append(("techniques", "stepped", "canvas"))
    return paths


def _design_words(mode: Mode, canopy: Mapping[str, Any] | None) -> str:
    design = mode.techniques.modules.sample.canvas if mode.techniques.modules else None
    if isinstance(design, FieldCanvas) and canopy is not None:
        return F.layout_words(design.field, canopy)
    return "the scene's design"


def _flow_sub(rest: str, key: str, value: str) -> str:
    pattern = re.compile(rf"(\b{re.escape(key)}:\s*)([^,}}]+?)(\s*[,}}])")
    if not pattern.search(rest):
        raise LocateError(f"no {key!r} in the flow mapping {rest!r}")
    return pattern.sub(lambda m: m.group(1) + value + m.group(3), rest, count=1)


def _nine_pace(
    trio: ConfigTrio, scene_id: str, canopy: Mapping[str, Any] | None, settings: ConductorSettings
) -> dict:
    """The nine-strand pace of the scene's stepped run: `canopy_pace` (the ripple's rings and max_step_s counted, as
    the D65 line has it); without a canopy, `run_pace` with no ripple."""
    if canopy is not None:
        run = ST.canopy_pace(trio, scene_id, dict(canopy), settings)
        if run is not None:
            return run
    tech = trio.modes.mode(scene_id).techniques.stepped
    assert tech is not None
    return ST.run_pace(tech.step_s, tech.run_s, len(D.GRID), settings)


def _comment_clause(run: dict, run_s: float, settings: ConductorSettings) -> str:
    """ "step every 7.5 s", or "hold the still design (<why, short>)" when the runner wouldn't step (the D65 line
    gives the whole reason)."""
    if run["still"] is None:
        return STEP_COMMENT_STEP.format(step=run["step_s"])
    step = run["step_s"]
    if step > settings.motion.stepped.max_step_s:
        why = f"{step:g} s steps, past motion.stepped.max_step_s"
    else:
        why = f"a {run_s:g} s run is under two {step:g} s steps"
    return STEP_COMMENT_STILL.format(why=why)


def rewrite_modes_yaml(
    text: str,
    approved: ConfigTrio,
    patched: ConfigTrio,
    scene_ids: list[str],
    *,
    canopy: Mapping[str, Any] | None = None,
    version_name: str = "",
    settings: ConductorSettings | None = None,
) -> tuple[str, list[str]]:
    """modes.yaml with each listed scene's mode block rewritten line by line; returns (text, review notes). When an
    edit moves the nine-strand pace (`stepped.canopy_pace` at `settings`' budget and ripple; the shipped defaults
    without), the `step_s` comment that gives it says the new pace, or that the still design holds."""
    pace_settings = settings if settings is not None else ConductorSettings()
    lines, newline = _split_lines(text)
    notes: list[str] = []
    for scene_id in scene_ids:
        a_mode, p_mode = approved.modes.mode(scene_id), patched.modes.mode(scene_id)
        before, after = a_mode.model_dump(mode="json"), p_mode.model_dump(mode="json")
        if before == after:
            continue
        a_scene, p_scene = approved.scenes.get(scene_id), patched.scenes.get(scene_id)
        start, end = _mode_block(lines, scene_id)
        design_paths = _design_paths(p_mode)
        design = ("techniques", "modules", "sample", "canvas")
        field_changed = _get(before, (*design, "field")) != _get(after, (*design, "field"))
        stops_before, stops_after = _get(before, (*design, "stops")), _get(after, (*design, "stops"))
        stops_moved = [s["at"] for s in stops_before] != [s["at"] for s in stops_after]
        palette_changed = [c.model_dump() for c in a_scene.palette] != [
            c.model_dump() for c in p_scene.palette
        ]
        direction_changed = (
            a_mode.techniques.stepped is not None and p_mode.techniques.stepped is not None
        ) and (a_mode.techniques.stepped.direction != p_mode.techniques.stepped.direction)
        solid_changed = a_scene.solid.model_dump() != p_scene.solid.model_dump()
        # the step_s comment's pace clause, when the timing or the nine-strand pace moved (the D65 line's own
        # `canopy_pace`, at these settings)
        pace_moved = ""
        a_st, p_st = a_mode.techniques.stepped, p_mode.techniques.stepped
        if a_st is not None and p_st is not None:
            pace_after = _nine_pace(patched, scene_id, canopy, pace_settings)
            timing = (a_st.step_s, a_st.run_s) != (p_st.step_s, p_st.run_s)
            if timing or pace_after != _nine_pace(approved, scene_id, canopy, pace_settings):
                pace_moved = _comment_clause(pace_after, p_st.run_s, pace_settings)
        edits: dict[int, list[str]] = {}
        walked = list(_walk(lines, start + 1, end))
        anchors: list[_Line] = []
        handled_stops: set[tuple[str, ...]] = set()
        for ln in walked:
            path = ln.path
            if ln.key is None:
                if path[-2:] == ("stops", "[]") and path[:-1] in handled_stops:
                    continue
                if path[-2:] == ("stops", "[]"):
                    raise LocateError(f"{MODES_FILE}:{ln.index + 1}: a stop outside a design")
                old_items = _get(before, path[:-1])
                new_items = _get(after, path[:-1])
                if old_items != new_items:
                    raise LocateError(
                        f"{MODES_FILE}:{ln.index + 1}: can't rewrite the list at {'/'.join(path)}"
                    )
                continue
            rest = ln.rest
            if path in design_paths:
                if rest.startswith("*"):
                    continue  # an alias: the shared design, untouched
                if rest and not rest.startswith("&"):
                    raise LocateError(f"{MODES_FILE}:{ln.index + 1}: the design isn't a block mapping")
                anchors.append(ln)
                continue
            if path[-1] == "stops" and path[:-1] in design_paths:
                handled_stops.add(path)
                items = [
                    x for x in walked if x.key is None and x.path == (*path, "[]") and x.index > ln.index
                ]
                old, new = _get(before, path), _get(after, path)
                if old != new:
                    if not items:
                        raise LocateError(f"{MODES_FILE}:{ln.index + 1}: the stops aren't a block list")
                    keep_comments = not stops_moved
                    indent = items[0].indent
                    new_lines = []
                    for n, stop in enumerate(new):
                        if n < len(old) and old[n] == stop and n < len(items) and keep_comments:
                            new_lines.append(lines[items[n].index])
                            continue
                        comment = items[n].comment if keep_comments and n < len(items) else ""
                        new_lines.append(
                            f"{' ' * indent}- {{at: {float(stop['at'])!r}, color: {_plain(stop['color'])}}}"
                            f"{comment}"
                        )
                    edits[items[0].index] = new_lines
                    for it in items[1:]:
                        edits[it.index] = []
                continue
            try:
                old, new = _get(before, path), _get(after, path)
            except KeyError:
                raise LocateError(
                    f"{MODES_FILE}:{ln.index + 1}: no {'/'.join(path)} in mode {scene_id!r}"
                ) from None
            comment = ln.comment
            if path == ("techniques", "stepped", "step_s") and pace_moved and _STEP_COMMENT.search(comment):
                # "nine strands on one account step every 7.5 s" was the runner's pace for the OLD timing
                comment = _STEP_COMMENT.sub(lambda _m, clause=pace_moved: clause, comment, count=1)
            stepped_comment = path in (("techniques", "stepped"), ("techniques", "stepped", "direction"))
            if stepped_comment and (field_changed or direction_changed):
                comment = ""  # "rays move out from the left edge" no longer describes the run
            if path == ("techniques", "strand", "look"):
                if old != new or solid_changed:
                    comment = f"   # {p_scene.solid.name.lower()}" if ln.comment or solid_changed else ""
                    edits[ln.index] = [f"{' ' * ln.indent}{ln.key}: {look_flow(new)}{comment}"]
                continue
            if not rest:
                if comment != ln.comment:
                    edits[ln.index] = [f"{' ' * ln.indent}{ln.key}:{comment}"]
                continue  # a mapping header; its children have their own lines
            if old == new:
                if comment != ln.comment:
                    edits[ln.index] = [f"{' ' * ln.indent}{ln.key}: {rest}{comment}"]
                continue
            if rest.startswith("{") and "*" in rest:
                # a flow mapping holding an alias (`sample: {canvas: *x, at: centre, brightness: 95}`)
                value = rest
                for key in new:
                    if key == "canvas" or old.get(key) == new[key]:
                        continue
                    value = _flow_sub(value, key, _scalar(new[key], plain=True))
                edits[ln.index] = [f"{' ' * ln.indent}{ln.key}: {value}{comment}"]
                continue
            if path[-1] == "field":
                value = field_flow(new)
            elif isinstance(new, Mapping | list):
                value = _flow(new) if path[-1] != "preview" else _flow(new, plain=False)
            else:
                plain = path[-1] in ("at", "direction", "kind", "recipe", "slot")
                value = _scalar(new, plain=plain)
            edits[ln.index] = [f"{' ' * ln.indent}{ln.key}: {value}{comment}"]
        # the design's anchor line and the comments inside it
        if field_changed or palette_changed:
            words = _design_words(p_mode, canopy)
            name = " ".join(version_name.split())  # one line, whatever the version is called
            label = f" (Design Lab: {name})" if name else " (Design Lab)"
            for anchor in anchors:
                head = f"{' ' * anchor.indent}{anchor.key}:{(' ' + anchor.rest) if anchor.rest else ''}"
                edits[anchor.index] = [f"{head}   # {words}{label}"]
                if field_changed:
                    stop = _block_end(lines, anchor.index, anchor.indent)
                    for i in range(anchor.index + 1, stop):
                        if lines[i].strip().startswith("#"):
                            edits[i] = []
        if not anchors and design_paths:
            raise LocateError(f"couldn't find mode {scene_id!r}'s design in {MODES_FILE}")
        lines = _apply_edits(lines, edits)
    return _join(lines, newline), notes


# ---- config/recipes.yaml -------------------------------------------------------------------------------------------
_RECIPE_KEY = re.compile(r"^  (?P<key>[^\s#][^:#]*?|\"[^\"]*\")\s*:\s*(#.*)?$")


def _owned_keys(trio: ConfigTrio) -> dict[str, list[str]]:
    """Scene id → the recipe keys its design names, in stop order."""
    out: dict[str, list[str]] = {}
    for mode_id, mode in trio.modes.modes.items():
        mods = mode.techniques.modules
        if mods is not None and mods.sample is not None and hasattr(mods.sample.canvas, "stops"):
            out[mode_id] = [s.color for s in mods.sample.canvas.stops]
    return out


def _recipe_block(key: str, recipe: Mapping[str, Any]) -> list[str]:
    return [
        f"  {_plain(key)}:",
        f"    label: {json.dumps(recipe['label'], ensure_ascii=False)}",
        f"    hex: {json.dumps(recipe['hex'])}",
        f"    rgb_only: {_entry_flow(recipe['rgb_only'])}",
        f"    blend: {_entry_flow(recipe['blend'])}",
        f"    strand: {_strand_flow(recipe['strand'])}",
    ]


def rewrite_recipes_yaml(text: str, approved: ConfigTrio, patched: ConfigTrio) -> str:
    """recipes.yaml with changed key blocks rewritten line by line, new keys inserted after the last key their
    scene owned, and keys no longer present deleted."""
    lines, newline = _split_lines(text)
    before = approved.recipes.model_dump(mode="json")["colors"]
    after = patched.recipes.model_dump(mode="json")["colors"]
    if before == after:
        return text
    blocks: dict[str, tuple[int, int]] = {}
    in_colors = False
    for i, line in enumerate(lines):
        if re.match(r"^colors\s*:", line):
            in_colors = True
            continue
        if in_colors and line and not line.startswith(" ") and not line.startswith("#"):
            in_colors = False
        if in_colors and (m := _RECIPE_KEY.match(line)):
            key = m.group("key").strip().strip('"')
            blocks[key] = (i, _block_end(lines, i, 2))
    missing = [k for k in before if k not in blocks]
    if missing:
        raise LocateError(f"couldn't find recipe(s) {', '.join(missing)} in {RECIPES_FILE}")
    edits: dict[int, list[str]] = {}
    for key, (start, end) in blocks.items():
        if key not in after:
            for i in range(start, end):
                edits[i] = []
            continue
        if before.get(key) == after[key]:
            continue
        seen = set()
        for ln in _walk(lines, start + 1, end):
            if ln.key is None or len(ln.path) != 1:
                raise LocateError(f"{RECIPES_FILE}:{ln.index + 1}: unexpected line in recipe {key!r}")
            name = ln.key
            seen.add(name)
            old, new = before[key].get(name), after[key].get(name)
            if old == new:
                continue
            if name in ("label", "hex"):
                value = json.dumps(new, ensure_ascii=False)
            elif name in ("rgb_only", "blend"):
                value = _entry_flow(new)
            elif name == "strand":
                value = _strand_flow(new)
            else:
                raise LocateError(f"{RECIPES_FILE}:{ln.index + 1}: unknown recipe field {name!r}")
            edits[ln.index] = [f"{' ' * ln.indent}{name}: {value}{ln.comment}"]
        if seen != set(after[key]):
            raise LocateError(f"recipe {key!r} in {RECIPES_FILE} doesn't list every field")
    # new keys: after the last key (in the file) that their scene owned
    new_keys = [k for k in after if k not in before]
    if new_keys:
        owners_after = _owned_keys(patched)
        owners_before = _owned_keys(approved)
        inserts: dict[int, list[str]] = {}
        for key in new_keys:
            scene = next((s for s, keys in owners_after.items() if key in keys), None)
            owned = [k for k in owners_before.get(scene or "", []) if k in blocks]
            if not owned:
                raise LocateError(f"recipe {key!r}: no key of its scene to put it after in {RECIPES_FILE}")
            last_end = max(blocks[k][1] for k in owned)
            inserts.setdefault(last_end - 1, []).extend(_recipe_block(key, after[key]))
        for at, block in inserts.items():
            edits[at] = [*edits.get(at, [lines[at]]), *block]
    return _join(_apply_edits(lines, edits), newline)


# ---- tests/fixtures_scene_frames.json ------------------------------------------------------------------------------
def frames_fixture(scenes: SceneCatalog, pins: dict) -> str:
    """json.dumps({**{s.id: [list(c) for c in s.frame(30)] for s in scenes}, "_pins": pins}, indent=0), no trailing
    newline."""
    frames: dict[str, Any] = {s.id: [list(c) for c in s.frame(30)] for s in scenes.scenes}
    return json.dumps({**frames, "_pins": pins}, indent=0)


def fixture_for(trio: ConfigTrio, canopy: Mapping[str, Any]) -> str:
    return frames_fixture(trio.scenes, scene_pins(trio.scenes, trio.modes, trio.recipes, canopy))


# ---- re-read ---------------------------------------------------------------------------------------------------------
def reread(texts: Mapping[str, str], canopy: dict) -> ConfigTrio:
    """The edited texts through the real loaders (SceneCatalog / RecipesFile / parse_modes)."""
    scenes = SceneCatalog.model_validate(yaml.safe_load(texts[SCENES_FILE]) or {})
    recipes = RecipesFile.model_validate(yaml.safe_load(texts[RECIPES_FILE]) or {})
    modes = parse_modes(
        yaml.safe_load(texts[MODES_FILE]) or {}, recipes=recipes, canopy=canopy, path=MODES_FILE
    )
    return ConfigTrio(scenes=scenes, modes=modes, recipes=recipes)


def _same(a: ConfigTrio, b: ConfigTrio) -> list[str]:
    """Which of the three files differ (model_dump equality, plus the modes' order)."""
    wrong = []
    if a.scenes.model_dump(mode="json") != b.scenes.model_dump(mode="json"):
        wrong.append(SCENES_FILE)
    if a.modes.model_dump(mode="json") != b.modes.model_dump(mode="json") or list(a.modes.modes) != list(
        b.modes.modes
    ):
        wrong.append(MODES_FILE)
    if a.recipes.model_dump(mode="json") != b.recipes.model_dump(mode="json"):
        wrong.append(RECIPES_FILE)
    return wrong


# ---- the diff ------------------------------------------------------------------------------------------------------
def unified_diff(path: str, before: str, after: str) -> str:
    """One file's git-apply-able unified diff ("" when unchanged), `\\ No newline at end of file` handled."""
    if before == after:
        return ""
    a, b = before.splitlines(keepends=True), after.splitlines(keepends=True)
    out = [f"diff --git a/{path} b/{path}\n", f"--- a/{path}\n", f"+++ b/{path}\n"]
    # difflib.unified_diff's matcher treats lines repeated across the file (every mode's `strand:`) as junk and
    # shows them as changed; without autojunk the hunks hold only the lines that really changed.
    for group in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(3):
        (_, i1, _, j1, _), (_, _, i2, _, j2) = group[0], group[-1]
        out.append(f"@@ -{_range(i1, i2)} +{_range(j1, j2)} @@\n")
        for tag, a1, a2, b1, b2 in group:
            if tag == "equal":
                out.extend(" " + line for line in a[a1:a2])
                continue
            out.extend("-" + line for line in a[a1:a2])
            out.extend("+" + line for line in b[b1:b2])
    return "".join(line if line.endswith("\n") else line + "\n" + NO_NEWLINE for line in out)


def _range(start: int, stop: int) -> str:
    """A unified diff's hunk range (difflib's own rule): "line" for one line, "line,count" otherwise."""
    length = stop - start
    if length == 1:
        return f"{start + 1}"
    return f"{start if not length else start + 1},{length}"


# ---- what changed, in words ----------------------------------------------------------------------------------------
def _num(v: float) -> str:
    return _number(v)


def describe_field(field: Any) -> str:
    if isinstance(field, F.LinearField):
        text = f"linear at {_num(field.angle_deg)}°"
    elif isinstance(field, F.RadialField):
        centres = " / ".join(f"({_num(x)}, {_num(y)})" for x, y in field.centres)
        text = f"radial from {centres}" + (" in the canopy's proportions" if field.metric == "canopy" else "")
        if field.radius_in != "auto":
            text += f", radius {_num(field.radius_in)} in"
    elif isinstance(field, F.SweepField):
        text = f"sweep around ({_num(field.centre_in[0])}, {_num(field.centre_in[1])}) from {_num(field.start_deg)}°"
    elif isinstance(field, F.FinsField):
        text = "fin rings (the drawing's drops, deep centre first)"
    else:
        text = str(field)
    if field.transform == "mirror":
        text += ", mirrored"
    if field.invert:
        text += ", inverted"
    if field.cycles != 1:
        text += f", {field.cycles} cycles"
    return text


def _colour(c: Any) -> str:
    return f"{c.name} {c.hex}"


@dataclass
class _Changes:
    approved: list[
        str
    ]  # D59: exactly what sets draft_changes' needs_d59 (a scene's name, its palette colours)
    layout: list[str]  # D63: the 2-D design and its module sample
    brightness: list[str]
    copy: list[str]  # the rung sentences the Lab regenerated
    notes: list[str]
    slot_repaint: list[str]
    solid: list[str] = field(default_factory=list)  # the strand's solid (a draft default, not a D59 colour)
    stepped: list[str] = field(default_factory=list)  # D65: the stepped run's timing
    client_copy: list[str] = field(default_factory=list)  # the client's own words (mood, description)


def _studio_notes(a: Any, p: Any) -> list[str]:
    """What demo-model.mjs SCENES (the Studio's hand-kept copy of the catalog) still holds after this export."""
    notes = []
    changed_hexes = [
        c.hex for i, c in enumerate(a.palette) if i >= len(p.palette) or p.palette[i].hex != c.hex
    ]
    if changed_hexes:
        notes.append(STUDIO_SCENES.format(hexes=", ".join(changed_hexes), scene=a.name))
    elif [(c.name, c.hex.upper()) for c in a.palette] != [(c.name, c.hex.upper()) for c in p.palette]:
        notes.append(
            STUDIO_SCENES_PALETTE.format(
                scene=a.name, n=len(a.palette), palette=", ".join(_colour(c) for c in a.palette)
            )
        )
    if (a.name, a.solid.name, a.solid.hex, a.brightness_draft) != (
        p.name,
        p.solid.name,
        p.solid.hex,
        p.brightness_draft,
    ):
        notes.append(
            STUDIO_SCENES_OPENS.format(
                scene=p.name, name=a.name, solid=_colour(a.solid), brightness=a.brightness_draft
            )
        )
    return notes


def _stepped_pace(
    patched: ConfigTrio, scene_id: str, canopy: Mapping[str, Any], settings: ConductorSettings
) -> str:
    """The D65 pace of an edited stepped run on the full canopy (the runner's rule, `stepped.effective_step_s`)."""
    run = ST.canopy_pace(patched, scene_id, dict(canopy), settings)
    assert run is not None
    if run["still"] is not None:
        return STEPPED_STILL.format(why=run["still"])
    return STEPPED_PACE.format(nine=run["step_s"], hold_at=run["hold_at"])


def _changes(
    approved: ConfigTrio,
    patched: ConfigTrio,
    scene_ids: list[str],
    canopy: Mapping[str, Any],
    settings: ConductorSettings | None = None,
) -> _Changes:
    ch = _Changes([], [], [], [], [], [])
    for scene_id in scene_ids:
        a, p = approved.scenes.get(scene_id), patched.scenes.get(scene_id)
        am, pm = approved.modes.mode(scene_id), patched.modes.mode(scene_id)
        name = a.name
        if a.name != p.name:
            ch.approved.append(f"{name}: name → {p.name}")
        for i in range(max(len(a.palette), len(p.palette))):
            old = a.palette[i] if i < len(a.palette) else None
            new = p.palette[i] if i < len(p.palette) else None
            if old is not None and new is not None and old.model_dump() == new.model_dump():
                continue
            if old is None:
                ch.approved.append(f"{name}: colour {i + 1} added, {_colour(new)}")
            elif new is None:
                ch.approved.append(f"{name}: colour {i + 1} removed, {_colour(old)}")
            elif old.name == new.name:
                ch.approved.append(f"{name}: {old.name} {old.hex} → {new.hex}")
            else:
                ch.approved.append(f"{name}: colour {i + 1} {_colour(old)} → {_colour(new)}")
        if a.default_solid != p.default_solid or a.solid.name != p.solid.name:
            ch.solid.append(f"{name}: solid colour {_colour(a.solid)} → {_colour(p.solid)}")
        if a.mood != p.mood:
            ch.client_copy.append(f'{name}: mood "{a.mood}" → "{p.mood}"')
        if a.description != p.description:
            ch.client_copy.append(f'{name}: description "{a.description}" → "{p.description}"')
        try:
            ch.notes.extend(
                f"{name}: {note}"
                for note in D.description_notes(
                    D.seed_draft(approved, scene_id), D.seed_draft(patched, scene_id)
                )
            )
        except D.DraftError:  # a scene with no 2-D design has no draft (the validator says why)
            pass
        a_design = am.techniques.modules.sample if am.techniques.modules else None
        p_design = pm.techniques.modules.sample if pm.techniques.modules else None
        if a_design is not None and p_design is not None:
            af, pf = a_design.canvas.field, p_design.canvas.field  # type: ignore[union-attr]
            if af != pf:
                ch.layout.append(
                    f"{name}: layout {describe_field(af)} ({F.layout_words(af, canopy)!r}) → "
                    f"{describe_field(pf)} ({F.layout_words(pf, canopy)!r})"
                )
            if a_design.at != p_design.at:
                ch.layout.append(f"{name}: module level samples at {a_design.at} → {p_design.at}")
        if a.brightness_draft != p.brightness_draft:
            ch.brightness.append(f"{name} {a.brightness_draft} → {p.brightness_draft}")
        for rung, getter in (
            ("module-level", lambda m: m.techniques.modules.feature if m.techniques.modules else None),
            ("moving", lambda m: m.techniques.stepped.moving_feature if m.techniques.stepped else None),
            ("one-colour", lambda m: m.techniques.strand.feature if m.techniques.strand else None),
        ):
            before, after = getter(am), getter(pm)
            if before != after:
                ch.copy.append(f'{name}: {rung} copy now reads "{after}"')
        a_st, p_st = am.techniques.stepped, pm.techniques.stepped
        if a_st is not None and p_st is not None:
            fields = ("direction", "steps_per_cycle", "step_s", "run_s")
            moved = [
                f"{f} {getattr(a_st, f)} → {getattr(p_st, f)}"
                for f in fields
                if getattr(a_st, f) != getattr(p_st, f)
            ]
            if moved:
                line = f"{name}: stepped run {', '.join(moved)}"
                timing = fields[1:]  # the pace changes with these, never with the direction
                if any(getattr(a_st, f) != getattr(p_st, f) for f in timing):
                    pace = _stepped_pace(patched, scene_id, canopy, settings or ConductorSettings())
                    line += f" ({pace})"
                ch.stepped.append(line)
        # a painted scene's T1 design: its canvas and the colours its stops name
        if am.slot is not None and am.techniques.canvas is not None and pm.techniques.canvas is not None:
            a_canvas, p_canvas = am.techniques.canvas.canvas, pm.techniques.canvas.canvas
            if _painted(approved, a_canvas) != _painted(patched, p_canvas):
                ch.slot_repaint.append(f"{name}: " + SLOT_REPAINT.format(slot=am.slot))
        ch.notes.extend(_studio_notes(a, p))
    return ch


def _stop_hexes(trio: ConfigTrio, canvas: Any) -> list[str | None]:
    """The hexes a canvas's stops name (what a painted slot holds, with the layout)."""
    colours = trio.recipes.colors
    return [
        colours[s.color].hex.upper() if s.color in colours else None for s in getattr(canvas, "stops", [])
    ]


def _painted(trio: ConfigTrio, canvas: Any) -> dict:
    """What a painted slot holds: the canvas with each stop's colour key resolved to its hex. A rename changes only
    the keys, so the frames, the slot hash and the slot stay as they are (draft_changes' `needs_slot_repaint` agrees)."""
    dumped = canvas.model_dump(mode="json")
    if "stops" in dumped:
        dumped["stops"] = [
            {**stop, "color": hex_} for stop, hex_ in zip(dumped["stops"], _stop_hexes(trio, canvas))
        ]
    return dumped


def _design_of(trio: ConfigTrio, scene_id: str) -> tuple[list[tuple[str, str]], dict] | None:
    """A scene's palette [(name, HEX)] and its module-level design's field (a FieldCanvas), or None without one."""
    try:
        scene, mode = trio.scenes.get(scene_id), trio.modes.mode(scene_id)
    except KeyError:
        return None
    modules = mode.techniques.modules
    canvas = modules.sample.canvas if modules is not None and modules.sample is not None else None
    if not isinstance(canvas, FieldCanvas):
        return None
    return [(c.name, c.hex.upper()) for c in scene.palette], canvas.field.model_dump(mode="json")


def promotions(
    patched: ConfigTrio, scene_ids: Iterable[str], *, approved: ConfigTrio | None = None
) -> list[tuple[str, str]]:
    """(base id, option id) for each base scene in `scene_ids` that now carries one of its options' designs: the
    first option (catalog order) whose palette [(name, hex)] and module-level field both equal the base's — what the
    Lab's "Make this the scene" leaves (D76). Inferred, never recorded: a later tweak of the base drops the pair.
    With `approved`, a base that already had that design in git is no promotion (nothing was made the scene)."""
    out: list[tuple[str, str]] = []
    for base_id in scene_ids:
        try:
            base = patched.scenes.get(base_id)
        except KeyError:
            continue
        if base.option_of is not None:
            continue
        design = _design_of(patched, base_id)
        if design is None:
            continue
        if approved is not None and _design_of(approved, base_id) == design:
            continue
        for option in patched.scenes.options_of(base_id):
            if _design_of(patched, option.id) == design:
                out.append((base_id, option.id))
                break
    return out


def _name_of(approved: ConfigTrio, patched: ConfigTrio, scene_id: str) -> str:
    try:
        return approved.scenes.get(scene_id).name
    except KeyError:
        return patched.scenes.get(scene_id).name


def _next_decision(root: Path) -> str:
    path = root / "docs" / "DECISIONS.md"
    try:
        ids = [int(n) for n in re.findall(r"\*\*D(\d+)\b", path.read_text(encoding="utf-8"))]
    except OSError:
        return "D<next>"
    return f"D{max(ids) + 1}" if ids else "D<next>"


def decision_draft(
    approved: ConfigTrio,
    patched: ConfigTrio,
    scene_ids: list[str],
    changes: _Changes,
    *,
    root: Path,
    version: Version | None,
    now: float,
    live_clause: str = STEPPED_SECTION_OFF,
) -> str:
    names = ", ".join(approved.scenes.get(s).name for s in scene_ids) or "no scene"
    when = datetime.fromtimestamp(now, UTC).astimezone()  # the box's local date, as version names use
    label = f'Design Lab version "{version.name}"' if version is not None else "a Design Lab draft"
    parts = [f"**{_next_decision(root)} — {names}: {label} ({when:%Y-%m-%d}).**"]
    if changes.approved:
        parts.append(
            "Approved colour changes (D59; needs the client's sign-off and a D59 update): "
            + "; ".join(changes.approved)
            + "."
        )
    for base_id, option_id in promotions(patched, scene_ids, approved=approved):
        parts.append(
            PROMOTION_LINE.format(
                base=_name_of(approved, patched, base_id),
                option=_name_of(approved, patched, option_id),
                option_id=option_id,
                base_id=base_id,
            )
        )
    if changes.solid:
        parts.append("Strand solid (a draft default, not a D59 colour): " + "; ".join(changes.solid) + ".")
    if changes.layout:
        parts.append("Layout changes (D63; Paris reviews): " + "; ".join(changes.layout) + ".")
    if changes.stepped:
        parts.append(f"Stepped timing (D65; {live_clause}): " + "; ".join(changes.stepped) + ".")
    if changes.brightness:
        parts.append("Brightness (drafts): " + "; ".join(changes.brightness) + ".")
    if changes.client_copy:
        parts.append(
            "Client copy edited (check it is true at every tier): " + "; ".join(changes.client_copy) + "."
        )
    if changes.copy:
        parts.append("Copy regenerated: " + "; ".join(changes.copy) + ".")
    parts.extend(changes.slot_repaint)
    parts.extend(changes.notes)
    parts.append("Exported from the Design Lab (D66); verified: re-read equal, suite to run in the PR.")
    return "\n".join(parts)


def changed_scenes(approved: ConfigTrio, patched: ConfigTrio) -> list[str]:
    """Scene ids whose scene or mode differs, in catalog order."""
    out = []
    for scene in approved.scenes.scenes:
        try:
            p_scene, p_mode = patched.scenes.get(scene.id), patched.modes.mode(scene.id)
        except KeyError:
            out.append(scene.id)
            continue
        if (
            scene.model_dump() != p_scene.model_dump()
            or approved.modes.mode(scene.id).model_dump() != p_mode.model_dump()
        ):
            out.append(scene.id)
    return out


# ---- the export ------------------------------------------------------------------------------------------------------
def export_patch(
    approved: ConfigTrio,
    patched: ConfigTrio,
    *,
    root: Path,
    canopy: dict,
    version: Version | None,
    now: float,
    settings: ConductorSettings | None = None,
) -> LabExport:
    """The four files rewritten for `patched`, as one unified diff against `root` (the repo: `config/` and, when
    present, `tests/`), re-read and proven equal to `patched` before it is offered. `settings` (the Conductor's)
    gives the stepped pace the D65 lines and the step_s comment state; the shipped defaults without."""
    root = Path(root)
    scene_ids = changed_scenes(approved, patched)
    live = bool((settings or ConductorSettings()).motion.stepped.live_enabled)
    live_clause = STEPPED_SECTION_ON if live else STEPPED_SECTION_OFF
    try:
        changes = _changes(approved, patched, scene_ids, canopy, settings)
    except (KeyError, ValueError) as e:  # a trio whose ids or ladders moved: never a patch
        return LabExport(
            "", [], scene_ids, "", [], [], [], f"the draft doesn't match the scenes ({_first_line(e)})"
        )
    review_notes = [*changes.slot_repaint, *changes.notes]

    def refused(problem: str) -> LabExport:
        return LabExport(
            "",
            [],
            scene_ids,
            "",
            changes.approved,
            changes.layout,
            review_notes,
            problem,
            changes.stepped,
            live_clause,
        )

    before: dict[str, str] = {}
    for path in EXPORT_FILES[:3]:
        try:
            before[path] = (root / path).read_text(encoding="utf-8")
        except OSError as e:
            return refused(f"{path} couldn't be read ({e.strerror or e})")
    before[FIXTURE_FILE] = fixture_for(approved, canopy)
    on_disk = root / FIXTURE_FILE
    if on_disk.exists() and on_disk.read_text(encoding="utf-8") != before[FIXTURE_FILE]:
        return refused(FIXTURE_STALE)
    try:
        wrong = _same(reread(before, canopy), approved)
    except Exception as e:  # noqa: BLE001 - any loader failure means no patch, said plainly
        return refused(f"config/ on disk doesn't load ({_first_line(e)})")
    if wrong:
        return refused(
            f"{', '.join(wrong)} on disk isn't the approved config the Lab loaded (changed since it started?); "
            "restart the Lab"
        )
    try:
        after = {
            SCENES_FILE: rewrite_scenes_yaml(before[SCENES_FILE], patched, scene_ids, approved=approved),
            MODES_FILE: rewrite_modes_yaml(
                before[MODES_FILE],
                approved,
                patched,
                scene_ids,
                canopy=canopy,
                version_name=version.name if version is not None else "",
                settings=settings,
            )[0],
            RECIPES_FILE: rewrite_recipes_yaml(before[RECIPES_FILE], approved, patched),
            FIXTURE_FILE: fixture_for(patched, canopy),
        }
    except (LocateError, KeyError, ValueError) as e:
        return refused(f"the edit couldn't be placed in the files ({_first_line(e)})")
    try:
        got = reread(after, canopy)
    except Exception as e:  # noqa: BLE001
        return refused(f"the edited files don't load ({_first_line(e)})")
    wrong = _same(got, patched)
    if wrong:
        return refused(f"the edited {', '.join(wrong)} re-read differently from the draft")
    others = [s.id for s in approved.scenes.scenes if s.id not in scene_ids]
    for scene_id in others:
        if (
            got.scenes.get(scene_id).model_dump() != approved.scenes.get(scene_id).model_dump()
            or got.modes.mode(scene_id).model_dump() != approved.modes.mode(scene_id).model_dump()
        ):
            return refused(f"the edit changed {scene_id}, which the draft doesn't")
    names = {approved.scenes.get(s).name: [approved.scenes.get(s).name, s] for s in scene_ids}
    for path in EXPORT_FILES[:3]:
        review_notes.extend(header_notes(path, before[path], names))
    files = [path for path in EXPORT_FILES if before[path] != after[path]]
    patch = "".join(unified_diff(path, before[path], after[path]) for path in EXPORT_FILES)
    decision = (
        decision_draft(
            approved,
            patched,
            scene_ids,
            changes,
            root=root,
            version=version,
            now=now,
            live_clause=live_clause,
        )
        if files
        else ""
    )
    return LabExport(
        patch=patch,
        files=files,
        scenes=scene_ids,
        decision=decision,
        approved_changes=changes.approved,
        layout_changes=changes.layout,
        review_notes=review_notes,
        stepped_changes=changes.stepped,
        stepped_live_clause=live_clause,
    )


def _first_line(e: BaseException) -> str:
    """The error in one line: a ConfigError's heading and its first bullet ("… has 2 problem(s): • mode …")."""
    text = [line.strip() for line in str(e).strip().splitlines() if line.strip()]
    return (" ".join(text[:2]) if text else type(e).__name__)[:300]


__all__ = [
    "EXPORT_FILES",
    "LabExport",
    "LocateError",
    "changed_scenes",
    "export_patch",
    "field_flow",
    "fixture_for",
    "frames_fixture",
    "promotions",
    "reread",
    "rewrite_modes_yaml",
    "rewrite_recipes_yaml",
    "rewrite_scenes_yaml",
    "split_comment",
    "unified_diff",
]
