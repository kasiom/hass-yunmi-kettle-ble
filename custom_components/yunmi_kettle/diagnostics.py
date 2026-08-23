"""Diagnostics support for Yunmi Kettle."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import YunmiKettleConfigEntry
from .const import CONF_MAC, CONF_TOKEN

TO_REDACT = {CONF_MAC, CONF_TOKEN}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: YunmiKettleConfigEntry
) -> dict[str, Any]:
    """Return redacted connection and state diagnostics."""
    coordinator = entry.runtime_data
    error = coordinator.last_error
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
            "source": entry.source,
            "version": entry.version,
        },
        "coordinator": {
            "connected": coordinator.connected,
            "poll_interval": coordinator.poll_interval,
            "last_update_success": coordinator.last_update_success,
            "last_advertisement": (
                coordinator.last_advertisement.isoformat()
                if coordinator.last_advertisement
                else None
            ),
            "last_success": (
                coordinator.last_success.isoformat() if coordinator.last_success else None
            ),
            "consecutive_failures": coordinator.consecutive_failures,
            "reconnect_backoff_remaining": round(coordinator.reconnect_backoff_remaining, 1),
            "last_error_type": type(error).__name__ if error else None,
            "data": dict(coordinator.data or {}),
        },
    }
