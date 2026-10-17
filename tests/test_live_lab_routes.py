"""The live Lab's JSON shapes (docs/live-lab/PLAN.md §2.11) against the real routes: every key of the example answers
in tests/fixtures_live_lab/*.json (what the Lab page, stream U, builds against) is in the route's answer, with the same
JSON type wherever the example isn't null. Ops only (the Lab's router guards), same-origin JSON, no-store."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from live_lab import GOLDEN, TIDES, ask, brighter, client_of, install_no_threads, publish, save, show

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
    assert set(live["scenes"][0]) == {"id", "name", "source", "palette", "mood", "held"}
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
