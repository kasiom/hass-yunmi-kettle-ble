"""Diagnostics support for Yunmi Kettle."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import YunmiKettleConfigEntry
from .const import CONF_MAC, CONF_TOKEN
from .protocol import KettleError

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
            "bluetooth_available": coordinator.bluetooth_available,
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
            "last_rssi": coordinator.last_rssi,
            "connection_started_at": (
                coordinator.connection_started_at.isoformat()
                if coordinator.connection_started_at
                else None
            ),
            "connection_uptime_seconds": (
                round(
                    (datetime.now(UTC) - coordinator.connection_started_at).total_seconds(),
                    1,
                )
                if coordinator.connection_started_at
                else None
            ),
            "last_disconnect": (
                coordinator.last_disconnect.isoformat() if coordinator.last_disconnect else None
            ),
            "last_push": (coordinator.last_push.isoformat() if coordinator.last_push else None),
            "last_watchdog": (
                coordinator.last_watchdog.isoformat() if coordinator.last_watchdog else None
            ),
            "connection_count": coordinator.connection_count,
            "connection_attempt_count": coordinator.connection_attempt_count,
            "disconnect_count": coordinator.disconnect_count,
            "service_cache_clear_count": coordinator.service_cache_clear_count,
            "full_snapshot_count": coordinator.full_snapshot_count,
            "push_count": coordinator.push_count,
            "watchdog_count": coordinator.watchdog_count,
            "consecutive_failures": coordinator.consecutive_failures,
            "gatt_failure_streak": coordinator.gatt_failure_streak,
            "gatt_quiet_mode": coordinator.gatt_quiet_mode,
            "reconnect_backoff_remaining": round(coordinator.reconnect_backoff_remaining, 1),
            "last_error_type": type(error).__name__ if error else None,
            "last_error_message": str(error) if isinstance(error, KettleError) else None,
            "data": dict(coordinator.data or {}),
        },
    }
