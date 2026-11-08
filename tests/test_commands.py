"""Law 3: only ticked devices, only their domain's services, only allowed parameters, checked state."""

from __future__ import annotations

from typing import Any

import pytest

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

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
PANEL_PAGE = "https://roraima.io/vendor.php?dispatch=iot_cloud_accounts.manage#iot-ha-links"


@pytest.fixture(autouse=True)
def short_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lock that never moves is judged in a fraction of a second, not nine."""
    monkeypatch.setattr(commands, "COMMAND_BUDGET", 0.3)


def _entrance(hass: HomeAssistant) -> str:
    """A registry device with a lock, a dimmable light, a battery sensor and a scene."""
    owner = MockConfigEntry(domain="demo")
    owner.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("demo", "ingresso")}, name="Ingresso"
    )
    registry = er.async_get(hass)
    for domain, unique_id, object_id, extra in (
        ("lock", "porta-lock", "porta", {}),
        ("light", "ingresso-light", "ingresso", {"capabilities": {"supported_color_modes": ["brightness"]}}),
        ("sensor", "porta-battery", "porta_batteria", {}),
        ("scene", "arrivo", "arrivo", {}),
    ):
        registry.async_get_or_create(
            domain, "demo", unique_id, device_id=device.id, suggested_object_id=object_id, **extra
        )
    hass.states.async_set("lock.porta", "locked", {"supported_features": 0})
    hass.states.async_set("light.ingresso", "off", {"supported_color_modes": ["brightness"]})
    hass.states.async_set("sensor.porta_batteria", "87", {"unit_of_measurement": "%"})
    hass.states.async_set("scene.arrivo", "unknown")
    return f"d{device.id}"


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
    on = _service(hass, "light", "turn_on", "on")
    entry, socket = await _ready(hass, server, [WASHER_ROW])
    resp = await _command(socket, device=LIGHT_ROW, part=LIGHT_PART, action="turn_on")
    assert resp == {"v": 1, "type": "resp", "id": resp["id"], "ok": False, "error": "not_shared"}
    resp = await _command(socket, device=WASHER_ROW, part=LIGHT_PART, action="turn_on")
    assert resp["error"] == "part_unknown" and "state" not in resp
    assert on == []
    await hass.config_entries.async_unload(entry.entry_id)


async def test_excluded_domain_and_read_only_part(hass: HomeAssistant, server: FakeServer) -> None:
    """A scene of the ticked device is never a part; its sensor is read-only."""
    row = _entrance(hass)
    forbidden = _forbidden(hass)
    entry, socket = await _ready(hass, server, [row])
    [announce] = socket.of_type("announce")
    keys = [p["key"] for p in announce["device"]["parts"]]
    assert SCENE_PART not in keys and BATTERY_PART in keys
    for part, action in ((SCENE_PART, "turn_on"), (BATTERY_PART, "turn_on"), ("homeassistant.restart", "restart")):
        resp = await _command(socket, device=row, part=part, action=action)
        assert resp["error"] == "domain_not_allowed", part
    assert forbidden == []
    await hass.config_entries.async_unload(entry.entry_id)


async def test_no_free_service(hass: HomeAssistant, server: FakeServer) -> None:
    """call_service, another domain's service or a service name are not Roraima actions."""
    row = _entrance(hass)
    forbidden = _forbidden(hass)
    unlock = _service(hass, "lock", "unlock", "unlocked")
    entry, socket = await _ready(hass, server, [row])
    for action, params in (
        ("call_service", {"domain": "homeassistant", "service": "restart"}),
        ("turn_on", {}),
        ("homeassistant.turn_on", {}),
        ("lock.unlock", {}),
        ("open", {}),
    ):
        resp = await _command(socket, device=row, part=LOCK_PART, action=action, params=params)
        assert resp["error"] == "action_not_allowed", action
    assert forbidden == [] and unlock == []
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
    """Brightness 0-100 as a number, alone; anything else is refused, never clamped."""
    row = _entrance(hass)
    on = _service(hass, "light", "turn_on", "on")
    entry, socket = await _ready(hass, server, [row])
    for params in (
        {"brightness": 150},
        {"brightness": -1},
        {"brightness": True},
        {"brightness": "50"},
        {"brightness": 50.5},
        {"brightness": 50, "transition": 0},
        {},
        [50],
    ):
        resp = await _command(socket, device=row, part=LAMP_PART, action="set_brightness", params=params)
        assert resp["error"] == "params_invalid", params
    assert on == []
    resp = await _command(socket, device=row, part=LAMP_PART, action="set_brightness", params={"brightness": 50})
    assert resp["ok"] is True and resp["state"] == "on"
    assert [dict(c.data) for c in on] == [{"entity_id": "light.ingresso", "brightness_pct": 50}]
    resp = await _command(socket, device=row, part=LAMP_PART, action="set_color_temp", params={"kelvin": 3000})
    assert resp["error"] == "action_not_allowed"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_lock_that_does_not_move(hass: HomeAssistant, server: FakeServer) -> None:
    """lock.unlock runs, the lock stays locked: the answer is a failure carrying the real state."""
    row = _entrance(hass)
    unlock = _service(hass, "lock", "unlock", None)
    forbidden = _forbidden(hass)
    entry, socket = await _ready(hass, server, [row])
    resp = await _command(socket, device=row, part=LOCK_PART, action="unlock")
    assert resp == {
        "v": 1, "type": "resp", "id": resp["id"], "ok": False, "error": "state_mismatch", "state": "locked"
    }
    assert [dict(c.data) for c in unlock] == [{"entity_id": "lock.porta"}]
    assert forbidden == []
    await hass.config_entries.async_unload(entry.entry_id)


async def test_lock_that_moves_late(hass: HomeAssistant, server: FakeServer) -> None:
    """A lock that reports unlocking, then unlocked a moment later, counts as done."""
    row = _entrance(hass)

    async def unlock(call: ServiceCall) -> None:
        hass.states.async_set("lock.porta", "unlocking", {"supported_features": 0})
        hass.loop.call_later(0.1, hass.states.async_set, "lock.porta", "unlocked", {"supported_features": 0})

    hass.services.async_register("lock", "unlock", unlock)
    entry, socket = await _ready(hass, server, [row])
    resp = await _command(socket, device=row, part=LOCK_PART, action="unlock")
    assert resp["ok"] is True and resp["state"] == "unlocked"
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
    hass.states.async_set("switch.lavatrice_di_prova_hai", "on", {"note": "only attributes change next"})
    hass.states.async_set("switch.lavatrice_di_prova_hai", "on", {"note": "again"})
    hass.states.async_set("switch.lavatrice_di_prova_hai", "off")
    await wait_until(lambda: len(socket.of_type("state")) == 2)
    assert socket.of_type("state")[1]["devices"] == {WASHER_ROW: {WASHER_PART: "off"}}
    assert all(LIGHT_ROW not in m["devices"] for m in socket.of_type("state"))
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
    row = f"d{plug.id}"
    entry, socket = await _ready(hass, server, [row])
    [announce] = socket.of_type("announce")
    parts = {p["key"]: p for p in announce["device"]["parts"]}
    restart = parts[part_key("button", "demo", "presa-restart")]
    assert restart["category"] == "config" and restart["actions"] == []
    assert parts[part_key("switch", "demo", "presa-0")]["actions"] == ["turn_on", "turn_off", "toggle"]
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
