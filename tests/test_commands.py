"""Law 3: only ticked devices, only the services Home Assistant offers for their entities, only
announced fields of the right kind, and every call reaches the part's own entity alone."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import math
from typing import Any

import pytest

from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.roraima import commands
from custom_components.roraima.const import DOMAIN
from custom_components.roraima.ids import entity_row_id, part_key
from custom_components.roraima.selection import shareable_rows

from .conftest import FakeServer, FakeSocket, wait_until
from .test_connection import LIGHT_PART, LIGHT_ROW, WASHER_PART, WASHER_ROW, _setup, _test_helpers

LOCK_PART = part_key("lock", "demo", "porta-lock")
LAMP_PART = part_key("light", "demo", "ingresso-light")
BATTERY_PART = part_key("sensor", "demo", "porta-battery")
SCENE_PART = part_key("scene", "demo", "arrivo")
THERMOSTAT_PART = part_key("climate", "demo", "salotto-climate")
PANEL_PAGE = "https://roraima.io/vendor.php?dispatch=iot_cloud_accounts.manage&selected_section=home_assistant"
THERMOSTAT_FEATURES = (
    ClimateEntityFeature.TARGET_TEMPERATURE
    | ClimateEntityFeature.TARGET_HUMIDITY
    | ClimateEntityFeature.TURN_ON
    | ClimateEntityFeature.TURN_OFF
)


@pytest.fixture(autouse=True)
def short_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """A service that hangs is judged in a fraction of a second, not nine."""
    monkeypatch.setattr(commands, "COMMAND_BUDGET", 0.3)


def _entrance(hass: HomeAssistant) -> str:
    """A registry device with a lock, a dimmable light, a battery sensor and a scene."""
    owner = MockConfigEntry(domain="demo")
    owner.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("demo", "ingresso")}, name="Ingresso"
    )
    registry = er.async_get(hass)
    for domain, unique_id, object_id in (
        ("lock", "porta-lock", "porta"),
        ("light", "ingresso-light", "ingresso"),
        ("sensor", "porta-battery", "porta_batteria"),
        ("scene", "arrivo", "arrivo"),
    ):
        registry.async_get_or_create(
            domain, "demo", unique_id, device_id=device.id, suggested_object_id=object_id
        )
    hass.states.async_set("lock.porta", "locked", {"supported_features": 0})
    hass.states.async_set(
        "light.ingresso", "off", {"supported_color_modes": ["brightness"], "supported_features": 0}
    )
    hass.states.async_set("sensor.porta_batteria", "87", {"unit_of_measurement": "%"})
    hass.states.async_set("scene.arrivo", "unknown")
    return f"d{device.id}"


def _thermostat(hass: HomeAssistant) -> str:
    """A thermostat that sets a temperature and a humidity, without preset modes."""
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "climate", "demo", "salotto-climate", suggested_object_id="salotto", original_name="Salotto"
    )
    hass.states.async_set(
        "climate.salotto",
        "heat",
        {
            "supported_features": THERMOSTAT_FEATURES,
            "hvac_modes": ["off", "heat"],
            "current_temperature": 19.5,
            "temperature": 21,
            "hvac_action": "heating",
            "friendly_name": "Salotto",
        },
    )
    return entity_row_id("climate", "demo", "salotto-climate")


def _service(hass: HomeAssistant, domain: str, service: str, new_state: str | None) -> list[ServiceCall]:
    """Register a service that moves the entity to new_state, or leaves it as it is."""
    calls: list[ServiceCall] = []

    async def handler(call: ServiceCall) -> None:
        calls.append(call)
        if new_state is not None:
            entity_id = call.data["entity_id"]
            hass.states.async_set(entity_id, new_state, hass.states.get(entity_id).attributes)

    hass.services.async_register(domain, service, handler)
    return calls


def _forbidden(hass: HomeAssistant) -> list[ServiceCall]:
    """Services no command may ever reach."""
    calls: list[ServiceCall] = []
    for domain, service in (
        ("homeassistant", "turn_on"),
        ("homeassistant", "turn_off"),
        ("homeassistant", "restart"),
        ("homeassistant", "stop"),
        ("scene", "turn_on"),
        ("script", "turn_on"),
        ("automation", "trigger"),
    ):
        calls += async_mock_service(hass, domain, service)
    return calls


async def _ready(hass: HomeAssistant, server: FakeServer, devices: list[str]):
    entry = await _setup(hass, devices)
    socket = server.sockets[0]
    await wait_until(lambda: socket.of_type("announce_done"))
    return entry, socket


async def _command(socket: FakeSocket, **cmd: Any) -> dict[str, Any]:
    cmd_id = f"{len(socket.sent):016x}"
    socket.push({"v": 1, "type": "cmd", "id": cmd_id, "params": {}, **cmd})
    await wait_until(lambda: any(r["id"] == cmd_id for r in socket.of_type("resp")))
    return next(r for r in socket.of_type("resp") if r["id"] == cmd_id)


def _parts(socket: FakeSocket) -> dict[str, dict[str, Any]]:
    return {p["key"]: p for m in socket.of_type("announce") for p in m["device"]["parts"]}


async def test_allowed_action_runs_and_reports_the_state(hass: HomeAssistant, server: FakeServer) -> None:
    """The ticked washer is switched with switch.turn_on, on its own entity, and the resp says on."""
    _test_helpers(hass)
    on = _service(hass, "switch", "turn_on", "on")
    off = _service(hass, "switch", "turn_off", "off")
    forbidden = _forbidden(hass)
    entry, socket = await _ready(hass, server, [WASHER_ROW])
    resp = await _command(socket, device=WASHER_ROW, part=WASHER_PART, action="turn_on")
    assert resp == {"v": 1, "type": "resp", "id": resp["id"], "ok": True, "state": "on"}
    assert [dict(c.data) for c in on] == [{"entity_id": "switch.lavatrice_di_prova_hai"}]
    resp = await _command(socket, device=WASHER_ROW, part=WASHER_PART, action="turn_off")
    assert resp["ok"] is True and resp["state"] == "off"
    assert len(off) == 1 and forbidden == []
    await hass.config_entries.async_unload(entry.entry_id)


async def test_device_not_ticked(hass: HomeAssistant, server: FakeServer) -> None:
    """The light exists but was not ticked: refused, nothing runs, no state leaks."""
    _test_helpers(hass)
    _service(hass, "switch", "turn_on", "on")
    on = _service(hass, "light", "turn_on", "on")
    entry, socket = await _ready(hass, server, [WASHER_ROW])
    resp = await _command(socket, device=LIGHT_ROW, part=LIGHT_PART, action="turn_on")
    assert resp == {"v": 1, "type": "resp", "id": resp["id"], "ok": False, "error": "not_shared"}
    resp = await _command(socket, device=WASHER_ROW, part=LIGHT_PART, action="turn_on")
    assert resp["error"] == "part_unknown" and "state" not in resp
    assert on == []
    await hass.config_entries.async_unload(entry.entry_id)


async def test_scene_is_no_part_and_sensor_has_no_action(hass: HomeAssistant, server: FakeServer) -> None:
    """A scene of the ticked device is never a part; its sensor is read-only."""
    row = _entrance(hass)
    forbidden = _forbidden(hass)
    async_mock_service(hass, "sensor", "turn_on")
    entry, socket = await _ready(hass, server, [row])
    parts = _parts(socket)
    assert SCENE_PART not in parts and parts[BATTERY_PART]["actions"] == []
    for part, action, error in (
        (SCENE_PART, "turn_on", "part_unknown"),
        ("homeassistant.restart", "restart", "part_unknown"),
        (BATTERY_PART, "turn_on", "action_not_allowed"),
    ):
        resp = await _command(socket, device=row, part=part, action=action)
        assert resp["error"] == error, part
    assert forbidden == []
    await hass.config_entries.async_unload(entry.entry_id)


async def test_only_services_offered_for_the_entity(hass: HomeAssistant, server: FakeServer) -> None:
    """A service of another domain, a name with a domain, or a service the lock does not support
    (open without the open feature) is never an action: nothing runs."""
    row = _entrance(hass)
    forbidden = _forbidden(hass)
    unlock = _service(hass, "lock", "unlock", "unlocked")
    opened = async_mock_service(hass, "lock", "open")
    entry, socket = await _ready(hass, server, [row])
    assert _parts(socket)[LOCK_PART]["actions"] == ["unlock"]
    for action, params in (
        ("call_service", {"domain": "homeassistant", "service": "restart"}),
        ("turn_on", {}),
        ("restart", {}),
        ("homeassistant.turn_on", {}),
        ("lock.unlock", {}),
        ("open", {}),
    ):
        resp = await _command(socket, device=row, part=LOCK_PART, action=action, params=params)
        assert resp["error"] == "action_not_allowed", action
    assert forbidden == [] and unlock == [] and opened == []
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    "params",
    [
        {"entity_id": "light.luce_lavanderia_di_prova_hai"},
        {"target": {"entity_id": "light.luce_lavanderia_di_prova_hai"}},
        {"device_id": "0123456789abcdef0123456789abcdef"},
        {"area_id": "cucina"},
        {"floor_id": "terra"},
        {"label_id": "tutto"},
        {"_entity_id": "switch.altro"},
        {"property": "switch.altro"},
    ],
)
async def test_other_target_refused(hass: HomeAssistant, server: FakeServer, params: dict) -> None:
    """A command cannot point at anything but the part it names."""
    _test_helpers(hass)
    on = _service(hass, "switch", "turn_on", "on")
    light = _service(hass, "light", "turn_on", "on")
    entry, socket = await _ready(hass, server, [WASHER_ROW])
    resp = await _command(socket, device=WASHER_ROW, part=WASHER_PART, action="turn_on", params=params)
    assert resp["error"] == "params_invalid"
    assert on == [] and light == []
    await hass.config_entries.async_unload(entry.entry_id)


async def test_parameters_in_their_limits_only(hass: HomeAssistant, server: FakeServer) -> None:
    """Only the announced fields, each of its kind and in its limits; refused, never clamped."""
    row = _entrance(hass)
    on = _service(hass, "light", "turn_on", "on")
    entry, socket = await _ready(hass, server, [row])
    fields = {f["key"]: f for f in _parts(socket)[LAMP_PART]["action_info"]["turn_on"]["fields"]}
    assert fields["brightness_pct"] | {"name": None} == {
        "key": "brightness_pct", "name": None, "type": "number", "required": False,
        "min": 0, "max": 100, "step": 1, "unit": "%", "options": None,
    }
    # The light has no transition and no colour: Home Assistant does not show those fields.
    assert "transition" not in fields and "color_temp_kelvin" not in fields
    for params in (
        {"brightness_pct": 150},
        {"brightness_pct": -1},
        {"brightness_pct": True},
        {"brightness_pct": "50"},
        {"brightness_pct": math.nan},
        {"brightness_pct": None},
        {"brightness_pct": 50, "transition": 0},
        {"color_temp_kelvin": 3000},
        {"profile": "relax\n"},
        {"profile": "x" * 256},
        [50],
    ):
        resp = await _command(socket, device=row, part=LAMP_PART, action="turn_on", params=params)
        assert resp["error"] == "params_invalid", params
    assert on == []
    resp = await _command(
        socket, device=row, part=LAMP_PART, action="turn_on", params={"brightness_pct": 50, "profile": "relax"}
    )
    assert resp["ok"] is True and resp["state"] == "on"
    assert [dict(c.data) for c in on] == [
        {"entity_id": "light.ingresso", "brightness_pct": 50, "profile": "relax"}
    ]
    await hass.config_entries.async_unload(entry.entry_id)


async def test_required_fields_and_choices(hass: HomeAssistant, server: FakeServer) -> None:
    """A required field must be there, a choice must be one of its options, and a service the
    thermostat does not support (no preset modes) is not an action."""
    thermostat = _thermostat(hass)
    lamp = _entrance(hass)
    hass.states.async_set(
        "light.ingresso", "off", {"supported_color_modes": ["brightness"], "supported_features": 8}
    )
    humidity = async_mock_service(hass, "climate", "set_humidity")
    temperature = async_mock_service(hass, "climate", "set_temperature")
    preset = async_mock_service(hass, "climate", "set_preset_mode")
    off = async_mock_service(hass, "light", "turn_off")
    entry, socket = await _ready(hass, server, [thermostat, lamp])
    parts = _parts(socket)
    assert parts[THERMOSTAT_PART]["actions"] == ["set_humidity", "set_temperature"]
    flash = next(f for f in parts[LAMP_PART]["action_info"]["turn_off"]["fields"] if f["key"] == "flash")
    assert flash["type"] == "select" and flash["options"] == ["long", "short"]
    for device, part, action, params, error in (
        (thermostat, THERMOSTAT_PART, "set_humidity", {}, "params_invalid"),
        (thermostat, THERMOSTAT_PART, "set_humidity", {"humidity": 120}, "params_invalid"),
        (thermostat, THERMOSTAT_PART, "set_preset_mode", {"preset_mode": "away"}, "action_not_allowed"),
        (lamp, LAMP_PART, "turn_off", {"flash": "medium"}, "params_invalid"),
    ):
        resp = await _command(socket, device=device, part=part, action=action, params=params)
        assert resp["error"] == error, (action, params)
    assert humidity == [] and temperature == [] and preset == [] and off == []
    resp = await _command(
        socket, device=thermostat, part=THERMOSTAT_PART, action="set_temperature",
        params={"temperature": 21.5},
    )
    assert resp["ok"] is True
    resp = await _command(socket, device=lamp, part=LAMP_PART, action="turn_off", params={"flash": "short"})
    assert resp["ok"] is True
    assert [dict(c.data) for c in temperature + off] == [
        {"entity_id": "climate.salotto", "temperature": 21.5},
        {"entity_id": "light.ingresso", "flash": "short"},
    ]
    await hass.config_entries.async_unload(entry.entry_id)


async def test_service_error_and_slow_service(hass: HomeAssistant, server: FakeServer) -> None:
    """A service that fails, or does not finish in time, is an ha_error carrying the real state."""
    row = _entrance(hass)

    async def unlock(call: ServiceCall) -> None:
        raise HomeAssistantError("jammed")

    async def lock(call: ServiceCall) -> None:
        await asyncio.sleep(5)

    hass.services.async_register("lock", "unlock", unlock)
    hass.services.async_register("lock", "lock", lock)
    entry, socket = await _ready(hass, server, [row])
    for action in ("unlock", "lock"):
        resp = await _command(socket, device=row, part=LOCK_PART, action=action)
        assert resp == {
            "v": 1, "type": "resp", "id": resp["id"], "ok": False, "error": "ha_error", "state": "locked"
        }, action
    await hass.config_entries.async_unload(entry.entry_id)


async def test_unavailable_part_is_not_commanded(hass: HomeAssistant, server: FakeServer) -> None:
    """An entity Home Assistant cannot reach is not commanded at all."""
    _test_helpers(hass)
    on = _service(hass, "switch", "turn_on", "on")
    entry, socket = await _ready(hass, server, [WASHER_ROW])
    hass.states.async_set("switch.lavatrice_di_prova_hai", "unavailable")
    resp = await _command(socket, device=WASHER_ROW, part=WASHER_PART, action="turn_on")
    assert resp["error"] == "unavailable" and on == []
    await hass.config_entries.async_unload(entry.entry_id)


async def test_states_only_of_shared_parts_grouped(hass: HomeAssistant, server: FakeServer) -> None:
    """Changes of the washer go out grouped, latest value only; the light is never followed."""
    _test_helpers(hass)
    entry, socket = await _ready(hass, server, [WASHER_ROW])
    hass.states.async_set("switch.lavatrice_di_prova_hai", "on")
    hass.states.async_set("light.luce_lavanderia_di_prova_hai", "on")
    await wait_until(lambda: socket.of_type("state"))
    assert socket.of_type("state")[0]["devices"] == {WASHER_ROW: {WASHER_PART: "on"}}
    hass.states.async_set("switch.lavatrice_di_prova_hai", "off")
    hass.states.async_set("switch.lavatrice_di_prova_hai", "on")
    hass.states.async_set("switch.lavatrice_di_prova_hai", "off")
    await wait_until(lambda: len(socket.of_type("state")) == 2)
    assert socket.of_type("state")[1]["devices"] == {WASHER_ROW: {WASHER_PART: "off"}}
    assert all(LIGHT_ROW not in m["devices"] for m in socket.of_type("state"))
    await hass.config_entries.async_unload(entry.entry_id)


async def test_attribute_changes_are_sent(hass: HomeAssistant, server: FakeServer) -> None:
    """A change of an announced attribute alone is sent under <key>:<attribute>; the others never."""
    row = _thermostat(hass)
    entry, socket = await _ready(hass, server, [row])
    attributes = _parts(socket)[THERMOSTAT_PART]["attributes"]
    assert set(attributes) == {"current_temperature", "hvac_action", "temperature"}
    assert attributes["current_temperature"]["state"] == "19.5"
    state = hass.states.get("climate.salotto")
    hass.states.async_set(
        "climate.salotto", "heat",
        {**state.attributes, "current_temperature": 20.0, "hvac_modes": ["off", "heat", "cool"]},
    )
    await wait_until(lambda: socket.of_type("state"))
    assert socket.of_type("state")[0]["devices"] == {
        row: {f"{THERMOSTAT_PART}:current_temperature": "20.0"}
    }
    state = hass.states.get("climate.salotto")
    hass.states.async_set("climate.salotto", "heat", {**state.attributes, "hvac_action": None})
    await wait_until(lambda: len(socket.of_type("state")) == 2)
    assert socket.of_type("state")[1]["devices"] == {row: {f"{THERMOSTAT_PART}:hvac_action": "unknown"}}
    # A new simple attribute is announced a few seconds later; the one without value stays.
    state = hass.states.get("climate.salotto")
    hass.states.async_set("climate.salotto", "heat", {**state.attributes, "preset_mode": "eco"})
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await wait_until(lambda: len(socket.of_type("announce")) == 2)
    attributes = socket.of_type("announce")[1]["device"]["parts"][0]["attributes"]
    assert set(attributes) == {"current_temperature", "hvac_action", "preset_mode", "temperature"}
    assert attributes["hvac_action"]["state"] == "unknown"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_position_never_leaves(hass: HomeAssistant, server: FakeServer) -> None:
    """A shared tracker sends its state and its other values; where it is never leaves home."""
    er.async_get(hass).async_get_or_create(
        "device_tracker", "demo", "bici-1", suggested_object_id="bici", original_name="Bici"
    )
    where = {"latitude": 45.0703, "longitude": 7.6869, "gps_accuracy": 12, "altitude": 240}
    others = {"battery_level": 80, "source_type": "gps", "friendly_name": "Bici"}
    hass.states.async_set("device_tracker.bici", "home", {**others, **where})
    row = entity_row_id("device_tracker", "demo", "bici-1")
    key = part_key("device_tracker", "demo", "bici-1")
    entry, socket = await _ready(hass, server, [row])
    part = _parts(socket)[key]
    assert part["state"] == "home"
    assert set(part["attributes"]) == {"battery_level", "source_type"}
    hass.states.async_set("device_tracker.bici", "home", {**others, **where, "latitude": 45.1})
    hass.states.async_set(
        "device_tracker.bici", "not_home", {**others, "latitude": 45.2, "longitude": 7.7, "altitude": 300}
    )
    await wait_until(lambda: socket.of_type("state"))
    assert socket.of_type("state")[0]["devices"] == {row: {key: "not_home"}}
    sent = json.dumps(socket.sent)
    assert not any(name in sent for name in where) and "45.0703" not in sent and "45.2" not in sent
    await hass.config_entries.async_unload(entry.entry_id)


async def test_service_button_is_read_only(hass: HomeAssistant, server: FakeServer) -> None:
    """A restart button of a shared plug is announced without actions and cannot be pressed."""
    owner = MockConfigEntry(domain="demo")
    owner.add_to_hass(hass)
    plug = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("demo", "presa")}, name="Presa"
    )
    registry = er.async_get(hass)
    registry.async_get_or_create("switch", "demo", "presa-0", device_id=plug.id, suggested_object_id="presa")
    registry.async_get_or_create(
        "button", "demo", "presa-restart", device_id=plug.id, suggested_object_id="presa_riavvia",
        entity_category=EntityCategory.CONFIG,
    )
    hass.states.async_set("switch.presa", "off")
    hass.states.async_set("button.presa_riavvia", "unknown")
    press = async_mock_service(hass, "button", "press")
    for service in ("turn_on", "turn_off", "toggle"):
        async_mock_service(hass, "switch", service)
    row = f"d{plug.id}"
    entry, socket = await _ready(hass, server, [row])
    parts = _parts(socket)
    restart = parts[part_key("button", "demo", "presa-restart")]
    assert restart["category"] == "config" and restart["actions"] == [] and restart["action_info"] == {}
    assert parts[part_key("switch", "demo", "presa-0")]["actions"] == ["toggle", "turn_off", "turn_on"]
    resp = await _command(socket, device=row, part=restart["key"], action="press")
    assert resp["error"] == "action_not_allowed" and "state" not in resp
    assert press == []
    await hass.config_entries.async_unload(entry.entry_id)


async def test_service_devices_are_not_offered(hass: HomeAssistant) -> None:
    """Backup, Sun and the like are not objects to sell: they never appear in the choice."""
    owner = MockConfigEntry(domain="demo")
    owner.add_to_hass(hass)
    devices = dr.async_get(hass)
    backup = devices.async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("demo", "backup")}, name="Backup",
        entry_type=dr.DeviceEntryType.SERVICE,
    )
    plug = devices.async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("demo", "presa")}, name="Presa"
    )
    registry = er.async_get(hass)
    registry.async_get_or_create("switch", "demo", "backup-auto", device_id=backup.id)
    registry.async_get_or_create("sensor", "demo", "backup-last", device_id=backup.id)
    registry.async_get_or_create("switch", "demo", "presa-0", device_id=plug.id)
    assert list(shareable_rows(hass)) == [f"d{plug.id}"]


async def test_panel_page_in_repairs(hass: HomeAssistant, server: FakeServer) -> None:
    """The repair after a revocation points at the Connect Home Assistant section of the panel."""
    entry = await _setup(hass, [])
    socket = server.sockets[0]
    await wait_until(lambda: socket.of_type("announce_done"))
    socket.server_close(4004)
    await wait_until(lambda: ir.async_get(hass).async_get_issue(DOMAIN, "code_revoked") is not None)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "code_revoked")
    assert issue.translation_placeholders == {"panel_url": PANEL_PAGE}
    await hass.config_entries.async_unload(entry.entry_id)


async def test_group_ticked_before_is_not_commandable(hass: HomeAssistant, server: FakeServer) -> None:
    """A group that was ticked is no longer announced, and a command on it reaches no member."""
    registry = er.async_get(hass)
    registry.async_get_or_create("lock", "group", "all_doors", suggested_object_id="all_doors")
    hass.states.async_set("lock.all_doors", "locked", {"entity_id": ["lock.front", "lock.back"]})
    unlock = async_mock_service(hass, "lock", "unlock")
    row = entity_row_id("lock", "group", "all_doors")
    entry, socket = await _ready(hass, server, [row])
    assert socket.of_type("announce") == [] and socket.of_type("announce_done")[0]["devices"] == []
    resp = await _command(
        socket, device=row, part=part_key("lock", "group", "all_doors"), action="unlock"
    )
    assert resp["error"] == "not_shared" and unlock == []
    await hass.config_entries.async_unload(entry.entry_id)
