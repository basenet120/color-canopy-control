"""Regression checks against real aiohttp + aioafero on a loopback server (no network, dummy credentials).

Found on the real kit at VWBD's workshop (Sep 27 2026): aioafero 9.3.0's `bridge.request()` releases the response
before returning it, so reading the body again raised ClientConnectionError on a 200 and "Show devices" failed with
"Couldn't reach Hubspace". The fake transport in tests/fake_afero.py can't catch this — it has no release behaviour —
so these tests use a real aiohttp ClientResponse. Contributed by VWBD with the fix; adapted to this repo.
"""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace

import aiohttp
import pytest
from aioafero.v1 import AferoBridgeV1

from conductor.clock import RealClock
from conductor.config import load_registry
from conductor.drivers import afero
from conductor.drivers.afero import AferoDriver, _Link
from conductor.drivers.afero_transport import GatedClientSession
from conductor.drivers.base import Outcome
from conductor.gate import GateSettings, LiveGate, Purpose, RuntimeMode


@asynccontextmanager
async def harness(status=200, body=b"[]", delay=0):
    calls = []
    tasks = set()

    async def handler(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        try:
            await reader.readuntil(b"\r\n\r\n")
            head = (
                f"HTTP/1.1 {status} Test\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
            )
            writer.write(head.encode())
            await writer.drain()
            if delay:
                await asyncio.sleep(delay)
            writer.write(body)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    clock = RealClock()
    gate = LiveGate(
        GateSettings(
            mode=RuntimeMode.BENCH,
            live=True,
            live_until=date(2099, 1, 1),
            device_allowlist=frozenset({"test-light"}),
        ),
        writable_devices=lambda: ["test-light"],
        clock=clock,
    )
    async with aiohttp.ClientSession() as local:

        async def forward(method, url, kwargs):
            calls.append(method)
            # A real ClientResponse with real context-manager / release behaviour.
            return await local.request(method, f"http://127.0.0.1:{port}/", **kwargs)

        async with GatedClientSession(gate, fake_transport=forward) as session:
            bridge = AferoBridgeV1(
                "test", "dummy-refresh", session, token="dummy-bearer", token_expiration=time.time() + 3600
            )
            driver = AferoDriver(
                load_registry(), gate, SimpleNamespace(available=False), clock=clock, telemetry=None
            )
            link = _Link(
                "bench", bridge=bridge, account_id="test-account", purpose=Purpose.BENCH, connected=True
            )
            driver._links["bench"] = link
            try:
                yield driver, link, calls
            finally:
                await bridge.close()
    server.close()
    await server.wait_closed()
    if tasks:
        await asyncio.gather(*tasks)


def test_discovery_reads_successful_response():
    async def run():
        body = json.dumps(
            [{"id": "test-light", "typeId": "metadevice.device", "friendlyName": "Bench lights"}]
        ).encode()
        async with harness(body=body) as (driver, link, calls):
            devices = await driver.discover("bench")
            assert [d.device_id for d in devices] == ["test-light"]
            assert calls == ["GET"]
            assert link.failures == 0

    asyncio.run(run())


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (200, Outcome.OK),
        (204, Outcome.OK),
        (400, Outcome.CLIENT_ERROR),
        (401, Outcome.AUTH_STALE),
        (403, Outcome.THROTTLED),
        (404, Outcome.NOT_FOUND),
        (429, Outcome.THROTTLED),
        (500, Outcome.SERVER_ERROR),
        (503, Outcome.THROTTLED),
        (504, Outcome.THROTTLED),
    ],
)
def test_status_and_single_send(status, outcome):
    async def run():
        body = b"" if status == 204 else b'{"result":"test"}'
        async with harness(status=status, body=body) as (driver, link, calls):
            reply = await driver._send(
                link,
                Purpose.BENCH,
                "put",
                "https://api2.afero.net/v1/accounts/test-account/metadevices/test-light/state",
                json={"values": []},
            )
            assert reply.outcome == outcome
            assert reply.status == status
            assert calls == ["PUT"]  # one HTTP attempt: the executor owns retries
            if outcome == Outcome.OK:
                assert reply.body == body

    asyncio.run(run())


def test_body_timeout_is_bounded(monkeypatch):
    monkeypatch.setattr(afero, "SEND_TIMEOUT_S", 0.05)

    async def run():
        async with harness(body=b"[]", delay=0.15) as (driver, link, calls):
            reply = await driver._send(link, Purpose.BENCH, "get", "https://api2.afero.net/v1/users/me")
            assert reply.outcome == Outcome.TIMEOUT
            assert calls == ["GET"]

    asyncio.run(run())


def test_unallowed_device_never_reaches_transport():
    async def run():
        async with harness() as (driver, link, calls):
            reply = await driver._send(
                link,
                Purpose.BENCH,
                "put",
                "https://api2.afero.net/v1/accounts/test-account/metadevices/other-light/state",
                json={"values": []},
            )
            assert reply.outcome == Outcome.DENIED
            assert calls == []

    asyncio.run(run())
