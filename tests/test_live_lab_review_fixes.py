"""The PR B review's fixes (docs/live-lab/PLAN.md §10, review 4), each pinned by the regression it fixes, on the
virtual clock over the shipped config/ (tests/live_lab.py's show):

- LL-PUB-1 / UI-2: out of a fallback, Revert to approved replaces the unusable file and clears the /ops line and alert
  (it is never "already what the show runs"), sends nothing, and leaves no Undo target that can't be put back.
- LL-PUB-2: the Lab publishes over the git the running show loaded (never config/ re-read when the Lab is first
  opened), and refuses while config/ on disk differs from it (a deploy under way).
- LL-PUB-3 / B1-6: a rolled-back publish re-picks under the restored trio and recomputes the T1 hold under it (in both
  directions: never a stale hold, never a dropped one).
- LL-PUB-4 / B1-2: a rollback during a fallback keeps the fallback's line and alert, and names what really runs; a
  confirm during a fallback never says it replaces the unusable publication.
- LL-PUB-5: a later good publish resolves the rollback warn; a scene shown again resolves its can't-be-shown warn.
- UI-1: the "Show <scene>" box is part of what ops confirms (sentence, notes and cost follow it).
- UI-4: "today" at T1 paints what the slots hold under the live looks, and never T1 while the scene is held.
- UI-5: GET /api/canopy/scenes is the scenes as the show runs them (18 since D77), with the install generation.
- B1-5: the no-data-folder refusal and Undo's checksum check.
"""

from __future__ import annotations

import copy
import shutil

import pytest
import yaml
from live_lab import (
    CALM,
    DEV,
    GIT,
    GOLDEN,
    PINKER,
    TIDES,
    ask,
    brighter,
    client_of,
    edited,
    install_no_threads,
    patched_trio,
    pinker_hour,
    publication_document,
    publish,
    save,
    show,
    write_document,
)

from conductor import rows as R
from conductor.canopy import LOOKS_SOURCE
from conductor.clock import run_virtual
from conductor.config import CONFIG_DIR
from conductor.design_lab import preview as P
from conductor.design_lab import publish as PUB
from conductor.design_lab import service as SVC
from conductor.engine_api import PlanError, Tier
from conductor.looks import compile_mode
from conductor.looks import live as L
from conductor.routes.devices import runtime_problems

PINK = patched_trio(pinker_hour())


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


def look_writes(writes) -> list:
    return [x for x in writes if not any(r.function_class == R.FRAME_CLASS for r in x[2])]


def rows_of(plan) -> dict:
    return {d: list(mt.rows) for d, mt in plan.modules.items()}


def looks_lines(w) -> list[str]:
    return [p for p in runtime_problems(w.services) if p.startswith(L.ALERT_PREFIX)]


def keys(w) -> set[str]:
    return {a.key for a in w.alerts()}


def _sha_mismatch() -> dict:
    doc = publication_document(PINK)
    doc["current"]["trio_sha"] = "000000000000"
    return doc


def _ladder() -> dict:
    trio = PINK.dump()
    trio["modes"]["modes"][GOLDEN]["ladder"] = [
        "canvas",
        "slot",
        "modules",
        "strand",
    ]  # what a deploy could cause
    return publication_document(PINK, trio=trio, trio_sha=L._sha(trio))


FALLBACKS = {
    "bad yaml": "format: 1\ncurrent: [unclosed\n",
    "trio_sha mismatch": _sha_mismatch(),
    "a ladder that differs from git's": _ladder(),
}


def _prepare(document):
    def prepare(services):
        write_document(services.data, document)

    return prepare


def _flaky_repick(w, *, times: int = 1):
    """The engine's re-pick raises `times` times for the publish's own source (a bug after reload_looks set the T1
    hold), then works (the rollback's re-pick)."""
    real = w.engine._request
    left = {"n": times}

    async def flaky(mode_id, *, source, ripple):
        if source == LOOKS_SOURCE and left["n"] > 0:
            left["n"] -= 1
            raise RuntimeError("a re-pick bug")
        return await real(mode_id, source=source, ripple=ripple)

    w.engine._request = flaky


# ---- LL-PUB-1 / UI-2: Revert out of a fallback -------------------------------------------------------------------------
@pytest.mark.parametrize("cause", list(FALLBACKS))
def test_revert_out_of_a_fallback_replaces_the_unusable_file_and_clears_the_alert(no_threads, cause):
    async def main():
        w = await show(prepare=_prepare(FALLBACKS[cause]))
        bad = w.overlay.read_bytes()
        before = (w.looks.problem, looks_lines(w), keys(w))
        writes = spy_writes(w)
        async with client_of(w) as c:
            asked = await ask(c, "approved")
            r = await publish(c, "approved")
            undo = await ask(c, "previous")
            live = (await c.get("/api/lab/live")).json()
        await w.engine.wait_idle()
        written = yaml.safe_load(w.overlay.read_text())
        copies = [p.read_bytes() for p in (w.overlay.parent / "history").glob("show-looks-*.yaml")]
        after = (w.looks.problem, looks_lines(w), keys(w))
        data = w.data
        w.owned = False
        await w.close()
        again = L.load_live_looks(CONFIG_DIR, data, settings=w.services.settings, canopy=w.lab.canopy)
        shutil.rmtree(data, ignore_errors=True)
        return before, asked, r, undo, live, written, copies, bad, after, list(writes), again

    before, asked, r, undo, live, written, copies, bad, after, writes, again = run_virtual(main)
    problem, lines, alert_keys = before
    assert problem and len(lines) == 1 and L.LOAD_KEY in alert_keys
    assert asked.status_code == 409 and asked.json()["confirm_required"], asked.text
    sentence = asked.json()["confirm"]
    assert sentence.startswith(
        "Revert this simulator to the approved looks: it already runs them, because overrides/show-looks.yaml "
        f"couldn't be used ({problem}); this replaces that file"
    ), sentence
    assert sentence.endswith("Nothing on the canopy changes now.") and "  " not in sentence
    assert asked.json()["cost"].startswith("sends nothing now")
    assert r.status_code == 200, r.text
    assert writes == []  # the show already ran git: nothing to send
    problem_after, lines_after, keys_after = after
    assert problem_after is None and lines_after == [] and L.LOAD_KEY not in keys_after
    assert written["current"]["kind"] == "approved" and written["current"]["trio"] is None
    assert written["previous"] is None  # the unusable publication is never offered back as Undo
    assert bad in copies  # the broken file is kept in overrides/history/
    assert live["fallback"] is False and live["previous"] is None
    assert undo.status_code == 409 and undo.json()["detail"] == PUB.NO_PREVIOUS
    assert again.problem is None and again.source == "approved"  # a restart starts clean


def test_without_a_fallback_revert_over_approved_is_still_nothing(no_threads):
    async def main():
        w = await show()
        async with client_of(w) as c:
            r = await ask(c, "approved")
        out = r.status_code, r.json(), w.overlay.exists()
        await w.close()
        return out

    assert run_virtual(main) == (409, {"detail": PUB.SAME, "nothing": True}, False)


# ---- LL-PUB-2: one git per process -------------------------------------------------------------------------------------
def test_the_lab_publishes_over_the_git_the_show_loaded_and_refuses_during_a_deploy(no_threads, tmp_path):
    config = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config)

    async def main():
        w = await show(config_dir=config)
        git_sha = L.trio_sha(w.looks.git)
        # a deploy's `git checkout` changes config/ on disk before its restart ...
        modes = config / "modes.yaml"
        data = yaml.safe_load(modes.read_text())
        ladder = data["modes"][GOLDEN]["ladder"]
        assert "stepped" in ladder
        data["modes"][GOLDEN]["ladder"] = [t for t in ladder if t != "stepped"]
        modes.write_text(yaml.safe_dump(data, sort_keys=False))
        # ... and the Lab is opened for the first time in this process only now
        lab = SVC.LabService(w.services)
        w.services.extras[SVC.KEY] = lab
        w.lab = lab
        async with client_of(w) as c:
            r = await ask(c, "approved")
            v = await ask(c, "v0001")
        out = (
            L.trio_sha(lab.approved) == git_sha,
            lab.config_sha == w.looks.git_sha,
            lab.canopy is w.looks.canopy,
            r.status_code,
            r.json(),
            v.status_code,
            v.json(),
            L.trio_sha(w.looks.trio) == git_sha,
            w.looks.generation,
            w.overlay.exists(),
        )
        await w.close()
        return out

    same_git, same_sha, same_canopy, status, body, vstatus, vbody, trio_same, generation, written = (
        run_virtual(main)
    )
    assert same_git and same_sha and same_canopy  # the Lab's approved trio is the one the show validated
    assert status == 409 and body["detail"] == PUB.DEPLOYING
    assert vstatus == 409 and vbody["detail"] == PUB.DEPLOYING  # checked before anything else
    assert trio_same and generation == 0 and not written


# ---- LL-PUB-3 / B1-6: the rollback's re-pick and the T1 hold ----------------------------------------------------------
def test_a_failure_after_the_repick_is_rolled_back_to_the_restored_trios_plan(no_threads, monkeypatch):
    """The real reload_looks runs (the new trio's plan is installed and sent), then something raises: the rollback
    re-picks under the restored trio, so the lights never keep the failed publish's plan."""

    async def main():
        w = await show()
        await w.engine.request_mode(CALM, source="ops")
        await w.engine.wait_idle()
        real = w.engine.reload_looks
        seen = {}

        async def after(**kw):
            await real(**kw)
            seen["plan"] = w.engine.current_plan()
            raise RuntimeError("a bug after the re-pick")

        monkeypatch.setattr(w.engine, "reload_looks", after)
        hold = set(w.engine.t1_hold)
        async with client_of(w) as c:
            vid = await save(c, {CALM: brighter(CALM)})
            new_trio = PUB.published_trio(w.lab, vid).trio
            r = await publish(c, vid)
        await w.engine.wait_idle()
        plan = w.engine.current_plan()
        ctx = w.wiring.context()
        new_ctx = w.wiring.context(new_trio.modes, new_trio.recipes)
        out = (
            r.status_code,
            plan is seen["plan"],
            rows_of(plan) == rows_of(compile_mode(plan.mode_id, plan.tier, ctx)),
            rows_of(plan) != rows_of(compile_mode(plan.mode_id, plan.tier, new_ctx)),
            set(w.engine.t1_hold) == hold,
        )
        await w.close()
        return out

    status, is_failed_plan, restored_rows, differs, hold_same = run_virtual(main)
    assert status == 500 and not is_failed_plan and restored_rows and differs and hold_same


def test_a_rolled_back_painted_publish_leaves_no_stale_t1_hold(no_threads):
    """Golden Hour at T1, a painted publish whose re-pick raises: rolled back, the slots hold the restored design, so
    Golden Hour is not held and goes back to T1 (before the fix it stayed below T1 until a restart)."""

    async def main():
        w = await show()
        await w.engine.request_mode(GOLDEN, source="ops")
        await w.engine.wait_idle()
        start = w.engine.current_plan().tier
        _flaky_repick(w)
        async with client_of(w) as c:
            vid = await save(c, {GOLDEN: pinker_hour()})
            r = await publish(c, vid)
        await w.engine.wait_idle()
        restored = L.trio_sha(w.looks.trio) == L.trio_sha(GIT)
        hold = set(w.engine.t1_hold)
        await w.engine.request_mode(GOLDEN, source="ops")
        await w.engine.wait_idle()
        out = start, r.status_code, restored, hold, w.engine.current_plan().tier
        await w.close()
        return out

    start, status, restored, hold, tier = run_virtual(main)
    assert start is Tier.T1 and status == 500 and restored
    assert GOLDEN not in hold and tier is Tier.T1


def test_a_rolled_back_revert_keeps_the_hold_the_restored_trio_needs(no_threads):
    """A published Golden Hour design is held (its slots still hold the approved one); a Revert whose re-pick raises
    is rolled back to that publication: the hold stands again (before the fix it was dropped, so Golden Hour showed
    T1 copy for a design its slots don't hold). B2: the publish's own repaint job is kept from running (as when it
    couldn't start), so the slots still hold the approved design."""

    async def main():
        w = await show()
        await w.engine.request_mode(GOLDEN, source="ops")
        await w.engine.wait_idle()
        real_start = w.engine.start_repaint
        # no operator repaint: the slots keep the approved design
        w.engine.start_repaint = lambda jobs, **kw: []
        async with client_of(w) as c:
            vid = await save(c, {GOLDEN: pinker_hour()})
            assert (await publish(c, vid)).status_code == 200
            await w.engine.wait_idle()
            w.engine.start_repaint = real_start
            held = GOLDEN in w.engine.t1_hold
            _flaky_repick(w)
            r = await publish(c, "approved")
        await w.engine.wait_idle()
        restored = w.looks.trio.scenes.get(GOLDEN).name
        out = held, r.status_code, restored, GOLDEN in w.engine.t1_hold, w.engine.current_plan().tier
        await w.close()
        return out

    held, status, restored, still_held, tier = run_virtual(main)
    assert held and status == 500 and restored == PINKER
    assert still_held and tier is not Tier.T1


# ---- LL-PUB-4 / B1-2: a rollback during a fallback --------------------------------------------------------------------
@pytest.mark.parametrize("cause", ["trio_sha mismatch", "bad yaml"])
@pytest.mark.parametrize("where", ["reload_looks", "install_looks"])
def test_a_rollback_during_a_fallback_keeps_its_line_and_alert(no_threads, monkeypatch, cause, where):
    async def main():
        w = await show(prepare=_prepare(FALLBACKS[cause]))
        bytes_before = w.overlay.read_bytes()
        problem, lines = w.looks.problem, looks_lines(w)
        if where == "install_looks":
            real, once = PUB.install_looks, {"n": 0}

            def failing(*a, **k):
                if once["n"] == 0:
                    once["n"] += 1
                    raise RuntimeError("an install bug")
                return real(*a, **k)

            monkeypatch.setattr(PUB, "install_looks", failing)
        else:

            async def boom(**_k):
                raise RuntimeError("a re-pick bug")

            monkeypatch.setattr(w.engine, "reload_looks", boom)
        async with client_of(w) as c:
            vid = await save(c, {CALM: brighter(CALM)})
            asked = await ask(c, vid)
            r = await publish(c, vid)
            live = (await c.get("/api/lab/live")).json()
        out = (
            asked.json(),
            r.status_code,
            r.json(),
            w.overlay.read_bytes() == bytes_before,
            (w.looks.problem, looks_lines(w)) == (problem, lines),
            keys(w),
            live["fallback"],
            [a.message for a in w.alerts() if a.key == "looks:publish"],
        )
        await w.close()
        return out

    asked, status, body, same_file, same_state, alert_keys, fallback, rollback = run_virtual(main)
    assert "replacing" not in asked["confirm"] and asked["replaces"] is None  # it runs the approved looks
    assert status == 500 and same_file and same_state and fallback is True
    assert L.LOAD_KEY in alert_keys and "looks:publish" in alert_keys
    assert "the show runs “the approved looks” again" in body["detail"], body
    assert len(rollback) == 1 and rollback[0].startswith("Published looks: a publish at ")
    assert rollback[0].endswith("and was rolled back to “the approved looks”.")


def test_a_rollback_keeps_a_masked_gits_alert(no_threads, monkeypatch):
    async def main():
        w = await show(prepare=_prepare(publication_document(PINK, base_config_sha="0123456789ab")))
        before = keys(w)

        async def boom(**_k):
            raise RuntimeError("a re-pick bug")

        monkeypatch.setattr(w.engine, "reload_looks", boom)
        async with client_of(w) as c:
            vid = await save(c, {CALM: brighter(CALM)})
            r = await publish(c, vid)
        out = before, r.status_code, r.json()["detail"], keys(w), w.looks.git_changed
        await w.close()
        return out

    before, status, detail, after, git_changed = run_virtual(main)
    assert L.GIT_KEY in before and status == 500 and git_changed
    assert L.GIT_KEY in after
    assert "the show runs “Client Oct 22 v3 – warmer sunset” again" in detail  # it really runs again


# ---- LL-PUB-5: warns that a later success resolves -------------------------------------------------------------------
def test_a_good_publish_resolves_the_rollback_warn(no_threads, monkeypatch):
    async def main():
        w = await show()
        real = w.engine.reload_looks
        once = {"n": 0}

        async def flaky(**kw):
            if once["n"] == 0:
                once["n"] += 1
                raise RuntimeError("a re-pick bug")
            return await real(**kw)

        monkeypatch.setattr(w.engine, "reload_looks", flaky)
        async with client_of(w) as c:
            vid = await save(c, {TIDES: brighter(TIDES)})
            failed = await publish(c, vid)
            during = keys(w)
            ok = await publish(c, vid)
        out = failed.status_code, during, ok.status_code, keys(w)
        await w.close()
        return out

    failed, during, ok, after = run_virtual(main)
    assert failed == 500 and "looks:publish" in during
    assert ok == 200 and "looks:publish" not in after


def test_a_scene_shown_again_resolves_its_cant_be_shown_warn_and_only_its_own(no_threads):
    async def main():
        w = await show()
        real = w.engine.deps.compile
        refused: set[str] = set()

        def compile_(mode_id, tier):
            if mode_id in refused:
                raise PlanError(f"{mode_id} doesn't compile under the new trio (test)")
            return real(mode_id, tier)

        w.engine.deps.compile = compile_
        for mode_id in (TIDES, GOLDEN):  # each showing when a re-pick under "new looks" can't compile it
            await w.engine.request_mode(mode_id, source="ops")
            await w.engine.wait_idle()
            refused.add(mode_id)
            st = await w.engine.reload_looks(changed=[mode_id], why="a test")
            await w.engine.wait_idle()
            assert st.refused
        both = keys(w)
        refused.discard(GOLDEN)  # Coastal Tides still can't be shown
        await w.engine.request_mode(GOLDEN, source="ops")  # Golden Hour compiles again and is on the canopy
        await w.engine.wait_idle()
        after = keys(w)
        w.engine.deps.compile = real
        await w.close()
        return both, after

    both, after = run_virtual(main)
    assert {f"looks:show:{TIDES}", f"looks:show:{GOLDEN}"} <= both
    assert f"looks:show:{GOLDEN}" not in after and f"looks:show:{TIDES}" in after


# ---- UI-1: "Show <scene>" is part of the confirm ---------------------------------------------------------------------
def test_the_show_box_is_part_of_what_ops_confirms(no_threads):
    async def main():
        w = await show()
        await w.engine.request_mode(CALM, source="ops")
        await w.engine.wait_idle()
        async with client_of(w) as c:
            vid = await save(c, {TIDES: edited(TIDES, **{"palette.0.hex": "#3060C0"})})
            plain = (await ask(c, vid)).json()
            shown = (await ask(c, vid, show=True)).json()
            writes = spy_writes(w)
            # a confirm of the box-off sentence sent with the box on: asked again, nothing written or sent
            mismatched = await ask(c, vid, show=True, confirm=plain["confirm"])
            mid = (list(writes), w.overlay.exists(), w.engine.current_plan().mode_id)
            ok = await ask(c, vid, show=True, confirm=shown["confirm"])
            await w.engine.wait_idle()
        out = plain, shown, mismatched.status_code, mismatched.json(), mid, ok.status_code, ok.json()
        out += (w.engine.current_plan().mode_id,)
        await w.close()
        return out

    plain, shown, mstatus, mbody, mid, status, body, mode = run_virtual(main)
    assert "Nothing on the canopy changes now" in plain["confirm"] and plain["cost"].startswith(
        "sends nothing now"
    )
    assert shown["show_offer"] == TIDES
    tides = GIT.scenes.get(TIDES).name  # D77: "Coastal Tides #1"
    assert f"The simulated canopy switches to {tides} now on 9 modules (" in shown["confirm"], shown[
        "confirm"
    ]
    assert (
        "at its designed brightness" in shown["confirm"]
        and "Nothing on the canopy changes" not in shown["confirm"]
    )
    assert shown["cost"].startswith("sends 9 writes now")
    assert mstatus == 409 and mbody["confirm_required"] and mbody["confirm"] == shown["confirm"]
    assert mid == ([], False, CALM)
    assert status == 200 and body["report"]["sent"] == 9 and mode == TIDES


def test_with_the_box_on_the_guest_power_and_dimming_notes_give_way_to_the_switch(no_threads):
    async def main():
        w = await show()
        await w.engine.request_mode(CALM, source="ops")
        await w.engine.wait_idle()
        await w.engine.set_guest_power(False, source="ops")
        await w.engine.wait_idle()
        async with client_of(w) as c:
            vid = await save(c, {TIDES: brighter(TIDES)})
            plain = (await ask(c, vid)).json()
            shown = (await ask(c, vid, show=True)).json()
        await w.close()
        return plain, shown

    plain, shown = run_virtual(main)
    assert plain["notes"] == [PUB.CANOPY_OFF]
    assert shown["notes"] == []
    assert "and turns the canopy back on (a guest had turned it off)" in shown["confirm"]


# ---- UI-4: "today" paints what the slots hold ------------------------------------------------------------------------
def test_today_paints_the_live_painted_design_once_the_slots_hold_it_and_never_t1_while_held(no_threads):
    async def main():
        w = await show()
        async with client_of(w) as c:
            vid = await save(c, {GOLDEN: pinker_hour()})
            assert (await publish(c, vid)).status_code == 200
        held_view = P.today_view(w.lab, w.looks.trio, GOLDEN)
        held = GOLDEN in w.engine.t1_hold
        await w.run_for(15 * 60)  # the reconciler's stale rewrite of custom-3 (≥ 600 s after commissioning)
        lifted = GOLDEN not in w.engine.t1_hold
        view = P.today_view(w.lab, w.looks.trio, GOLDEN)
        live_t1 = compile_mode(GOLDEN, Tier.T1, w.wiring.context())
        git_t1 = compile_mode(GOLDEN, Tier.T1, w.wiring.context(GIT.modes, GIT.recipes))
        await w.close()
        return held, held_view, lifted, view, live_t1, git_t1

    held, held_view, lifted, view, live_t1, git_t1 = run_virtual(main)
    assert held and held_view["tier"] != "canvas"  # the engine shows module level: so does "today"
    assert lifted and view["tier"] == "canvas" and view["heads"] == "published"
    assert view["note"] == P.HEADS_SLOTS.format(which="published")
    assert live_t1.modules[DEV].slot_hash != git_t1.modules[DEV].slot_hash
    assert view["feature"] == live_t1.feature


# ---- UI-5: the Studio's scenes ---------------------------------------------------------------------------------------
def test_the_studios_scenes_are_the_live_looks_with_their_generation(no_threads):
    async def main():
        w = await show()
        async with client_of(w) as c:
            before = (await c.get("/api/canopy/scenes")).json()
            vid = await save(
                c, {TIDES: edited(TIDES, name="Deep Coastal Tides", **{"palette.0.hex": "#3060C0"})}
            )
            assert (await publish(c, vid)).status_code == 200
            after = (await c.get("/api/canopy/scenes")).json()
            panel = (await c.get("/api/kiosk/panel")).json()
        await w.close()
        return before, after, panel

    before, after, panel = run_virtual(main)
    tides = next(s for s in after["scenes"] if s["id"] == TIDES)
    on_ipad = next(s for s in panel["scenes"] if s["id"] == TIDES)
    assert after["generation"] == before["generation"] + 1 and after["live_id"] == "p0001"
    assert [s["id"] for s in after["scenes"]] == [s.id for s in GIT.scenes.scenes]
    assert tides["name"] == on_ipad["name"] == "Deep Coastal Tides" and tides["source"] == "published"
    assert [c["hex"] for c in tides["palette"]] == [c["hex"] for c in on_ipad["palette"]]
    assert tides["palette"][0]["hex"] == "#3060C0"
    assert set(tides) == {
        "id",
        "name",
        "mood",
        "description",
        "brightness",
        "default_solid",
        "palette",
        "source",
    }
    old = next(s for s in before["scenes"] if s["id"] == TIDES)
    assert old["name"] == GIT.scenes.get(TIDES).name and old["source"] == "approved"


# ---- B1-5: the no-data-folder row and Undo's checksum ---------------------------------------------------------------
def test_a_runtime_with_no_data_folder_refuses_before_any_confirm(no_threads):
    async def main():
        w = await show()
        w.looks.path = None
        async with client_of(w) as c:
            vid = await save(c, {TIDES: brighter(TIDES)})
            r = await ask(c, vid)
        refused = [e for e in w.events("lab") if e.get("action") == "publish_refused"]
        out = r.status_code, r.json(), w.overlay.exists(), w.looks.generation, refused
        await w.close()
        return out

    status, body, written, generation, refused = run_virtual(main)
    assert (status, body) == (409, {"detail": PUB.NO_DATA})  # never a confirm that can't land
    assert not written and generation == 0 and refused == []


def test_undo_of_a_hand_edited_stored_previous_is_refused_by_its_checksum(no_threads):
    async def main():
        w = await show()
        async with client_of(w) as c:
            v1 = await save(c, {TIDES: brighter(TIDES)})
            assert (await publish(c, v1)).status_code == 200
            v2 = await save(c, {CALM: brighter(CALM)})
            assert (await publish(c, v2)).status_code == 200
        data = w.data
        w.owned = False
        await w.close()
        doc = yaml.safe_load(w.overlay.read_text())
        golden = next(s for s in doc["previous"]["trio"]["scenes"]["scenes"] if s["id"] == GOLDEN)
        golden["description"] = (
            "Edited by hand, checksum kept."  # passes validate_trio; only the checksum says so
        )
        write_document(data, doc)
        w2 = await show(data=data)
        loaded = w2.looks.source, w2.looks.problem  # `previous` is not checked at load: Undo checks it
        before, generation = w2.overlay.read_bytes(), w2.looks.generation
        async with client_of(w2) as c:
            r = await ask(c, "previous")
        out = (
            loaded,
            r.status_code,
            r.json(),
            w2.overlay.read_bytes() == before,
            w2.looks.generation == generation,
        )
        await w2.close()
        shutil.rmtree(data, ignore_errors=True)
        return out

    loaded, status, body, same_file, same_generation = run_virtual(main)
    assert loaded == ("published", None)
    assert status == 422 and "don't match their checksum" in body["detail"]
    assert body["problems"][0]["message"] == "trio_sha" and same_file and same_generation


def test_undo_of_a_stored_previous_the_scene_rules_refuse_is_422(no_threads):
    """B1-4: a stored previous trio (checksum intact) that only validate_trio's blocking scene rules refuse."""

    async def main():
        w = await show()
        async with client_of(w) as c:
            v1 = await save(c, {TIDES: brighter(TIDES)})
            assert (await publish(c, v1)).status_code == 200
            v2 = await save(c, {CALM: brighter(CALM)})
            assert (await publish(c, v2)).status_code == 200
            prev = w.looks.file.previous
            trio = copy.deepcopy(prev.trio)
            trio["modes"]["modes"][GOLDEN]["techniques"]["canvas"]["brightness"] = 37
            prev.trio, prev.trio_sha = trio, L._sha(trio)  # as a restart would load it: the checksum matches
            before, gen, live = w.overlay.read_bytes(), w.looks.generation, L.trio_sha(w.looks.trio)
            r = await ask(c, "previous")
        out = (
            r.status_code,
            r.json(),
            w.overlay.read_bytes() == before,
            w.looks.generation == gen,
            L.trio_sha(w.looks.trio) == live,
        )
        await w.close()
        return out

    status, body, same_file, same_gen, same_trio = run_virtual(main)
    assert status == 422, body
    assert any("isn't the scene's brightness" in p["message"] for p in body["problems"]), body
    assert same_file and same_gen and same_trio


# ---- B1-3: the kiosk's sampled module colours follow the live recipes ------------------------------------------------
GOODNIGHT = "goodnight-glow"
SAMPLED = (CALM, TIDES, GOODNIGHT)  # the modules-first scenes whose module level samples a design


def _sample(trio, mode_id: str) -> list[str]:
    """The module-level design at each kiosk position, worked out here (not through kiosk._sampled_colours)."""
    from conductor.config import DeviceEntry
    from conductor.looks import CompileContext, module_look
    from conductor.routes import kiosk as K

    ctx = CompileContext(registry=[], capabilities={}, modes=trio.modes, recipes=trio.recipes)
    mode = trio.modes.modes[mode_id]
    return [
        K._rgb_hex(
            module_look(
                mode, DeviceEntry(id=f"k-{p}", role="canopy_module", account="kiosk", module=p), ctx
            ).rgb
        )
        for p in K.GRID
    ]


def test_a_published_palette_reaches_the_kiosks_sampled_module_colours(no_threads):
    from conductor.routes import kiosk as K

    async def main():
        w = await show()
        async with client_of(w) as c:
            vid = await save(c, {GOODNIGHT: edited(GOODNIGHT, **{"palette.0.hex": "#FF0000"})})
            assert (await publish(c, vid)).status_code == 200
            await w.engine.wait_idle()
            live = w.looks.trio
            static = {m["id"]: m for m in K._modes_or_503(w.services)}
            engine = w.services.extras.pop(K.ENGINE_KEY)  # no plan to override the static colours
            try:
                routed = {m["id"]: m for m in (await c.get("/api/kiosk/modes")).json()}
            finally:
                w.services.extras[K.ENGINE_KEY] = engine
        await w.close()
        return live, static, routed

    live, static, routed = run_virtual(main)
    expected = _sample(live, GOODNIGHT)
    assert expected != _sample(GIT, GOODNIGHT)
    assert static[GOODNIGHT]["module_colours"] == expected
    assert routed[GOODNIGHT]["module_colours"] == expected


def test_the_kiosks_sampled_colours_never_need_the_git_defaults(no_threads, monkeypatch):
    """The broad except in kiosk._sampled_colours must not hide a reader that falls back on the git defaults."""
    from conductor.looks import compile as C
    from conductor.routes import kiosk as K

    async def main():
        w = await show()
        async with client_of(w) as c:
            vid = await save(c, {GOODNIGHT: edited(GOODNIGHT, **{"palette.0.hex": "#FF0000"})})
            assert (await publish(c, vid)).status_code == 200
            await w.engine.wait_idle()

            def boom(*_a, **_k):
                raise RuntimeError("a live reader used the git defaults")

            monkeypatch.setattr(C, "_default_modes", boom)
            monkeypatch.setattr(C, "_default_recipes", boom)
            static = {m["id"]: m for m in K._modes_or_503(w.services)}
        await w.close()
        return static

    static = run_virtual(main)
    assert all(static[m]["module_colours"] is not None for m in SAMPLED), {
        m: static[m]["module_colours"] for m in SAMPLED
    }
