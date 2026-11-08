"""Sensors of the Roraima device: connection status and shared devices."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RoraimaConfigEntry
from .client import (
    STATUS_CONNECTED,
    STATUS_DISCONNECTED,
    STATUS_REVOKED,
    RoraimaClient,
    update_signal,
)
from .const import DOMAIN, PANEL_URL
from .product_sensor import async_setup_product_sensors


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RoraimaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the two sensors of the Roraima device."""
    client = entry.runtime_data
    async_add_entities([ConnectionSensor(client), SharedDevicesSensor(client)])
    async_setup_product_sensors(hass, client, async_add_entities)


class RoraimaSensor(SensorEntity):
    """A sensor of the Roraima service device."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, client: RoraimaClient, key: str) -> None:
        """Attach the sensor to the Roraima device of this config entry."""
        self._client = client
        entry_id = client.entry.entry_id
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Roraima",
            manufacturer="Roraima",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=PANEL_URL,
        )

    async def async_added_to_hass(self) -> None:
        """Follow status changes of the connection."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, update_signal(self._client.entry.entry_id), self._updated
            )
        )

    @callback
    def _updated(self) -> None:
        self.async_write_ha_state()


class ConnectionSensor(RoraimaSensor):
    """Whether this Home Assistant is connected to Roraima."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [STATUS_CONNECTED, STATUS_DISCONNECTED, STATUS_REVOKED]

    def __init__(self, client: RoraimaClient) -> None:
        """Create the connection sensor."""
        super().__init__(client, "connection")

    @property
    def native_value(self) -> str:
        """Current connection status."""
        return self._client.status


class SharedDevicesSensor(RoraimaSensor):
    """How many devices are shared with Roraima."""

    def __init__(self, client: RoraimaClient) -> None:
        """Create the shared devices sensor."""
        super().__init__(client, "shared_devices")

    @property
    def native_value(self) -> int:
        """Number of shared devices announced to Roraima."""
        return self._client.shared_count
