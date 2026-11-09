"""Names of actions, fields and attributes, in the language of this Home Assistant.

Home Assistant translates its own services and attributes; Roraima shows those names as they are.
A name Home Assistant does not translate is the key itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import logging
import re
from typing import Any

from homeassistant.const import ATTR_DEVICE_CLASS
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations

from .const import LABEL_MAX

_LOGGER = logging.getLogger(__name__)

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def label(text: Any, fallback: str) -> str:
    """A name without control characters, at most 64 letters; the fallback if there is none."""
    if isinstance(text, str):
        text = _CONTROL.sub("", text).strip()[:LABEL_MAX].strip()
        if text:
            return text
    return fallback[:LABEL_MAX]


@dataclass(frozen=True, slots=True)
class Names:
    """The translations needed to name the parts of one announce round."""

    services: Mapping[str, str] = field(default_factory=dict)
    components: Mapping[str, str] = field(default_factory=dict)
    platforms: Mapping[str, str] = field(default_factory=dict)

    def action_name(self, domain: str, service: str, description: Mapping[str, Any]) -> str:
        """The name of a service."""
        text = self.services.get(f"component.{domain}.services.{service}.name")
        return label(text or description.get("name"), service)

    def field_name(self, domain: str, service: str, key: str, config: Mapping[str, Any]) -> str:
        """The name of a field of a service."""
        text = self.services.get(f"component.{domain}.services.{service}.fields.{key}.name")
        return label(text or config.get("name"), key)

    def attribute_name(self, entry: er.RegistryEntry, state: State | None, attribute: str) -> str:
        """The name of an attribute: the integration's own, else the domain's, else the key."""
        if entry.translation_key:
            text = self.platforms.get(
                f"component.{entry.platform}.entity.{entry.domain}."
                f"{entry.translation_key}.state_attributes.{attribute}.name"
            )
            if text:
                return label(text, attribute)
        device_class = state.attributes.get(ATTR_DEVICE_CLASS) if state is not None else None
        if not isinstance(device_class, str):
            device_class = entry.device_class or entry.original_device_class
        for group in (device_class, "_"):
            if group:
                text = self.components.get(
                    f"component.{entry.domain}.entity_component.{group}"
                    f".state_attributes.{attribute}.name"
                )
                if text:
                    return label(text, attribute)
        return label(None, attribute)


async def async_names(hass: HomeAssistant, entries: Iterable[er.RegistryEntry]) -> Names:
    """Load the translations of the domains and integrations of these entities."""
    entries = list(entries)
    domains = {entry.domain for entry in entries}
    platforms = {entry.platform for entry in entries if entry.translation_key}
    language = hass.config.language
    try:
        return Names(
            services=await _async_category(hass, language, "services", domains),
            components=await _async_category(hass, language, "entity_component", domains),
            platforms=await _async_category(hass, language, "entity", platforms),
        )
    except Exception:  # noqa: BLE001 - a name is the key when Home Assistant cannot translate it
        _LOGGER.exception("The translations of Home Assistant could not be read")
        return Names()


async def _async_category(
    hass: HomeAssistant, language: str, category: str, integrations: set[str]
) -> Mapping[str, str]:
    if not integrations:
        return {}
    return await async_get_translations(hass, language, category, integrations)
