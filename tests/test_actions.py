"""Law 3 on Home Assistant's own descriptions: what a part may do is what Home Assistant offers for
its entity, in its own domain, and no field can name another target."""

from __future__ import annotations

from typing import Any

import pytest

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from custom_components.roraima.actions import check_params, part_actions


def _service(*filters: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {"target": {"entity": list(filters)}, "fields": fields}


DESCRIPTIONS: dict[str, dict[str, Any]] = {
    "lock": {
        "unlock": _service({"domain": ["lock"]}, code={"selector": {"text": {}}}),
        "open": _service({"domain": ["lock"], "supported_features": [1]}),
        "aim": _service(
            {"domain": ["lock"]},
            entity_id={"selector": {"text": {}}},
            device_id={"selector": {"device": {}}},
            area_id={"selector": {"area": {}}},
            floor_id={"selector": {"floor": {}}},
            label_id={"selector": {"label": {}}},
            target={"selector": {"target": {}}},
            other={"selector": {"entity": {}}},
            entry={"selector": {"config_entry": {}}},
            filename={"selector": {"text": {}}},
            many={"selector": {"text": {"multiple": True}}},
            colour={"selector": {"color_rgb": {}}},
            advanced={"fields": {"level": {"selector": {"number": {"min": 0, "max": 10, "step": 1}}}}},
        ),
        "join": _service(
            {"domain": ["lock"]},
            members={"required": True, "selector": {"entity": {"multiple": True}}},
        ),
        "doors_only": _service({"domain": ["lock"], "device_class": ["door"]}),
        "nuki_only": _service({"domain": ["lock"], "integration": "nuki"}),
        "get_codes": {**_service({"domain": ["lock"]}), "response": {"optional": False}},
        "reload": {"fields": {}},
    },
    "homeassistant": {
        "turn_on": {"target": {}, "fields": {}},
        "restart": {"fields": {}},
    },
}


def _lock(hass: HomeAssistant, **extra: Any) -> er.RegistryEntry:
    return er.async_get(hass).async_get_or_create(
        "lock", "demo", "porta", suggested_object_id="porta", **extra
    )


async def test_only_services_offered_for_the_entity(hass: HomeAssistant) -> None:
    """Target filters decide, as in Home Assistant: features, class, integration; never another
    domain, never a service without a target, never one that only answers or needs another entity."""
    lock = _lock(hass)
    plain = State("lock.porta", "locked", {"supported_features": 0})
    assert list(part_actions(DESCRIPTIONS, lock, plain)) == ["aim", "unlock"]
    door = State("lock.porta", "locked", {"supported_features": 1, "device_class": "door"})
    assert list(part_actions(DESCRIPTIONS, lock, door)) == ["aim", "doors_only", "open", "unlock"]


async def test_no_field_names_another_target(hass: HomeAssistant) -> None:
    """Entity, device, area, floor, label, target and integration fields never enter, nor a file
    of the host or a list; a section's fields are fields of the service."""
    actions = part_actions(DESCRIPTIONS, _lock(hass), State("lock.porta", "locked"))
    assert list(actions["aim"]) == ["level"]
    assert actions["unlock"]["code"].kind == "text"


async def test_service_part_has_no_action(hass: HomeAssistant) -> None:
    """A restart or identify entity is read only, whatever its domain offers."""
    lock = _lock(hass, entity_category=EntityCategory.CONFIG)
    assert part_actions(DESCRIPTIONS, lock, State("lock.porta", "locked")) == {}


@pytest.mark.parametrize(
    "params",
    [
        {"level": 11},
        {"level": "3"},
        {"level": 3, "entity_id": "lock.altra"},
        {"entity_id": "lock.altra"},
        {"other": "lock.altra"},
        {"target": {"entity_id": "lock.altra"}},
        {"filename": "/config/www/x.jpg"},
        ["level"],
    ],
)
async def test_parameters_are_announced_fields_only(hass: HomeAssistant, params: Any) -> None:
    """A parameter that is not a field of the action, or not of its kind, refuses the command."""
    fields = part_actions(DESCRIPTIONS, _lock(hass), State("lock.porta", "locked"))["aim"]
    assert check_params(fields, params) is None
    assert check_params(fields, {"level": 3}) == {"level": 3}
