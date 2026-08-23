"""Offline-aware BLE coordinator for the Yunmi kettle."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from time import monotonic

from bleak import BleakClient
from bleak_retry_connector import establish_connection
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_MAC,
    CONF_POLL_INTERVAL,
    CONF_TOKEN,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    DURATION_UNIT_MIN,
    MAX_RECONNECT_BACKOFF,
    PROFILE_COUNT,
    PROP_ACTIVE_PROFILE,
    PROP_CURRENT_TEMP,
    PROP_KEEPWARM_SWITCH,
    PROP_PROFILE_DURATION,
    PROP_PROFILE_TEMP,
    PROP_STATUS,
    TRANSIENT_RETRY_INTERVAL,
    VTYPE_U8,
)
from .protocol import KettleError, YunmiKettle

_LOGGER = logging.getLogger(__name__)


class YunmiKettleCoordinator(DataUpdateCoordinator[dict[str, int]]):
    """Own the BLE session, publish pushes and serialize all GATT traffic."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.address: str = entry.data[CONF_MAC]
        self._token = bytes.fromhex(entry.data[CONF_TOKEN])
        self.poll_interval = int(
            entry.options.get(
                CONF_POLL_INTERVAL,
                entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
            )
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{self.address}",
            update_interval=timedelta(seconds=self.poll_interval),
            always_update=False,
        )
        self._client: BleakClient | None = None
        self._dev: YunmiKettle | None = None
        self._operation_lock = asyncio.Lock()
        self._unregister_bluetooth = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._dropping = False
        self._consecutive_failures = 0
        self._retry_not_before = 0.0
        self._last_advertisement: datetime | None = None
        self._last_success: datetime | None = None
        self._last_error: Exception | None = None

    @property
    def connected(self) -> bool:
        """Return whether the persistent authenticated BLE session is alive."""
        return bool(self._client and self._client.is_connected and self._dev)

    @property
    def last_advertisement(self) -> datetime | None:
        """Return the last time Home Assistant saw the kettle advertise."""
        return self._last_advertisement

    @property
    def last_success(self) -> datetime | None:
        """Return the last successful snapshot or push timestamp."""
        return self._last_success

    @property
    def last_error(self) -> Exception | None:
        """Return the most recent communication error."""
        return self._last_error

    @property
    def consecutive_failures(self) -> int:
        """Return the current consecutive communication failure count."""
        return self._consecutive_failures

    @property
    def reconnect_backoff_remaining(self) -> float:
        """Return seconds until an advertisement may trigger another attempt."""
        return max(0.0, self._retry_not_before - monotonic())

    def async_start(self) -> None:
        """Start watching advertisements so a powered-on kettle reconnects now."""
        if self._unregister_bluetooth is not None:
            return
        self._unregister_bluetooth = bluetooth.async_register_callback(
            self.hass,
            self._async_bluetooth_callback,
            BluetoothCallbackMatcher(address=self.address, connectable=True),
            BluetoothScanningMode.PASSIVE,
        )

    @callback
    def _async_bluetooth_callback(
        self, service_info: BluetoothServiceInfoBleak, _change: object
    ) -> None:
        self._last_advertisement = datetime.now(UTC)
        if self.connected or monotonic() < self._retry_not_before:
            return
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        self._reconnect_task = self.hass.async_create_task(
            self._async_refresh_after_advertisement(),
            f"{DOMAIN} reconnect {service_info.address}",
        )

    async def _async_refresh_after_advertisement(self) -> None:
        try:
            await self.async_request_refresh()
        finally:
            self._reconnect_task = None

    def _disconnected_callback(self, client: BleakClient) -> None:
        self.hass.loop.call_soon_threadsafe(self._async_schedule_disconnected, client)

    @callback
    def _async_schedule_disconnected(self, client: BleakClient) -> None:
        if self._dropping or client is not self._client:
            return
        self.hass.async_create_task(
            self._async_handle_disconnected(client),
            f"{DOMAIN} disconnected {self.address}",
        )

    async def _async_handle_disconnected(self, client: BleakClient) -> None:
        if client is not self._client:
            return
        dev = self._dev
        self._client = None
        self._dev = None
        if dev is not None:
            await dev.async_stop()
        error = KettleError("BLE connection closed")
        self._last_error = error
        self._consecutive_failures += 1
        self.update_interval = None
        self._clear_advertisement_history()
        # Do not start a long connection attempt against a kettle whose smart
        # plug was just switched off. The next real advertisement reconnects it.
        self.async_set_update_error(error)

    async def _ensure_session(self) -> YunmiKettle:
        if self.connected:
            assert self._dev is not None
            return self._dev
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise KettleError("device is not in range of a Bluetooth adapter or proxy")

        client = await establish_connection(
            BleakClient,
            ble_device,
            self.address,
            disconnected_callback=self._disconnected_callback,
        )
        dev = YunmiKettle(client, self._token, self._async_handle_push)
        try:
            await dev.start()
            await dev.login()
        except Exception:
            await dev.async_stop()
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            raise
        self._client = client
        self._dev = dev
        return dev

    def _merge_properties(
        self,
        properties: dict[tuple[int, int], int],
        current: dict[str, int] | None = None,
    ) -> dict[str, int]:
        data = dict(current if current is not None else (self.data or {}))
        for (siid, piid), value in properties.items():
            if (siid, piid) == PROP_CURRENT_TEMP:
                data["current_temp"] = value
            elif (siid, piid) == PROP_STATUS:
                data["status"] = value
            elif (siid, piid) == PROP_KEEPWARM_SWITCH:
                data["keepwarm"] = value
            elif (siid, piid) == PROP_ACTIVE_PROFILE:
                data["active_profile"] = value
            elif siid == 5 and 1 <= piid <= PROFILE_COUNT:
                data[f"profile_temp_{piid}"] = value
            elif siid == 5 and 6 <= piid <= 5 + PROFILE_COUNT:
                data[f"profile_duration_{piid - 5}"] = value * DURATION_UNIT_MIN
        active = data.get("active_profile")
        if active is not None and f"profile_temp_{active + 1}" in data:
            data["profile_temp"] = data[f"profile_temp_{active + 1}"]
        return data

    @callback
    def _async_handle_push(self, properties: dict[tuple[int, int], int]) -> None:
        """Publish unsolicited property pushes without waiting for a poll."""
        self._mark_success()
        self.async_set_updated_data(self._merge_properties(properties))

    def _mark_success(self) -> None:
        self._last_success = datetime.now(UTC)
        self._last_error = None
        self._consecutive_failures = 0
        self._retry_not_before = 0.0
        self.update_interval = timedelta(seconds=self.poll_interval)

    def _handle_update_failure(self, err: Exception) -> dict[str, int]:
        self._last_error = err
        self._consecutive_failures += 1
        # HA de-duplicates identical advertisements. Forget the last packet so
        # switching the kettle's smart plug back on always wakes this entry.
        self._clear_advertisement_history()
        delay = min(
            TRANSIENT_RETRY_INTERVAL * 2 ** (self._consecutive_failures - 1),
            MAX_RECONNECT_BACKOFF,
        )
        self._retry_not_before = monotonic() + delay
        if self.data is not None and self._consecutive_failures == 1:
            self.update_interval = timedelta(seconds=TRANSIENT_RETRY_INTERVAL)
            _LOGGER.warning(
                "Temporary kettle communication failure; retrying in %s seconds: %s",
                TRANSIENT_RETRY_INTERVAL,
                err,
            )
            return dict(self.data)
        self.update_interval = None
        raise UpdateFailed(str(err)) from err

    def _clear_advertisement_history(self) -> None:
        """Allow the next identical wake-up packet to trigger the callback."""
        # This public helper was added in Home Assistant 2026.5. Retain basic
        # compatibility with older supported releases where it is not exposed.
        if clear_history := getattr(bluetooth, "async_clear_advertisement_history", None):
            clear_history(self.hass, self.address)

    async def _async_update_data(self) -> dict[str, int]:
        async with self._operation_lock:
            try:
                dev = await self._ensure_session()
                properties = await dev.subscribe_and_read()
            except Exception as err:  # noqa: BLE001
                await self._drop()
                return self._handle_update_failure(err)
        self._mark_success()
        return self._merge_properties(properties)

    async def _drop(self) -> None:
        client = self._client
        dev = self._dev
        self._client = None
        self._dev = None
        self._dropping = True
        try:
            if dev is not None:
                await dev.async_stop()
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
        finally:
            self._dropping = False

    async def async_shutdown(self) -> None:  # type: ignore[override]
        if self._unregister_bluetooth is not None:
            self._unregister_bluetooth()
            self._unregister_bluetooth = None
        if self._reconnect_task is not None and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            await asyncio.gather(self._reconnect_task, return_exceptions=True)
        await super().async_shutdown()
        await self._drop()

    async def _async_set_property(self, pair: tuple[int, int], value: int) -> None:
        async with self._operation_lock:
            try:
                dev = await self._ensure_session()
                echo = await dev.set_property(*pair, value, VTYPE_U8)
            except Exception as err:  # noqa: BLE001
                await self._drop()
                self._last_error = err
                self._consecutive_failures += 1
                self._retry_not_before = monotonic() + TRANSIENT_RETRY_INTERVAL
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="communication_failed",
                    translation_placeholders={"details": str(err)},
                ) from err
        self._mark_success()
        self.async_set_updated_data(self._merge_properties(echo))

    async def async_set_profile_temp(self, profile: int, temp: int) -> None:
        """Set a one-based profile temperature and require device confirmation."""
        await self._async_set_property(PROP_PROFILE_TEMP(profile), int(temp))

    async def async_set_profile_duration(self, profile: int, minutes: int) -> None:
        """Set a one-based profile duration in the device's 30-minute units."""
        raw = max(1, round(int(minutes) / DURATION_UNIT_MIN))
        await self._async_set_property(PROP_PROFILE_DURATION(profile), raw)

    async def async_set_active_profile(self, profile: int) -> None:
        """Select a one-based profile; the device stores a zero-based index."""
        await self._async_set_property(PROP_ACTIVE_PROFILE, profile - 1)

    async def async_set_keepwarm(self, on: bool) -> None:
        """Enable or disable keep-warm after boiling."""
        await self._async_set_property(PROP_KEEPWARM_SWITCH, 1 if on else 0)
