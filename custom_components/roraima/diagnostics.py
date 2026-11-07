"""Diagnostics for the Roraima integration, without the pairing code."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import RoraimaConfigEntry
from .const import CONF_CODE, CONF_INSTANCE_ID

TO_REDACT = {CONF_CODE, CONF_INSTANCE_ID}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: RoraimaConfigEntry
) -> dict[str, Any]:
    """Return what helps support without exposing the code."""
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "connection": entry.runtime_data.diagnostics(),
    }
