"""The Yunmi Kettle (Mi Smart Kettle Pro, BLE) integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import LEGACY_CONF_TARGET_TEMP, PLATFORMS
from .coordinator import YunmiKettleCoordinator

type YunmiKettleConfigEntry = ConfigEntry[YunmiKettleCoordinator]


async def async_reload_entry(hass: HomeAssistant, entry: YunmiKettleConfigEntry) -> None:
    """Reload the config entry after its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: YunmiKettleConfigEntry) -> bool:
    """Set up Yunmi Kettle from a config entry."""
    coordinator = YunmiKettleCoordinator(hass, entry)
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    coordinator.async_start()
    # A powered-off kettle is a normal state. Forward the entities immediately
    # as unavailable; cached or live Bluetooth advertisements start the session.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: YunmiKettleConfigEntry) -> bool:
    """Migrate legacy options without changing entity IDs or device identity."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        data = dict(entry.data)
        options = dict(entry.options)
        data.pop(LEGACY_CONF_TARGET_TEMP, None)
        options.pop(LEGACY_CONF_TARGET_TEMP, None)
        hass.config_entries.async_update_entry(
            entry,
            data=data,
            options=options,
            version=2,
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: YunmiKettleConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
