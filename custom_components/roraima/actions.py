"""What a part can do: the services Home Assistant applies to its entity, and their fields.

Roraima keeps no list of commands (contract §2.5). The actions of a part are the services of the
entity's own domain whose target, in Home Assistant's service descriptions, covers that entity:
the same test Home Assistant makes when it offers the actions of an entity. A field is kept only in
a kind that can be checked here: a number in its limits, yes or no, one of a few choices, a short
text. A field that points at anything else of Home Assistant (an entity, a device, an area, a
target...) never enters, so a command can move only the entity of its own part (law 3).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
import logging
import math
import re
from typing import Any

from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_get_all_descriptions

from .const import ACTION_FORM, LABEL_MAX, MAX_ACTIONS, MAX_FIELDS, MAX_OPTIONS, TEXT_MAX, UNIT_MAX

_LOGGER = logging.getLogger(__name__)

type Descriptions = Mapping[str, Mapping[str, Any]]

NUMBER = "number"
BOOLEAN = "boolean"
SELECT = "select"
TEXT = "text"

# Fields that would aim the call at another target than the part's own entity (law 3).
TARGET_KEYS = frozenset({"entity_id", "device_id", "area_id", "floor_id", "label_id", "target"})
# A text naming a file or a folder (a snapshot, a recording) acts on the host, not on the part.
_HOST_PATH = re.compile(r"(?:^|_)(?:file|filename|path|dir|directory|folder)(?:_|$)")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_UNSAFE_UNIT = re.compile(r"[<>\"'&\x00-\x1f\x7f-\x9f]")
# Selectors whose value is one plain text: a state of the entity itself, a time of day.
_TEXT_SELECTORS = frozenset({"text", "state", "time"})
# A text that names an address or a path would make Home Assistant open something of the home
# network or of the host (play_media, media-source:, file:): a compromised server must not. A
# colon followed by a space ("Ciao: arrivo") and a time ("08:30") are not addresses.
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:\S")
_PARENT = re.compile(r"(^|/)\.\.(/|$)")


def names_address(text: str) -> bool:
    """Whether a text names an address or a path: `://` or a backslash anywhere, a leading slash,
    a `..` segment, or a scheme followed at once by something (`file:/`, `media-source:x`).

    The ends are trimmed first, as address parsers do: " file:/x" is still an address.
    """
    text = text.strip()
    return (
        "://" in text
        or "\\" in text
        or text.startswith("/")
        or _PARENT.search(text) is not None
        or _SCHEME.match(text) is not None
    )


@dataclass(frozen=True, slots=True)
class Field:
    """One field of a service, in one of the four kinds a command can carry."""

    key: str
    kind: str
    required: bool
    low: float | None = None
    high: float | None = None
    step: float | None = None
    unit: str | None = None
    options: tuple[str, ...] | None = None

    def accepts(self, value: Any) -> bool:
        """Whether a value sent by Roraima has this field's kind and limits."""
        if self.kind == NUMBER:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            if not math.isfinite(value):
                return False
            return (self.low is None or value >= self.low) and (
                self.high is None or value <= self.high
            )
        if self.kind == BOOLEAN:
            return isinstance(value, bool)
        if not isinstance(value, str) or len(value) > TEXT_MAX or _CONTROL.search(value):
            return False
        if self.kind == TEXT:
            return not names_address(value)
        return value in (self.options or ())


type Actions = dict[str, dict[str, Field]]


async def async_descriptions(hass: HomeAssistant) -> Descriptions:
    """Home Assistant's descriptions of every service, or none if they cannot be read."""
    try:
        return await async_get_all_descriptions(hass)
    except Exception:  # noqa: BLE001 - without descriptions a part has no actions, never a guess
        _LOGGER.exception("The service descriptions of Home Assistant could not be read")
        return {}


def part_actions(descriptions: Descriptions, entry: er.RegistryEntry, state: State | None) -> Actions:
    """The services of the entity's domain that apply to it, each with the fields it can carry."""
    # Service parts (restart, factory reset, identify...) stay readable, never commandable:
    # selling the use of a device never needs them.
    if entry.entity_category is not None:
        return {}
    services = descriptions.get(entry.domain)
    if not isinstance(services, Mapping):
        return {}
    actions: Actions = {}
    for service in sorted(s for s in services if isinstance(s, str)):
        description = services[service]
        if not ACTION_FORM.fullmatch(service):
            continue
        if not isinstance(description, Mapping) or not _applies(description, entry, state):
            continue
        fields = _fields(description, state)
        if fields is None:
            continue
        actions[service] = fields
        if len(actions) == MAX_ACTIONS:
            break
    return actions


def check_params(fields: Mapping[str, Field], params: Any) -> dict[str, Any] | None:
    """The service data of a command, or None unless every parameter is an allowed field."""
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return None
    if any(key not in fields for key in params):
        return None
    if any(field.required and key not in params for key, field in fields.items()):
        return None
    if not all(fields[key].accepts(value) for key, value in params.items()):
        return None
    return dict(params)


def _applies(description: Mapping[str, Any], entry: er.RegistryEntry, state: State | None) -> bool:
    """Whether Home Assistant offers this service for the entity (its target covers it)."""
    if "target" not in description:
        return False
    response = description.get("response")
    # A service that only answers (a forecast, a list of events) does nothing to the device, and
    # cannot be called without asking for its answer, which Roraima never receives.
    if isinstance(response, Mapping) and response.get("optional") is False:
        return False
    target = description["target"]
    if target is None:
        return True
    if not isinstance(target, Mapping):
        return False
    filters = target.get("entity")
    if not filters:
        return True
    if isinstance(filters, Mapping):
        filters = [filters]
    if not isinstance(filters, list):
        return False
    return any(isinstance(f, Mapping) and _matches(f, entry, state) for f in filters)


def _matches(entity_filter: Mapping[str, Any], entry: er.RegistryEntry, state: State | None) -> bool:
    """One entity filter of a target: every criterion it sets must hold."""
    integration = entity_filter.get("integration")
    if integration and integration != entry.platform:
        return False
    domains = _as_list(entity_filter.get("domain"))
    if domains and entry.domain not in domains:
        return False
    classes = _as_list(entity_filter.get("device_class"))
    if classes:
        device_class = _device_class(entry, state)
        if device_class is None or device_class not in classes:
            return False
    masks = _as_list(entity_filter.get("supported_features"))
    if masks:
        features = _features(entry, state)
        if not any(_is_int(mask) and mask & features == mask for mask in masks):
            return False
    return True


def _fields(description: Mapping[str, Any], state: State | None) -> dict[str, Field] | None:
    """The fields a command may carry, or None if a required one cannot be carried."""
    kept: list[Field] = []
    for key, config in _flat_fields(description.get("fields")):
        if not _shown(config.get("filter"), state):
            continue
        required = config.get("required") is True
        field = _field(key, config, required)
        if field is None:
            if required:
                return None
            continue
        if all(f.key != key for f in kept):
            kept.append(field)
    if len(kept) > MAX_FIELDS:
        first = {f.key for f in sorted(kept, key=lambda f: not f.required)[:MAX_FIELDS]}
        kept = [f for f in kept if f.key in first]
    return {f.key: f for f in kept}


def _flat_fields(fields: Any) -> Iterator[tuple[str, Mapping[str, Any]]]:
    """Every field of a service; the fields of a section are fields of the service too."""
    if not isinstance(fields, Mapping):
        return
    for key, config in fields.items():
        if not isinstance(key, str) or not isinstance(config, Mapping):
            continue
        inner = config.get("fields")
        if isinstance(inner, Mapping) and "selector" not in config:
            for inner_key, inner_config in inner.items():
                if isinstance(inner_key, str) and isinstance(inner_config, Mapping):
                    yield inner_key, inner_config
            continue
        yield key, config


def _shown(field_filter: Any, state: State | None) -> bool:
    """Whether Home Assistant shows a field for this entity, by the field's own filter."""
    if not isinstance(field_filter, Mapping) or not field_filter:
        return True
    if state is None:
        return False
    features = _int(state.attributes.get(ATTR_SUPPORTED_FEATURES))
    if any(_is_int(f) and f & features for f in _as_list(field_filter.get("supported_features"))):
        return True
    wanted = field_filter.get("attribute")
    if isinstance(wanted, Mapping):
        for name, values in wanted.items():
            if name in state.attributes and _any_of(_as_list(values), state.attributes[name]):
                return True
    return False


def _field(key: str, config: Mapping[str, Any], required: bool) -> Field | None:
    """A field in one of the four kinds, or None if its kind is anything else."""
    if not ACTION_FORM.fullmatch(key) or key in TARGET_KEYS:
        return None
    selector = config.get("selector")
    if not isinstance(selector, Mapping) or len(selector) != 1:
        return None
    kind, settings = next(iter(selector.items()))
    if settings is None:
        settings = {}
    if not isinstance(settings, Mapping) or settings.get("multiple"):
        return None
    if kind == "number":
        return Field(
            key,
            NUMBER,
            required,
            low=_number(settings.get("min")),
            high=_number(settings.get("max")),
            step=_number(settings.get("step")),
            unit=_unit(settings.get("unit_of_measurement")),
        )
    if kind == "color_temp":
        kelvin = settings.get("unit") == "kelvin"
        return Field(
            key,
            NUMBER,
            required,
            low=_number(settings.get("min", settings.get("min_mireds"))),
            high=_number(settings.get("max", settings.get("max_mireds"))),
            unit="K" if kelvin else "mired",
        )
    if kind == "boolean":
        return Field(key, BOOLEAN, required)
    if kind == "select":
        options = None if settings.get("custom_value") else _options(settings.get("options"))
        if options is None:
            return Field(key, TEXT, required)
        return Field(key, SELECT, required, options=options)
    if kind in _TEXT_SELECTORS and not _HOST_PATH.search(key):
        return Field(key, TEXT, required)
    return None


def field_configs(description: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """The description of each field of a service, by key, sections included."""
    configs: dict[str, Mapping[str, Any]] = {}
    for key, config in _flat_fields(description.get("fields")):
        configs.setdefault(key, config)
    return configs


def field_info(field: Field, name: str) -> dict[str, Any]:
    """A field as the announce carries it (contract §2.5)."""
    return {
        "key": field.key,
        "name": name,
        "type": field.kind,
        "required": field.required,
        "min": field.low,
        "max": field.high,
        "step": field.step,
        "unit": field.unit,
        "options": list(field.options) if field.options is not None else None,
    }


def _options(options: Any) -> tuple[str, ...] | None:
    """The values of a short list of choices, or None if they do not fit as choices."""
    if not isinstance(options, list) or not options or len(options) > MAX_OPTIONS:
        return None
    values: list[str] = []
    for option in options:
        value = option.get("value") if isinstance(option, Mapping) else option
        if not isinstance(value, str) or not value or len(value) > LABEL_MAX or _CONTROL.search(value):
            return None
        if value not in values:
            values.append(value)
    return tuple(values)


def _number(value: Any) -> float | None:
    if not _is_number(value) or not math.isfinite(value):
        return None
    return int(value) if float(value).is_integer() else float(value)


def _unit(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > UNIT_MAX or _UNSAFE_UNIT.search(value):
        return None
    return value


def _device_class(entry: er.RegistryEntry, state: State | None) -> str | None:
    if state is not None:
        value = state.attributes.get(ATTR_DEVICE_CLASS)
        return value if isinstance(value, str) else None
    return entry.device_class or entry.original_device_class


def _features(entry: er.RegistryEntry, state: State | None) -> int:
    if state is not None:
        return _int(state.attributes.get(ATTR_SUPPORTED_FEATURES))
    return _int(entry.supported_features)


def _any_of(values: list[Any], value: Any) -> bool:
    if isinstance(value, (list, tuple)):
        return any(item in values for item in value)
    if isinstance(value, Mapping):
        return False
    return value in values


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return [value]


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _int(value: Any) -> int:
    return int(value) if _is_int(value) else 0
