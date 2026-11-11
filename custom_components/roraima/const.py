"""Constants for the Roraima integration."""

from __future__ import annotations

import os
import re
from typing import Final

DOMAIN: Final = "roraima"

TUNNEL_URL: Final = "wss://api.roraima.io/tunnel"
# Test servers are reachable only through the container environment, never from the UI.
TUNNEL_URL_ENV: Final = "RORAIMA_TUNNEL_URL"

PANEL_URL: Final = "https://roraima.io/vendor.php?dispatch=iot_cloud_accounts.manage&selected_section=home_assistant"

CODE_PATTERN: Final = re.compile(r"^rha_[0-9a-f]{64}$")

# Contract §2.5: Roraima keeps no list of domains, actions or attributes. Home Assistant names
# them; these forms and limits are all that is checked.
DOMAIN_FORM: Final = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
ACTION_FORM: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ATTRIBUTE_FORM: Final = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
# One key of an attribute that is a group: it travels as `<attribute>.<key>`.
PIECE_FORM: Final = re.compile(r"^[A-Za-z0-9_]{1,40}$")
MAX_ACTIONS: Final = 32
MAX_FIELDS: Final = 16
MAX_OPTIONS: Final = 32
# Extra values of one part, each key of a group counted: a limit of quantity, never of kind.
MAX_ATTRIBUTES: Final = 64
# `<part key>:<attribute>` must fit what Roraima accepts as the key of a measure.
METRIC_MAX: Final = 128
# The values inside one free object of a command, and the longest time a command may ask for.
MAX_OBJECT_ITEMS: Final = 64
DURATION_MAX: Final = 366 * 86400
LABEL_MAX: Final = 64
TEXT_MAX: Final = 255
UNIT_MAX: Final = 32

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
