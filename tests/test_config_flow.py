"""Tests for the Roraima config flow."""

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.roraima.const import DOMAIN


async def test_user_step_asks_for_the_code(hass: HomeAssistant) -> None:
    """The first screen asks only for the pairing code."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert list(result["data_schema"].schema) == ["code"]
