"""The live Lab's JSON shapes (docs/live-lab/PLAN.md §2.11) against the real routes: every key of the example answers
in tests/fixtures_live_lab/*.json (what the Lab page, stream U, builds against) is in the route's answer, with the same
JSON type wherever the example isn't null. Ops only (the Lab's router guards), same-origin JSON, no-store."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from live_lab import (
    GOLDEN,
    TIDES,
    ask,
    brighter,
    client_of,
    install_no_threads,
    pinker_hour,
    publish,
    save,
    show,
)

from conductor.clock import run_virtual

FIXTURES = Path(__file__).resolve().parent / "fixtures_live_lab"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _kind(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    return type(value).__name__


def shape_problems(example: Any, answer: Any, where: str = "") -> list[str]:
    """Each key of `example` must be in `answer` (recursively; a list's first element stands for all), with the
    same JSON type when the example's value isn't null."""
    out: list[str] = []
    if answer is None:  # every object and list in §2.11 may be null (`| null`); the key itself must be there
        return out
    if isinstance(example, dict):
        if not isinstance(answer, dict):
            return [f"{where or '(top)'}: expected an object, got {_kind(answer)}"]
        for key, value in example.items():
            if key not in answer:
                out.append(f"{where}.{key}: missing")
                continue
            out += shape_problems(value, answer[key], f"{where}.{key}")
    elif isinstance(example, list):
        if not isinstance(answer, list):
            return [f"{where}: expected a list, got {_kind(answer)}"]
        if example and answer:
            out += shape_problems(example[0], answer[0], f"{where}[0]")
    elif example is not None and answer is not None and _kind(example) != _kind(answer):
        out.append(f"{where}: {_kind(answer)} where the example has {_kind(example)}")
    return out


@pytest.fixture
def no_threads(monkeypatch):
    install_no_threads(monkeypatch)


def test_the_fixtures_are_the_planned_shapes():
    """The examples carry every key §2.11 names (so a renamed key fails here, not only in the browser)."""
    live = fixture("live.json")
    assert set(live) == {
        "source", "simulated", "fallback", "problem", "current", "previous", "scenes", "history", "repaint",
        "canopy", "writable",
    }  # fmt: skip
    assert set(live["current"]) >= {
        "id",
        "kind",
        "version",
        "name",
        "published_at",
        "by",
        "changed",
        "git_changed",
        "version_exists",
    }
    assert set(live["scenes"][0]) == {"id", "name", "source", "palette", "mood", "held", "on_ipad"}
    assert set(live["history"][0]) >= {
        "id",
        "kind",
        "version",
        "name",
        "published_at",
        "by",
        "changed",
        "repaint",
        "outcome",
        "version_exists",
    }
    confirm = fixture("confirm_required.json")
    assert set(confirm) == {
        "detail", "confirm_required", "confirm", "cost", "replaces", "changes", "repaint", "ends_trial",
        "show_offer", "notes", "warnings",
    }  # fmt: skip
    assert set(fixture("publish_answer.json")["report"]) == {
        "id",
        "sent",
        "repick",
        "ended_trial",
        "copies",
        "held",
        "repaint",
    }
    assert set(fixture("repaint_info.json")) == {
        "slot", "commissioned", "eligible", "total", "app", "simulated", "available", "sentence",
    }  # fmt: skip


def test_every_answer_has_the_fixtures_shape(no_threads):
    async def main():
        w = await show()
        out = {}
        async with client_of(w) as c:
            await w.engine.request_mode(TIDES, source="ops")
            await w.engine.wait_idle()
            out["live approved"] = (await c.get("/api/lab/live")).json()
            catalog = (await c.get("/api/lab/catalog")).json()
            out["catalog"] = catalog
            out["state"] = (await c.get("/api/lab/state")).json()
            vid = await save(c, {TIDES: brighter(TIDES)})
            asked = await ask(c, vid)
            out["confirm"] = (asked.status_code, asked.json(), dict(asked.headers))
            published = await publish(c, vid, who="Alon")
            out["publish"] = (published.status_code, published.json())
            out["live published"] = (await c.get("/api/lab/live")).json()
            v2 = await save(c, {TIDES: brighter(TIDES), GOLDEN: brighter(GOLDEN)})
            await publish(c, v2)
            out["live twice"] = (await c.get("/api/lab/live")).json()
        await w.close()
        return out

    out = run_virtual(main)
    live = fixture("live.json")
    for key in ("live approved", "live published", "live twice"):
        problems = shape_problems({k: v for k, v in live.items() if out[key][k] is not None}, out[key])
        assert problems == [], (key, problems)
    assert out["live approved"]["current"] is None and out["live approved"]["source"] == "approved"
    assert out["live published"]["current"]["by"] == "Alon"
    assert out["live twice"]["previous"]["id"] == out["live published"]["current"]["id"]
    assert shape_problems(live["previous"], out["live twice"]["previous"]) == []
    status, body, headers = out["confirm"]
    assert status == 409 and shape_problems(fixture("confirm_required.json"), body) == []
    assert headers["cache-control"] == "no-store"
    status, body = out["publish"]
    example = fixture("publish_answer.json")
    assert status == 200 and shape_problems(example, body) == []
    assert shape_problems(live, body["live"]) == []
    scene_example = fixture("catalog_scene.json")
    golden = next(s for s in out["catalog"]["scenes"] if s["id"] == GOLDEN)
    assert shape_problems(scene_example["scene"], golden) == []
    assert shape_problems(fixture("repaint_info.json"), golden["repaint"]) == []
    assert shape_problems(scene_example["show_looks"], out["catalog"]["show_looks"]) == []
    assert shape_problems(scene_example["state_show_looks"], out["state"]["show_looks"]) == []
    assert next(s for s in out["catalog"]["scenes"] if s["id"] == TIDES)["repaint"] is None  # not painted


def test_the_catalog_and_live_say_which_scenes_are_on_the_ipad(app_client):
    """D76: the Lab reads /ops's "Show on the iPad" switches (never sets them): CatalogJSON carries each scene's
    option_of, group and on_ipad, LiveJSON its on_ipad, so "On the iPad now" follows /ops."""
    app_client.app.state.services.settings.kiosk.hidden_scenes = [GOLDEN]  # as the /ops switch does: in place
    catalog = app_client.get("/api/lab/catalog")
    assert catalog.status_code == 200, catalog.text
    scenes = {s["id"]: s for s in catalog.json()["scenes"]}
    assert (scenes[GOLDEN]["on_ipad"], scenes[GOLDEN]["option_of"], scenes[GOLDEN]["group"]) == (
        False,
        None,
        GOLDEN,
    )
    # D77's options group under their base (option_of), every other scene is its own group
    assert all(
        s["on_ipad"] is True and s["group"] == (s["option_of"] or sid)
        for sid, s in scenes.items()
        if sid != GOLDEN
    )
    assert scenes["golden-hour-2"]["option_of"] == scenes["golden-hour-2"]["group"] == GOLDEN
    live = app_client.get("/api/lab/live")
    assert live.status_code == 200, live.text
    on = {s["id"]: s["on_ipad"] for s in live.json()["scenes"]}
    assert on[GOLDEN] is False and all(v is True for k, v in on.items() if k != GOLDEN)
    # the examples the Lab page builds against carry the keys too
    assert {"option_of", "group", "on_ipad"} <= set(fixture("catalog_scene.json")["scene"])
    for name in (
        "live",
        "live_approved",
        "live_published",
        "live_held",
        "live_repainting",
        "live_fallback",
    ):
        assert all(s["on_ipad"] is True for s in fixture(f"{name}.json")["scenes"]), name
    assert all(s["on_ipad"] is True for s in fixture("publish_ok.json")["live"]["scenes"])
    app_client.app.state.services.settings.kiosk.hidden_scenes = []
    assert all(s["on_ipad"] for s in app_client.get("/api/lab/catalog").json()["scenes"])


def test_the_live_routes_are_ops_only_and_same_origin(app_client_auth):
    ops = ("crew", "canopy")
    assert app_client_auth.get("/api/lab/live").status_code == 401
    assert (
        app_client_auth.post(
            "/api/lab/publish", json={"version": "approved", "expected_live": "approved"}
        ).status_code
        == 401
    )
    answer = app_client_auth.get("/api/lab/live", auth=ops)
    assert answer.status_code == 200 and answer.headers["cache-control"] == "no-store"
    cross = app_client_auth.post(
        "/api/lab/publish",
        json={"version": "approved", "expected_live": "approved"},
        auth=ops,
        headers={"Origin": "https://evil.example"},
    )
    assert cross.status_code == 403
    form = app_client_auth.post(
        "/api/lab/publish",
        content="version=approved",
        auth=ops,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert form.status_code in (415, 422)
    # no engine here (CONDUCTOR_ENGINE=off): a publish answers 503 with the Lab's sentence
    none = app_client_auth.post(
        "/api/lab/publish", json={"version": "approved", "expected_live": "approved"}, auth=ops
    )
    assert none.status_code == 503 and "nothing can be published" in none.json()["detail"]


def test_the_lab_pages_fixtures_match_the_real_answers(no_threads):
    """Stream U built the Lab page against its own §2.11 examples (`live_*.json`, `publish_*.json`,
    `catalog_additions.json`, `state_additions.json`; pinned key for key by tests/lab-model.test.mjs): every key the
    page reads is in the real server's answer, with the same JSON type. (`live_repainting.json` and `repaint.json` are
    B2's RepaintJSON: LiveJSON.repaint is null until then, which shape_problems accepts.)"""

    async def main():
        w = await show()
        out = {}
        async with client_of(w) as c:
            await w.engine.request_mode(TIDES, source="ops")
            await w.engine.wait_idle()
            out["live approved"] = (await c.get("/api/lab/live")).json()
            vid = await save(c, {TIDES: brighter(TIDES)})
            out["confirm"] = (await ask(c, vid)).json()
            first = await ask(c, vid)
            out["publish"] = (await publish(c, vid, who="Alon")).json()
            # the same dialog confirmed after someone else published: the conflict
            v2 = await save(c, {TIDES: brighter(TIDES), GOLDEN: pinker_hour()})
            await publish(c, v2, who="B")
            # B2: Pinker Hour runs held while its slots are repainted (the publish answers before the repaint)
            out["live held"] = (await c.get("/api/lab/live")).json()
            out["catalog"] = (await c.get("/api/lab/catalog")).json()
            out["state"] = (await c.get("/api/lab/state")).json()
            for _ in range(40):  # the repaint's quiet window: LiveJSON.repaint is the RepaintJSON in progress
                if w.engine.repaint_quiet:
                    break
                await w.run_for(0.25)
            out["live repainting"] = (await c.get("/api/lab/live")).json()
            # a publish is refused while Pinker Hour's slots are being repainted
            await w.engine.wait_repaint()
            out["live repainted"] = (await c.get("/api/lab/live")).json()
            stale = await ask(c, vid, expected_live="approved", confirm=first.json()["confirm"])
            out["conflict"] = (stale.status_code, stale.json())
        await w.close()
        return out

    out = run_virtual(main)
    for name, key in (
        ("live_approved", "live approved"),
        ("live_published", "live held"),
        ("live_held", "live held"),
    ):
        assert shape_problems(fixture(f"{name}.json"), out[key]) == [], name
    assert any(s["held"] for s in out["live held"]["scenes"]), "the painted Pinker Hour runs held in the sim"
    assert shape_problems(fixture("publish_confirm_required.json"), out["confirm"]) == []
    # `state` in a publish answer is the engine's CanopyState, as in the try-on's answers (the page takes a Lab
    # StateJSON from it only when it has `engine`, so it ignores this one and reads GET /api/lab/state itself)
    ok = {k: v for k, v in fixture("publish_ok.json").items() if k != "state"}
    assert shape_problems(ok, out["publish"]) == []
    assert "engine" not in out["publish"]["state"] and "phase" in out["publish"]["state"]
    assert shape_problems(fixture("publish_report.json"), out["publish"]["report"]) == []
    status, body = out["conflict"]
    assert status == 409 and body.get("conflict") is True, body
    assert shape_problems(fixture("publish_conflict.json"), body) == []
    additions = fixture("catalog_additions.json")
    golden = next(s for s in out["catalog"]["scenes"] if s["id"] == GOLDEN)
    # the layout is a union keyed by `kind` (the example's is radial; Pinker Hour's is linear)
    example = {
        **additions["scene"],
        "live_look": {k: v for k, v in additions["scene"]["live_look"].items() if k != "layout"},
    }
    assert shape_problems(example, golden) == []
    assert golden["live_look"]["layout"]["kind"] == "linear" and golden["live_source"] == "published"
    assert shape_problems(additions["show_looks"], out["catalog"]["show_looks"]) == []
    assert shape_problems(fixture("state_additions.json"), out["state"]) == []
    # B2's RepaintJSON (U's live_repainting.json / repaint.json) against the real one, during and after
    during = out["live repainting"]["repaint"]
    assert during is not None and during["running"] is True and during["scene"] == GOLDEN, during
    assert shape_problems(fixture("live_repainting.json"), out["live repainting"]) == []
    assert shape_problems(fixture("repaint.json"), during) == []
    assert set(fixture("repaint.json")) == set(during)
    after = out["live repainted"]["repaint"]
    assert (
        after["running"] is False
        and after["finished_at"]
        and {m["status"] for m in after["modules"]} == {"applied"}
    )
    assert out["state"]["show_looks"]["repainting"] is True


def test_the_lab_pages_fallback_fixture_matches_the_real_answer(tmp_path, no_threads):
    from live_lab import write_document

    write_document(
        tmp_path, {"format": 1, "current": {"kind": "version"}}
    )  # unusable: the show runs approved

    async def main():
        w = await show(data=tmp_path)
        async with client_of(w) as c:
            live = (await c.get("/api/lab/live")).json()
        await w.close()
        return live

    live = run_virtual(main)
    assert live["fallback"] is True and live["problem"], live
    assert shape_problems(fixture("live_fallback.json"), live) == []
