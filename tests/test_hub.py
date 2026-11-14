"""A Roraima Hub pairs from its own file: nobody types the code, nothing is shared, removing it does not revoke."""

import json
import os
from pathlib import Path

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.roraima.const import (
    CONF_CODE,
    CONF_DEVICES,
    CONF_HUB,
    CONF_INSTANCE_ID,
    DOMAIN,
    HUB_FILE_ENV,
)
from custom_components.roraima.hub import read_hub_code

from .conftest import CODE, FakeServer, wait_until

OTHER = "rha_" + "cd" * 32


@pytest.fixture
def hub_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "abbinamento.json"
    monkeypatch.setenv(HUB_FILE_ENV, str(path))
    return path


def _write(path: Path, code: str) -> None:
    path.write_text(json.dumps({"v": 1, "code": code}), encoding="utf-8")


def test_file_forms(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Only a regular small file with the right form gives a code, and the log never shows its content."""
    path = tmp_path / "abbinamento.json"
    assert read_hub_code(str(path)) is None
    _write(path, CODE)
    assert read_hub_code(str(path)) == CODE
    for bad in ({"v": 2, "code": CODE}, {"v": 1, "code": CODE[:-1]}, {"v": 1}, [CODE]):
        path.write_text(json.dumps(bad), encoding="utf-8")
        assert read_hub_code(str(path)) is None
    path.write_text(json.dumps({"v": 1, "code": CODE, "pad": "x" * 5000}), encoding="utf-8")
    assert read_hub_code(str(path)) is None
    os.unlink(path)
    os.mkdir(path)
    assert read_hub_code(str(path)) is None
    assert CODE not in caplog.text


async def test_no_file_no_entry(hass: HomeAssistant, hub_file: Path) -> None:
    """Anywhere but on a Hub the integration stays as it is: added by hand."""
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: {}})
    await hass.async_block_till_done()
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_hub_creates_its_entry_with_nothing_shared(
    hass: HomeAssistant, hub_file: Path, server: FakeServer
) -> None:
    """The entry is born from the file, without asking Roraima first, and shares no device."""
    _write(hub_file, CODE)
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: {}})
    await hass.async_block_till_done()
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.data[CONF_CODE] == CODE
    assert entry.data[CONF_HUB] is True
    assert len(entry.data[CONF_INSTANCE_ID]) == 32
    assert entry.options == {CONF_DEVICES: []}


async def test_new_code_replaces_the_old_before_loading(
    hass: HomeAssistant, hub_file: Path, server: FakeServer
) -> None:
    """A new code from the Hub is the one the entry opens its connection with."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="a" * 32,
        data={CONF_CODE: CODE, CONF_INSTANCE_ID: "a" * 32, CONF_HUB: True},
        options={CONF_DEVICES: []},
    )
    entry.add_to_hass(hass)
    _write(hub_file, OTHER)
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: {}})
    await hass.async_block_till_done()
    assert entry.data[CONF_CODE] == OTHER
    await wait_until(lambda: server.calls)
    assert server.calls[0][1] == OTHER


async def test_removing_the_hub_entry_does_not_revoke(
    hass: HomeAssistant, hub_file: Path, server: FakeServer
) -> None:
    """The pairing belongs to the Hub: removing the entry by hand keeps the code alive."""
    _write(hub_file, CODE)
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: {}})
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert all(not sock.of_type("revoke") for sock in server.sockets)
