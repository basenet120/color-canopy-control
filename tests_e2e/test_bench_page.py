"""/ops/bench, /ops/bench/clock/ and /ops/accounts in a real browser at phone size (390×844), on the simulator.

The Done-means for P3 is "Bench Gates A–C can be run from /ops/bench on a phone": Gate A to PASS, Gate B answered
Yes, Apply flags disabled for a SIMULATED run, Copy anonymized JSON (no emails, no MACs), and the filming clock
(HH:MM:SS.mmm, flashing on every bench write). Fixture identity only (bench@example.invalid, 07 §9).

Screenshots: docs/screenshots/bench-phone.png, bench-result-phone.png, clock-phone.png, accounts-phone.png.
"""

from __future__ import annotations

import json
import re
import urllib.request
from collections.abc import Iterator

import pytest
from conftest import AUTH_HEADER, PHONE, _serve

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
MAC = re.compile(r"\b[0-9a-fA-F]{2}([:-])(?:[0-9a-fA-F]{2}\1){4}[0-9a-fA-F]{2}\b")
CLOCK = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}$")
FIXTURE_EMAIL = "bench@example.invalid"


@pytest.fixture(scope="module")
def bench_server(tmp_path_factory) -> Iterator[str]:
    """The Conductor on the simulator with the account form turned on (CONDUCTOR_ACCOUNT_CONNECT=1) and no canopy
    engine (the bench page doesn't need one)."""
    with _serve(
        "conductor.app:app",
        tmp_path_factory.mktemp("conductor-bench"),
        CONDUCTOR_ENGINE="off",
        CONDUCTOR_ACCOUNT_CONNECT="1",
    ) as url:
        yield url


@pytest.fixture
def phone(browser, bench_server):
    contexts = []

    def make(**kw):
        ctx = browser.new_context(
            base_url=bench_server,
            viewport=PHONE,
            extra_http_headers=AUTH_HEADER,
            device_scale_factor=1,
            has_touch=True,
            **kw,
        )
        contexts.append(ctx)
        return ctx

    yield make
    for ctx in contexts:
        ctx.close()


@pytest.fixture
def call(bench_server):
    def _call(path: str, method: str = "GET", body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{bench_server}{path}",
            data=data,
            method=method,
            headers={**AUTH_HEADER, "Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=20) as response:
            return json.loads(response.read() or b"null")

    return _call


def _run(page, job: str) -> None:
    """Tap Run on a job card and wait until THIS run (not the previous one) has finished."""
    title = page.locator(f".job-card[data-job='{job}'] h3").inner_text().split(". ", 1)[1]
    page.locator(f".job-card[data-job='{job}'] button.big").click()
    page.wait_for_selector("#run-panel:not([hidden])")
    page.wait_for_function(
        """title => document.querySelector('#run-title').textContent.startsWith(title)
            && document.querySelector('#run-pill').textContent === 'Finished'
            && !document.querySelector('#result').hidden""",
        arg=title,
        timeout=30000,
    )


def test_bench_page_runs_gates_a_and_b_on_a_phone(phone, screenshots, bench_server):
    ctx = phone(permissions=["clipboard-read", "clipboard-write"])
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("/ops/bench/")
    page.wait_for_selector(".job-card[data-job='gate_a']")
    page.wait_for_function("document.querySelector('#live').dataset.state === 'open'")
    assert page.locator("#sim-banner").is_visible()
    assert page.input_value("#device") == "bench-kit"
    # every job is listed, in runbook order, and the two set-up jobs show their tick box outside the Options
    jobs = page.locator(".job-card").evaluate_all("cards => cards.map(c => c.dataset.job)")
    assert jobs[:3] == ["gate_a", "index_order", "gate_b"] and jobs[-1] == "restore"
    assert {"stale_clock_put", "active_slot_rewrite", "rate_ramp", "head_count_boot"} <= set(jobs)
    for job, words in (("gate_b_unplugged", "switched the kit off"), ("head_count_boot", "strings 2 and 3")):
        confirm = page.locator(f".job-card[data-job='{job}'] > label.confirm")
        assert words in confirm.inner_text() and confirm.is_visible()
    # nothing on the page is wider than the phone
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
    page.screenshot(path=str(screenshots / "bench-phone.png"))

    # Gate A → PASS
    _run(page, "gate_a")
    assert page.inner_text("#verdict").lower() == "pass"
    assert page.locator("#run-sim").is_visible()
    assert page.locator("#profile").is_visible()

    # Gate B → needs eyes → Yes → PASS after the answer; Apply flags stays disabled for a simulated run
    _run(page, "gate_b")
    assert page.inner_text("#verdict").lower() == "needs eyes"
    question = page.locator("#questions .question")
    assert "slot 2" in question.locator("p").first.inner_text()
    question.locator("button.yes").click()
    page.wait_for_function("document.querySelector('#confirmed').textContent.includes('PASS')")
    assert question.locator("button.yes").get_attribute("aria-pressed") == "true"
    flags = page.locator("#flags")
    assert flags.is_disabled() and "t2_select" in flags.inner_text()
    assert "simulated run" in page.inner_text("#flags-why")

    # Copy anonymized JSON → the clipboard holds the record with no emails and no MACs
    page.click("#copy")
    page.wait_for_function("document.querySelector('#copy-status').textContent.startsWith('Copied')")
    text = page.evaluate("navigator.clipboard.readText()")
    payload = json.loads(text)
    assert payload["job"] == "gate_b" and payload["result"]["verdict"] == "needs_eyes"
    assert not EMAIL.search(text) and not MAC.search(text)
    assert "sim-bench-kit" not in text  # the driver-level address is a placeholder too

    page.locator("#run-panel").scroll_into_view_if_needed()
    page.evaluate("document.querySelector('#run-panel').scrollIntoView({block: 'start'})")
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
    page.screenshot(path=str(screenshots / "bench-result-phone.png"))
    assert errors == []


def test_clock_page_shows_milliseconds_and_flashes_on_a_bench_write(phone, call, screenshots):
    page = phone().new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("/ops/bench/clock/")
    page.wait_for_function(
        "/^\\d{2}:\\d{2}:\\d{2}\\.\\d{3}$/.test(document.querySelector('#time').textContent)"
    )
    assert CLOCK.match(page.inner_text("#time"))
    page.wait_for_function("document.querySelector('#live').textContent === 'Listening for bench writes'")
    page.wait_for_function("document.querySelector('#offset').textContent.startsWith('Server clock')")
    # record every flash (it lasts 150 ms, too short to poll for)
    page.evaluate(
        """() => {
            window.__flashes = 0;
            const f = document.querySelector('#flash');
            new MutationObserver(() => { if (f.classList.contains('on')) window.__flashes += 1; })
                .observe(f, {attributes: true, attributeFilter: ['class']});
        }"""
    )
    first = page.inner_text("#time")
    run = call("/api/bench/run", "POST", {"job": "restore", "device_id": "bench-kit", "params": {}})
    assert run["simulated"] is True
    page.wait_for_function("window.__flashes >= 1", timeout=15000)
    page.wait_for_function("document.querySelector('#put').textContent.startsWith('PUT sent')", timeout=15000)
    put = page.inner_text("#put")
    assert re.search(r"PUT sent \d{2}:\d{2}:\d{2}\.\d{3} → applied", put), put
    assert page.inner_text("#time") != first  # the digits run
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
    page.screenshot(path=str(screenshots / "clock-phone.png"))
    assert errors == []


def test_accounts_page_on_a_phone_with_the_fixture_identity(phone, call, screenshots):
    page = phone().new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("/ops/accounts/")
    page.wait_for_selector("#form-on:not([hidden])")
    assert page.locator("#sim-note").is_visible()
    page.select_option("#account", "bench")
    page.fill("#email", FIXTURE_EMAIL)
    page.fill("#password", "fixture-password")
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
    page.evaluate("document.querySelector('#signin-h').scrollIntoView({block: 'start'})")
    page.screenshot(path=str(screenshots / "accounts-phone.png"))
    # the simulated sign-in works end to end with the fixture identity, and the form turns itself off
    page.click("#form-on button[type=submit]")
    page.wait_for_load_state()
    page.goto("/ops/accounts/")
    page.wait_for_selector("#form-off:not([hidden])")
    summary = call("/api/accounts")
    assert summary["form_enabled"] is False
    assert errors == []
