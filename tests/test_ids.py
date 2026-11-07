"""The ids and part keys must match the examples of the connection contract."""

import pytest

from custom_components.roraima.ids import device_row_id, entity_row_id, part_key


@pytest.mark.parametrize(
    ("domain", "platform", "unique_id", "key"),
    [
        ("switch", "shelly", "aabbccddeeff-switch:0", "switch.aabbccddeeff_switch_0_24ce7840"),
        ("sensor", "shelly", "aabbccddeeff-switch:0-power", "sensor.aabbccddeeff_switch_0_power_ab646d9e"),
        (
            "binary_sensor",
            "zha",
            "00:15:8d:00:02:b5:4a:1f-1-1280_IAS_ZONE_With_A_Very_Long_Suffix_That_Goes_On",
            "binary_sensor.00_15_8d_00_02_b5_4a_1f_1_1280_ias_zone_w_cdcc2062",
        ),
        ("light", "hue", "Sala-Lampada", "light.sala_lampada_7ceb757f"),
        ("light", "hue", "sala_lampada", "light.sala_lampada_e74dcd93"),
        ("switch", "tasmota", "----", "switch.4435394d"),
        ("sensor", "esphome", "Temperatura°C", "sensor.temperatura_c_50929077"),
        ("switch", "template", "roraima_hai_lavatrice", "switch.roraima_hai_lavatrice_e385b4cd"),
        ("light", "template", "roraima_hai_luce_lavanderia", "light.roraima_hai_luce_lavanderia_d7254fac"),
        ("sensor", "shelly", "4841490000c3-wifi_rssi", "sensor.4841490000c3_wifi_rssi_81303ef8"),
    ],
)
def test_part_key(domain: str, platform: str, unique_id: str, key: str) -> None:
    """Every contract example gives its key, never longer than 64 characters."""
    assert part_key(domain, platform, unique_id) == key
    assert len(key) <= 64


def test_row_ids() -> None:
    """The test helpers and a registry device get the contract ids."""
    assert entity_row_id("switch", "template", "roraima_hai_lavatrice") == "e18f1852a1a55f842c6ef5719de1faf69"
    assert entity_row_id("light", "template", "roraima_hai_luce_lavanderia") == "ea8747d88f715add0459f4e6722e76537"
    assert device_row_id("48414900000000000000000000000003") == "d48414900000000000000000000000003"
    assert device_row_id("not-a-registry-id") is None
