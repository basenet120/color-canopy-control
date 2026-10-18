"""Publishing from the Design Lab to the live show (D68, docs/live-lab/PLAN.md §2.4–§2.9, §5.2 B1), on the virtual
clock over the shipped config/ (tests/live_lab.py's show).

- The hot-apply checklist: one publish refreshes every process-lifetime copy of the looks without a restart, one
  parametrized case per row of §2.4's table (so a removed line of `install_looks` fails its own case).
- No live reader falls back on the git defaults; the re-pick is one burst that keeps guest brightness; the T1 hold
  keeps a painted scene's copy true until its slots hold the design; refusals write and send nothing; a confirm
  computed against a replaced look is a conflict; Undo, Revert, the try-on, a rollback and a write failure.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from live_lab import (
    CALM,
    DEV,
    GIT,
    GOLDEN,
    NEW_PINK,
    PINKER,
    TIDES,
    ask,
    brighter,
    client_of,
    edited,
    install_no_threads,
    pinker_hour,
    publish,
    save,
    show,
)

from conductor import rows as R
from conductor.canopy import LOOKS_SOURCE, ONE_BURST_SOURCES, RECOMPILE_SOURCES
from conductor.clock import run_virtual
from conductor.config import CONFIG_DIR
from conductor.design_lab import draft as D
from conductor.design_lab import publish as PUB
from conductor.design_lab import trial as T
from conductor.engine_api import Tier
from conductor.looks import compile_mode
from conductor.looks import live as L

SLOT = "custom-3"
GOODNIGHT = "goodnight-glow"


@pytest.fixture
def no_threads(monkeypatch):
    install_no_threads(monkeypatch)


def spy_writes(w) -> list[tuple[float, str, list]]:
    writes: list[tuple[float, str, list]] = []
    driver = w.services.engine_driver
    original = driver.write

    async def write(address, rows, **kw):
        writes.append((w.clock.monotonic(), address, list(rows)))
        return await original(address, rows, **kw)

    driver.write = write
    return writes


def slot_writes(writes) -> list:
    return [x for x in writes if any(r.function_class == R.FRAME_CLASS for r in x[2])]


def look_writes(writes) -> list:
    return [x for x in writes if not any(r.function_class == R.FRAME_CLASS for r in x[2])]


def rows_of(plan) -> dict:
    return {d: list(mt.rows) for d, mt in plan.modules.items()}


def drafted(d: dict) -> D.SceneDraft:
    return D.SceneDraft.model_validate(d)


# ---- the checklist (§2.4): every copy refreshed by one publish -------------------------------------------------------
ROWS = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15", "16", "19"]


@pytest.fixture(scope="module")
def checklist() -> dict[str, tuple[bool, Any]]:
    with pytest.MonkeyPatch.context() as mp:
        install_no_threads(mp)
        return _checklist(mp)


def _checklist(mp) -> dict[str, tuple[bool, Any]]:
    import conductor.show as SHOW
    from conductor.alerts import AlertBoard
    from conductor.looks import compile as C
    from conductor.module.manager import ModuleManager
    from conductor.routes import kiosk as K
    from conductor.routes.design import mode_design

    checked = {"stepped": 0}
    real_check = SHOW._check_stepped

    def spy_check(settings, modes):
        checked["stepped"] += 1
        return real_check(settings, modes)

    mp.setattr(SHOW, "_check_stepped", spy_check)
    patterns: dict[str, dict] = {}
    real_pattern = K._pattern

    def spy_pattern(spec, tier=None):
        if tier is not None and isinstance(spec, dict) and spec.get("label"):
            patterns[spec["label"]] = spec
        return real_pattern(spec, tier)

    mp.setattr(K, "_pattern", spy_pattern)

    async def main():
        w = await show()
        services, engine, wiring, lab = w.services, w.engine, w.wiring, w.lab
        manager = ModuleManager.of(services)

        async def close() -> None:
            return None

        session = SimpleNamespace(
            deps=manager.deps(), lease_alive=lambda: True, close=close, abandon=lambda: None
        )
        manager.sessions["fake-open-session"] = session
        wiring.rehearsal_modes(GOLDEN)  # a cached rehearsal design of the old looks
        before_hash = engine.t1_target(DEV, SLOT)[1].slot_hash
        # a try-on of Golden Hour is up when the publish lands (row 7), so Golden Hour is showing (row 6)
        trial = T.build_trial(
            lab, drafted(brighter(GOLDEN)), version=None, now_mono=w.clock.monotonic(), now=w.clock.now()
        )
        st = await engine.start_trial(trial)
        assert not st.refused, st.message
        await engine.wait_idle()
        looks = w.looks
        generation = looks.generation
        checked["stepped"] = 0
        async with client_of(w) as c:
            assert (await c.get("/api/lab/catalog")).status_code == 200  # the Lab's live seeds, cached
            vid = await save(c, {GOLDEN: pinker_hour()})
            published = await publish(c, vid, who="Alon")
            assert published.status_code == 200, published.text
            await engine.wait_idle()
            live = looks.trio
            out: dict[str, tuple[bool, Any]] = {}
            shares = {id(x) for x in (lab.approved.scenes, lab.approved.modes, lab.approved.recipes)}
            out["0"] = (
                looks.generation == generation + 1
                and not ({id(live.scenes), id(live.modes), id(live.recipes)} & shares),
                looks.generation,
            )
            out["1"] = (wiring.modes_file is live.modes, None)
            out["2"] = (wiring.context().recipes is live.recipes, None)
            rehearsal = wiring.rehearsal_modes(GOLDEN)
            out["3"] = (
                rehearsal.modes[GOLDEN].label == PINKER
                and rehearsal.modes[GOLDEN].techniques.stepped.canvas.model_dump()
                == live.modes.modes[GOLDEN].techniques.stepped.canvas.model_dump(),
                rehearsal.modes[GOLDEN].label,
            )
            out["4"] = (checked["stepped"] >= 1, checked["stepped"])
            after_hash = engine.t1_target(DEV, SLOT)[1].slot_hash
            expected = compile_mode(GOLDEN, Tier.T1, wiring.context()).modules[DEV].slot_hash
            out["5"] = (after_hash != before_hash and after_hash == expected, (before_hash, after_hash))
            plan = engine.current_plan()
            git_ctx = wiring.context(GIT.modes, GIT.recipes)
            out["6"] = (
                plan.mode_id == GOLDEN
                and plan.label == PINKER
                and rows_of(plan) == rows_of(compile_mode(GOLDEN, plan.tier, wiring.context()))
                and rows_of(plan) != rows_of(compile_mode(GOLDEN, plan.tier, git_ctx)),
                (plan.label, plan.tier),
            )
            out["7"] = (engine.trial is None, engine.trial)
            out["8"] = (C._default_modes(str(CONFIG_DIR)).mode(GOLDEN).label == "Golden Hour", None)
            panel = (await c.get("/api/kiosk/panel")).json()
            golden = next(s for s in panel["scenes"] if s["id"] == GOLDEN)
            out["9"] = (golden["name"] == PINKER and golden["palette"][2]["hex"] == NEW_PINK, golden["name"])
            frame = [list(rgb) for rgb in live.scenes.get(GOLDEN).frame(30)]
            out["10"] = (golden["frame"] == frame, None)
            listing = (await c.get("/api/kiosk/modes")).json()
            golden_mode = next(m for m in listing if m["id"] == GOLDEN)
            out["11"] = (
                golden_mode["label"] == PINKER
                and golden_mode["module_colours"] is not None
                and PINKER in patterns
                and "Golden Hour" not in patterns,
                (golden_mode["label"], sorted(patterns)),
            )
            state = (await c.get("/api/canopy/state")).json()
            labels = {m["id"]: m["label"] for m in state["modes"]}
            out["12"] = (labels[GOLDEN] == PINKER, labels[GOLDEN])
            out["13"] = (AlertBoard.of(services)._labels(GOLDEN) == PINKER, None)
            design = (await c.get(f"/api/canopy/design?mode={GOLDEN}")).json()
            from conductor.looks import CompileContext

            git_design = mode_design(
                GOLDEN, CompileContext(registry=[], capabilities={}, config_dir=CONFIG_DIR)
            )
            live_design = mode_design(
                GOLDEN, CompileContext(registry=[], capabilities={}, modes=live.modes, recipes=live.recipes)
            )
            out["14"] = (
                design["heads"] == live_design["heads"] and design["heads"] != git_design["heads"],
                design["label"],
            )
            out["15"] = (
                manager.modes is live.modes
                and manager.recipes is live.recipes
                and session.deps.modes is live.modes
                and session.deps.recipes is live.recipes,
                None,
            )
            catalog = (await c.get("/api/lab/catalog")).json()
            scene = next(s for s in catalog["scenes"] if s["id"] == GOLDEN)
            out["16"] = (
                scene["live_look"]["name"] == PINKER
                and scene["live_source"] == "published"
                and scene["approved"]["name"] == "Golden Hour"
                and T.show_trio(lab).scenes.get(GOLDEN).name == PINKER
                and all(k[0] == looks.generation for k in lab._live_seeds),
                (scene["live_look"]["name"], scene["live_source"]),
            )
            out["19"] = (GOLDEN in engine.t1_hold, sorted(engine.t1_hold))
        await w.close()
        return out

    return run_virtual(main)


@pytest.mark.parametrize("row", ROWS)
def test_publish_refreshes_every_copy_without_a_restart(checklist, row):
    ok, detail = checklist[row]
    assert ok, f"§2.4 row {row}: {detail!r}"


def test_install_looks_installs_deep_copies_into_every_holder(tmp_path):
    """The checklist function itself, line by line, on stand-ins: each holder of a copy is told, the live trio is
    a deep copy (never the caller's objects), the generation moves and a fallback's problem clears."""
    calls: list[tuple] = []
    wiring = SimpleNamespace(install_looks=lambda modes, recipes: calls.append(("show", modes, recipes)))
    engine = SimpleNamespace(invalidate_t1_plans=lambda: calls.append(("engine",)))
    manager = SimpleNamespace(install_looks=lambda recipes, modes: calls.append(("module", recipes, modes)))
    lab = SimpleNamespace(live_changed=lambda: calls.append(("lab",)))
    records: list[dict] = []
    services = SimpleNamespace(
        config_dir=CONFIG_DIR,
        data=tmp_path,
        telemetry=SimpleNamespace(record=lambda kind, **k: records.append({"kind": kind, **k})),
        extras={"show": wiring, "canopy": engine, "module_manager": manager, "design_lab": lab},
    )
    looks = L.LiveLooks(
        git=GIT, trio=GIT, source="approved", file=L.ShowLooksFile(), path=None, problem="it was broken"
    )
    services.extras[L.LOOKS_KEY] = looks
    done = L.install_looks(services, GIT, why="a test")
    assert done == [
        "live looks",
        "show modes",
        "show recipes",
        "rehearsal designs",
        "T1 plans",
        "module page",
        "design lab",
    ]
    assert looks.generation == 1 and looks.problem is None
    live = looks.trio
    assert live.scenes is not GIT.scenes and live.modes is not GIT.modes and live.recipes is not GIT.recipes
    assert L.trio_sha(live) == L.trio_sha(GIT)
    assert calls == [
        ("show", live.modes, live.recipes),
        ("engine",),
        ("module", live.recipes, live.modes),
        ("lab",),
    ]
    assert (
        calls[0][1] is live.modes and calls[2][1] is live.recipes
    )  # the very objects the show compiles from
    assert [r["action"] for r in records if r["kind"] == "lab"] == ["looks_installed"]
    assert any(r.get("resolves") == [L.LOAD_KEY, L.GIT_KEY] for r in records if r["kind"] == "alert")


# ---- no live reader uses the git defaults ----------------------------------------------------------------------------
def test_no_live_reader_uses_the_git_defaults(no_threads, monkeypatch):
    from conductor import guest_panel, scenes
    from conductor.looks import compile as C
    from conductor.routes import kiosk as K

    async def main():
        w = await show()
        async with client_of(w) as c:
            vid = await save(c, {GOLDEN: pinker_hour()})
            assert (await publish(c, vid)).status_code == 200
            await w.engine.wait_repaint()  # B2: the slot repaint's quiet window refuses guests for a moment
            await w.engine.wait_idle()

            def boom(*_a, **_k):
                raise RuntimeError("a live reader used the git defaults")

            for target, name in (
                (C, "_default_modes"),
                (C, "_default_recipes"),
                (scenes, "load_scenes"),
                (K, "_read_yaml"),
            ):
                monkeypatch.setattr(target, name, boom)
            monkeypatch.setattr(guest_panel, "load_scenes", boom, raising=False)
            from conductor.module.manager import ModuleManager

            statuses = {}
            for path in (
                "/api/kiosk/panel",
                "/api/kiosk/modes",
                "/api/kiosk/state",
                "/api/canopy/state",
                f"/api/canopy/design?mode={GOLDEN}",
                "/api/ops/checklist",
                "/api/lab/live",
                "/api/lab/catalog",
            ):
                statuses[path] = (await c.get(path)).status_code
            selected = await c.post("/api/kiosk/select", json={"mode": TIDES})
            statuses["select"] = selected.status_code
            await w.engine.wait_idle()
            manager = ModuleManager.of(w.services)
            ctx_ok = manager.modes is w.looks.trio.modes
            await w.engine.request_mode(GOLDEN, source="ops")  # an engine re-pick
            await w.engine.wait_idle()
            trial = T.build_trial(
                w.lab, drafted(brighter(CALM)), version=None, now_mono=w.clock.monotonic(), now=w.clock.now()
            )
            st = await w.engine.start_trial(trial)
            await w.engine.wait_idle()
        out = statuses, ctx_ok, st.refused, w.engine.current_plan().mode_id
        await w.close()
        return out

    statuses, ctx_ok, refused, mode = run_virtual(main)
    assert statuses == dict.fromkeys(statuses, 200), statuses
    assert ctx_ok and not refused and mode == CALM


# ---- the re-pick ----------------------------------------------------------------------------------------------------
def test_looks_is_a_recompile_source_that_keeps_guest_brightness():
    assert LOOKS_SOURCE in RECOMPILE_SOURCES and LOOKS_SOURCE in ONE_BURST_SOURCES


@pytest.mark.parametrize("stagger", [0.0, 1.2])
def test_the_repick_is_one_burst_that_keeps_the_guests_brightness(no_threads, stagger):
    from conductor.config import load_settings

    base = load_settings(CONFIG_DIR).model_dump()
    base["kiosk"]["ripple_stagger_s"] = stagger
    settings = type(load_settings(CONFIG_DIR)).model_validate(base)

    async def main():
        w = await show(settings=settings)
        await w.engine.request_mode(GOODNIGHT, source="ops")  # a still look (a stepped one sends its steps)
        await w.engine.wait_idle()
        await w.engine.set_guest_brightness(40, source="ops")
        await w.engine.wait_idle()
        writes = spy_writes(w)
        async with client_of(w) as c:
            vid = await save(c, {GOODNIGHT: brighter(GOODNIGHT)})
            r = await publish(c, vid)
            await w.engine.wait_idle()
            await w.run_for(5)
            changed = list(writes)
            # the current scene unchanged (a version is every drafted scene over the approved looks, so it keeps
            # Goodnight Glow's published look): zero writes
            writes.clear()
            other = await save(c, {GOODNIGHT: brighter(GOODNIGHT), CALM: brighter(CALM)})
            r2 = await publish(c, other)
            await w.engine.wait_idle()
            await w.run_for(5)
            unchanged = list(writes)
        out = r.status_code, r.json(), changed, r2.status_code, r2.json(), unchanged, w.engine.state()
        await w.close()
        return out

    status, body, changed, status2, body2, unchanged, state = run_virtual(main)
    assert status == 200 and status2 == 200, (body, body2)
    addresses = [a for _t, a, _rows in look_writes(changed)]
    assert len(addresses) == 9 and len(set(addresses)) == 9  # one write per module
    assert max(t for t, _a, _r in changed) - min(t for t, _a, _r in changed) < 1.0  # one burst, no ripple
    assert slot_writes(changed) == []
    assert all(
        any(r.key == ("brightness", None) and r.value == 40 for r in rows)
        for _t, _a, rows in look_writes(changed)
    )
    assert body["report"]["repick"] is True and body["report"]["sent"] == 9
    assert unchanged == [] and body2["report"]["repick"] is False and body2["report"]["sent"] == 0
    assert state.guest_brightness == 40


def test_show_after_publishing_is_one_burst_of_that_scene(no_threads):
    async def main():
        w = await show()
        writes = spy_writes(w)
        async with client_of(w) as c:
            vid = await save(c, {TIDES: brighter(TIDES)})
            first = await ask(c, vid, show=True)
            r = await publish(c, vid, show=True)
            await w.engine.wait_idle()
        out = first.json(), r.status_code, r.json(), list(writes), w.engine.current_plan().mode_id
        await w.close()
        return out

    first, status, body, writes, mode = run_virtual(main)
    assert first["show_offer"] == TIDES and first["cost"].startswith("sends 9 writes now")
    assert status == 200 and mode == TIDES
    assert len(look_writes(writes)) == 9 and body["report"]["sent"] == 9


# ---- the T1 hold (sim: every module recorded) ------------------------------------------------------------------------
def test_a_painted_scene_is_held_at_module_level_until_its_slots_hold_the_design(no_threads):
    """Golden Hour at T1 (sim: nine records), a published layout change: the copy drops below T1 at once (the
    publish answers while the slots still hold the old design); B2's operator repaint writes custom-3 once per module,
    the hold lifts, and the showing Golden Hour goes back to T1 by itself with one look PUT per module."""

    async def main():
        w = await show()
        await w.engine.request_mode(GOLDEN, source="ops")
        await w.engine.wait_idle()
        start_tier = w.engine.current_plan().tier
        writes = spy_writes(w)
        async with client_of(w) as c:
            vid = await save(c, {GOLDEN: pinker_hour()})
            r = await publish(c, vid)
        plan = w.engine.current_plan()
        held = (plan.tier, plan.feature, GOLDEN in w.engine.t1_hold)
        await w.engine.wait_repaint()
        tiers = []
        for _ in range(10):
            await w.run_for(1)
            tiers.append(w.engine.current_plan().tier)
        await w.engine.wait_idle()
        final = w.engine.current_plan()
        out = (start_tier, r.status_code, held, tiers, final, list(writes), w.engine.t1_hold)
        await w.close()
        return out

    start, status, held, tiers, final, after, hold = run_virtual(main)
    assert start is Tier.T1 and status == 200
    tier, feature, is_held = held
    assert tier is not Tier.T1 and is_held
    assert (
        "Hubspace strand set to a single colour" in feature
    )  # the module-level design's sentence, truthfully
    assert final.mode_id == GOLDEN and final.tier is Tier.T1 and not hold
    rewrites = slot_writes(after)
    assert len(rewrites) == 9 and len({a for _t, a, _r in rewrites}) == 9  # the repaint, once each
    first_t1 = tiers.index(Tier.T1)
    assert all(t is not Tier.T1 for t in tiers[:first_t1])
    upgrade = [x for x in look_writes(after) if x[0] > max(t for t, _a, _r in rewrites)]
    assert len(upgrade) == 9 and len({a for _t, a, _r in upgrade}) == 9  # one T1 burst


def test_a_restart_over_a_published_file_seeds_the_hold_from_stale_records(no_threads, monkeypatch):
    """Records of the approved design on disk, a published layout change in the file, no start-up commissioning:
    the restarted show seeds the hold (never T1 with copy the slots don't hold) until the reconciler rewrites the
    slots, then T1."""
    from conductor.show import ShowWiring

    async def main():
        w = await show()
        async with client_of(w) as c:
            vid = await save(c, {GOLDEN: pinker_hour()})
            assert (await publish(c, vid)).status_code == 200
        data = w.data
        w.owned = False
        await w.close()

        async def no_commission(self):
            return None

        monkeypatch.setattr(ShowWiring, "_sim_commission", no_commission)
        w2 = await show(data=data, quiet=False)
        await w2.run_for(3)
        seeded = GOLDEN in w2.engine.t1_hold
        await w2.engine.request_mode(GOLDEN, source="ops")
        await w2.engine.wait_idle()
        tier_now = w2.engine.current_plan().tier
        await w2.run_for(120)
        await w2.engine.request_mode(GOLDEN, source="ops")
        await w2.engine.wait_idle()
        later = w2.engine.current_plan().tier, GOLDEN in w2.engine.t1_hold
        source = w2.looks.source
        await w2.close()
        import shutil

        shutil.rmtree(data, ignore_errors=True)
        return seeded, tier_now, later, source

    seeded, tier_now, later, source = run_virtual(main)
    assert source == "published" and seeded and tier_now is not Tier.T1
    assert later == (Tier.T1, False)


def test_after_a_restart_the_show_compiles_the_published_trio_with_no_slot_writes(no_threads):
    async def main():
        w = await show()
        async with client_of(w) as c:
            vid = await save(c, {TIDES: brighter(TIDES)})
            assert (await publish(c, vid)).status_code == 200
        data = w.data
        w.owned = False
        await w.close()
        w2 = await show(data=data)
        writes = spy_writes(w2)
        await w2.engine.request_mode(TIDES, source="ops")
        await w2.engine.wait_idle()
        compiled = rows_of(w2.engine.current_plan())
        expected_ctx = w2.wiring.context()
        await w2.run_for(20 * 60)
        stale = [w2.engine.stale_slot(DEV, s, None) for s in ("custom-1", "custom-2", "custom-3")]
        alerts = [a.message for a in w2.alerts() if "pattern in custom-" in a.message]
        out = (
            w2.looks.source,
            compiled == rows_of(compile_mode(TIDES, w2.engine.current_plan().tier, expected_ctx)),
            slot_writes(writes),
            alerts,
            w2.looks.trio.modes.modes[TIDES].label,
            stale,
        )
        await w2.close()
        import shutil

        shutil.rmtree(data, ignore_errors=True)
        return out

    source, compiled_ok, slots, alerts, label, _stale = run_virtual(main)
    assert source == "published" and compiled_ok and label == "Coastal Tides"
    assert slots == [] and alerts == []


# ---- the slot locks (§2.6.0) --------------------------------------------------------------------------------------------
def _record_app(services, device_id: str = DEV, slot: str = SLOT) -> None:
    entry = services.registry.get(device_id)
    services.update_registry(device_id, entry.slot_patch(slot, digest="abc123", origin="app", effects=None))


def test_a_painted_design_change_over_a_hand_painted_slot_is_refused(no_threads):
    async def main():
        w = await show()
        before = w.overlay.exists()
        async with client_of(w) as c:
            design = await save(c, {GOLDEN: pinker_hour()})  # saved before Module 1 was painted in the app
            _record_app(w.services)
            writes = spy_writes(w)
            vid = await save(c, {GOLDEN: brighter(GOLDEN)})  # brightness only: never locked
            ok = await publish(c, vid)
            refused = await publish(c, design)
        out = (
            before,
            ok.status_code,
            ok.text,
            refused.status_code,
            refused.json(),
            list(writes),
            w.looks.live_id,
        )
        await w.close()
        return out

    before, ok, ok_text, status, body, writes, live = run_virtual(main)
    assert not before and ok == 200, ok_text
    assert slot_writes(writes) == []
    assert status == 409 and body["field"].startswith(f"{GOLDEN}.")
    assert body["detail"].startswith(
        "Golden Hour's slot is hand-painted in the Hubspace app on 1 module (Module 1)"
    )
    assert live == "p0001"  # the brightness publish stands; the design one wrote nothing


def test_commissioned_slots_without_gate_c_publish_with_no_slot_write_on_hardware(no_threads, monkeypatch):
    """B2 on hardware: api records but no module with its own Gate C (no t1_frames proven at its address): a painted
    design change publishes (the B1 api lock is gone) with no slot repaint (nothing is eligible), a brightness change
    too, and Revert to approved — never a slot write."""
    from live_lab import patched_trio, publication_document, write_document

    def prepare(services):
        for entry in services.registry.canopy_modules():
            services.update_registry(
                entry.id, entry.slot_patch(SLOT, digest="0123456789abcdef", origin="api", effects=None)
            )
        # the show already runs a published Golden Hour design (published before the slots were commissioned)
        write_document(services.data, publication_document(patched_trio(pinker_hour())))

    async def main():
        w = await show(live=True, monkeypatch=monkeypatch, prepare=prepare)
        writes = spy_writes(w)
        async with client_of(w) as c:
            design = await save(c, {GOLDEN: edited(GOLDEN, **{"palette.0.hex": "#FFE0A0"})})
            first = (await ask(c, design)).json()
            done = await publish(c, design)
            bright = await save(c, {GOLDEN: {**pinker_hour(), "brightness": pinker_hour()["brightness"] - 5}})
            ok = await publish(c, bright)
            back = await publish(c, "approved")  # Golden Hour's design changes back over the api records
        await w.engine.wait_repaint()
        out = (first, done.status_code, ok.status_code, back.status_code, back.json(), slot_writes(writes))
        await w.close()
        return out

    first, done, ok, back, back_body, slots = run_virtual(main)
    assert first["confirm_required"] and first["repaint"] == [] and "no slot repaint" in first["cost"]
    assert done == 200 and ok == 200 and back == 200, back_body
    assert slots == []  # nothing eligible: no slot write


def test_kits_without_gate_c_publish_a_design_with_zero_slot_writes(no_threads, monkeypatch):
    """Today's show box: no bench flags, nothing commissioned: the design publishes at module level."""

    async def main():
        w = await show(live=True, monkeypatch=monkeypatch)
        await w.engine.request_mode(GOLDEN, source="ops")
        await w.engine.wait_idle()
        writes = spy_writes(w)
        async with client_of(w) as c:
            catalog = (await c.get("/api/lab/catalog")).json()
            vid = await save(c, {GOLDEN: pinker_hour()})
            r = await publish(c, vid)
            await w.engine.wait_idle()
            listing = (await c.get("/api/kiosk/modes")).json()
        info = next(s for s in catalog["scenes"] if s["id"] == GOLDEN)["repaint"]
        golden = next(m for m in listing if m["id"] == GOLDEN)
        expected = [
            "#" + "".join(f"{v:02X}" for v in m["rgb"])
            for m in sorted(
                __import__("conductor.routes.design", fromlist=["mode_design"]).mode_design(
                    GOLDEN, w.wiring.context()
                )["modules"],
                key=lambda m: m["module"],
            )
        ]
        out = r.status_code, info, slot_writes(writes), golden, expected, w.engine.current_plan().tier
        await w.close()
        return out

    status, info, slots, golden, _expected, tier = run_virtual(main)
    assert status == 200 and slots == [] and tier is Tier.T3
    assert info["sentence"] == "No slot repaint: no module has custom-3 commissioned on this show box."
    assert info["available"] is True and info["commissioned"] == []
    assert golden["label"] == PINKER and golden["module_colours"] is not None


# ---- refusals: nothing written, nothing sent -------------------------------------------------------------------------
REFUSAL_STATES = ["stopped", "commissioning", "outage", "rehearsal"]


@pytest.mark.parametrize("state", REFUSAL_STATES)
def test_the_engine_states_that_refuse_a_publish(no_threads, state):
    async def main():
        w = await show()
        async with client_of(w) as c:
            vid = await save(c, {TIDES: brighter(TIDES)})
            first = await ask(c, vid)
            confirm = first.json()["confirm"]
            engine = w.engine
            if state == "stopped":
                engine._stopped = True
            elif state == "commissioning":
                engine._commissioning = True
            elif state == "outage":
                engine._outage = {"since": w.clock.now(), "since_mono": w.clock.monotonic(), "reason": "test"}
            else:
                engine._rehearsal = SimpleNamespace(mode=TIDES, run_s=60)
            writes = spy_writes(w)
            registry = (w.data / "overrides" / "registry.yaml").read_bytes()
            r = await ask(c, vid, confirm=confirm)
            out = r.status_code, r.json(), w.overlay.exists(), list(writes), registry, w.looks.generation
            engine._stopped = engine._commissioning = False
            engine._outage = engine._rehearsal = None
            after = (w.data / "overrides" / "registry.yaml").read_bytes()
        await w.close()
        return (*out, after)

    status, body, written, writes, registry, generation, after = run_virtual(main)
    assert status == 409 and not written and writes == [] and registry == after and generation == 0
    expected = {
        "stopped": "The canopy engine is stopped (canopy-mac start).",
        "commissioning": "A kit is being commissioned (/ops/bench); publish once it ends.",
        "rehearsal": "A stepped rehearsal is running; stop it first.",
    }
    if state == "outage":
        assert body["detail"].startswith("Hubspace isn't answering (resting since ")
        assert body["detail"].endswith("publish it once /ops says the canopy is back.")
        assert body["retry_after_s"] > 0
    else:
        assert body["detail"] == expected[state]
    if state == "commissioning":
        assert body["retry_after_s"] == 30.0


def test_publish_is_allowed_while_guest_input_is_paused(no_threads):
    async def main():
        w = await show()
        w.engine.pause_guest_input(True, source="ops")
        async with client_of(w) as c:
            vid = await save(c, {TIDES: brighter(TIDES)})
            r = await publish(c, vid)
        out = r.status_code, w.engine.paused
        await w.close()
        return out

    assert run_virtual(main) == (200, True)


def test_the_lab_refusals_and_their_sentences(no_threads):
    async def main():
        w = await show()
        out = {}
        async with client_of(w) as c:
            out["no previous"] = (await ask(c, "previous")).json()
            out["unknown"] = await ask(c, "v0099")
            out["same"] = (await publish(c, "approved")).json()
            bad = await c.post("/api/lab/publish", json={"version": "../x", "expected_live": "approved"})
            out["bad"] = bad.status_code
            vid = await save(c, {TIDES: brighter(TIDES)})
            first = await ask(c, vid)
            out["confirm"] = first.json()
            wrong = await ask(c, vid, confirm="Publish something else.")
            out["wrong confirm"] = wrong.status_code, wrong.json()["confirm_required"]
            assert (await publish(c, vid)).status_code == 200
            out["same again"] = (await publish(c, vid)).json()
            out["delete live"] = await c.request("DELETE", f"/api/lab/versions/{vid}", json={})
        out["file"] = w.overlay.exists()
        await w.close()
        return out

    out = run_virtual(main)
    assert out["no previous"]["detail"] == "There is no earlier publish to go back to."
    assert out["unknown"].status_code == 404
    assert out["same"] == {"detail": "This is already what the show runs.", "nothing": True}
    assert out["bad"] == 422
    confirm = out["confirm"]
    assert confirm["confirm_required"] and confirm["confirm"] == confirm["detail"]
    assert set(confirm) >= {
        "cost",
        "replaces",
        "changes",
        "repaint",
        "ends_trial",
        "show_offer",
        "notes",
        "warnings",
    }
    assert out["wrong confirm"] == (409, True)
    assert out["same again"]["nothing"] is True
    assert out["delete live"].status_code == 409
    assert (
        out["delete live"].json()["detail"]
        == "This version is live on the show; publish another (or revert) first."
    )


def test_a_stale_version_is_refused_with_its_problems(no_threads, monkeypatch):
    async def main():
        w = await show()
        async with client_of(w) as c:
            vid = await save(c, {TIDES: brighter(TIDES)})

            def stale(*_a, **_k):
                raise D.DraftError([D.Problem("x", "scene_id", "no longer applies")])

            monkeypatch.setattr(D, "apply_drafts", stale)
            r = await ask(c, vid)
        await w.close()
        return r.status_code, r.json()

    status, body = run_virtual(main)
    assert status == 409 and body["stale"] is True and body["problems"][0]["message"] == "no longer applies"


def test_a_publish_while_another_runs_is_refused_as_busy(no_threads):
    async def main():
        w = await show()
        async with client_of(w) as c:
            vid = await save(c, {TIDES: brighter(TIDES)})
            lock = PUB._publish_lock(w.lab)
            await lock.acquire()
            try:
                r = await asyncio.wait_for(ask(c, vid), 5)  # never waits for the lock
            finally:
                lock.release()
        out = r.status_code, r.json(), w.overlay.exists()
        await w.close()
        return out

    assert run_virtual(main) == (409, {"detail": "Another publish is in progress."}, False)


def test_concurrent_publishes_give_one_200_and_one_409(no_threads):
    async def main():
        w = await show()
        async with client_of(w) as c:
            a = await save(c, {TIDES: brighter(TIDES)})
            b = await save(c, {CALM: brighter(CALM)})
            ca = (await ask(c, a)).json()["confirm"]
            cb = (await ask(c, b)).json()["confirm"]
            ra, rb = await asyncio.gather(
                ask(c, a, confirm=ca), ask(c, b, confirm=cb, expected_live="approved")
            )
        await w.close()
        return [ra.status_code, rb.status_code], [ra.json(), rb.json()]

    statuses, bodies = run_virtual(main)
    assert sorted(statuses) == [200, 409]
    refused = bodies[statuses.index(409)]
    assert refused["detail"] == PUB.BUSY or refused.get("conflict") is True


def test_a_confirm_computed_against_a_replaced_look_is_a_conflict(no_threads):
    """Two clients: B publishes between A's confirm_required and A's confirm → A gets conflict + live; the overlay
    holds B's bytes."""

    async def main():
        w = await show()
        async with client_of(w) as c:
            a = await save(c, {TIDES: brighter(TIDES)})
            b = await save(c, {CALM: brighter(CALM)})
            asked = await ask(c, a)
            assert (await publish(c, b, who="Paris")).status_code == 200
            after_b = w.overlay.read_bytes()
            r = await ask(c, a, confirm=asked.json()["confirm"], expected_live="approved")
            after_a = w.overlay.read_bytes()
        await w.close()
        return r.status_code, r.json(), after_b == after_a

    status, body, same = run_virtual(main)
    assert status == 409 and body["conflict"] is True and same
    assert body["detail"].startswith("The show changed while you were deciding: “")
    assert body["detail"].endswith("by Paris. Look again before publishing.")
    assert body["live"]["current"]["by"] == "Paris"


# ---- Undo, Revert, the try-on -----------------------------------------------------------------------------------------
def test_undo_reinstalls_the_previous_publication_even_after_its_version_is_deleted(no_threads):
    async def main():
        w = await show()
        async with client_of(w) as c:
            v1 = await save(c, {TIDES: brighter(TIDES)})
            assert (await publish(c, v1)).status_code == 200
            one = L.trio_sha(w.looks.trio)
            v2 = await save(c, {CALM: brighter(CALM)})
            assert (await publish(c, v2)).status_code == 200
            two = w.looks.file.current
            deleted = await c.request("DELETE", f"/api/lab/versions/{v1}", json={})
            asked = await ask(c, "previous")
            undo = await publish(c, "previous")
            live = (await c.get("/api/lab/live")).json()
        out = (
            deleted.status_code,
            asked.json()["confirm"],
            undo.status_code,
            L.trio_sha(w.looks.trio),
            one,
            two,
            live,
        )
        await w.close()
        return out

    deleted, sentence, status, now, one, two, live = run_virtual(main)
    assert deleted == 200 and status == 200
    assert sentence.startswith("Go back to “")
    assert now == one
    assert live["previous"]["id"] == two.id
    assert [h["outcome"] for h in live["history"]][:3] == ["live", "rolled back", "replaced"]
    assert live["current"]["version_exists"] is False


def test_revert_to_approved_follows_git_after_a_restart(no_threads):
    async def main():
        w = await show()
        async with client_of(w) as c:
            vid = await save(c, {TIDES: brighter(TIDES)})
            assert (await publish(c, vid)).status_code == 200
            asked = await ask(c, "approved")
            back = await publish(c, "approved")
        current = w.looks.file.current
        data = w.data
        w.owned = False
        await w.close()
        again = L.load_live_looks(CONFIG_DIR, data, settings=w.services.settings, canopy=w.lab.canopy)
        import shutil

        shutil.rmtree(data, ignore_errors=True)
        return asked.json()["confirm"], back.status_code, current, again

    sentence, status, current, again = run_virtual(main)
    assert sentence.startswith("Revert this simulator to the approved looks: Coastal Tides changes back.")
    assert status == 200 and current.kind == "approved" and current.trio is None
    assert again.source == "approved" and L.trio_sha(again.trio) == L.trio_sha(GIT)


def test_a_publish_during_a_try_on_ends_it_in_one_burst(no_threads):
    async def main():
        w = await show()
        await w.engine.request_mode(CALM, source="ops")
        await w.engine.wait_idle()
        trial = T.build_trial(
            w.lab, drafted(brighter(GOLDEN)), version=None, now_mono=w.clock.monotonic(), now=w.clock.now()
        )
        await w.engine.start_trial(trial)
        await w.engine.wait_idle()
        writes = spy_writes(w)
        async with client_of(w) as c:
            vid = await save(c, {TIDES: brighter(TIDES)})  # not the try-on's scene
            asked = await ask(c, vid)
            r = await publish(c, vid)
            await w.engine.wait_idle()
            # a try-on after the publish keeps the other scenes' published looks
            later = T.show_trio(w.lab)
        ends = [e for e in w.events("lab") if e["action"] == "trial_end"]
        out = asked.json(), r.status_code, w.engine.trial, list(writes), ends, later, w.engine.current_plan()
        await w.close()
        return out

    asked, status, trial, writes, ends, later, plan = run_virtual(main)
    assert asked["ends_trial"] is True and asked["confirm"].endswith("The running try-on ends.")
    assert "sends 9 writes now" in asked["cost"]  # driven although the try-on's scene didn't change
    assert status == 200 and trial is None
    assert len(look_writes(writes)) == 9  # one burst total
    assert "the look was published" in ends[-1]["why"]
    assert plan.mode_id == GOLDEN and not plan.label.endswith("(try-on)")
    assert later.scenes.get(TIDES).brightness_draft != GIT.scenes.get(TIDES).brightness_draft


# ---- failures -----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("where", ["install_looks", "reload_looks"])
def test_an_exception_after_the_write_rolls_back_byte_for_byte(no_threads, monkeypatch, where):
    async def main():
        w = await show()
        async with client_of(w) as c:
            first = await save(c, {TIDES: brighter(TIDES)})
            assert (await publish(c, first)).status_code == 200
            before = w.overlay.read_bytes()
            before_sha = L.trio_sha(w.looks.trio)
            failed = {"n": 0}
            if where == "install_looks":
                real = PUB.install_looks

                def once(*a, **k):
                    if failed["n"] == 0:
                        failed["n"] += 1
                        raise RuntimeError("an install bug")
                    return real(*a, **k)

                monkeypatch.setattr(PUB, "install_looks", once)
            else:

                async def boom(**_k):
                    raise RuntimeError("a re-pick bug")

                monkeypatch.setattr(w.engine, "reload_looks", boom)
            second = await save(c, {CALM: brighter(CALM)})
            r = await publish(c, second)
            await w.engine.wait_idle()
        plan = w.engine.current_plan()
        out = (
            r.status_code,
            r.json(),
            w.overlay.read_bytes() == before,
            L.trio_sha(w.looks.trio) == before_sha,
            rows_of(plan) == rows_of(compile_mode(plan.mode_id, plan.tier, w.wiring.context())),
            [a.message for a in w.alerts() if "rolled back" in a.message],
        )
        await w.close()
        return out

    status, body, same_file, same_trio, repicked, alerts = run_virtual(main)
    assert status == 500 and body["detail"].startswith("The publish failed on the server (RuntimeError)")
    assert same_file and same_trio and repicked and alerts


def test_a_write_failure_is_read_only_and_installs_nothing(no_threads, monkeypatch):
    import conductor.tokens as TOK

    async def main():
        w = await show()
        async with client_of(w) as c:
            vid = await save(c, {TIDES: brighter(TIDES)})
            asked = await ask(c, vid)

            def refuse(*_a, **_k):
                raise OSError("read-only file system")

            monkeypatch.setattr(TOK, "atomic_write", refuse)
            r = await ask(c, vid, confirm=asked.json()["confirm"])
        out = r.status_code, r.json(), w.looks.generation, L.trio_sha(w.looks.trio)
        await w.close()
        return out

    status, body, generation, sha = run_virtual(main)
    assert status == 409 and body["read_only"] is True and generation == 0 and sha == L.trio_sha(GIT)


# ---- copy truth and alerts ----------------------------------------------------------------------------------------------
def test_the_copy_follows_the_tier_in_use_after_a_publish(no_threads):
    async def main():
        w = await show()
        await w.engine.request_mode(TIDES, source="ops")
        await w.engine.wait_idle()
        async with client_of(w) as c:
            d = edited(TIDES, description="A gentle tide of blues and greens along the water.")
            vid = await save(c, {TIDES: d})
            r = await publish(c, vid)
        await w.engine.wait_idle()
        plan = w.engine.current_plan()
        mode = w.looks.trio.modes.mode(TIDES)
        rung = {
            "stepped": mode.techniques.stepped,
            "modules": mode.techniques.modules,
            "strand": mode.techniques.strand,
        }
        out = r.status_code, plan.tier, plan.feature, rung
        await w.close()
        return out

    status, tier, feature, rung = run_virtual(main)
    assert status == 200
    technique = rung[str(tier)]
    allowed = {getattr(technique, "feature", None), getattr(technique, "moving_feature", None)}
    if (
        tier is Tier.STEPPED
    ):  # a stepped look holds its still design (the modules rung's sentence) after its run
        allowed.add(rung["modules"].feature)
    assert feature in allowed


def test_a_scenes_repaint_alert_is_keyed_by_its_scene():
    from conductor.alerts import AlertBoard

    board = AlertBoard()
    feed = board.feed
    feed({"kind": "alert", "level": "warn", "message": "Published looks: Golden Hour: x", "mode_id": GOLDEN})
    feed(
        {
            "kind": "alert",
            "level": "warn",
            "message": "Published looks: overrides/show-looks.yaml couldn't be used (x)",
        }
    )
    feed({"kind": "alert", "level": "info", "message": "Published looks: Coastal Tides ok", "mode_id": TIDES})
    assert {a.key for a in board.active()} == {f"looks:repaint:{GOLDEN}", "looks:load"}
    feed({"kind": "alert", "level": "info", "message": "Published looks: Golden Hour ok", "mode_id": GOLDEN})
    assert {a.key for a in board.active()} == {"looks:load"}  # a repaint success never clears looks:load
    feed(
        {
            "kind": "alert",
            "level": "info",
            "message": "Published looks: installed x.",
            "resolves": ["looks:load"],
        }
    )
    assert board.active() == []


def test_the_writer_paths_stay_the_engines():
    """publish.py calls only conductor.looks.live and the engine/wiring: no driver, no executor (the Lab's writer walk
    in tests/test_lab_access.py covers every Lab source; this pins the module's imports)."""
    import ast

    source = Path(PUB.__file__).read_text(encoding="utf-8")
    names = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
        elif isinstance(node, ast.Import):
            names += [a.name for a in node.names]
    assert not [n for n in names if n.startswith(("conductor.drivers", "conductor.executor"))]


def test_write_then_install_order(no_threads, monkeypatch):
    """A crash right after the write: the file on disk is the new publication, so a restart shows what ops
    confirmed (the write precedes the install)."""

    async def main():
        w = await show()
        order = []
        real_write, real_install = PUB.write_looks, PUB.install_looks

        def write(*a, **k):
            order.append("write")
            return real_write(*a, **k)

        def install(*a, **k):
            order.append("install")
            return real_install(*a, **k)

        monkeypatch.setattr(PUB, "write_looks", write)
        monkeypatch.setattr(PUB, "install_looks", install)
        async with client_of(w) as c:
            vid = await save(c, {TIDES: brighter(TIDES)})
            assert (await publish(c, vid)).status_code == 200
        reloaded = L.load_live_looks(CONFIG_DIR, w.data, settings=w.services.settings, canopy=w.lab.canopy)
        out = order, reloaded.source, L.trio_sha(reloaded.trio) == L.trio_sha(w.looks.trio)
        await w.close()
        return out

    order, source, same = run_virtual(main)
    assert order == ["write", "install"] and source == "published" and same


def test_a_refused_repick_during_a_try_on_puts_the_safe_look_up_never_the_draft(no_threads):
    """reload_looks over a try-on whose scene can't be compiled under the new trio: the override is gone, the safe
    look is on (never the try-on's plan), and the warn is keyed by the scene."""
    from conductor.engine_api import PlanError

    async def main():
        w = await show()
        trial = T.build_trial(
            w.lab, drafted(brighter(GOLDEN)), version=None, now_mono=w.clock.monotonic(), now=w.clock.now()
        )
        await w.engine.start_trial(trial)
        await w.engine.wait_idle()
        trial_plan = w.engine.current_plan()
        real = w.engine.deps.compile

        def refuse(mode_id, tier):
            if mode_id == GOLDEN:
                raise PlanError("Golden Hour doesn't compile under the new trio (test)")
            return real(mode_id, tier)

        w.engine.deps.compile = refuse
        st = await w.engine.reload_looks(changed=[GOLDEN], why="a test")
        await w.engine.wait_idle()
        plan = w.engine.current_plan()
        keys = {a.key for a in w.alerts()}
        out = st.refused, w.engine.trial, plan is trial_plan, plan.mode_id, keys
        w.engine.deps.compile = real
        await w.close()
        return out

    refused, trial, same, mode, keys = run_virtual(main)
    assert refused and trial is None and not same
    assert mode == CALM  # the safe look
    assert f"looks:show:{GOLDEN}" in keys
