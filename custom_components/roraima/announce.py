"""The announce message of one shared row: identity, names and parts, nothing else."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
import logging
from typing import Any

from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from .actions import Descriptions, async_descriptions, field_configs, field_info, part_actions
from .attributes import attribute_keys, attribute_texts
from .const import PROTOCOL_VERSION, TEXT_MAX
from .ids import device_row_id
from .names import Names, async_names
from .selection import Row

_LOGGER = logging.getLogger(__name__)

# What a part announced: each action with the keys of its fields.
type PartActions = dict[str, frozenset[str]]


@dataclass(frozen=True, slots=True)
class Catalog:
    """What Home Assistant says about its services and names, read once per announce round."""

    descriptions: Descriptions
    names: Names


async def async_catalog(hass: HomeAssistant, rows: Iterable[Row]) -> Catalog:
    """Read the service descriptions and the translations the parts of these rows need."""
    entries = [entry for row in rows for entry in row.parts.values()]
    return Catalog(await async_descriptions(hass), await async_names(hass, entries))


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)[:TEXT_MAX]


def state_text(state: State | None) -> str:
    """The state of a part as Roraima receives it."""
    return str(state.state)[:TEXT_MAX] if state is not None else "unavailable"


def _action_info(
    catalog: Catalog, entry: er.RegistryEntry, actions: Mapping[str, Any]
) -> dict[str, Any]:
    services = catalog.descriptions.get(entry.domain) or {}
    info: dict[str, Any] = {}
    for service, fields in actions.items():
        description = services.get(service) or {}
        configs = field_configs(description)
        info[service] = {
            "name": catalog.names.action_name(entry.domain, service, description),
            "fields": [
                field_info(
                    field,
                    catalog.names.field_name(
                        entry.domain, service, field.key, configs.get(field.key, {})
                    ),
                )
                for field in fields.values()
            ],
        }
    return info


def _part(
    hass: HomeAssistant,
    catalog: Catalog,
    key: str,
    entry: er.RegistryEntry,
    kept: Collection[str],
) -> dict[str, Any]:
    state = hass.states.get(entry.entity_id)
    name = state.name if state is not None else None
    device_class = entry.device_class or entry.original_device_class
    if device_class is None and state is not None:
        device_class = state.attributes.get("device_class")
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) if state is not None else None
    actions = part_actions(catalog.descriptions, entry, state)
    keys = attribute_keys(state, kept)
    texts = attribute_texts(state, keys)
    return {
        "key": key,
        "name": _text(name or entry.name or entry.original_name or entry.entity_id),
        "domain": entry.domain,
        "category": entry.entity_category.value if entry.entity_category else None,
        "device_class": _text(device_class),
        "unit": _text(unit or entry.unit_of_measurement),
        "actions": list(actions),
        "action_info": _action_info(catalog, entry, actions),
        "attributes": {
            attribute: {
                "name": catalog.names.attribute_name(entry, state, attribute),
                "state": texts[attribute],
            }
            for attribute in keys
        },
        "state": state_text(state),
    }


def build_announce(
    hass: HomeAssistant,
    catalog: Catalog,
    row: Row,
    max_bytes: int,
    kept: Mapping[str, Collection[str]] | None = None,
) -> dict[str, Any]:
    """Return the announce message of one row, kept under the message size limit.

    `kept` holds the attributes each part announced last time, which stay while they have no value.
    """
    kept = kept or {}
    device = row.device
    via = None
    if device is not None and device.via_device_id:
        via = device_row_id(device.via_device_id)
    parts = [
        _part(hass, catalog, key, entry, kept.get(key, ())) for key, entry in row.parts.items()
    ]
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
    _fit(message, max_bytes)
    return message


def _size(value: Any) -> int:
    # The same encoder the WebSocket uses: ASCII only, so characters are bytes.
    return len(json.dumps(value))


def _strip_fields(part: dict[str, Any]) -> bool:
    stripped = False
    for info in part["action_info"].values():
        if info["fields"]:
            info["fields"] = []
            stripped = True
    return stripped


def _strip_attributes(part: dict[str, Any]) -> bool:
    if not part["attributes"]:
        return False
    part["attributes"] = {}
    return True


def _fit(message: dict[str, Any], max_bytes: int) -> None:
    """Shrink an announce over the limit: first the fields of the actions, then the attributes,
    then the parts, always from the last part; each step is written in the log."""
    device = message["device"]
    parts: list[dict[str, Any]] = device["parts"]
    base = _size({**message, "device": {**device, "parts": []}})
    sizes = [_size(part) for part in parts]

    def over() -> bool:
        # ", " joins the parts of the list.
        return base + sum(sizes) + 2 * max(len(sizes) - 1, 0) > max_bytes

    if not over():
        return
    for what, strip in (
        ("the fields of the actions", _strip_fields),
        ("the attributes", _strip_attributes),
    ):
        count = 0
        for index in range(len(parts) - 1, -1, -1):
            if not over():
                break
            if strip(parts[index]):
                sizes[index] = _size(parts[index])
                count += 1
        if count:
            _LOGGER.warning(
                "Announce of %s over %s bytes: leaving out %s of %s parts",
                device["id"],
                max_bytes,
                what,
                count,
            )
    while parts and over():
        dropped = parts.pop()
        sizes.pop()
        _LOGGER.warning(
            "Announce of %s over %s bytes: leaving out part %s",
            device["id"],
            max_bytes,
            dropped["key"],
        )


def announced_actions(message: dict[str, Any]) -> dict[str, PartActions]:
    """Part key -> action -> keys of its announced fields, as Roraima received them."""
    announced: dict[str, PartActions] = {}
    for part in message["device"]["parts"]:
        info = part["action_info"]
        announced[part["key"]] = {
            action: frozenset(field["key"] for field in info[action]["fields"])
            for action in part["actions"]
        }
    return announced


def announced_attributes(message: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """Part key -> its announced attributes."""
    return {part["key"]: tuple(part["attributes"]) for part in message["device"]["parts"]}


def fingerprint(message: dict[str, Any]) -> str:
    """Return a digest of an announce without the states, to see when it really changed."""
    device = dict(message["device"])
    device["parts"] = [
        {
            **{k: v for k, v in part.items() if k not in ("state", "attributes")},
            "attributes": {a: v["name"] for a, v in part["attributes"].items()},
        }
        for part in device["parts"]
    ]
    return hashlib.sha256(json.dumps(device, sort_keys=True).encode()).hexdigest()
