"""The announce message of one shared row: identity, names and parts, nothing else."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.components.cover import CoverEntityFeature
from homeassistant.components.fan import FanEntityFeature
from homeassistant.components.light import (
    ATTR_SUPPORTED_COLOR_MODES,
    brightness_supported,
    color_temp_supported,
)
from homeassistant.components.lock import LockEntityFeature
from homeassistant.components.valve import ValveEntityFeature
from homeassistant.const import ATTR_SUPPORTED_FEATURES, ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from .const import PROTOCOL_VERSION
from .ids import device_row_id
from .selection import Row

_LOGGER = logging.getLogger(__name__)

TEXT_MAX = 255
HVAC_MODES = frozenset({"off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"})
ON_OFF = ["turn_on", "turn_off", "toggle"]


def _features(entry: er.RegistryEntry, state: State | None) -> int:
    if state is not None and ATTR_SUPPORTED_FEATURES in state.attributes:
        return int(state.attributes[ATTR_SUPPORTED_FEATURES] or 0)
    return int(entry.supported_features or 0)


def _capability(entry: er.RegistryEntry, state: State | None, name: str) -> Any:
    if state is not None and name in state.attributes:
        return state.attributes[name]
    return (entry.capabilities or {}).get(name)


def part_actions(entry: er.RegistryEntry, state: State | None) -> list[str]:
    """Return the Roraima actions this entity really supports (contract §2.4)."""
    domain = entry.domain
    features = _features(entry, state)
    if domain in ("switch", "input_boolean"):
        return list(ON_OFF)
    if domain == "fan":
        actions = []
        if features & FanEntityFeature.TURN_ON:
            actions.append("turn_on")
        if features & FanEntityFeature.TURN_OFF:
            actions.append("turn_off")
        if len(actions) == 2:
            actions.append("toggle")
        return actions
    if domain == "light":
        actions = list(ON_OFF)
        modes = _capability(entry, state, ATTR_SUPPORTED_COLOR_MODES) or []
        if brightness_supported(modes):
            actions.append("set_brightness")
        if color_temp_supported(modes):
            actions.append("set_color_temp")
        return actions
    if domain == "lock":
        actions = ["lock", "unlock"]
        if features & LockEntityFeature.OPEN:
            actions.append("open")
        return actions
    if domain in ("cover", "valve"):
        flags = CoverEntityFeature if domain == "cover" else ValveEntityFeature
        return [
            action
            for action, flag in (
                ("open", flags.OPEN),
                ("close", flags.CLOSE),
                ("stop", flags.STOP),
                ("set_position", flags.SET_POSITION),
            )
            if features & flag
        ]
    if domain == "climate":
        actions = []
        if features & ClimateEntityFeature.TURN_ON:
            actions.append("turn_on")
        if features & ClimateEntityFeature.TURN_OFF:
            actions.append("turn_off")
        if features & ClimateEntityFeature.TARGET_TEMPERATURE:
            actions.append("set_temperature")
        if HVAC_MODES.intersection(_capability(entry, state, "hvac_modes") or []):
            actions.append("set_hvac_mode")
        return actions
    if domain == "button":
        return ["press"]
    return []


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)[:TEXT_MAX]


def state_text(state: State | None) -> str:
    """The state of a part as Roraima receives it."""
    return str(state.state)[:TEXT_MAX] if state is not None else "unavailable"


def _part(hass: HomeAssistant, key: str, entry: er.RegistryEntry) -> dict[str, Any]:
    state = hass.states.get(entry.entity_id)
    name = state.name if state is not None else None
    device_class = entry.device_class or entry.original_device_class
    if device_class is None and state is not None:
        device_class = state.attributes.get("device_class")
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) if state is not None else None
    return {
        "key": key,
        "name": _text(name or entry.name or entry.original_name or entry.entity_id),
        "domain": entry.domain,
        "category": entry.entity_category.value if entry.entity_category else None,
        "device_class": _text(device_class),
        "unit": _text(unit or entry.unit_of_measurement),
        "actions": part_actions(entry, state),
        "state": state_text(state),
    }


def build_announce(hass: HomeAssistant, row: Row, max_bytes: int) -> dict[str, Any]:
    """Return the announce message of one row, kept under the message size limit."""
    device = row.device
    via = None
    if device is not None and device.via_device_id:
        via = device_row_id(device.via_device_id)
    parts = [_part(hass, key, entry) for key, entry in row.parts.items()]
    message: dict[str, Any] = {
        "v": PROTOCOL_VERSION,
        "type": "announce",
        "device": {
            "id": row.row_id,
            "name": _text(
                (device.name_by_user or device.name) if device is not None else None
            )
            or _text(row.label),
            "manufacturer": _text(device.manufacturer) if device is not None else None,
            "model": _text(device.model) if device is not None else None,
            "model_id": _text(device.model_id) if device is not None else None,
            "via_device": via if via != row.row_id else None,
            "parts": parts,
        },
    }
    while parts and len(json.dumps(message)) > max_bytes:
        dropped = parts.pop()
        _LOGGER.warning(
            "Announce of %s over %s bytes: leaving out part %s",
            row.row_id,
            max_bytes,
            dropped["key"],
        )
    return message


def fingerprint(message: dict[str, Any]) -> str:
    """Return a digest of an announce without the states, to see when it really changed."""
    device = dict(message["device"])
    device["parts"] = [
        {k: v for k, v in part.items() if k != "state"} for part in device["parts"]
    ]
    return hashlib.sha256(json.dumps(device, sort_keys=True).encode()).hexdigest()
