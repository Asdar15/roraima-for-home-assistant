"""The announce: names in the language of Home Assistant, simple attributes only, and a message that
shrinks in order (fields, then attributes, then parts) to stay under the limit."""

from __future__ import annotations

import copy
import json
from typing import Any

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.roraima.announce import _fit
from custom_components.roraima.attributes import attribute_keys, attribute_texts

from .conftest import FakeServer, wait_until
from .test_commands import LAMP_PART, _entrance
from .test_connection import _setup


def test_only_simple_attributes_travel() -> None:
    """Numbers, texts and yes/no; never what describes the entity, a list, a secret or an address."""
    state = State(
        "media_player.tv",
        "playing",
        {
            "friendly_name": "TV",
            "supported_features": 1,
            "entity_picture": "/api/media_player_proxy/media_player.tv?token=abc",
            "entity_picture_local": "/api/media_player_proxy/media_player.tv?token=abc",
            "access_token": "abc",
            "source_list": ["HDMI 1", "HDMI 2"],
            "media_content_id": "https://example.org/stream?id=1",
            "Bad Key": 1,
            "not_a_number": float("nan"),
            "media_position_updated_at": object(),
            "media_duration": None,
            "volume_level": 0.4,
            "is_volume_muted": False,
            "media_title": "News",
        },
    )
    assert attribute_keys(state) == ["is_volume_muted", "media_title", "volume_level"]
    assert attribute_texts(state, ["is_volume_muted", "volume_level"]) == {
        "is_volume_muted": "false",
        "volume_level": "0.4",
    }
    # Announced before, now without a value: it stays, as unknown.
    assert "media_duration" in attribute_keys(state, kept=["media_duration"])
    assert attribute_texts(state, ["media_duration"]) == {"media_duration": "unknown"}


async def test_names_in_the_language_of_home_assistant(hass: HomeAssistant, server: FakeServer) -> None:
    """Action, field and attribute names are Home Assistant's own translations."""
    hass.config.language = "it"
    row = _entrance(hass)
    async_mock_service(hass, "light", "turn_on")
    hass.states.async_set(
        "light.ingresso",
        "on",
        {"supported_color_modes": ["brightness"], "supported_features": 0, "brightness": 128},
    )
    entry = await _setup(hass, [row])
    socket = server.sockets[0]
    await wait_until(lambda: socket.of_type("announce_done"))
    part = next(p for p in socket.of_type("announce")[0]["device"]["parts"] if p["key"] == LAMP_PART)
    services = await async_get_translations(hass, "it", "services", ["light"])
    attributes = await async_get_translations(hass, "it", "entity_component", ["light"])
    info = part["action_info"]["turn_on"]
    assert info["name"] == services["component.light.services.turn_on.name"]
    brightness = next(f for f in info["fields"] if f["key"] == "brightness_pct")
    assert brightness["name"] == services["component.light.services.turn_on.fields.brightness_pct.name"]
    assert part["attributes"]["brightness"] == {
        "name": attributes["component.light.entity_component._.state_attributes.brightness.name"],
        "state": "128",
    }
    await hass.config_entries.async_unload(entry.entry_id)


def _part(index: int) -> dict[str, Any]:
    field = {
        "name": "Field", "type": "text", "required": False, "min": None, "max": None,
        "step": None, "unit": None, "options": None,
    }
    return {
        "key": f"light.part_{index}",
        "name": "Light",
        "domain": "light",
        "category": None,
        "device_class": None,
        "unit": None,
        "actions": ["turn_on"],
        "action_info": {
            "turn_on": {"name": "Turn on", "fields": [{"key": f"f_{n}", **field} for n in range(8)]}
        },
        "attributes": {f"a_{n}": {"name": "Attribute", "state": "1"} for n in range(8)},
        "state": "on",
    }


def _message(fields: bool = True, attributes: bool = True) -> dict[str, Any]:
    parts = [_part(index) for index in range(3)]
    for part in parts:
        if not fields:
            part["action_info"]["turn_on"]["fields"] = []
        if not attributes:
            part["attributes"] = {}
    return {
        "v": 1,
        "type": "announce",
        "device": {
            "id": "d" + "0" * 32, "name": "Row", "manufacturer": None, "model": None,
            "model_id": None, "via_device": None, "parts": parts,
        },
    }


def _fitted(limit: int) -> list[dict[str, Any]]:
    message = copy.deepcopy(_message())
    _fit(message, limit)
    assert len(json.dumps(message)) <= limit
    return message["device"]["parts"]


def test_announce_shrinks_fields_then_attributes_then_parts() -> None:
    """From the last part: first the fields of its actions, then its attributes, then the part."""
    full = len(json.dumps(_message()))
    no_fields = len(json.dumps(_message(fields=False)))
    bare = len(json.dumps(_message(fields=False, attributes=False)))
    assert _fitted(full) == _message()["device"]["parts"]

    parts = _fitted(full - 1)
    assert [bool(p["action_info"]["turn_on"]["fields"]) for p in parts] == [True, True, False]
    assert all(p["attributes"] for p in parts)

    parts = _fitted(no_fields)
    assert not any(p["action_info"]["turn_on"]["fields"] for p in parts)
    assert all(p["attributes"] for p in parts) and len(parts) == 3

    parts = _fitted(bare)
    assert len(parts) == 3 and all(p["actions"] == ["turn_on"] and not p["attributes"] for p in parts)

    parts = _fitted(bare - 1)
    assert [p["key"] for p in parts] == ["light.part_0", "light.part_1"]
