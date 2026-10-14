"""Offline-only byte streams. No sockets or HTTP client implementation."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
import json
import time
from typing import Protocol


@dataclass
class MockConnection:
    chunks: tuple[bytes, ...] = field(default=(), repr=False)
    status: int = 200
    detail_code: str | None = None
    retry_after: str | None = None
    live_id: str | None = None
    hold_open: bool = False
    closed: bool = False

    async def read(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            if self.closed:
                return
            await asyncio.sleep(0)
            yield chunk
        while self.hold_open and not self.closed:
            await asyncio.sleep(1)
            yield b":heartbeat\n\n"

    async def close(self):
        self.closed = True


class SSESource(Protocol):
    async def open(self) -> MockConnection: ...


class MockSSESource:
    def __init__(self, connections: list[MockConnection]):
        self.connections = connections
        self.open_calls = 0

    async def open(self) -> MockConnection:
        index = self.open_calls
        self.open_calls += 1
        if index < len(self.connections):
            return self.connections[index]
        return MockConnection(status=404)


def demo_source() -> MockSSESource:
    """Fictional fixture: duplicate, conflict, decimals, and two ID-less events."""
    base = str(time.time_ns() // 1_000_000)
    data = []
    for event_id, amount in [(base + "-0", 10), (base + "-0", 10), (base + "-0", 20), (base + "-1", 1.25), (None, 0.5), (None, 0.5)]:
        payload = {"user": {"id": "mock-private-listener", "nickname": "모의 후원자"}, "amount": amount, "message": "모의 메시지", "time": "2026-10-14T00:00:00Z"}
        prefix = f"id:{event_id}\r\n" if event_id else ""
        data.append(prefix + "event:donation\r\ndata:" + json.dumps(payload, ensure_ascii=False) + "\r\n\r\n")
    wire = (":heartbeat\r\n\r\n" + "".join(data) + 'event:end\r\ndata:{"reason":"LIVE_ENDED"}\r\n\r\n').encode()
    return MockSSESource([MockConnection(tuple(wire[index:index + 13] for index in range(0, len(wire), 13)), live_id="mock-demo-session")])
