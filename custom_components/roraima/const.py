"""Constants for the Roraima integration."""

from __future__ import annotations

import os
import re
from typing import Final

DOMAIN: Final = "roraima"

TUNNEL_URL: Final = "wss://api.roraima.io/tunnel"
# Test servers are reachable only through the container environment, never from the UI.
TUNNEL_URL_ENV: Final = "RORAIMA_TUNNEL_URL"

PANEL_URL: Final = "https://roraima.io/vendor.php?dispatch=iot_cloud_accounts.manage#iot-ha-links"

CODE_PATTERN: Final = re.compile(r"^rha_[0-9a-f]{64}$")

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

PROTOCOL_VERSION: Final = 1
MAX_DEVICES: Final = 256
MAX_MESSAGE_BYTES: Final = 262144
MESSAGES_PER_SECOND: Final = 10
MESSAGE_BURST: Final = 40
ANNOUNCES_PER_SECOND: Final = 5
# Commands running at once; one more is refused at once, so a flood cannot pile up tasks.
MAX_COMMANDS_IN_FLIGHT: Final = 32

WELCOME_TIMEOUT: Final = 10
IDLE_TIMEOUT: Final = 95
RETRY_FIRST: Final = 5
RETRY_MAX: Final = 300
RETRY_SLOW: Final = 60

CLOSE_REVOKED: Final = 4004
CLOSE_INSTANCE_CONFLICT: Final = 4007
CLOSE_TOO_MANY_MESSAGES: Final = 4008

ISSUE_REVOKED: Final = "code_revoked"
ISSUE_CONFLICT: Final = "instance_conflict"

CONF_CODE: Final = "code"
CONF_INSTANCE_ID: Final = "instance_id"
CONF_DEVICES: Final = "devices"


def tunnel_url() -> str:
    """Return the address of the Roraima connection endpoint."""
    return os.environ.get(TUNNEL_URL_ENV, "").strip() or TUNNEL_URL
