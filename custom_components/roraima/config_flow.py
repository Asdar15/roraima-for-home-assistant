"""Config flow for the Roraima integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import CONF_CODE, DOMAIN, PANEL_URL

CODE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CODE): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
    }
)


class RoraimaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pair this Home Assistant with a Roraima account."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the pairing code shown in the Roraima panel."""
        return self.async_show_form(
            step_id="user",
            data_schema=CODE_SCHEMA,
            description_placeholders={"panel_url": PANEL_URL},
        )
