"""Connection + polling coordinator for the Yunmi kettle."""
from __future__ import annotations

import logging
from datetime import timedelta

from bleak import BleakClient
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_MAC,
    CONF_POLL_INTERVAL,
    CONF_TOKEN,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    DURATION_UNIT_MIN,
    PROFILE_COUNT,
    PROP_ACTIVE_PROFILE,
    PROP_CURRENT_TEMP,
    PROP_KEEPWARM_SWITCH,
    PROP_PROFILE_DURATION,
    PROP_PROFILE_TEMP,
    PROP_STATUS,
    VTYPE_U8,
)
from .protocol import YunmiKettle

_LOGGER = logging.getLogger(__name__)


class YunmiKettleCoordinator(DataUpdateCoordinator[dict]):
    """Owns the BLE session; polls status/temp; sends commands."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.address: str = entry.data[CONF_MAC]
        self._token = bytes.fromhex(entry.data[CONF_TOKEN])
        interval = entry.options.get(
            CONF_POLL_INTERVAL, entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        )
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}_{self.address}",
            update_interval=timedelta(seconds=interval),
        )
        self._client: BleakClient | None = None
        self._dev: YunmiKettle | None = None

    async def _ensure_session(self) -> YunmiKettle:
        if self._dev and self._client and self._client.is_connected:
            return self._dev
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if not ble_device:
            raise UpdateFailed(f"{self.address} not in range of any Bluetooth adapter/proxy")
        self._client = await establish_connection(BleakClient, ble_device, self.address)
        self._dev = YunmiKettle(self._client, self._token)
        await self._dev.start()
        await self._dev.login()
        return self._dev

    async def _async_update_data(self) -> dict:
        try:
            dev = await self._ensure_session()
            res = await dev.subscribe_and_read()
            data: dict = dict(self.data or {})
            if PROP_CURRENT_TEMP in res:
                data["current_temp"] = res[PROP_CURRENT_TEMP]
            if PROP_STATUS in res:
                data["status"] = res[PROP_STATUS]
            if PROP_KEEPWARM_SWITCH in res:
                data["keepwarm"] = res[PROP_KEEPWARM_SWITCH]
            if PROP_ACTIVE_PROFILE in res:
                data["active_profile"] = res[PROP_ACTIVE_PROFILE]
            for n in range(1, PROFILE_COUNT + 1):
                if PROP_PROFILE_TEMP(n) in res:
                    data[f"profile_temp_{n}"] = res[PROP_PROFILE_TEMP(n)]
                if PROP_PROFILE_DURATION(n) in res:
                    data[f"profile_duration_{n}"] = res[PROP_PROFILE_DURATION(n)] * DURATION_UNIT_MIN
            # active profile's temp, for the "current keep-warm temp" number
            active = data.get("active_profile", 0)
            if f"profile_temp_{active + 1}" in data:
                data["profile_temp"] = data[f"profile_temp_{active + 1}"]
            return data
        except Exception as err:  # noqa: BLE001
            await self._drop()
            raise UpdateFailed(str(err)) from err

    async def _drop(self) -> None:
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self._client = None
        self._dev = None

    async def async_shutdown(self) -> None:  # type: ignore[override]
        await super().async_shutdown()
        await self._drop()

    # --- commands used by entities ---

    def _apply(self, echo: dict, extra: dict | None = None) -> None:
        """Fold the device's post-set echo (op 0x04) + optimistic values into data."""
        data = dict(self.data or {})
        data.update(extra or {})
        for (siid, piid), val in echo.items():
            if (siid, piid) == PROP_CURRENT_TEMP:
                data["current_temp"] = val
            elif (siid, piid) == PROP_STATUS:
                data["status"] = val
            elif (siid, piid) == PROP_KEEPWARM_SWITCH:
                data["keepwarm"] = val
            elif (siid, piid) == PROP_ACTIVE_PROFILE:
                data["active_profile"] = val
            elif siid == 5 and 1 <= piid <= PROFILE_COUNT:
                data[f"profile_temp_{piid}"] = val
            elif siid == 5 and 6 <= piid <= 5 + PROFILE_COUNT:
                data[f"profile_duration_{piid - 5}"] = val * DURATION_UNIT_MIN
        active = data.get("active_profile", 0)
        if f"profile_temp_{active + 1}" in data:
            data["profile_temp"] = data[f"profile_temp_{active + 1}"]
        self.async_set_updated_data(data)

    async def async_set_profile_temp(self, profile: int, temp: int) -> None:
        """profile is 1-based."""
        dev = await self._ensure_session()
        echo = await dev.set_property(*PROP_PROFILE_TEMP(profile), int(temp), VTYPE_U8)
        self._apply(echo, {f"profile_temp_{profile}": int(temp)})

    async def async_set_profile_duration(self, profile: int, minutes: int) -> None:
        """profile is 1-based; minutes is rounded to the device's 30-min units."""
        dev = await self._ensure_session()
        raw = max(1, round(int(minutes) / DURATION_UNIT_MIN))
        echo = await dev.set_property(*PROP_PROFILE_DURATION(profile), raw, VTYPE_U8)
        self._apply(echo, {f"profile_duration_{profile}": raw * DURATION_UNIT_MIN})

    async def async_set_active_profile(self, profile: int) -> None:
        """profile is 1-based; on-device selector is 0-based."""
        dev = await self._ensure_session()
        echo = await dev.set_property(*PROP_ACTIVE_PROFILE, profile - 1, VTYPE_U8)
        self._apply(echo, {"active_profile": profile - 1})

    async def async_set_keepwarm(self, on: bool) -> None:
        dev = await self._ensure_session()
        echo = await dev.set_property(*PROP_KEEPWARM_SWITCH, 1 if on else 0, VTYPE_U8)
        self._apply(echo, {"keepwarm": 1 if on else 0})
