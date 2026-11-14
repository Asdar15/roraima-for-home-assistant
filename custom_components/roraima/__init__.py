"""The Roraima integration."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, instance_id
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType

from .client import RoraimaClient
from .const import CONF_CODE, CONF_HUB, CONF_INSTANCE_ID, DOMAIN, hub_file
from .hub import read_hub_code
from .link import async_revoke

PLATFORMS = [Platform.SENSOR]

# `roraima:` in configuration.yaml, empty: a Roraima Hub uses it to pair from its file.
CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """On a Roraima Hub, pair from the code the Hub wrote; anywhere else nothing changes."""
    code = await hass.async_add_executor_job(read_hub_code, hub_file())
    if code is None:
        return True
    entries = hass.config_entries.async_entries(DOMAIN)
    if entries:
        entry = entries[0]
        if entry.data.get(CONF_CODE) != code or not entry.data.get(CONF_HUB):
            # Before the entry loads: the new code is the one it opens the connection with.
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_CODE: code, CONF_HUB: True}
            )
        return True
    instance = await instance_id.async_get(hass)
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data={CONF_CODE: code, CONF_INSTANCE_ID: instance},
        )
    )
    return True

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
    """The seller removed the integration: Roraima revokes the pairing code.

    The pairing of a Roraima Hub belongs to the Hub, not to whoever removed the entry: it stays, and the entry comes
    back from the Hub file at the next start.
    """
    if entry.data.get(CONF_HUB):
        return
    await async_revoke(hass, entry.data[CONF_CODE], entry.data[CONF_INSTANCE_ID])
