"""Numbers: the active keep-warm temperature, plus each of the 5 profiles'
keep-warm temperature and keep-warm duration."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import YunmiKettleConfigEntry
from .const import (
    DURATION_UNIT_MIN,
    MAX_DURATION,
    MAX_TEMP,
    MIN_DURATION,
    MIN_TEMP,
    PROFILE_COUNT,
)
from .entity import YunmiKettleEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: YunmiKettleConfigEntry, add: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[NumberEntity] = []
    for n in range(1, PROFILE_COUNT + 1):
        entities.append(KettleProfileTemp(coordinator, n))
        entities.append(KettleProfileDuration(coordinator, n))
    add(entities)


class KettleProfileTemp(YunmiKettleEntity, NumberEntity):
    _attr_translation_key = "profile_temp"
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = MIN_TEMP
    _attr_native_max_value = MAX_TEMP
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:thermometer"

    def __init__(self, coordinator, profile: int) -> None:
        super().__init__(coordinator, f"profile_temp_{profile}")
        self._profile = profile
        self._attr_translation_placeholders = {"profile": str(profile)}

    @property
    def native_value(self) -> int | None:
        return (self.coordinator.data or {}).get(f"profile_temp_{self._profile}")

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_profile_temp(self._profile, int(value))


class KettleProfileDuration(YunmiKettleEntity, NumberEntity):
    _attr_translation_key = "profile_duration"
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = MIN_DURATION
    _attr_native_max_value = MAX_DURATION
    _attr_native_step = DURATION_UNIT_MIN
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator, profile: int) -> None:
        super().__init__(coordinator, f"profile_duration_{profile}")
        self._profile = profile
        self._attr_translation_placeholders = {"profile": str(profile)}

    @property
    def native_value(self) -> int | None:
        return (self.coordinator.data or {}).get(f"profile_duration_{self._profile}")

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_profile_duration(self._profile, int(value))
