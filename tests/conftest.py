"""Fixtures for the Roraima integration tests: a fake Roraima end of the connection."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock, patch

from aiohttp import WSMessage, WSMsgType, WSServerHandshakeError
import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

CODE = "rha_" + "ab" * 32


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the integration from custom_components in every test."""
    return


class FakeSocket:
    """The Roraima side of one WebSocket, driven by the test."""

    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self.sent: list[dict[str, Any]] = []
        self.incoming: asyncio.Queue[WSMessage] = asyncio.Queue()
        self.closed = False
        self.close_code: int | None = None

    async def send_json(self, data: dict[str, Any]) -> None:
        if self.closed:
            raise ConnectionResetError
        self.sent.append(data)
        if data.get("type") == "hello":
            if self.server.on_hello == "welcome":
                self.push(
                    {
                        "v": 1,
                        "type": "welcome",
                        "link_id": 42,
                        "time": 0,
                        "limits": {
                            "max_devices": 256,
                            "max_parts": 128,
                            "max_messages_per_second": 10,
                            "burst": 40,
                            "max_message_bytes": 262144,
                        },
                    }
                )
            else:
                self.server_close(self.server.on_hello)
        elif data.get("type") == "revoke":
            self.server_close(4004)

    def push(self, data: dict[str, Any]) -> None:
        self.incoming.put_nowait(WSMessage(WSMsgType.TEXT, json.dumps(data), None))

    def server_close(self, code: int) -> None:
        self.close_code = code
        self.incoming.put_nowait(WSMessage(WSMsgType.CLOSE, code, ""))

    async def receive(self, timeout: float | None = None) -> WSMessage:
        async with asyncio.timeout(timeout):
            return await self.incoming.get()

    async def close(self, code: int = 1000) -> bool:
        if not self.closed:
            self.closed = True
            self.close_code = self.close_code or code
            self.incoming.put_nowait(WSMessage(WSMsgType.CLOSED, None, None))
        return True

    def of_type(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.sent if m.get("type") == kind]


class FakeServer:
    """Replaces the outbound WebSocket: records every opening."""

    def __init__(self) -> None:
        self.status: int | None = None
        self.error: Exception | None = None
        self.on_hello: str | int = "welcome"
        self.calls: list[tuple[str, str]] = []
        self.sockets: list[FakeSocket] = []

    async def connect(self, session: Any, url: str, code: str) -> FakeSocket:
        self.calls.append((url, code))
        if self.error is not None:
            raise self.error
        if self.status is not None:
            raise WSServerHandshakeError(MagicMock(), (), status=self.status)
        socket = FakeSocket(self)
        self.sockets.append(socket)
        return socket


@pytest.fixture
def server():
    """A fake Roraima that welcomes every valid hello."""
    fake = FakeServer()
    with patch("custom_components.roraima.link.ws_connect", fake.connect):
        yield fake


async def wait_until(predicate, timeout: float = 5) -> None:
    """Wait for a condition set by the integration's background tasks."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)
