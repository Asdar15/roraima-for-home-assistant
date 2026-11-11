"""State changes of the shared parts, grouped: at most one message a second (contract §4.1).

A part sends its state under its key and each announced attribute under `<key>:<attribute>`
(contract §2.5): a change of an attribute alone is a change too.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
import logging
import time
from typing import Any

from aiohttp import ClientError

from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .announce import state_text
from .attributes import attribute_keys, attribute_texts, room_for
from .const import MAX_ATTRIBUTES, PROTOCOL_VERSION

_LOGGER = logging.getLogger(__name__)

MAX_VALUES = 1024
INTERVAL = 1.0


@dataclass(frozen=True, slots=True)
class Followed:
    """A followed entity: its shared row, its part key and the attributes it announced."""

    row_id: str
    key: str
    attributes: tuple[str, ...] = ()


class StateRelay:
    """Listen only to the shared parts and send their new values, the latest values only."""

    def __init__(
        self,
        hass: HomeAssistant,
        send: Callable[[dict[str, Any]], Awaitable[None]],
        spawn: Callable[[Awaitable[None]], None],
        grown: Callable[[], None] | None = None,
    ) -> None:
        """Nothing is followed until track is called.

        `grown` is called when a followed entity gains an attribute it did not announce.
        """
        self.hass = hass
        self._send = send
        self._spawn = spawn
        self._grown = grown
        self._parts: dict[str, Followed] = {}
        self._entities: dict[tuple[str, str], str] = {}
        self._signaled: dict[str, frozenset[str]] = {}
        self._unsubscribe: CALLBACK_TYPE | None = None
        self._pending: dict[str, dict[str, str]] = {}
        self._timer: CALLBACK_TYPE | None = None
        self._last = 0.0

    @property
    def followed(self) -> int:
        """How many entities are followed."""
        return len(self._parts)

    @callback
    def track(self, parts: Mapping[str, Followed]) -> None:
        """Follow exactly these entities (entity id -> what it announced)."""
        parts = dict(parts)
        if parts == self._parts:
            return
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._parts = parts
        self._entities = {(f.row_id, f.key): entity_id for entity_id, f in parts.items()}
        # An attribute asked for and now announced may be asked for again if it goes and comes back.
        self._signaled = {
            e: keys - set(parts[e].attributes) for e, keys in self._signaled.items() if e in parts
        }
        kept: set[tuple[str, str]] = set()
        for followed in parts.values():
            kept.add((followed.row_id, followed.key))
            kept.update((followed.row_id, f"{followed.key}:{a}") for a in followed.attributes)
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
    def recheck(self, row_id: str, announced: Iterable[dict[str, Any]]) -> None:
        """After an announce: send again any value that changed since the message was built."""
        for part in announced:
            entity_id = self._entities.get((row_id, part["key"]))
            if entity_id is None:
                continue
            state = self.hass.states.get(entity_id)
            text = state_text(state)
            if text != part.get("state"):
                self._queue(row_id, part["key"], text)
            sent = part.get("attributes") or {}
            for attribute, value in attribute_texts(state, self._parts[entity_id].attributes).items():
                if value != (sent.get(attribute) or {}).get("state"):
                    self._queue(row_id, f"{part['key']}:{attribute}", value)

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
        entity_id = event.data["entity_id"]
        new, old = event.data["new_state"], event.data["old_state"]
        followed = self._parts.get(entity_id)
        if followed is None or new is None:
            return
        if old is None or old.state != new.state:
            self._queue(followed.row_id, followed.key, state_text(new))
        if followed.attributes:
            before = attribute_texts(old, followed.attributes) if old is not None else {}
            for attribute, value in attribute_texts(new, followed.attributes).items():
                if before.get(attribute) != value:
                    self._queue(followed.row_id, f"{followed.key}:{attribute}", value)
        self._check_growth(entity_id, followed, new)

    @callback
    def _check_growth(self, entity_id: str, followed: Followed, new: State) -> None:
        """An attribute appeared that the part did not announce: ask for a new announce, once."""
        if self._grown is None or len(followed.attributes) >= MAX_ATTRIBUTES:
            return
        fresh = frozenset(
            attribute_keys(new, followed.attributes, room_for(followed.key))
        ) - set(followed.attributes)
        signaled = self._signaled.get(entity_id, frozenset())
        if not fresh or fresh <= signaled:
            return
        self._signaled[entity_id] = signaled | fresh
        self._grown()

    @callback
    def _queue(self, row_id: str, key: str, text: str) -> None:
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
