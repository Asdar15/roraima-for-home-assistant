"""The three sensors of each Roraima product: status, free at, last sale."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .client import STATUS_CONNECTED, RoraimaClient, update_signal
from .products import PRODUCT_STATES, new_product_signal, product_identifier, product_signal


@callback
def async_setup_product_sensors(
    hass: HomeAssistant,
    client: RoraimaClient,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors of known products now, and of new products when they arrive."""
    entry = client.entry
    added: set[int] = set()

    @callback
    def _add(product_id: int) -> None:
        if product_id in added or product_id not in client.products.products:
            return
        added.add(product_id)
        async_add_entities(
            [
                ProductStatusSensor(client, product_id, added),
                ProductFreeAtSensor(client, product_id, added),
                ProductLastSaleSensor(client, product_id, added),
            ]
        )

    for product_id in list(client.products.products):
        _add(product_id)
    entry.async_on_unload(
        async_dispatcher_connect(hass, new_product_signal(entry.entry_id), _add)
    )


class ProductSensor(SensorEntity):
    """A sensor of one Roraima product, available while the connection is open."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, client: RoraimaClient, product_id: int, key: str, added: set[int]
    ) -> None:
        """Attach the sensor to the device of its product."""
        self._client = client
        self._product_id = product_id
        self._added = added
        entry_id = client.entry.entry_id
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry_id}_product_{product_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={product_identifier(entry_id, product_id)}
        )

    @property
    def _product(self):
        return self._client.products.products.get(self._product_id)

    @property
    def available(self) -> bool:
        """Values are current only while Roraima can send them."""
        product = self._product
        return (
            self._client.status == STATUS_CONNECTED
            and product is not None
            and product.known
        )

    async def async_added_to_hass(self) -> None:
        """Follow the product and the connection."""
        entry_id = self._client.entry.entry_id
        for signal in (product_signal(entry_id, self._product_id), update_signal(entry_id)):
            self.async_on_remove(
                async_dispatcher_connect(self.hass, signal, self._updated)
            )

    async def async_will_remove_from_hass(self) -> None:
        """Let the product get new sensors if it comes back."""
        self._added.discard(self._product_id)

    @callback
    def _updated(self) -> None:
        self.async_write_ha_state()


class ProductStatusSensor(ProductSensor):
    """Free or in use."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(PRODUCT_STATES)

    def __init__(self, client: RoraimaClient, product_id: int, added: set[int]) -> None:
        """Create the status sensor."""
        super().__init__(client, product_id, "product_status", added)

    @property
    def native_value(self) -> str | None:
        """Current status of the product."""
        product = self._product
        return product.state if product else None


class ProductFreeAtSensor(ProductSensor):
    """When the current use is expected to end; empty while the product is free."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, client: RoraimaClient, product_id: int, added: set[int]) -> None:
        """Create the free-at sensor."""
        super().__init__(client, product_id, "product_free_at", added)

    @property
    def native_value(self) -> datetime | None:
        """Expected end of the current use."""
        product = self._product
        return product.busy_until if product else None


class ProductLastSaleSensor(ProductSensor):
    """When the product was last sold."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, client: RoraimaClient, product_id: int, added: set[int]) -> None:
        """Create the last sale sensor."""
        super().__init__(client, product_id, "product_last_sale", added)

    @property
    def native_value(self) -> datetime | None:
        """Time of the last paid sale."""
        product = self._product
        return product.last_sale_at if product else None
