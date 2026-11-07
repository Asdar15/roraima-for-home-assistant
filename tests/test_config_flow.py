"""Pairing: only a code Roraima accepts creates the entry, and nothing is shared at first."""

from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientConnectionError

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.roraima.const import CONF_CODE, CONF_DEVICES, DOMAIN, TUNNEL_URL
from custom_components.roraima.link import ws_connect

from .conftest import CODE, FakeServer


async def _start(hass: HomeAssistant) -> dict:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_code_travels_only_in_the_header() -> None:
    """The code is never part of the address."""
    session = MagicMock()
    session.ws_connect = AsyncMock()
    await ws_connect(session, TUNNEL_URL, CODE)
    url = session.ws_connect.call_args.args[0]
    assert url == "wss://api.roraima.io/tunnel"
    assert CODE not in url
    assert session.ws_connect.call_args.kwargs["headers"] == {"Authorization": f"Bearer {CODE}"}


async def test_accepted_code_then_empty_selection(hass: HomeAssistant, server: FakeServer) -> None:
    """A welcomed code leads to the device step, which starts with nothing selected."""
    result = await _start(hass)
    with patch("custom_components.roraima.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CODE: f"  {CODE}\n"}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "devices"
        hello = server.sockets[0].of_type("hello")[0]
        assert len(hello["instance_id"]) == 32
        assert server.sockets[0].closed
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CODE] == CODE
    assert result["options"] == {CONF_DEVICES: []}


async def test_malformed_code_never_reaches_roraima(hass: HomeAssistant, server: FakeServer) -> None:
    """A code without the right form is refused without opening a connection."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "rha_" + "ab" * 31}
    )
    assert result["errors"] == {"base": "invalid_code"}
    assert server.calls == []


async def test_wrong_or_revoked_code(hass: HomeAssistant, server: FakeServer) -> None:
    """Roraima answers 401 to an unknown or revoked code: no entry."""
    server.status = 401
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: CODE})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_code"}


async def test_code_bound_to_another_instance(hass: HomeAssistant, server: FakeServer) -> None:
    """Roraima closes with 4007 after hello: the code belongs elsewhere."""
    server.on_hello = 4007
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: CODE})
    assert result["errors"] == {"base": "code_in_use"}


async def test_roraima_unreachable(hass: HomeAssistant, server: FakeServer) -> None:
    """No network: the form says so and nothing is saved."""
    server.error = ClientConnectionError()
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: CODE})
    assert result["errors"] == {"base": "cannot_connect"}
