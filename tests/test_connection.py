"""The running connection: revocation, what is announced, and no command before the choice."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.roraima.const import CONF_CODE, CONF_DEVICES, CONF_INSTANCE_ID, DOMAIN
from custom_components.roraima.selection import shareable_rows

from .conftest import CODE, FakeServer, wait_until

INSTANCE = "48414900000000000000000000000001"
WASHER_ROW = "e18f1852a1a55f842c6ef5719de1faf69"
WASHER_PART = "switch.roraima_hai_lavatrice_e385b4cd"
LIGHT_ROW = "ea8747d88f715add0459f4e6722e76537"
LIGHT_PART = "light.roraima_hai_luce_lavanderia_d7254fac"


def _test_helpers(hass: HomeAssistant) -> None:
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "switch", "template", "roraima_hai_lavatrice",
        suggested_object_id="lavatrice_di_prova_hai", original_name="Lavatrice di prova HAI",
    )
    registry.async_get_or_create(
        "light", "template", "roraima_hai_luce_lavanderia",
        suggested_object_id="luce_lavanderia_di_prova_hai",
        original_name="Luce lavanderia di prova HAI",
        capabilities={"supported_color_modes": ["onoff"]},
    )
    hass.states.async_set("switch.lavatrice_di_prova_hai", "off", {"friendly_name": "Lavatrice di prova HAI"})
    hass.states.async_set(
        "light.luce_lavanderia_di_prova_hai", "off",
        {"friendly_name": "Luce lavanderia di prova HAI", "supported_color_modes": ["onoff"]},
    )


async def _setup(hass: HomeAssistant, devices: list[str]) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=INSTANCE,
        data={CONF_CODE: CODE, CONF_INSTANCE_ID: INSTANCE},
        options={CONF_DEVICES: devices},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_revoked_code_stops_and_opens_a_repair(hass: HomeAssistant, server: FakeServer) -> None:
    """After 4004 the integration does not reconnect and shows a notice in Repairs."""
    entry = await _setup(hass, [])
    socket = server.sockets[0]
    await wait_until(lambda: socket.of_type("announce_done"))
    socket.server_close(4004)
    await wait_until(lambda: hass.states.get("sensor.roraima_connection").state == "revoked")
    assert ir.async_get(hass).async_get_issue(DOMAIN, "code_revoked") is not None
    assert len(server.calls) == 1
    assert any(flow["context"]["source"] == "reauth" for flow in hass.config_entries.flow.async_progress())
    await hass.config_entries.async_unload(entry.entry_id)


async def test_nothing_shared_nothing_executed(hass: HomeAssistant, server: FakeServer) -> None:
    """With no device chosen, a command from Roraima is refused and no service runs."""
    _test_helpers(hass)
    calls = async_mock_service(hass, "switch", "turn_on")
    entry = await _setup(hass, [])
    socket = server.sockets[0]
    await wait_until(lambda: socket.of_type("announce_done"))
    assert socket.of_type("announce") == []
    assert socket.of_type("announce_done")[0]["devices"] == []
    socket.push({"v": 1, "type": "cmd", "id": "0123456789abcdef", "device": WASHER_ROW,
                 "part": WASHER_PART, "action": "turn_on", "params": {}})
    await wait_until(lambda: socket.of_type("resp"))
    assert socket.of_type("resp")[0] == {
        "v": 1, "type": "resp", "id": "0123456789abcdef", "ok": False, "error": "not_shared"
    }
    assert calls == []
    await hass.config_entries.async_unload(entry.entry_id)


async def test_only_the_chosen_device_is_announced(hass: HomeAssistant, server: FakeServer) -> None:
    """The washer is announced with its contract id, key and services; the light stays private."""
    _test_helpers(hass)
    for service in ("turn_on", "turn_off", "toggle"):
        async_mock_service(hass, "switch", service)
    calls = async_mock_service(hass, "light", "turn_on")
    entry = await _setup(hass, [WASHER_ROW])
    socket = server.sockets[0]
    await wait_until(lambda: socket.of_type("announce_done"))
    [announce] = socket.of_type("announce")
    device = announce["device"]
    assert device["id"] == WASHER_ROW
    assert device["name"] == "Lavatrice di prova HAI"
    assert [p["key"] for p in device["parts"]] == [WASHER_PART]
    part = device["parts"][0]
    assert part["actions"] == ["toggle", "turn_off", "turn_on"]
    assert set(part["action_info"]) == set(part["actions"])
    assert all(info["fields"] == [] for info in part["action_info"].values())
    assert part["attributes"] == {}
    assert socket.of_type("announce_done")[0]["devices"] == [WASHER_ROW]
    assert hass.states.get("sensor.roraima_shared_devices").state == "1"
    socket.push({"v": 1, "type": "cmd", "id": "fedcba9876543210", "device": LIGHT_ROW,
                 "part": LIGHT_PART, "action": "turn_on", "params": {}})
    await wait_until(lambda: socket.of_type("resp"))
    assert socket.of_type("resp")[0]["error"] == "not_shared"
    assert calls == []
    await hass.config_entries.async_unload(entry.entry_id)


async def test_ping_and_removal_revokes(hass: HomeAssistant, server: FakeServer) -> None:
    """Pings get their pong; removing the integration sends revoke."""
    entry = await _setup(hass, [])
    socket = server.sockets[0]
    socket.push({"v": 1, "type": "ping", "t": 1762540000000})
    await wait_until(lambda: socket.of_type("pong"))
    assert socket.of_type("pong")[0]["t"] == 1762540000000
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert server.sockets[-1].of_type("revoke") == [{"v": 1, "type": "revoke"}]


async def test_groups_are_never_offered(hass: HomeAssistant) -> None:
    """A group, or any entity that lists other entities, moves entities that were not chosen."""
    _test_helpers(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "lock", "group", "all_doors", suggested_object_id="all_doors", original_name="All doors"
    )
    hass.states.async_set(
        "lock.all_doors", "locked", {"entity_id": ["lock.front", "lock.back"]}
    )
    registry.async_get_or_create(
        "light", "hue", "room-1", suggested_object_id="living_room", original_name="Living room"
    )
    hass.states.async_set(
        "light.living_room", "off", {"entity_id": ("light.a", "light.b")}
    )
    labels = [row.label for row in shareable_rows(hass).values()]
    assert labels == ["Lavatrice di prova HAI", "Luce lavanderia di prova HAI"]


async def test_scenes_scripts_and_automations_are_never_offered(hass: HomeAssistant) -> None:
    """They run actions on other entities, chosen or not: they never appear in the choice."""
    _test_helpers(hass)
    registry = er.async_get(hass)
    for domain, platform, unique_id in (
        ("script", "script", "open_everything"),
        ("automation", "automation", "1700000000000"),
        ("scene", "homeassistant", "arrival"),
        ("scene", "hue", "living-room-relax"),
    ):
        registry.async_get_or_create(domain, platform, unique_id, original_name=unique_id)
    hass.states.async_set("script.open_everything", "off", {"mode": "single", "current": 0})
    hass.states.async_set("automation.1700000000000", "on", {"id": "1700000000000"})
    labels = [row.label for row in shareable_rows(hass).values()]
    assert labels == ["Lavatrice di prova HAI", "Luce lavanderia di prova HAI"]
