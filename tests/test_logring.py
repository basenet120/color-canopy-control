"""The /ops log tail's ring handler (conductor/logring.py): bounded, incremental, and never a secret."""

from __future__ import annotations

import logging

import pytest

from conductor import logring, redact


@pytest.fixture
def ring():
    logring.uninstall()
    handler = logring.install(capacity=5)
    yield handler
    logring.uninstall()


def _log():
    return logging.getLogger("conductor.test_logring")


def test_keeps_only_the_last_n_records_in_order(ring):
    for i in range(8):
        _log().warning("line %d", i)
    records = ring.records()
    assert [r["message"] for r in records] == [f"line {i}" for i in range(3, 8)]
    assert [r["seq"] for r in records] == [4, 5, 6, 7, 8] and ring.last_seq == 8
    assert records[-1]["level"] == "WARNING" and records[-1]["logger"] == "conductor.test_logring"
    assert isinstance(records[-1]["t"], float)


def test_since_returns_only_newer_records_and_limit_the_newest(ring):
    _log().warning("a")
    seen = ring.last_seq
    _log().warning("b")
    _log().warning("c")
    assert [r["message"] for r in ring.records(since=seen)] == ["b", "c"]
    assert [r["message"] for r in ring.records(limit=1)] == ["c"]
    assert ring.records(since=ring.last_seq) == []


def test_conductor_info_records_reach_the_ring(ring):
    _log().info("engine started")
    assert ring.records()[-1]["message"] == "engine started"
    logging.getLogger("conductor.test_logring").debug("too chatty")
    assert ring.records()[-1]["message"] == "engine started"


def test_registered_secrets_bearer_tokens_and_enroll_links_never_stored(ring):
    secret = "sentinel-password-4b3a2918"
    redact.add_secret(secret)
    _log().warning("login with %s", secret)
    # a record built without the redacting factory (logging.makeLogRecord) is still scrubbed by the ring
    _log().handle(
        logging.makeLogRecord(
            {
                "name": "conductor.test_logring",
                "levelno": logging.ERROR,
                "levelname": "ERROR",
                "msg": f"Authorization: Bearer abcdefghijklmnop /enroll/tok-{secret} {secret}",
            }
        )
    )
    try:
        raise RuntimeError(f"failed with {secret}")
    except RuntimeError:
        _log().exception("it broke")
    text = repr(ring.records())
    assert secret not in text and "abcdefghijklmnop" not in text
    last = ring.records()[-1]
    assert last["exc"] and "RuntimeError" in last["exc"] and "***" in last["exc"]


def test_install_is_idempotent_and_uninstall_detaches(ring):
    assert logring.install() is ring and logring.installed() is ring
    assert logging.getLogger().handlers.count(ring) == 1
    logring.uninstall()
    assert logring.installed() is None and ring not in logging.getLogger().handlers
    _log().warning("after uninstall")
    assert all(r["message"] != "after uninstall" for r in ring.records())


def test_long_messages_are_clipped(ring):
    _log().warning("x" * 5000)
    assert len(ring.records()[-1]["message"]) == logring.MAX_MESSAGE_CHARS
