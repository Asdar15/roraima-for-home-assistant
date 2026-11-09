"""What the seller can share with Roraima: devices, and entities without a device."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import logging
import re

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, DOMAIN_FORM
from .ids import device_row_id, entity_row_id, part_key

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Row:
    """One shareable row: a device of the registry, or an entity that has no device."""

    row_id: str
    label: str
    device: dr.DeviceEntry | None
    parts: dict[str, er.RegistryEntry]


# A group moves all its members, shared or not; so does any entity that lists them (a scene, a
# light group). Scenes, scripts and automations run actions of their own on any entity: like groups,
# they would let Roraima move devices the seller did not choose (law 3). Every other domain is
# shared, with the services Home Assistant offers for it (contract §2.5).
GROUP_PLATFORM = "group"
ATTR_MEMBERS = "entity_id"
RUNNER_DOMAINS = frozenset({"automation", "scene", "script"})
# Where people are is personal data: people, trackers and zones are never shared, nor a part whose
# key tells a place (the geocoded location of a phone has the street address as its state).
LOCATION_DOMAINS = re.compile(r"^(person|device_tracker|zone)$")
LOCATION_KEY = re.compile(r"(^|_)(geocoded|location|address|gps|coordinates)(_|$)")


def _tells_a_place(entry: er.RegistryEntry) -> bool:
    code = part_key(entry.domain, entry.platform, str(entry.unique_id)).split(".", 1)[1]
    return LOCATION_KEY.search(code) is not None


def _shareable(hass: HomeAssistant, entry: er.RegistryEntry) -> bool:
    if (
        not DOMAIN_FORM.fullmatch(entry.domain)
        or entry.domain in RUNNER_DOMAINS
        or LOCATION_DOMAINS.fullmatch(entry.domain) is not None
        or entry.platform in (DOMAIN, GROUP_PLATFORM)
        or entry.disabled_by is not None
        or not entry.unique_id
        or _tells_a_place(entry)
    ):
        return False
    state = hass.states.get(entry.entity_id)
    return state is None or not isinstance(state.attributes.get(ATTR_MEMBERS), (list, tuple))


def _parts(entries: list[er.RegistryEntry]) -> dict[str, er.RegistryEntry]:
    parts: dict[str, er.RegistryEntry] = {}
    for entry in sorted(entries, key=lambda e: str(e.unique_id)):
        key = part_key(entry.domain, entry.platform, str(entry.unique_id))
        if key in parts:
            _LOGGER.warning(
                "Two entities give the part key %s; keeping %s, leaving out %s",
                key,
                parts[key].entity_id,
                entry.entity_id,
            )
            continue
        parts[key] = entry
    return dict(sorted(parts.items()))


def shareable_rows(hass: HomeAssistant) -> dict[str, Row]:
    """Return every shareable row keyed by its shared id, sorted by label."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    area_reg = ar.async_get(hass)

    by_device: dict[str, list[er.RegistryEntry]] = defaultdict(list)
    loose: list[er.RegistryEntry] = []
    for entry in ent_reg.entities.values():
        if not _shareable(hass, entry):
            continue
        if entry.device_id is None:
            loose.append(entry)
        else:
            by_device[entry.device_id].append(entry)

    rows: list[Row] = []
    for registry_id, entries in by_device.items():
        device = dev_reg.async_get(registry_id)
        row_id = device_row_id(registry_id)
        if device is None or device.disabled_by is not None or row_id is None:
            continue
        # Service devices (Backup, Sun, weather...) are not objects to sell; sharing one by
        # mistake would cost the seller a monthly fee.
        if device.entry_type is dr.DeviceEntryType.SERVICE:
            continue
        label = device.name_by_user or device.name or device.model or row_id
        area = area_reg.async_get_area(device.area_id) if device.area_id else None
        if area is not None:
            label = f"{label} ({area.name})"
        rows.append(Row(row_id, label, device, _parts(entries)))
    for entry in loose:
        state = hass.states.get(entry.entity_id)
        label = (
            entry.name
            or entry.original_name
            or (state.name if state is not None else None)
            or entry.entity_id
        )
        row_id = entity_row_id(entry.domain, entry.platform, str(entry.unique_id))
        rows.append(Row(row_id, label, None, _parts([entry])))
    rows.sort(key=lambda row: (row.label.casefold(), row.row_id))
    return {row.row_id: row for row in rows}
