"""The Yunmi Kettle (Mi Smart Kettle Pro, BLE) integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, LEGACY_CONF_TARGET_TEMP, PLATFORMS
from .coordinator import YunmiKettleCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Yunmi Kettle from a config entry."""
    coordinator = YunmiKettleCoordinator(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    coordinator.async_start()
    # A powered-off kettle is a normal state. Load the entities as unavailable
    # and let the Bluetooth advertisement callback reconnect when it returns.
    await coordinator.async_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
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


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
