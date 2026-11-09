"""The extra values of a part: the simple attributes of its state (contract §2.5).

An attribute travels when its value is a number, a text or yes/no. The ones that describe the entity
instead of measuring it (its name, icon, picture, unit, class, features) stay home, and so does any
list or table. Names that can hold a secret, values that hold an address or a path, and any name
that tells a place (latitude, gps_accuracy, location_name, ip_address, heading...) never leave
either.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
import math
import re
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import State

from .const import ATTRIBUTE_FORM, MAX_ATTRIBUTES, TEXT_MAX

DESCRIPTIVE = frozenset(
    {
        "friendly_name",
        "icon",
        "entity_picture",
        "unit_of_measurement",
        "device_class",
        "state_class",
        "supported_features",
        "attribution",
        "assumed_state",
        "restored",
        "editable",
    }
)
# A camera keeps the key to its own pictures in `access_token`; any such name stays home.
_SECRET = re.compile(r"token|secret|passw|api_?key|credential|cookie|auth")
# A place is personal data: a name with one of these pieces between "_" never leaves, in an
# announce or in a state (gps_accuracy, location_name, ip_address, postal_code...). "position" is
# left out on purpose: the current_position of a cover is not a place.
_PLACE = re.compile(
    r"(^|_)(lat|lon|lng|latitude|longitude|gps|location|address|altitude|elevation|course"
    r"|heading|accuracy|geo|geocoded|coordinates|thoroughfare|locality|postal|zip|country"
    r"|administrative|areas|ocean|inland|place|city|street)(_|$)"
)
# An address or a path can carry a key in it (a picture or a stream with ?token=...), and a path
# shows the files of the host: neither leaves.
_ADDRESS = re.compile(r"://|^/|[?&][a-z_]*token=", re.IGNORECASE)

UNKNOWN = "unknown"


def value_text(value: Any) -> str | None:
    """The value of an attribute as Roraima receives it, or None if it is not a simple value."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return int.__repr__(value)
    if isinstance(value, float):
        return float.__repr__(value) if math.isfinite(value) else None
    if isinstance(value, str):
        text = str.__str__(value)
        return None if _ADDRESS.search(text) else text[:TEXT_MAX]
    return None


def shareable(name: Any) -> bool:
    """Whether an attribute with this name may ever travel."""
    return (
        isinstance(name, str)
        and ATTRIBUTE_FORM.fullmatch(name) is not None
        and name not in DESCRIPTIVE
        and _SECRET.search(name) is None
        and _PLACE.search(name) is None
    )


def attribute_keys(state: State | None, kept: Collection[str] = ()) -> list[str]:
    """The attributes a part announces, at most 16 in alphabetical order.

    An attribute announced before stays while Home Assistant keeps it without a value (the
    brightness of a light that is off), so the list does not change at every switch.
    """
    if state is None:
        return []
    keys = [
        name
        for name, value in state.attributes.items()
        if shareable(name)
        and (value_text(value) is not None or (value is None and name in kept))
    ]
    return sorted(keys)[:MAX_ATTRIBUTES]


def attribute_texts(state: State | None, keys: Iterable[str]) -> dict[str, str]:
    """The current value of each announced attribute; unknown when it has none."""
    keys = [key for key in keys if shareable(key)]
    if state is None or state.state == STATE_UNAVAILABLE:
        return dict.fromkeys(keys, STATE_UNAVAILABLE)
    texts: dict[str, str] = {}
    for key in keys:
        text = value_text(state.attributes.get(key))
        texts[key] = UNKNOWN if text is None else text
    return texts
