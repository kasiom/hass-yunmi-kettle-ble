"""Switch: keep-warm on/off (insulation switch)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import YunmiKettleConfigEntry
from .entity import YunmiKettleEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: YunmiKettleConfigEntry, add: AddEntitiesCallback
) -> None:
    add([KettleKeepWarm(entry.runtime_data)])


class KettleKeepWarm(YunmiKettleEntity, SwitchEntity):
    _attr_translation_key = "keepwarm"
    _attr_icon = "mdi:kettle-steam"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "keepwarm")

    @property
    def is_on(self) -> bool | None:
        v = (self.coordinator.data or {}).get("keepwarm")
        return None if v is None else bool(v)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_keepwarm(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_keepwarm(False)
