"""Roraima products: no buyer data reaches Home Assistant, even if Roraima sent it."""

import json

from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.roraima.const import CONF_CODE, CONF_DEVICES, CONF_INSTANCE_ID, DOMAIN

from .conftest import CODE, FakeServer, wait_until

INSTANCE = "48414900000000000000000000000001"
BUYER = {
    "email": "test+buyer@roraima.io",
    "customer_name": "Buyer Secret",
    "phone": "+39000111222",
    "order_id": 9790151,
    "access_code": "16773670",
    "link": "https://roraima.io/secret-link",
}
ALLOWED = {
    "device_id", "type", "product_id", "product_name", "kind", "at",
    "duration_minutes", "tokens", "program", "ends_at",
}


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=INSTANCE,
        data={CONF_CODE: CODE, CONF_INSTANCE_ID: INSTANCE},
        options={CONF_DEVICES: []},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_no_buyer_data_in_events_or_sensors(hass: HomeAssistant, server: FakeServer) -> None:
    """Fields outside the contract are dropped; products missing from a full list go away."""
    entry = await _setup(hass)
    socket = server.sockets[0]
    await wait_until(lambda: socket.of_type("announce_done"))
    events: list[Event] = []
    hass.bus.async_listen("roraima_event", events.append)

    socket.push({"v": 1, "type": "roraima_state", "complete": True, "products": [
        {"product_id": 381, "name": "Washer", "kind": "time", "state": "free",
         "busy_until": None, "last_sale_at": None, **BUYER},
        {"product_id": 382, "name": "Light", "kind": "tokens", "state": "free",
         "busy_until": None, "last_sale_at": None},
    ]})
    socket.push({"v": 1, "type": "roraima_event", "id": "3f9c0a1b2c3d4e5f", "event": "start",
                 "at": "2026-11-07T17:10:03Z", "product": {"product_id": 381, "name": "Washer", **BUYER},
                 "kind": "time", "duration_minutes": 30, "tokens": None,
                 "program": {"program_id": 7, "name": "Quick", **BUYER},
                 "ends_at": "2026-11-07T17:40:03Z", **BUYER})
    socket.push({"v": 1, "type": "roraima_state", "complete": False, "products": [
        {"product_id": 381, "name": "Washer", "kind": "time", "state": "in_use",
         "busy_until": "2026-11-07T17:40:03Z", "last_sale_at": "2026-11-07T16:55:12Z", **BUYER},
    ]})
    await wait_until(lambda: events and hass.states.get("sensor.washer_status").state == "in_use")

    data = events[0].data
    assert set(data) == ALLOWED
    assert data["type"] == "start" and data["program"] == "Quick"
    assert data["device_id"] is not None
    dumped = json.dumps(data) + json.dumps(
        [s.as_dict() for s in hass.states.async_all() if s.entity_id.startswith("sensor.")],
        default=str,
    )
    for value in BUYER.values():
        assert str(value) not in dumped
    assert hass.states.get("sensor.washer_free_at").state == "2026-11-07T17:40:03+00:00"

    socket.push({"v": 1, "type": "roraima_state", "complete": True, "products": [
        {"product_id": 381, "name": "Washer", "kind": "time", "state": "free",
         "busy_until": None, "last_sale_at": "2026-11-07T16:55:12Z"},
    ]})
    await wait_until(lambda: hass.states.get("sensor.light_status") is None)
    products = [
        d for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if d.name in ("Washer", "Light")
    ]
    assert [d.name for d in products] == ["Washer"]
