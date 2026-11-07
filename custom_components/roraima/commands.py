"""Commands from Roraima: only shared parts, only the services of their own domain (law 3).

A command that Roraima sends names a shared device, one of its announced parts, a Roraima
action and its parameters. Everything else is refused, even when it comes from Roraima: a
compromised server can move at most the parts the seller shared, and only the way each domain
allows. A command counts as done only when the entity reaches the state the action should give.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
import logging
import math
from typing import Any

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import Context, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event

from .announce import part_actions, state_text
from .selection import shareable_rows

_LOGGER = logging.getLogger(__name__)

# Seconds for the service call and the expected state together; the contract wants the resp within 10.
COMMAND_BUDGET = 9.0

HVAC_MODES = frozenset({"off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"})
MOVING = frozenset({"opening", "closing"})


@dataclass(frozen=True, slots=True)
class Param:
    """The one parameter an action takes: its name in the cmd, in the service, and its limits."""

    name: str
    field: str
    low: float = 0
    high: float = 0
    whole: bool = True
    choices: frozenset[str] | None = None


BRIGHTNESS = Param("brightness", "brightness_pct", 0, 100)
KELVIN = Param("kelvin", "color_temp_kelvin", 1000, 10000)
POSITION = Param("position", "position", 0, 100)
TEMPERATURE = Param("temperature", "temperature", -50, 150, whole=False)
HVAC_MODE = Param("hvac_mode", "hvac_mode", choices=HVAC_MODES)


def _on_off(domain: str) -> dict[tuple[str, str], tuple[str, Param | None]]:
    return {(domain, action): (action, None) for action in ("turn_on", "turn_off", "toggle")}


# (domain, Roraima action) -> (service of that same domain, its parameter): contract §2.4.
# The service domain is always the entity's own domain: never homeassistant.* nor another one.
SERVICES: dict[tuple[str, str], tuple[str, Param | None]] = {
    **_on_off("switch"),
    **_on_off("input_boolean"),
    **_on_off("fan"),
    **_on_off("light"),
    ("light", "set_brightness"): ("turn_on", BRIGHTNESS),
    ("light", "set_color_temp"): ("turn_on", KELVIN),
    ("lock", "lock"): ("lock", None),
    ("lock", "unlock"): ("unlock", None),
    ("lock", "open"): ("open", None),
    ("cover", "open"): ("open_cover", None),
    ("cover", "close"): ("close_cover", None),
    ("cover", "stop"): ("stop_cover", None),
    ("cover", "set_position"): ("set_cover_position", POSITION),
    ("valve", "open"): ("open_valve", None),
    ("valve", "close"): ("close_valve", None),
    ("valve", "stop"): ("stop_valve", None),
    ("valve", "set_position"): ("set_valve_position", POSITION),
    ("climate", "turn_on"): ("turn_on", None),
    ("climate", "turn_off"): ("turn_off", None),
    ("climate", "set_temperature"): ("set_temperature", TEMPERATURE),
    ("climate", "set_hvac_mode"): ("set_hvac_mode", HVAC_MODE),
    ("button", "press"): ("press", None),
}

COMMAND_DOMAINS = frozenset(domain for domain, _ in SERVICES)


@dataclass(frozen=True, slots=True)
class Target:
    """An accepted command: the entity of this moment, the service and its data."""

    entity_id: str
    domain: str
    action: str
    service: str
    data: dict[str, Any]


def service_data(param: Param | None, params: Any) -> dict[str, Any] | None:
    """Return the service data for the cmd parameters, or None if they are not exactly allowed."""
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return None
    if param is None:
        return {} if not params else None
    if set(params) != {param.name}:
        return None
    value = params[param.name]
    if param.choices is not None:
        return {param.field: value} if isinstance(value, str) and value in param.choices else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if param.whole and value != int(value):
        return None
    if not param.low <= value <= param.high:
        return None
    return {param.field: int(value) if param.whole else float(value)}


def resolve(
    hass: HomeAssistant,
    selected: Collection[str],
    announced: Mapping[str, Collection[str]],
    cmd: Mapping[str, Any],
) -> Target | str:
    """Return what a cmd may do, or the contract code that refuses it."""
    device, part, action = cmd.get("device"), cmd.get("part"), cmd.get("action")
    if not isinstance(device, str) or device not in selected or device not in announced:
        return "not_shared"
    if not isinstance(part, str) or "." not in part:
        return "part_unknown"
    domain = part.split(".", 1)[0]
    if domain not in COMMAND_DOMAINS:
        return "domain_not_allowed"
    if part not in announced[device]:
        return "part_unknown"
    row = shareable_rows(hass).get(device)
    if row is None:
        return "not_shared"
    entry = row.parts.get(part)
    if entry is None:
        return "part_unknown"
    if entry.domain != domain:
        return "domain_not_allowed"
    if not isinstance(action, str) or (domain, action) not in SERVICES:
        return "action_not_allowed"
    if action not in part_actions(entry, hass.states.get(entry.entity_id)):
        return "action_not_allowed"
    service, param = SERVICES[(domain, action)]
    data = service_data(param, cmd.get("params"))
    if data is None:
        return "params_invalid"
    return Target(entry.entity_id, domain, action, service, data)


def expectation(target: Target, before: State) -> Callable[[State], bool] | None:
    """Return the test of the state the action should give, or None if it cannot be known."""
    domain, action, data = target.domain, target.action, target.data
    if action == "toggle":
        if before.state not in (STATE_ON, STATE_OFF):
            return None
        wanted = STATE_OFF if before.state == STATE_ON else STATE_ON
        return lambda s: s.state == wanted
    if domain == "lock":
        wanted = {"lock": "locked", "unlock": "unlocked", "open": "open"}[action]
        return lambda s: s.state == wanted
    if domain in ("cover", "valve"):
        if action == "open":
            return lambda s: s.state in ("open", "opening")
        if action == "close":
            return lambda s: s.state in ("closed", "closing")
        if action == "stop":
            return lambda s: s.state not in MOVING and s.state != STATE_UNAVAILABLE
        if data["position"] == 0:
            return lambda s: s.state in ("closed", "closing")
        return lambda s: s.state in ("open", "opening", "closing")
    if domain == "climate":
        if action == "turn_off":
            return lambda s: s.state == "off"
        if action == "turn_on":
            return lambda s: s.state in HVAC_MODES and s.state != "off"
        if action == "set_hvac_mode":
            return lambda s: s.state == data["hvac_mode"]
        wanted_temp = data["temperature"]
        return lambda s: _near(s.attributes.get("temperature"), wanted_temp)
    if domain == "button":
        return lambda s: s.state != before.state and s.state != STATE_UNAVAILABLE
    if action == "turn_off" or data.get("brightness_pct") == 0:
        return lambda s: s.state == STATE_OFF
    return lambda s: s.state == STATE_ON


def _near(value: Any, wanted: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and abs(value - wanted) <= 0.5
    )


def _answer(error: str | None, state: State | None) -> dict[str, Any]:
    answer: dict[str, Any] = {"ok": error is None}
    if error is not None:
        answer["error"] = error
    answer["state"] = state_text(state)
    return answer


async def async_run(hass: HomeAssistant, target: Target) -> dict[str, Any]:
    """Call the domain's service and wait for the expected state: the resp without its id."""
    entity_id = target.entity_id
    before = hass.states.get(entity_id)
    if before is None or before.state == STATE_UNAVAILABLE:
        return _answer("unavailable", before)
    reached = expectation(target, before)
    if reached is None:
        return _answer("unavailable", before)

    seen = asyncio.Event()

    @callback
    def _changed(event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if new is not None and reached(new):
            seen.set()

    unsubscribe = async_track_state_change_event(hass, [entity_id], _changed)
    deadline = asyncio.get_running_loop().time() + COMMAND_BUDGET
    try:
        try:
            async with asyncio.timeout_at(deadline):
                await hass.services.async_call(
                    target.domain,
                    target.service,
                    {"entity_id": entity_id, **target.data},
                    blocking=True,
                    context=Context(),
                )
        except TimeoutError:
            _LOGGER.debug("%s.%s on %s did not return in time", target.domain, target.service, entity_id)
        except Exception as err:  # noqa: BLE001 - any failure of the service is answered, never lost
            _LOGGER.warning(
                "%s.%s on %s failed: %s", target.domain, target.service, entity_id, type(err).__name__
            )
            return _answer("ha_error", hass.states.get(entity_id))
        current = hass.states.get(entity_id)
        if not seen.is_set() and not (current is not None and reached(current)):
            try:
                async with asyncio.timeout_at(deadline):
                    await seen.wait()
            except TimeoutError:
                pass
    finally:
        unsubscribe()
    current = hass.states.get(entity_id)
    done = seen.is_set() or (current is not None and reached(current))
    return _answer(None if done else "state_mismatch", current)
