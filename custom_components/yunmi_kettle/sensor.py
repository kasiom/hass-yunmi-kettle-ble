"""Sensors: current water temperature and status."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import YunmiKettleConfigEntry
from .const import STATUS_NAMES
from .entity import YunmiKettleEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: YunmiKettleConfigEntry, add: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    add([KettleTemp(coordinator), KettleStatus(coordinator), KettleKeepwarmTarget(coordinator)])


class KettleTemp(YunmiKettleEntity, SensorEntity):
    _attr_translation_key = "current_temp"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "current_temp")

    @property
    def native_value(self) -> int | None:
        return (self.coordinator.data or {}).get("current_temp")


class KettleStatus(YunmiKettleEntity, SensorEntity):
    _attr_translation_key = "status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(STATUS_NAMES.values())

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "status")

    @property
    def native_value(self) -> str | None:
        return STATUS_NAMES.get((self.coordinator.data or {}).get("status"))


class KettleKeepwarmTarget(YunmiKettleEntity, SensorEntity):
    """The temperature the kettle will keep warm at = the active profile's preset.
    Read-only; edit it via the per-profile temperature numbers + the profile select."""

    _attr_translation_key = "keepwarm_target"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:target"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "keepwarm_target")

    @property
    def native_value(self) -> int | None:
        return (self.coordinator.data or {}).get("profile_temp")
