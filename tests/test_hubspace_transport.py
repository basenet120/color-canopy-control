r"""Real aiohttp/aioafero transport regressions, with HTTP confined to loopback.

Run from the app folder:
    .venv\Scripts\python.exe -m unittest discover -s tests -p test_hubspace_transport.py -v

Uses the app's installed dependencies and stdlib unittest; pytest is not required.
Dummy tokens never leave this process's loopback server. No saved credentials or
runtime registry overlay are loaded, and no Hubspace endpoint is contacted.
"""

import asyncio
import contextlib
import json
import time
import unittest
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import aiohttp
from aioafero.v1 import AferoBridgeV1

from conductor.clock import RealClock
from conductor.config import load_registry
from conductor.drivers import afero
from conductor.drivers.afero import AferoDriver, _Link
from conductor.drivers.afero_transport import GatedClientSession, purpose_scope
from conductor.drivers.base import Outcome
from conductor.gate import GateSettings, LiveGate, Purpose, RuntimeMode


@asynccontextmanager
async def harness(status=200, body=b"[]", delay=0):
    """Gate normally, then forward each permitted request to a real local server."""
    calls = []
    tasks = set()

    async def handler(reader, writer):
        tasks.add(asyncio.current_task())
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            # Consume the small request body before closing the socket, including
            # on Windows, where unread data can otherwise reset the connection.
            for header in headers.split(b"\r\n")[1:]:
                name, separator, value = header.partition(b":")
                if separator and name.lower() == b"content-length":
                    await reader.readexactly(int(value.strip()))
            calls.append(headers.split(b" ", 1)[0].decode("ascii"))
            writer.write(
                f"HTTP/1.1 {status} Test\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n".encode("ascii")
            )
            await writer.drain()
            if delay:
                await asyncio.sleep(delay)
            writer.write(body)
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            # Expected when a body timeout makes the client abandon its socket.
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    try:
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
        async with aiohttp.ClientSession(trust_env=False) as local:
            async def forward(method, url, kwargs):
                # Always discard the cloud URL after the real gate has checked it.
                # Return a genuine ClientResponse with real read/release behavior.
                return await local.request(method, f"http://127.0.0.1:{port}/", **kwargs)

            async with GatedClientSession(gate, fake_transport=forward) as session:
                bridge = AferoBridgeV1(
                    "test", "dummy-refresh", session,
                    token="dummy-bearer", token_expiration=time.time() + 3600,
                )
                driver = None
                try:
                    driver = AferoDriver(
                        load_registry(), gate, SimpleNamespace(available=False),
                        clock=clock, telemetry=None,
                    )
                    link = _Link(
                        "bench", bridge=bridge, account_id="test-account",
                        purpose=Purpose.BENCH, connected=True,
                    )
                    driver._links["bench"] = link
                    yield driver, link, calls
                finally:
                    if driver is None:
                        await bridge.close()
                    else:
                        await driver.close()
    finally:
        server.close()
        await server.wait_closed()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


class HubspaceTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_pinned_library_reproduces_legacy_response_read_failure(self):
        # Characterize why the old driver's bridge.request(...); response.read()
        # sequence failed even when Hubspace had returned HTTP 200. This exercises
        # the pinned library itself, without mocking response.read or release.
        async with harness(body=b"[]") as (_, link, calls):
            with purpose_scope(Purpose.BENCH):
                response = await asyncio.wait_for(
                    link.bridge.request("get", "https://api2.afero.net/v1/users/me"),
                    timeout=1,
                )
            self.assertIsInstance(response, aiohttp.ClientResponse)
            self.assertEqual(response.status, 200)
            with self.assertRaises(aiohttp.ClientConnectionError):
                await response.read()
            self.assertEqual(calls, ["GET"])

    async def test_discovery_reads_successful_response(self):
        body = json.dumps([
            {"id": "test-light", "typeId": "metadevice.device", "friendlyName": "Bench lights"},
        ]).encode()
        async with harness(body=body) as (driver, link, calls):
            devices = await driver.discover("bench")
            self.assertEqual([device.device_id for device in devices], ["test-light"])
            self.assertEqual(calls, ["GET"])
            self.assertEqual(link.failures, 0)

    async def test_body_timeout_is_bounded(self):
        # Leave time for Windows' first local connection; the body still stalls for five seconds.
        with patch.object(afero, "SEND_TIMEOUT_S", 0.3):
            async with harness(body=b"[]", delay=5) as (driver, link, calls):
                started = time.monotonic()
                reply = await asyncio.wait_for(
                    driver._send(link, Purpose.BENCH, "get", "https://api2.afero.net/v1/users/me"),
                    timeout=1,
                )
                self.assertEqual(reply.outcome, Outcome.TIMEOUT)
                self.assertLess(time.monotonic() - started, 1)
                self.assertEqual(calls, ["GET"])

    async def test_unallowed_device_never_reaches_transport(self):
        async with harness() as (driver, link, calls):
            reply = await driver._send(
                link, Purpose.BENCH, "put",
                "https://api2.afero.net/v1/accounts/test-account/metadevices/other-light/state",
                json={"values": []},
            )
            self.assertEqual(reply.outcome, Outcome.DENIED)
            self.assertEqual(calls, [])


def status_test(status, outcome):
    async def test(self):
        body = b"" if status == 204 else b'{"result":"test"}'
        async with harness(status=status, body=body) as (driver, link, calls):
            reply = await driver._send(
                link, Purpose.BENCH, "put",
                "https://api2.afero.net/v1/accounts/test-account/metadevices/test-light/state",
                json={"values": []},
            )
            self.assertEqual(reply.outcome, outcome)
            self.assertEqual(reply.status, status)
            self.assertEqual(calls, ["PUT"])
            if outcome == Outcome.OK:
                self.assertEqual(reply.body, body)
    return test


for _status, _outcome in [
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
]:
    setattr(HubspaceTransportTests, f"test_status_{_status}_single_send", status_test(_status, _outcome))


if __name__ == "__main__":
    unittest.main()
