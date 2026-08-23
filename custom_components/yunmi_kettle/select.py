"""Select: which of the 5 keep-warm profiles is active."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import YunmiKettleConfigEntry
from .const import PROFILE_COUNT
from .entity import YunmiKettleEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: YunmiKettleConfigEntry, add: AddEntitiesCallback
) -> None:
    add([KettleActiveProfile(entry.runtime_data)])


class KettleActiveProfile(YunmiKettleEntity, SelectEntity):
    _attr_translation_key = "active_profile"
    _attr_icon = "mdi:format-list-numbered"
    _attr_options = [str(n) for n in range(1, PROFILE_COUNT + 1)]

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "active_profile")

    @property
    def current_option(self) -> str | None:
        active = (self.coordinator.data or {}).get("active_profile")
        return None if active is None else str(active + 1)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_active_profile(int(option))
