"""The connection kept open from this Home Assistant to Roraima."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from datetime import datetime
import json
import logging
import re
import time
from typing import Any

from aiohttp import ClientError, ClientWebSocketResponse, WSMsgType

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .announce import build_announce, fingerprint
from .const import (
    ANNOUNCES_PER_SECOND,
    CONF_CODE,
    CONF_DEVICES,
    CONF_INSTANCE_ID,
    DOMAIN,
    IDLE_TIMEOUT,
    ISSUE_CONFLICT,
    ISSUE_REVOKED,
    MAX_DEVICES,
    MAX_MESSAGE_BYTES,
    MESSAGE_BURST,
    MESSAGES_PER_SECOND,
    PANEL_URL,
    PROTOCOL_VERSION,
    RETRY_FIRST,
    RETRY_MAX,
    RETRY_SLOW,
    TUNNEL_URL,
    tunnel_url,
)
from .link import LinkError, Outcome, async_open, outcome_for_close
from .selection import shareable_rows

_LOGGER = logging.getLogger(__name__)

CMD_ID = re.compile(r"^[0-9a-f]{16}$")
STATUS_CONNECTED = "connected"
STATUS_DISCONNECTED = "disconnected"
STATUS_REVOKED = "revoked"


def update_signal(entry_id: str) -> str:
    """Dispatcher signal sent when the connection status changes."""
    return f"{DOMAIN}_{entry_id}_update"


class RoraimaClient:
    """Keep the connection open, announce the shared rows, answer Roraima."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Prepare the client; nothing is opened until async_start."""
        self.hass = hass
        self.entry = entry
        self.status = STATUS_DISCONNECTED
        self.link_id: int | None = None
        self.limits: dict[str, Any] = {}
        self.connected_at: datetime | None = None
        self.last_error: str | None = None
        self.last_close_code: int | None = None
        self.shared_count = 0
        self.notices: deque[dict[str, Any]] = deque(maxlen=20)
        self._ws: ClientWebSocketResponse | None = None
        self._stopped = False
        self._sent: dict[str, str] = {}
        self._done: list[str] | None = None
        self._parts: dict[str, set[str]] = {}
        self._announce_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._tokens = float(MESSAGE_BURST)
        self._refilled = time.monotonic()
        self._rate = float(MESSAGES_PER_SECOND)
        self._burst = float(MESSAGE_BURST)
        self._max_bytes = MAX_MESSAGE_BYTES
        self._tasks: set[asyncio.Task] = set()
        self._resync = Debouncer(
            hass, _LOGGER, cooldown=5, immediate=False, function=self._async_resync
        )

    @property
    def selected(self) -> list[str]:
        """Row ids the seller ticked, in order, without repeats."""
        return list(dict.fromkeys(self.entry.options.get(CONF_DEVICES, [])))

    @callback
    def async_start(self) -> None:
        """Start the connection and follow registry changes of shared rows."""
        for event in (
            dr.EVENT_DEVICE_REGISTRY_UPDATED,
            er.EVENT_ENTITY_REGISTRY_UPDATED,
        ):
            self.entry.async_on_unload(
                self.hass.bus.async_listen(event, self._registry_changed)
            )
        self.entry.async_create_background_task(
            self.hass, self._async_run(), f"{DOMAIN} connection"
        )

    async def async_stop(self) -> None:
        """Stop for good: unload or removal."""
        self._stopped = True
        self._resync.async_cancel()
        for task in list(self._tasks):
            task.cancel()
        if self._ws is not None:
            await self._ws.close()

    @callback
    def async_selection_changed(self) -> None:
        """The seller changed the selection: announce the difference."""
        self._spawn(self._async_announce(full=False))

    @callback
    def _registry_changed(self, event: Event) -> None:
        if self._ws is not None:
            self._resync.async_schedule_call()

    async def _async_resync(self) -> None:
        await self._async_announce(full=False)

    def _spawn(self, coro) -> None:
        task = self.entry.async_create_background_task(
            self.hass, coro, f"{DOMAIN} announce"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @callback
    def _set_status(self, status: str) -> None:
        self.status = status
        async_dispatcher_send(self.hass, update_signal(self.entry.entry_id))

    async def _async_run(self) -> None:
        delay = RETRY_FIRST
        while not self._stopped:
            outcome, welcomed = await self._async_session()
            if self._stopped:
                return
            if outcome in (Outcome.REVOKED, Outcome.CONFLICT):
                self._stop_with_issue(outcome)
                return
            if outcome is Outcome.REFUSED:
                _LOGGER.error("Roraima refused the connection (%s)", self.last_error)
                return
            if welcomed:
                delay = RETRY_FIRST
            wait = max(RETRY_SLOW, delay) if outcome is Outcome.SLOW else delay
            _LOGGER.debug("Reconnecting in %s s after %s", wait, self.last_error)
            await asyncio.sleep(wait)
            delay = min(delay * 2, RETRY_MAX)

    async def _async_session(self) -> tuple[Outcome, bool]:
        try:
            ws, welcome = await async_open(
                self.hass,
                self.entry.data[CONF_CODE],
                self.entry.data[CONF_INSTANCE_ID],
            )
        except LinkError as err:
            self.last_error = err.detail
            return err.outcome, False
        self._ws = ws
        self._on_welcome(welcome)
        try:
            return await self._async_receive(ws), True
        finally:
            self._ws = None
            for task in list(self._tasks):
                task.cancel()
            await ws.close()
            if self.status == STATUS_CONNECTED:
                self._set_status(STATUS_DISCONNECTED)

    @callback
    def _on_welcome(self, welcome: dict[str, Any]) -> None:
        limits = welcome.get("limits")
        self.limits = limits if isinstance(limits, dict) else {}
        link_id = welcome.get("link_id")
        self.link_id = link_id if isinstance(link_id, int) else None
        self._rate = self._limit("max_messages_per_second", MESSAGES_PER_SECOND)
        self._burst = self._limit("burst", MESSAGE_BURST)
        self._max_bytes = int(self._limit("max_message_bytes", MAX_MESSAGE_BYTES))
        self._tokens = self._burst
        self._refilled = time.monotonic()
        self.connected_at = dt_util.utcnow()
        self.last_error = None
        self.last_close_code = None
        for issue in (ISSUE_REVOKED, ISSUE_CONFLICT):
            ir.async_delete_issue(self.hass, DOMAIN, issue)
        self._set_status(STATUS_CONNECTED)
        self._spawn(self._async_announce(full=True))

    def _limit(self, name: str, ours: float) -> float:
        value = self.limits.get(name)
        if isinstance(value, (int, float)) and 0 < value < ours:
            return float(value)
        return float(ours)

    async def _async_receive(self, ws: ClientWebSocketResponse) -> Outcome:
        while True:
            try:
                msg = await ws.receive(timeout=IDLE_TIMEOUT)
            except TimeoutError:
                self.last_error = "idle_timeout"
                return Outcome.RETRY
            if msg.type is WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    continue
                if isinstance(data, dict) and data.get("v") == PROTOCOL_VERSION:
                    await self._async_handle(data)
                continue
            if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
                break
            if msg.type is WSMsgType.ERROR:
                self.last_error = "socket_error"
                return Outcome.RETRY
        self.last_close_code = ws.close_code
        self.last_error = f"closed_{ws.close_code}"
        return outcome_for_close(ws.close_code)

    async def _async_handle(self, data: dict[str, Any]) -> None:
        kind = data.get("type")
        try:
            if kind == "ping":
                await self._send({"v": PROTOCOL_VERSION, "type": "pong", "t": data.get("t")})
            elif kind == "cmd":
                await self._async_cmd(data)
            elif kind == "notice":
                self._on_notice(data)
            else:
                _LOGGER.debug("Ignoring message of type %s", kind)
        except (ClientError, ConnectionError) as err:
            _LOGGER.debug("Could not answer %s: %s", kind, err)

    @callback
    def _on_notice(self, data: dict[str, Any]) -> None:
        notice = {
            "code": str(data.get("code"))[:64],
            "device": str(data.get("device"))[:64] if data.get("device") else None,
            "detail": data.get("detail") if isinstance(data.get("detail"), dict) else None,
            "at": dt_util.utcnow().isoformat(),
        }
        self.notices.append(notice)
        _LOGGER.warning("Roraima notice: %s", notice)

    async def _async_cmd(self, data: dict[str, Any]) -> None:
        cmd_id = data.get("id")
        if not isinstance(cmd_id, str) or not CMD_ID.fullmatch(cmd_id):
            return
        device = data.get("device")
        parts = self._parts.get(device) if isinstance(device, str) else None
        if parts is None:
            error = "not_shared"
        elif data.get("part") not in parts:
            error = "part_unknown"
        else:
            # Commands on shared parts are not executed yet.
            error = "action_not_allowed"
        await self._send(
            {"v": PROTOCOL_VERSION, "type": "resp", "id": cmd_id, "ok": False, "error": error}
        )

    async def _async_announce(self, full: bool) -> None:
        async with self._announce_lock:
            if self._ws is None:
                return
            rows = shareable_rows(self.hass)
            selected = [row_id for row_id in self.selected if row_id in rows][:MAX_DEVICES]
            if full:
                self._sent.clear()
                self._done = None
            sent_any = False
            try:
                for row_id in selected:
                    message = build_announce(self.hass, rows[row_id], self._max_bytes)
                    self._parts[row_id] = {p["key"] for p in message["device"]["parts"]}
                    digest = fingerprint(message)
                    if self._sent.get(row_id) == digest:
                        continue
                    await self._send(message)
                    self._sent[row_id] = digest
                    sent_any = True
                    await asyncio.sleep(1 / ANNOUNCES_PER_SECOND)
                for row_id in [r for r in self._parts if r not in selected]:
                    del self._parts[row_id]
                    self._sent.pop(row_id, None)
                if sent_any or selected != self._done:
                    await self._send(
                        {"v": PROTOCOL_VERSION, "type": "announce_done", "devices": selected}
                    )
                    self._done = selected
            except (ClientError, ConnectionError) as err:
                _LOGGER.debug("Announce interrupted: %s", err)
                return
            self.shared_count = len(selected)
            async_dispatcher_send(self.hass, update_signal(self.entry.entry_id))

    async def _send(self, message: dict[str, Any]) -> None:
        async with self._send_lock:
            now = time.monotonic()
            self._tokens = min(self._burst, self._tokens + (now - self._refilled) * self._rate)
            self._refilled = now
            if self._tokens < 1:
                await asyncio.sleep((1 - self._tokens) / self._rate)
                self._tokens = 1.0
                self._refilled = time.monotonic()
            self._tokens -= 1
            ws = self._ws
            if ws is None or ws.closed:
                raise ConnectionResetError("connection closed")
            await ws.send_json(message)

    @callback
    def _stop_with_issue(self, outcome: Outcome) -> None:
        issue = ISSUE_REVOKED if outcome is Outcome.REVOKED else ISSUE_CONFLICT
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=issue,
            translation_placeholders={"panel_url": PANEL_URL},
        )
        self._set_status(
            STATUS_REVOKED if outcome is Outcome.REVOKED else STATUS_DISCONNECTED
        )
        self.entry.async_start_reauth(self.hass)

    def diagnostics(self) -> dict[str, Any]:
        """Connection facts without codes, names or addresses."""
        return {
            "status": self.status,
            "link_id": self.link_id,
            "limits": self.limits,
            "connected_at": self.connected_at.isoformat() if self.connected_at else None,
            "last_error": self.last_error,
            "last_close_code": self.last_close_code,
            "test_address_in_use": tunnel_url() != TUNNEL_URL,
            "selected": len(self.selected),
            "shared": self.shared_count,
            "parts_per_device": sorted(len(parts) for parts in self._parts.values()),
            "notices": list(self.notices),
        }
