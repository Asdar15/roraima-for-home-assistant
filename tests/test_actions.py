"""Law 3 on Home Assistant's own descriptions: what a part may do is what Home Assistant offers for
its entity, in its own domain, and no field can name another target."""

from __future__ import annotations

from typing import Any

import pytest

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from custom_components.roraima.actions import check_params, field_info, part_actions


def _service(*filters: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {"target": {"entity": list(filters)}, "fields": fields}


DESCRIPTIONS: dict[str, dict[str, Any]] = {
    "lock": {
        "unlock": _service(
            {"domain": ["lock"]}, code={"selector": {"text": {}}}, mode={"selector": {"state": {}}}
        ),
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
        "send": _service(
            {"domain": ["lock"]},
            command={"required": True, "selector": {"object": {}}},
            colour={"selector": {"color_rgb": {}}},
            wait={"selector": {"duration": {}}},
            when={"selector": {"datetime": {}}},
            day={"selector": {"date": {}}},
            days={"selector": {"select": {"multiple": True, "options": ["mon", "tue", "wed"]}}},
            tags={"selector": {"select": {"multiple": True, "custom_value": True, "options": ["a"]}}},
            file_path={"selector": {"object": {}}},
            pictures={"selector": {"media": {}}},
        ),
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
    assert list(part_actions(DESCRIPTIONS, lock, plain)) == ["aim", "send", "unlock"]
    door = State("lock.porta", "locked", {"supported_features": 1, "device_class": "door"})
    assert list(part_actions(DESCRIPTIONS, lock, door)) == [
        "aim", "doors_only", "open", "send", "unlock"
    ]


async def test_no_field_names_another_target(hass: HomeAssistant) -> None:
    """Entity, device, area, floor, label, target and integration fields never enter, nor a file
    of the host; a section's fields are fields of the service."""
    actions = part_actions(DESCRIPTIONS, _lock(hass), State("lock.porta", "locked"))
    assert {key: field.kind for key, field in actions["aim"].items()} == {
        "many": "object", "colour": "color", "level": "number",
    }
    assert "join" not in actions
    assert actions["unlock"]["code"].kind == "text" and actions["unlock"]["mode"].kind == "text"


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
    # A whole number too large to be a number refuses the command, it does not break it.
    assert check_params(fields, {"level": 10**400}) is None


@pytest.mark.parametrize(
    "value",
    [
        "http://192.168.1.1/admin",
        "rtsp://camera.local/stream",
        "media-source://media_source/local/x.mp3",
        "media-source:x",
        "file:/etc/passwd",
        "FILE:/etc/passwd",
        " file:/config/secrets.yaml",
        "mailto:host@example.org",
        "Media-Source:local",
        "/config/secrets.yaml",
        " /media/x.mp3",
        "\\\\nas\\share",
        "\\x",
        "Ciao \\ arrivo",
        "C:\\Windows",
        "..",
        "../secrets.yaml",
        "media/../../secrets.yaml",
        "local/..",
        "Ciao:arrivo",
    ],
)
async def test_text_never_names_an_address_or_a_path(hass: HomeAssistant, value: str) -> None:
    """A text field, or a state field carried as text, never opens an address or a path."""
    fields = part_actions(DESCRIPTIONS, _lock(hass), State("lock.porta", "locked"))["unlock"]
    for key in ("code", "mode"):
        assert check_params(fields, {key: value}) is None, key


async def test_words_and_times_are_not_addresses(hass: HomeAssistant) -> None:
    """A colon followed by a space, a time or a plain word is an ordinary text."""
    fields = part_actions(DESCRIPTIONS, _lock(hass), State("lock.porta", "locked"))["unlock"]
    for value in ("Ciao: arrivo", "08:30", "1234", "heat_cool", "Eco 40-60", "Wait..", "3.5"):
        assert check_params(fields, {"code": value, "mode": value}) == {"code": value, "mode": value}


async def test_every_kind_of_field_a_command_can_carry(hass: HomeAssistant) -> None:
    """A colour, a length of time, a moment, a date, some of a few choices and a free object enter
    with their form; a required free object no longer leaves the service home."""
    fields = part_actions(DESCRIPTIONS, _lock(hass), State("lock.porta", "locked"))["send"]
    assert {key: field.kind for key, field in fields.items()} == {
        "command": "object", "colour": "color", "wait": "duration", "when": "datetime",
        "day": "date", "days": "multi", "tags": "object",
    }
    assert fields["command"].required
    info = field_info(fields["days"], "Days")
    assert info["type"] == "multi" and info["options"] == ["mon", "tue", "wed"]
    assert field_info(fields["wait"], "Wait")["unit"] == "s"
    assert check_params(fields, {}) is None
    assert check_params(
        fields,
        {
            "command": '["Power", "VolumeUp"]', "colour": [255, 120, 0], "wait": 3725.5,
            "when": "2026-11-12 08:30:00", "day": "2026-11-12", "days": ["tue", "mon"],
            "tags": "ospiti",
        },
    ) == {
        "command": ["Power", "VolumeUp"], "colour": [255, 120, 0],
        "wait": {"hours": 1, "minutes": 2, "seconds": 5.5},
        "when": "2026-11-12 08:30:00", "day": "2026-11-12", "days": ["tue", "mon"],
        "tags": "ospiti",
    }
    # A free object that is not JSON is the text itself; one that is, the value it reads as.
    assert check_params(fields, {"command": "Play"}) == {"command": "Play"}
    assert check_params(fields, {"command": '{"key": "Play", "repeat": [1, 2]}'}) == {
        "command": {"key": "Play", "repeat": [1, 2]}
    }


@pytest.mark.parametrize(
    "params",
    [
        {"colour": [255, 120]},
        {"colour": [255, 120, 256]},
        {"colour": [255, 120, 0.5]},
        {"colour": [True, 1, 2]},
        {"colour": "#ff7800"},
        {"wait": -1},
        {"wait": 366 * 86400 + 1},
        {"wait": "60"},
        {"wait": float("inf")},
        {"when": "2026-11-12T08:30:00"},
        {"when": "2026-13-12 08:30:00"},
        {"when": "2026-11-12 25:30:00"},
        {"day": "2026-02-30"},
        {"day": "12/11/2026"},
        {"days": []},
        {"days": ["mon", "sun"]},
        {"days": ["mon", "mon"]},
        {"days": "mon"},
        {"days": [1]},
        {"file_path": "x"},
        {"pictures": "x"},
    ],
)
async def test_fields_out_of_their_form_refuse_the_command(hass: HomeAssistant, params: Any) -> None:
    """Each kind has one form; anything else refuses the whole command."""
    fields = part_actions(DESCRIPTIONS, _lock(hass), State("lock.porta", "locked"))["send"]
    assert check_params(fields, {"command": "Play", **params}) is None


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "http://192.168.1.1/admin",
        "/config/secrets.yaml",
        '["Play", "http://192.168.1.1/admin"]',
        '["Play", "/config/secrets.yaml"]',
        '{"url": "media-source:x"}',
        '{"a": {"b": "file:/etc/passwd"}}',
        '{"entity_id": "lock.altra"}',
        '{"data": {"entity_id": "lock.altra"}}',
        '[{"target": "lock.altra"}]',
        '{"/etc/passwd": 1}',
        '[[["too deep"]]]',
        '{"a": {"b": {"c": 1}}}',
        "null",
        '["a", null]',
        "[1e999]",
        "[" + ", ".join(["1"] * 64) + "]",
        '"\u0000"',
        "x" * 256,
    ],
)
async def test_free_object_keeps_form_and_limits(hass: HomeAssistant, text: str) -> None:
    """A free object never carries an address, another target, a value that is not simple, more
    than two levels or more than 64 values."""
    fields = part_actions(DESCRIPTIONS, _lock(hass), State("lock.porta", "locked"))["send"]
    assert check_params(fields, {"command": text}) is None
    assert check_params(fields, {"command": "[" + ", ".join(["1"] * 63) + "]"}) is not None
    # What does not read as JSON is a plain text, on both sides of the connection.
    assert check_params(fields, {"command": "[NaN]"}) == {"command": "[NaN]"}
