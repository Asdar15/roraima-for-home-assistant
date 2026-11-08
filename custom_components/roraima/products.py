"""Roraima products in Home Assistant: their state and the notices of sales and uses.

Roraima tells this Home Assistant how the seller's products are doing (roraima_state) and
sends a notice at each paid sale, start and end of a use (roraima_event). Each product is a
service device with three sensors; each notice becomes a Home Assistant event that starts
automations. Nothing here sends anything back to Roraima.

The messages never carry buyer data, and the integration keeps it that way on its side too:
every field is rebuilt from the few the contract allows, and anything else is dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import DOMAIN, PANEL_URL

_LOGGER = logging.getLogger(__name__)

EVENT_RORAIMA = "roraima_event"
EVENT_TYPES = ("purchase", "start", "end")
KINDS = ("time", "tokens")
PRODUCT_STATES = ("free", "in_use")
MAX_PRODUCTS = 100
MAX_NAME = 255

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_UTC_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_IDENTIFIER = re.compile(r"^(?P<entry>.+)_product_(?P<pid>[1-9]\d{0,18})$")


def new_product_signal(entry_id: str) -> str:
    """Dispatcher signal sent when a product needs its sensors."""
    return f"{DOMAIN}_{entry_id}_new_product"


def product_signal(entry_id: str, product_id: int) -> str:
    """Dispatcher signal sent when the state of one product changes."""
    return f"{DOMAIN}_{entry_id}_product_{product_id}"


def product_identifier(entry_id: str, product_id: int) -> tuple[str, str]:
    """Device registry identifier of a product."""
    return (DOMAIN, f"{entry_id}_product_{product_id}")


def _product_id(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 10**19:
        return value
    return None


def _count(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**6:
        return value
    return None


def _name(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    text = _CONTROL.sub("", value).strip()[:MAX_NAME]
    return text or fallback


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not _UTC_TIME.fullmatch(value):
        return None
    return dt_util.parse_datetime(value)


def _choice(value: Any, allowed: tuple[str, ...]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


@dataclass(slots=True)
class Product:
    """What Home Assistant knows about one Roraima product."""

    product_id: int
    name: str
    kind: str | None = None
    state: str | None = None
    busy_until: datetime | None = None
    last_sale_at: datetime | None = None
    known: bool = False


class ProductHub:
    """Keep the products of this connection and turn notices into events."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Start from the product devices already in the registry."""
        self.hass = hass
        self.entry = entry
        self.products: dict[int, Product] = {}
        self.events_fired = 0
        self.ignored = 0
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
            product_id = self._own_product(device)
            if product_id is not None:
                self.products[product_id] = Product(product_id, device.name or str(product_id))

    def _own_product(self, device: dr.DeviceEntry) -> int | None:
        for domain, value in device.identifiers:
            if domain != DOMAIN:
                continue
            match = _IDENTIFIER.fullmatch(value)
            if match and match["entry"] == self.entry.entry_id:
                return int(match["pid"])
        return None

    def device_id(self, product_id: int) -> str | None:
        """Registry id of the device of a product."""
        device = dr.async_get(self.hass).async_get_device(
            identifiers={product_identifier(self.entry.entry_id, product_id)}
        )
        return device.id if device else None

    @callback
    def async_on_state(self, data: dict[str, Any]) -> None:
        """Apply a roraima_state: the full list after welcome, one product after a notice."""
        items = data.get("products")
        if not isinstance(items, list):
            self.ignored += 1
            return
        complete = data.get("complete") is True
        seen: set[int] = set()
        for item in items[:MAX_PRODUCTS]:
            product = self._parse_state(item)
            if product is None:
                self.ignored += 1
                continue
            seen.add(product.product_id)
            self._store(product)
        if complete:
            self._remove_missing(seen)

    def _parse_state(self, item: Any) -> Product | None:
        if not isinstance(item, dict):
            return None
        product_id = _product_id(item.get("product_id"))
        state = _choice(item.get("state"), PRODUCT_STATES)
        if product_id is None or state is None:
            return None
        return Product(
            product_id=product_id,
            name=_name(item.get("name"), str(product_id)),
            kind=_choice(item.get("kind"), KINDS),
            state=state,
            busy_until=_time(item.get("busy_until")) if state == "in_use" else None,
            last_sale_at=_time(item.get("last_sale_at")),
            known=True,
        )

    @callback
    def _store(self, product: Product) -> None:
        old = self.products.get(product.product_id)
        is_new = old is None or self.device_id(product.product_id) is None
        if old is not None and not product.known:
            # A notice names the product but does not say its state: keep what we know.
            product.state = old.state
            product.busy_until = old.busy_until
            product.last_sale_at = old.last_sale_at
            product.known = old.known
            product.kind = product.kind or old.kind
        self.products[product.product_id] = product
        self._ensure_device(product)
        if is_new:
            async_dispatcher_send(self.hass, new_product_signal(self.entry.entry_id), product.product_id)
        async_dispatcher_send(
            self.hass, product_signal(self.entry.entry_id, product.product_id)
        )

    @callback
    def _ensure_device(self, product: Product) -> None:
        dev_reg = dr.async_get(self.hass)
        identifier = product_identifier(self.entry.entry_id, product.product_id)
        device = dev_reg.async_get_device(identifiers={identifier})
        if device is not None:
            if device.name != product.name:
                dev_reg.async_update_device(device.id, name=product.name)
            return
        hub = dev_reg.async_get_device(identifiers={(DOMAIN, self.entry.entry_id)})
        dev_reg.async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={identifier},
            name=product.name,
            manufacturer="Roraima",
            entry_type=dr.DeviceEntryType.SERVICE,
            configuration_url=PANEL_URL,
            via_device=(DOMAIN, self.entry.entry_id) if hub is not None else None,
        )

    @callback
    def _remove_missing(self, seen: set[int]) -> None:
        dev_reg = dr.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(dev_reg, self.entry.entry_id):
            product_id = self._own_product(device)
            if product_id is not None and product_id not in seen:
                _LOGGER.info("Roraima product %s is no longer part of this connection", product_id)
                dev_reg.async_update_device(device.id, remove_config_entry_id=self.entry.entry_id)
        for product_id in [p for p in self.products if p not in seen]:
            del self.products[product_id]

    @callback
    def async_on_event(self, data: dict[str, Any]) -> None:
        """Turn a roraima_event into a Home Assistant event, with the allowed fields only."""
        event = _choice(data.get("event"), EVENT_TYPES)
        product = data.get("product")
        product_id = _product_id(product.get("product_id")) if isinstance(product, dict) else None
        at = _time(data.get("at"))
        if event is None or product_id is None or at is None:
            self.ignored += 1
            _LOGGER.debug("Ignoring a Roraima notice without a valid type, product or time")
            return
        name = _name(product.get("name"), str(product_id))
        kind = _choice(data.get("kind"), KINDS)
        self._store(Product(product_id, name, kind))
        program = data.get("program")
        program_name = (
            _name(program.get("name"), "") or None if isinstance(program, dict) else None
        )
        ends_at = _time(data.get("ends_at"))
        payload = {
            "device_id": self.device_id(product_id),
            "type": event,
            "product_id": product_id,
            "product_name": name,
            "kind": kind,
            "at": at.isoformat(),
            "duration_minutes": _count(data.get("duration_minutes")),
            "tokens": _count(data.get("tokens")),
            "program": program_name,
            "ends_at": ends_at.isoformat() if ends_at else None,
        }
        self.events_fired += 1
        self.hass.bus.async_fire(EVENT_RORAIMA, payload)

    def diagnostics(self) -> dict[str, Any]:
        """Counts only: product names are the seller's, not needed for support."""
        return {
            "products": len(self.products),
            "events_fired": self.events_fired,
            "ignored": self.ignored,
        }
