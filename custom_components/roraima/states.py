"""State changes of the shared parts, grouped: at most one message a second (contract §4.1)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import logging
import time
from typing import Any

from aiohttp import ClientError

from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .announce import state_text
from .const import PROTOCOL_VERSION

_LOGGER = logging.getLogger(__name__)

MAX_VALUES = 1024
INTERVAL = 1.0

type Part = tuple[str, str]


class StateRelay:
    """Listen only to the shared parts and send their new states, the latest values only."""

    def __init__(
        self,
        hass: HomeAssistant,
        send: Callable[[dict[str, Any]], Awaitable[None]],
        spawn: Callable[[Awaitable[None]], None],
    ) -> None:
        """Nothing is followed until track is called."""
        self.hass = hass
        self._send = send
        self._spawn = spawn
        self._parts: dict[str, Part] = {}
        self._entities: dict[Part, str] = {}
        self._unsubscribe: CALLBACK_TYPE | None = None
        self._pending: dict[str, dict[str, str]] = {}
        self._timer: CALLBACK_TYPE | None = None
        self._last = 0.0

    @property
    def followed(self) -> int:
        """How many entities are followed."""
        return len(self._parts)

    @callback
    def track(self, parts: Mapping[str, Part]) -> None:
        """Follow exactly these entities (entity id -> shared device, part key)."""
        parts = dict(parts)
        if parts == self._parts:
            return
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._parts = parts
        self._entities = {part: entity_id for entity_id, part in parts.items()}
        kept = set(parts.values())
        for row_id in list(self._pending):
            values = {k: v for k, v in self._pending[row_id].items() if (row_id, k) in kept}
            if values:
                self._pending[row_id] = values
            else:
                del self._pending[row_id]
        if parts:
            self._unsubscribe = async_track_state_change_event(
                self.hass, list(parts), self._changed
            )

    @callback
    def recheck(self, row_id: str, announced: list[dict[str, Any]]) -> None:
        """After an announce: send again any state that changed since the message was built."""
        for part in announced:
            entity_id = self._entities.get((row_id, part["key"]))
            if entity_id is None:
                continue
            text = state_text(self.hass.states.get(entity_id))
            if text != part.get("state"):
                self._queue((row_id, part["key"]), text)

    @callback
    def stop(self) -> None:
        """The connection is closed: follow nothing, forget what was waiting."""
        self.track({})
        self._pending.clear()
        if self._timer is not None:
            self._timer()
            self._timer = None

    @callback
    def _changed(self, event: Event[EventStateChangedData]) -> None:
        new, old = event.data["new_state"], event.data["old_state"]
        part = self._parts.get(event.data["entity_id"])
        if part is None or new is None or (old is not None and old.state == new.state):
            return
        self._queue(part, state_text(new))

    @callback
    def _queue(self, part: Part, text: str) -> None:
        row_id, key = part
        self._pending.setdefault(row_id, {})[key] = text
        if self._timer is None:
            wait = max(0.0, self._last + INTERVAL - time.monotonic())
            self._timer = async_call_later(self.hass, wait, self._due)

    @callback
    def _due(self, _now: Any) -> None:
        self._timer = None
        self._spawn(self._async_flush())

    async def _async_flush(self) -> None:
        batch: dict[str, dict[str, str]] = {}
        room = MAX_VALUES
        for row_id in list(self._pending):
            values = self._pending[row_id]
            for key in list(values)[:room]:
                batch.setdefault(row_id, {})[key] = values.pop(key)
            room = MAX_VALUES - sum(len(v) for v in batch.values())
            if not values:
                del self._pending[row_id]
            if room <= 0:
                break
        if not batch:
            return
        self._last = time.monotonic()
        try:
            await self._send({"v": PROTOCOL_VERSION, "type": "state", "devices": batch})
        except (ClientError, ConnectionError) as err:
            _LOGGER.debug("States not sent: %s", err)
            return
        if self._pending and self._timer is None:
            self._timer = async_call_later(self.hass, INTERVAL, self._due)
