"""The extra values of a part: the attributes of its state, taken by their form (contract §2.5).

An attribute travels when its value is a number, a text, yes/no or a moment in time. A list of such
values travels as one text; a group of them travels as one value for each of its keys, under
`<attribute>.<key>`. Anything deeper stays home, and the log says so. The ones that describe the
entity instead of measuring it (its name, icon, picture, unit, class, features) stay home too.
Names that can hold a secret, values that hold an address or a path, and any name that tells a
place or the home network (latitude, gps_accuracy, location_name, ip_address, ssid...) never leave,
whether they name an attribute or a key inside one.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
import datetime as dt
import math
import re
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import State

from .const import ATTRIBUTE_FORM, MAX_ATTRIBUTES, METRIC_MAX, PIECE_FORM, TEXT_MAX

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
# What tells the home network apart: its addresses and the name of its Wi-Fi.
_NETWORK = re.compile(r"(^|_)(ip|ipv4|ipv6|mac|ssid|bssid|hostname)(_|$)")
# An address or a path can carry a key in it (a picture or a stream with ?token=...), and a path
# shows the files of the host: neither leaves.
_ADDRESS = re.compile(r"://|^/|[?&][a-z_]*token=", re.IGNORECASE)
# The keys of a group are often written ipAddress: read them as ip_address too.
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

UNKNOWN = "unknown"
_CUT = "…"
_JOIN = ", "


def _simple_text(value: Any) -> str | None:
    """One simple value as text, or None if it is not one."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return int.__repr__(value)
    if isinstance(value, float):
        return float.__repr__(value) if math.isfinite(value) else None
    if isinstance(value, str):
        text = str.__str__(value)
        return None if _ADDRESS.search(text) else text[:TEXT_MAX]
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    return None


def value_text(value: Any) -> str | None:
    """The value of an attribute as Roraima receives it, or None if it does not travel.

    A list of simple values is one text; a list cut at the limit ends with "…", so the cut shows.
    """
    if isinstance(value, (list, tuple, set, frozenset)):
        texts = [_simple_text(item) for item in value]
        if any(text is None for text in texts):
            return None
        if isinstance(value, (set, frozenset)):
            texts.sort()
        joined = _JOIN.join(texts)  # type: ignore[arg-type]
        return joined if len(joined) <= TEXT_MAX else joined[: TEXT_MAX - 1] + _CUT
    return _simple_text(value)


def _private(*words: str) -> bool:
    return any(
        _SECRET.search(word) or _PLACE.search(word) or _NETWORK.search(word) for word in words
    )


def shareable(name: Any) -> bool:
    """Whether an attribute with this name may ever travel."""
    return (
        isinstance(name, str)
        and ATTRIBUTE_FORM.fullmatch(name) is not None
        and name not in DESCRIPTIVE
        and not _private(name)
    )


def _piece_shareable(piece: Any) -> bool:
    """Whether a key of a group may ever travel: its form, and the same names as an attribute."""
    return (
        isinstance(piece, str)
        and PIECE_FORM.fullmatch(piece) is not None
        and not _private(piece.lower(), _CAMEL.sub("_", piece).lower())
    )


def shareable_key(key: Any) -> bool:
    """Whether an announced key (`attribute` or `attribute.key`) may ever travel."""
    if not isinstance(key, str):
        return False
    name, dot, piece = key.partition(".")
    return shareable(name) and (not dot or _piece_shareable(piece))


def room_for(part_key: str) -> int:
    """How long the key of an extra value of this part may be (`<part>:<key>` is a measure)."""
    return METRIC_MAX - len(part_key) - 1


def _lookup(state: State, key: str) -> Any:
    """The value under an announced key in this moment, or None."""
    name, dot, piece = key.partition(".")
    value = state.attributes.get(name)
    if isinstance(value, Mapping):
        return value.get(piece) if dot else None
    return None if dot else value


def _empty(state: State, key: str) -> bool:
    """Whether Home Assistant still keeps the place of this value, without a value in it."""
    name, dot, piece = key.partition(".")
    if name not in state.attributes:
        return False
    value = state.attributes[name]
    if value is None:
        return True
    return bool(dot) and isinstance(value, Mapping) and value.get(piece) is None


def _values(state: State) -> dict[str, Any]:
    """Every value the attributes of a state offer, by key; a group gives one for each key."""
    values: dict[str, Any] = {}
    for name, value in state.attributes.items():
        if not shareable(name):
            continue
        if isinstance(value, Mapping):
            for piece, inner in value.items():
                if _piece_shareable(piece):
                    values[f"{name}.{piece}"] = inner
        else:
            values[name] = value
    return values


def candidate_keys(
    state: State | None, kept: Collection[str] = (), room: int = METRIC_MAX
) -> list[str]:
    """Every extra value a part could announce, in alphabetical order, before the limit of 64.

    A value announced before stays while Home Assistant keeps it without a value (the brightness
    of a light that is off), so the list does not change at every switch.
    """
    if state is None:
        return []
    keys = {key for key, value in _values(state).items() if value_text(value) is not None}
    keys.update(key for key in kept if shareable_key(key) and _empty(state, key))
    return sorted(key for key in keys if len(key) <= room)


def attribute_keys(
    state: State | None, kept: Collection[str] = (), room: int = METRIC_MAX
) -> list[str]:
    """The extra values a part announces: the first 64 in alphabetical order."""
    return candidate_keys(state, kept, room)[:MAX_ATTRIBUTES]


def left_home(state: State | None, room: int = METRIC_MAX) -> dict[str, str]:
    """What stays home for its form alone, and why: for the log, never for Roraima.

    Names that stay home for what they tell (a secret, a place, the network) are not listed.
    """
    if state is None:
        return {}
    out: dict[str, str] = {}
    for name, value in state.attributes.items():
        if not shareable(name) or value is None:
            continue
        if isinstance(value, Mapping):
            for piece, inner in value.items():
                key = f"{name}.{piece}"
                if not isinstance(piece, str) or PIECE_FORM.fullmatch(piece) is None:
                    out[f"{name}.*"] = "a key of the group is not made of letters, digits and _"
                elif not _piece_shareable(piece) or inner is None:
                    continue
                elif isinstance(inner, Mapping):
                    out[key] = "a group inside a group"
                elif value_text(inner) is None:
                    out[key] = "not a simple value, or an address"
                elif len(key) > room:
                    out[key] = "its key is too long"
        elif isinstance(value, (list, tuple, set, frozenset)):
            if value_text(value) is None:
                out[name] = "a list of values that are not simple"
        elif value_text(value) is None:
            out[name] = "not a simple value, or an address"
    return out


def attribute_texts(state: State | None, keys: Iterable[str]) -> dict[str, str]:
    """The current value of each announced key; unknown when it has none."""
    keys = [key for key in keys if shareable_key(key)]
    if state is None or state.state == STATE_UNAVAILABLE:
        return dict.fromkeys(keys, STATE_UNAVAILABLE)
    texts: dict[str, str] = {}
    for key in keys:
        text = value_text(_lookup(state, key))
        texts[key] = UNKNOWN if text is None else text
    return texts
