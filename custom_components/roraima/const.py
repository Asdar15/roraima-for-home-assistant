"""Constants for the Roraima integration."""

from __future__ import annotations

import os
from typing import Final

DOMAIN: Final = "roraima"

TUNNEL_URL: Final = "wss://api.roraima.io/tunnel"
# Test servers are reachable only through the container environment, never from the UI.
TUNNEL_URL_ENV: Final = "RORAIMA_TUNNEL_URL"

PANEL_URL: Final = "https://roraima.io/vendor.php"

ALLOWED_DOMAINS: Final = (
    "switch",
    "light",
    "lock",
    "cover",
    "fan",
    "climate",
    "valve",
    "button",
    "input_boolean",
    "sensor",
    "binary_sensor",
)
READ_ONLY_DOMAINS: Final = frozenset({"sensor", "binary_sensor"})

MAX_DEVICES: Final = 256

CONF_CODE: Final = "code"
CONF_DEVICES: Final = "devices"


def tunnel_url() -> str:
    """Return the address of the Roraima connection endpoint."""
    return os.environ.get(TUNNEL_URL_ENV, "").strip() or TUNNEL_URL
