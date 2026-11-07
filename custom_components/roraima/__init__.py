"""The Roraima integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.start import async_at_started

from .client import RoraimaClient
from .const import CONF_CODE, CONF_INSTANCE_ID
from .link import async_revoke

PLATFORMS = [Platform.SENSOR]

type RoraimaConfigEntry = ConfigEntry[RoraimaClient]


async def async_setup_entry(hass: HomeAssistant, entry: RoraimaConfigEntry) -> bool:
    """Set up Roraima from a config entry."""
    client = RoraimaClient(hass, entry)
    entry.runtime_data = client
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    @callback
    def _start(_hass: HomeAssistant) -> None:
        client.async_start()

    entry.async_on_unload(async_at_started(hass, _start))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: RoraimaConfigEntry) -> None:
    entry.runtime_data.async_selection_changed()


async def async_unload_entry(hass: HomeAssistant, entry: RoraimaConfigEntry) -> bool:
    """Close the connection and unload the entities."""
    await entry.runtime_data.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The seller removed the integration: Roraima revokes the pairing code."""
    await async_revoke(hass, entry.data[CONF_CODE], entry.data[CONF_INSTANCE_ID])
