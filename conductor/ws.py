"""WebSocket push with ticket auth (07 §2). One endpoint, topic subscriptions, JSON messages.

Protocol (all messages are JSON objects with a `type`):

client → server
  {"type": "auth", "ticket": "<from GET /api/ws-ticket>"}     must be the first message, within 5 s
  {"type": "subscribe", "topics": ["canopy", "module:<id>"]}   topics the principal may see (see TOPIC_RULES)
  {"type": "pong"}                                             reply to ping (optional)

server → client
  {"type": "hello", "role": "...", "mode": "sim|bench|live", "simulated": bool, "topics": [...]}
  {"type": "event", "topic": "<topic>", "data": {...}}
  {"type": "ping"}                                              every 20 s
  {"type": "error", "message": "..."}                           e.g. a topic the role can't see

Close codes: 1008 for no/bad/expired ticket, bad Origin, or a first message that isn't auth.

Topics:
  canopy          — canopy mode/tier/module status (kiosk, ops, visualizer)
  ops             — ops dashboard: health, alerts, rate gauges, locks (ops only)
  telemetry       — every telemetry event (ops only)
  bench           — bench job progress (ops only)
  module:<id>     — the single-module page for device <id> (ops; tester with a grant for <id>)
  vignette:<id>   — a vignette page (ops; vignette tablet enrolled for <id>)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from conductor.wsauth import WS_CLOSE_POLICY, Ticket, TicketBook, origin_allowed

log = logging.getLogger(__name__)
AUTH_TIMEOUT_S = 5.0
PING_INTERVAL_S = 20.0
QUEUE_MAX = 200  # per client; a slow client drops its oldest messages instead of growing memory


def topic_allowed(role: str, grants: frozenset[str], topic: str) -> bool:
    if role == "ops":
        return True
    if topic == "canopy":
        return role in ("kiosk", "vignette")
    kind, _, target = topic.partition(":")
    if kind == "module":
        return role == "tester" and target in grants
    if kind == "vignette":
        return role == "vignette" and target in grants
    return False


@dataclass(eq=False)  # identity equality/hash: clients live in a set
class _Client:
    ticket: Ticket
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(QUEUE_MAX))
    topics: set[str] = field(default_factory=set)

    def offer(self, message: dict) -> None:
        if self.queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
        self.queue.put_nowait(message)


class WsHub:
    def __init__(self, tickets: TicketBook, hello: Callable[[], dict] | None = None):
        self.tickets = tickets
        self.hello = hello or (dict)
        self._clients: set[_Client] = set()
        self._snapshots: dict[str, Callable[[], dict]] = {}

    def register_snapshot(self, topic_prefix: str, fn: Callable[[str], dict]) -> None:
        """Called on subscribe so a new client gets the current state immediately (e.g. 'module', 'canopy')."""
        self._snapshots[topic_prefix] = fn

    def publish(self, topic: str, data: dict) -> None:
        message = {"type": "event", "topic": topic, "data": data}
        for client in list(self._clients):
            if topic in client.topics:
                client.offer(message)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def serve(self, websocket) -> None:
        """Run one client connection (Starlette/FastAPI WebSocket)."""
        headers = {k.lower(): v for k, v in websocket.headers.items()}
        if not origin_allowed(headers):
            await websocket.close(code=WS_CLOSE_POLICY)
            return
        await websocket.accept()
        try:
            first = await asyncio.wait_for(websocket.receive_text(), AUTH_TIMEOUT_S)
            msg = json.loads(first)
        except (TimeoutError, ValueError, TypeError):
            await websocket.close(code=WS_CLOSE_POLICY)
            return
        except Exception:  # noqa: BLE001 - client went away during auth
            return
        ticket = (
            self.tickets.redeem(msg.get("ticket"))
            if isinstance(msg, dict) and msg.get("type") == "auth"
            else None
        )
        if ticket is None:
            await websocket.close(code=WS_CLOSE_POLICY)
            return
        client = _Client(ticket)
        self._clients.add(client)
        sender = asyncio.create_task(self._send_loop(websocket, client))
        try:
            await websocket.send_text(json.dumps({"type": "hello", "role": ticket.role, **self.hello()}))
            while True:
                text = await websocket.receive_text()
                try:
                    msg = json.loads(text)
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("type") == "subscribe":
                    self._subscribe(client, msg.get("topics") or [])
        except Exception as e:  # noqa: BLE001 - disconnects surface as various exceptions
            log.debug("websocket closed: %s", type(e).__name__)
        finally:
            self._clients.discard(client)
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sender

    def _subscribe(self, client: _Client, topics: list) -> None:
        for topic in topics:
            if not isinstance(topic, str):
                continue
            if not topic_allowed(client.ticket.role, client.ticket.grants, topic):
                client.offer({"type": "error", "message": f"not allowed to watch {topic}"})
                continue
            client.topics.add(topic)
            snapshot = self._snapshots.get(topic.partition(":")[0])
            if snapshot:
                try:
                    client.offer({"type": "event", "topic": topic, "data": snapshot(topic)})
                except Exception:
                    log.exception("snapshot for %s failed", topic)

    async def _send_loop(self, websocket, client: _Client) -> None:
        while True:
            try:
                message = await asyncio.wait_for(client.queue.get(), PING_INTERVAL_S)
            except TimeoutError:
                message = {"type": "ping"}
            await websocket.send_text(json.dumps(message, default=str))
