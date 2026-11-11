"""The announce: names in the language of Home Assistant, the attributes taken by their form (a
simple value, a list as one text, a group as one value for each key), and a message that shrinks
in order (fields, then attributes, then parts) to stay under the limit."""

from __future__ import annotations

import copy
import json
from typing import Any

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.roraima.announce import _fit
from custom_components.roraima.attributes import (
    attribute_keys,
    attribute_texts,
    candidate_keys,
    left_home,
    room_for,
    value_text,
)
from custom_components.roraima.const import MAX_ATTRIBUTES
from custom_components.roraima.ids import part_key

from .conftest import FakeServer, wait_until
from .test_commands import LAMP_PART, _entrance
from .test_connection import _setup


# Every name that tells a place, each by one of its pieces between "_".
PLACES = {
    "latitude": 45.07,
    "longitude": 7.68,
    "lat": 45.07,
    "lon": 7.68,
    "home_lng": 7.68,
    "gps_accuracy": 12,
    "location_name": "Casa",
    "ip_address": "192.168.1.20",
    "altitude": 240,
    "elevation": 239,
    "course": 90,
    "heading": 91,
    "accuracy": 5,
    "geo_hash": "u0j2",
    "geocoded": "Via Roma 1",
    "coordinates": "45.07,7.68",
    "thoroughfare": "Via Roma",
    "locality": "Torino",
    "postal_code": "10121",
    "zip": "10121",
    "country": "IT",
    "administrative_area": "Piemonte",
    "areas_of_interest": "Centro",
    "ocean": "none",
    "inland_water": "Po",
    "place_name": "Casa",
    "city": "Torino",
    "street": "Via Roma",
}


def test_only_simple_attributes_travel() -> None:
    """Numbers, texts, yes/no and lists of them; never what describes the entity, a secret, an
    address or a value that is not simple."""
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
            "pictures": ["cover.jpg", "/local/cover.jpg"],
            "forecast": [{"temperature": 20}],
            "media_content_id": "https://example.org/stream?id=1",
            "Bad Key": 1,
            "not_a_number": float("nan"),
            "media_position_updated_at": object(),
            "media_duration": None,
            **PLACES,
            "current_position": 40,
            "volume_level": 0.4,
            "is_volume_muted": False,
            "media_title": "News",
        },
    )
    # "position" is not a place: the current position of a cover travels.
    assert attribute_keys(state) == [
        "current_position", "is_volume_muted", "media_title", "source_list", "volume_level"
    ]
    # A list is one text; one address in it, or one value that is not simple, keeps it all home.
    assert attribute_texts(state, ["source_list", "pictures", "forecast"]) == {
        "source_list": "HDMI 1, HDMI 2", "pictures": "unknown", "forecast": "unknown",
    }
    # A place never travels, even if it was announced by mistake.
    assert attribute_keys(state, kept=list(PLACES)) == attribute_keys(state)
    assert attribute_texts(state, list(PLACES)) == {}
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
        "attributes_left": 0,
        "state": "on",
    }


def _message(fields: bool = True, attributes: bool = True) -> dict[str, Any]:
    parts = [_part(index) for index in range(3)]
    for part in parts:
        if not fields:
            part["action_info"]["turn_on"]["fields"] = []
        if not attributes:
            part["attributes"] = {}
            part["attributes_left"] = 8
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


def test_lists_and_groups_travel_by_their_form() -> None:
    """A list is one text, a group one value for each key under <attribute>.<key>; the rules on
    places, secrets and the home network hold for every key, and anything deeper stays home."""
    state = State(
        "light.lamp",
        "on",
        {
            "rgb_color": (255, 120, 0),
            "effect_list": ["colorloop", "random"],
            "empty": [],
            "energy": {"L1": 230.1, "total": 12, "phases": [1, 2], "nested": {"a": 1}, "note": None},
            "wifi": {"ssid": "Casa", "ip": "192.168.1.9", "ipAddress": "192.168.1.9", "rssi": -60},
            "owner": {"accessToken": "abc", "gpsLatitude": 45.07, "homeCity": "Torino", "name": "x"},
            "odd": {"with space": 1, 7: 2, "ok": True},
        },
    )
    keys = attribute_keys(state)
    assert keys == [
        "effect_list", "empty", "energy.L1", "energy.phases", "energy.total", "odd.ok",
        "owner.name", "rgb_color", "wifi.rssi",
    ]
    assert attribute_texts(state, keys) == {
        "effect_list": "colorloop, random", "empty": "", "energy.L1": "230.1",
        "energy.phases": "1, 2", "energy.total": "12", "odd.ok": "true", "owner.name": "x",
        "rgb_color": "255, 120, 0", "wifi.rssi": "-60",
    }
    # Never, even if they were announced by mistake.
    private = ["wifi.ssid", "wifi.ip", "wifi.ipAddress", "owner.accessToken", "owner.gpsLatitude",
               "owner.homeCity", "energy.nested"]
    assert attribute_keys(state, kept=private) == keys
    assert attribute_texts(state, private) == {"energy.nested": "unknown"}
    # A key of a group announced before stays while it has no value.
    assert "energy.note" in attribute_keys(state, kept=["energy.note"])
    assert attribute_texts(state, ["energy.note"]) == {"energy.note": "unknown"}
    # What stays home for its form is told to the log, by name; what is private is not listed.
    assert left_home(state) == {
        "energy.nested": "a group inside a group",
        "odd.*": "a key of the group is not made of letters, digits and _",
    }


def test_network_names_never_travel() -> None:
    """The addresses of the home network and the name of its Wi-Fi stay home."""
    state = State(
        "sensor.plug",
        "1",
        {"ip": "192.168.1.9", "mac": "aa:bb", "ssid": "Casa", "bssid": "aa", "hostname": "plug",
         "wifi_ssid": "Casa", "zip_code": "10121", "shipping": 3, "macro": "x",
         # A Tasmota group: its keys start with a capital, and an acronym is all in capitals.
         "net": {"Hostname": "plug", "IPAddress": "192.168.1.9", "MACAddress": "aa:bb", "Mac": "aa",
                 "IPAddr": "192.168.1.9", "IPv4Address": "192.168.1.9", "SSId": "Casa",
                 "GPSLatitude": 45.07, "GPSAccuracy": 5, "ZIPCode": "10121", "APIKey": "abc",
                 "RSSI": -60, "LinkCount": 3, "HTTPPort": 80}},
    )
    assert attribute_keys(state) == ["macro", "net.HTTPPort", "net.LinkCount", "net.RSSI", "shipping"]


async def test_network_entities_stay_home_and_addresses_are_no_state(
    hass: HomeAssistant, server: FakeServer
) -> None:
    """An entity whose names tell the home network is not shared: the Wi-Fi or a host anywhere in
    a name, ip and mac only as the last word or before address. A state that is an address or a
    path travels as unknown."""
    owner = MockConfigEntry(domain="demo")
    owner.add_to_hass(hass)
    router = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("demo", "router")}, name="Router"
    )
    registry = er.async_get(hass)
    home = ["router_ssid", "x_external_ip", "x_hostname", "wan_ip_address", "lan_mac_addr", "ap_bssid_5g"]
    shared = ["mac_mini", "vip_lounge_temperature", "ip_camera_motion", "macro_scene_button", "stream"]
    for name in home + shared:
        domain = "switch" if name == "mac_mini" else "sensor"
        registry.async_get_or_create(
            domain, "demo", f"router-{name}", device_id=router.id, suggested_object_id=name
        )
        hass.states.async_set(f"{domain}.{name}", "on" if domain == "switch" else "1")
    hass.states.async_set("sensor.stream", "rtsp://user:secret@192.168.1.7/live")
    entry = await _setup(hass, [f"d{router.id}"])
    socket = server.sockets[0]
    await wait_until(lambda: socket.of_type("announce_done"))
    parts = {p["key"]: p for p in socket.of_type("announce")[0]["device"]["parts"]}
    assert sorted(parts) == sorted(
        part_key("switch" if name == "mac_mini" else "sensor", "demo", f"router-{name}")
        for name in shared
    )
    assert parts[part_key("sensor", "demo", "router-stream")]["state"] == "unknown"
    hass.states.async_set("sensor.stream", "/config/www/snap.jpg?token=abc")
    hass.states.async_set("sensor.vip_lounge_temperature", "2")
    await wait_until(lambda: socket.of_type("state"))
    sent = json.dumps(socket.sent)
    assert "rtsp" not in sent and "snap.jpg" not in sent and "192.168.1.7" not in sent
    await hass.config_entries.async_unload(entry.entry_id)


def test_a_long_list_shows_its_cut() -> None:
    """A list longer than a state ends with the sign of the cut."""
    text = value_text([f"effect_{n}" for n in range(60)])
    assert text is not None and len(text) == 255 and text.endswith("…")
    assert value_text(["a", float("nan")]) is None


def test_quantity_limit_and_key_room() -> None:
    """At most 64 extra values for a part, the first in alphabetical order; a key that would not
    fit a measure of Roraima stays home."""
    state = State("sensor.many", "1", {f"value_{n:03d}": n for n in range(70)})
    candidates = candidate_keys(state)
    assert len(candidates) == 70 and len(attribute_keys(state)) == MAX_ATTRIBUTES == 64
    assert attribute_keys(state) == candidates[:64]
    part = "sensor." + "x" * 57
    state = State("sensor.long", "1", {"a" * 40: {"b" * 22: 1, "c" * 23: 2}})
    assert room_for(part) == 63
    assert attribute_keys(state, room=room_for(part)) == ["a" * 40 + "." + "b" * 22]
    assert left_home(state, room_for(part)) == {"a" * 40 + "." + "c" * 23: "its key is too long"}


async def test_extra_values_over_the_limit_are_counted(
    hass: HomeAssistant, server: FakeServer
) -> None:
    """A part with more extra values than the limit announces the first 64 and how many stay home;
    a key of a group is named after its attribute."""
    row = _entrance(hass)
    hass.states.async_set(
        "sensor.porta_batteria", "87", {**{f"value_{n:03d}": n for n in range(66)}, "cell": {"mv": 3100}}
    )
    entry = await _setup(hass, [row])
    socket = server.sockets[0]
    await wait_until(lambda: socket.of_type("announce_done"))
    parts = {p["key"]: p for p in socket.of_type("announce")[0]["device"]["parts"]}
    battery = next(p for k, p in parts.items() if k.startswith("sensor."))
    assert len(battery["attributes"]) == 64 and battery["attributes_left"] == 3
    assert battery["attributes"]["cell.mv"] == {"name": "cell.mv", "state": "3100"}
    assert parts[LAMP_PART]["attributes_left"] == 0
    await hass.config_entries.async_unload(entry.entry_id)
