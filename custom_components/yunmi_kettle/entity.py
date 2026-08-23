"""Base entity for Yunmi Kettle."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_NAME, DEFAULT_NAME, DOMAIN
from .coordinator import YunmiKettleCoordinator


class YunmiKettleEntity(CoordinatorEntity[YunmiKettleCoordinator]):
    """Common base: shared device info + availability + unique_id prefix."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: YunmiKettleCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_device_info = DeviceInfo(
            connections={("bluetooth", coordinator.address)},
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.entry.data.get(CONF_NAME, DEFAULT_NAME),
            manufacturer="Xiaomi",
            model="Mi Smart Kettle Pro (yunmi.kettle.v14)",
        )
