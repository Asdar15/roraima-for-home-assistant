"""Config flow for the Roraima integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import instance_id
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CODE_PATTERN,
    CONF_CODE,
    CONF_DEVICES,
    CONF_HUB,
    CONF_INSTANCE_ID,
    DOMAIN,
    MAX_DEVICES,
    PANEL_URL,
)
from .link import LinkError, Outcome, async_open
from .selection import shareable_rows

PLACEHOLDERS = {"panel_url": PANEL_URL}

CODE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CODE): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
    }
)


async def async_check_code(hass: HomeAssistant, code: str, instance: str) -> str | None:
    """Ask Roraima whether it accepts the code; return an error key or None."""
    if not CODE_PATTERN.fullmatch(code):
        return "invalid_code"
    try:
        ws, _ = await async_open(hass, code, instance)
    except LinkError as err:
        if err.outcome is Outcome.REVOKED:
            return "invalid_code"
        if err.outcome is Outcome.CONFLICT:
            return "code_in_use"
        return "cannot_connect"
    await ws.close()
    return None


def devices_schema(hass: HomeAssistant, current: list[str]) -> vol.Schema:
    """The device picker: empty at first, only the domains Roraima may use."""
    rows = shareable_rows(hass)
    options = [SelectOptionDict(value=row_id, label=row.label) for row_id, row in rows.items()]
    default = [row_id for row_id in current if row_id in rows]
    return vol.Schema(
        {
            vol.Optional(CONF_DEVICES, default=default): SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        }
    )


def _picked(hass: HomeAssistant, user_input: dict[str, Any]) -> list[str] | None:
    rows = shareable_rows(hass)
    picked = [row_id for row_id in dict.fromkeys(user_input.get(CONF_DEVICES, [])) if row_id in rows]
    return picked if len(picked) <= MAX_DEVICES else None


class RoraimaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pair this Home Assistant with a Roraima account."""

    VERSION = 1

    def __init__(self) -> None:
        """Start with nothing paired."""
        self._code: str | None = None
        self._instance: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Change the selection of shared devices."""
        return RoraimaOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the pairing code shown in the Roraima panel."""
        errors: dict[str, str] = {}
        if user_input is not None:
            code = user_input[CONF_CODE].strip()
            instance = await instance_id.async_get(self.hass)
            await self.async_set_unique_id(instance)
            self._abort_if_unique_id_configured()
            error = await async_check_code(self.hass, code, instance)
            if error is None:
                self._code, self._instance = code, instance
                return await self.async_step_devices()
            errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=CODE_SCHEMA,
            errors=errors,
            description_placeholders=PLACEHOLDERS,
        )

    async def async_step_import(
        self, import_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """A Roraima Hub pairs from its own file, without calling Roraima and with nothing shared."""
        await self.async_set_unique_id(import_data[CONF_INSTANCE_ID])
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title="Roraima",
            data={
                CONF_CODE: import_data[CONF_CODE],
                CONF_INSTANCE_ID: import_data[CONF_INSTANCE_ID],
                CONF_HUB: True,
            },
            options={CONF_DEVICES: []},
        )

    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the seller pick the devices Roraima may use; none at first."""
        errors: dict[str, str] = {}
        if user_input is not None:
            picked = _picked(self.hass, user_input)
            if picked is not None:
                return self.async_create_entry(
                    title="Roraima",
                    data={CONF_CODE: self._code, CONF_INSTANCE_ID: self._instance},
                    options={CONF_DEVICES: picked},
                    description_placeholders=PLACEHOLDERS,
                )
            errors["base"] = "too_many_devices"
        return self.async_show_form(
            step_id="devices",
            data_schema=devices_schema(self.hass, []),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Roraima no longer accepts the code."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new pairing code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            entry = self._get_reauth_entry()
            code = user_input[CONF_CODE].strip()
            error = await async_check_code(self.hass, code, entry.data[CONF_INSTANCE_ID])
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_CODE: code}
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=CODE_SCHEMA,
            errors=errors,
            description_placeholders=PLACEHOLDERS,
        )


class RoraimaOptionsFlow(OptionsFlow):
    """Change which devices Roraima may use."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the device picker with the current selection."""
        errors: dict[str, str] = {}
        if user_input is not None:
            picked = _picked(self.hass, user_input)
            if picked is not None:
                return self.async_create_entry(data={CONF_DEVICES: picked})
            errors["base"] = "too_many_devices"
        current = self.config_entry.options.get(CONF_DEVICES, [])
        return self.async_show_form(
            step_id="init",
            data_schema=devices_schema(self.hass, current),
            errors=errors,
        )
