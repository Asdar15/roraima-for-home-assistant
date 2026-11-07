"""Opening the outbound connection to Roraima: handshake, hello and welcome."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from enum import StrEnum
import json
import logging
import re
from typing import Any

from aiohttp import (
    ClientError,
    ClientSession,
    ClientWebSocketResponse,
    WSMsgType,
    WSServerHandshakeError,
)

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_integration

from .const import (
    CLOSE_INSTANCE_CONFLICT,
    CLOSE_REVOKED,
    CLOSE_TOO_MANY_MESSAGES,
    DOMAIN,
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    WELCOME_TIMEOUT,
    tunnel_url,
)

_LOGGER = logging.getLogger(__name__)

_VERSION_TEXT = re.compile(r"[^0-9A-Za-z.+_-]")


class Outcome(StrEnum):
    """What to do after a connection ends or fails to open."""

    RETRY = "retry"
    SLOW = "slow"
    REVOKED = "revoked"
    CONFLICT = "conflict"
    REFUSED = "refused"


class LinkError(Exception):
    """The connection could not be opened or was closed by Roraima."""

    def __init__(self, outcome: Outcome, detail: str) -> None:
        super().__init__(detail)
        self.outcome = outcome
        self.detail = detail


def outcome_for_status(status: int) -> Outcome:
    """Map the HTTP answer given before the upgrade (contract §3)."""
    if status == 401:
        return Outcome.REVOKED
    if status in (400, 403):
        return Outcome.REFUSED
    if status == 429:
        return Outcome.SLOW
    return Outcome.RETRY


def outcome_for_close(code: int | None) -> Outcome:
    """Map the close code of the connection (contract §5)."""
    if code == CLOSE_REVOKED:
        return Outcome.REVOKED
    if code == CLOSE_INSTANCE_CONFLICT:
        return Outcome.CONFLICT
    if code == CLOSE_TOO_MANY_MESSAGES:
        return Outcome.SLOW
    return Outcome.RETRY


def _version_text(value: str) -> str:
    return _VERSION_TEXT.sub("", value)[:32] or "0"


async def ws_connect(session: ClientSession, url: str, code: str) -> ClientWebSocketResponse:
    """Open the WebSocket; the code travels only in the Authorization header."""
    return await session.ws_connect(
        url,
        headers={"Authorization": f"Bearer {code}"},
        max_msg_size=MAX_MESSAGE_BYTES,
    )


async def hello_message(hass: HomeAssistant, instance_id: str) -> dict[str, Any]:
    """Return the first message of every connection."""
    integration = await async_get_integration(hass, DOMAIN)
    return {
        "v": PROTOCOL_VERSION,
        "type": "hello",
        "integration_version": _version_text(str(integration.version or "0")),
        "ha_version": _version_text(HA_VERSION),
        "instance_id": instance_id,
    }


async def _wait_welcome(ws: ClientWebSocketResponse) -> dict[str, Any]:
    async with asyncio.timeout(WELCOME_TIMEOUT):
        while True:
            msg = await ws.receive()
            if msg.type is WSMsgType.TEXT:
                with suppress(ValueError):
                    data = json.loads(msg.data)
                    if isinstance(data, dict) and data.get("type") == "welcome":
                        return data
                continue
            if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
                code = ws.close_code
                raise LinkError(outcome_for_close(code), f"closed_{code}")
            if msg.type is WSMsgType.ERROR:
                raise LinkError(Outcome.RETRY, "socket_error")


async def async_open(
    hass: HomeAssistant, code: str, instance_id: str
) -> tuple[ClientWebSocketResponse, dict[str, Any]]:
    """Open the connection, send hello and wait for welcome."""
    session = async_get_clientsession(hass)
    try:
        ws = await ws_connect(session, tunnel_url(), code)
    except WSServerHandshakeError as err:
        raise LinkError(outcome_for_status(err.status), f"http_{err.status}") from err
    except (ClientError, OSError, TimeoutError) as err:
        raise LinkError(Outcome.RETRY, type(err).__name__) from err
    try:
        await ws.send_json(await hello_message(hass, instance_id))
        welcome = await _wait_welcome(ws)
    except TimeoutError as err:
        await ws.close()
        raise LinkError(Outcome.RETRY, "no_welcome") from err
    except (ClientError, OSError) as err:
        await ws.close()
        raise LinkError(Outcome.RETRY, type(err).__name__) from err
    except LinkError:
        await ws.close()
        raise
    return ws, welcome


async def async_revoke(hass: HomeAssistant, code: str, instance_id: str) -> None:
    """Tell Roraima that the seller removed the integration."""
    try:
        async with asyncio.timeout(WELCOME_TIMEOUT * 2):
            ws, _ = await async_open(hass, code, instance_id)
            try:
                await ws.send_json({"v": PROTOCOL_VERSION, "type": "revoke"})
                await ws.receive()
            finally:
                await ws.close()
    except (LinkError, TimeoutError, ClientError, OSError) as err:
        _LOGGER.warning("Could not tell Roraima that the integration was removed: %s", err)
