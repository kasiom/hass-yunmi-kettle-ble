"""Offline-aware BLE coordinator for the Yunmi kettle."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from time import monotonic

from bleak_retry_connector import (
    BleakClientWithServiceCache,
    clear_cache,
    establish_connection,
)
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_MAC,
    CONF_POLL_INTERVAL,
    CONF_TOKEN,
    DEFAULT_POLL_INTERVAL,
    DISCONNECT_GRACE_PERIOD,
    DOMAIN,
    DURATION_UNIT_MIN,
    GATT_FAILURES_BEFORE_QUIET,
    GATT_QUIET_RETRY_INTERVAL,
    GATT_RETRY_INTERVAL,
    MAX_GATT_QUIET_BACKOFF,
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
from .protocol import KettleAuthError, KettleError, KettleGattError, YunmiKettle

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
        self._client: BleakClientWithServiceCache | None = None
        self._dev: YunmiKettle | None = None
        self._operation_lock = asyncio.Lock()
        self._unregister_bluetooth = None
        self._unregister_unavailable = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._disconnect_grace_task: asyncio.Task[None] | None = None
        self._disconnect_grace_deadline = 0.0
        self._dropping = False
        self._consecutive_failures = 0
        self._gatt_failure_streak = 0
        self._retry_not_before = 0.0
        self._bluetooth_available = False
        self._last_advertisement: datetime | None = None
        self._last_success: datetime | None = None
        self._last_error: Exception | None = None
        self._last_rssi: int | None = None
        self._connection_started_at: datetime | None = None
        self._last_disconnect: datetime | None = None
        self._last_push: datetime | None = None
        self._last_watchdog: datetime | None = None
        self._connection_count = 0
        self._connection_attempt_count = 0
        self._disconnect_count = 0
        self._service_cache_clear_count = 0
        self._full_snapshot_count = 0
        self._push_count = 0
        self._watchdog_count = 0

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
    def last_rssi(self) -> int | None:
        """Return RSSI from the advertisement that most recently woke the entry."""
        return self._last_rssi

    @property
    def connection_started_at(self) -> datetime | None:
        """Return when the current authenticated session started."""
        return self._connection_started_at

    @property
    def last_disconnect(self) -> datetime | None:
        """Return when the most recent session ended unexpectedly."""
        return self._last_disconnect

    @property
    def last_push(self) -> datetime | None:
        """Return when the most recent unsolicited property push arrived."""
        return self._last_push

    @property
    def last_watchdog(self) -> datetime | None:
        """Return when the most recent lightweight watchdog read succeeded."""
        return self._last_watchdog

    @property
    def connection_count(self) -> int:
        """Return successful authenticated sessions since entry setup."""
        return self._connection_count

    @property
    def connection_attempt_count(self) -> int:
        """Return BLE connection attempts since entry setup."""
        return self._connection_attempt_count

    @property
    def disconnect_count(self) -> int:
        """Return unexpected or error-driven disconnects since entry setup."""
        return self._disconnect_count

    @property
    def full_snapshot_count(self) -> int:
        """Return complete subscription snapshots since entry setup."""
        return self._full_snapshot_count

    @property
    def push_count(self) -> int:
        """Return unsolicited property pushes since entry setup."""
        return self._push_count

    @property
    def watchdog_count(self) -> int:
        """Return lightweight status reads since entry setup."""
        return self._watchdog_count

    @property
    def consecutive_failures(self) -> int:
        """Return the current consecutive communication failure count."""
        return self._consecutive_failures

    @property
    def gatt_failure_streak(self) -> int:
        """Return consecutive failures caused by an incomplete GATT table."""
        return self._gatt_failure_streak

    @property
    def gatt_quiet_mode(self) -> bool:
        """Return whether repeated GATT failures activated the quiet cooldown."""
        return (
            self._gatt_failure_streak >= GATT_FAILURES_BEFORE_QUIET
            and self.reconnect_backoff_remaining > 0
        )

    @property
    def service_cache_clear_count(self) -> int:
        """Return successful invalid GATT service-cache removals."""
        return self._service_cache_clear_count

    @property
    def bluetooth_available(self) -> bool:
        """Return whether a connectable scanner currently sees the kettle."""
        return self._bluetooth_available

    @property
    def reconnect_backoff_remaining(self) -> float:
        """Return seconds until an advertisement may trigger another attempt."""
        return max(0.0, self._retry_not_before - monotonic())

    @property
    def disconnect_grace_remaining(self) -> float:
        """Return seconds before a transient disconnect marks data unavailable."""
        return max(0.0, self._disconnect_grace_deadline - monotonic())

    @property
    def disconnect_grace_active(self) -> bool:
        """Return whether confirmed data is being held through a brief dropout."""
        return (
            not self.connected
            and self.data is not None
            and self.disconnect_grace_remaining > 0
        )

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
        self._unregister_unavailable = bluetooth.async_track_unavailable(
            self.hass,
            self._async_bluetooth_unavailable,
            self.address,
            connectable=True,
        )

    @callback
    def _async_bluetooth_callback(
        self, service_info: BluetoothServiceInfoBleak, _change: BluetoothChange
    ) -> None:
        self._bluetooth_available = True
        self._last_advertisement = datetime.now(UTC)
        self._last_rssi = getattr(service_info, "rssi", None)
        if self.connected:
            return
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        self._reconnect_task = self.hass.async_create_task(
            self._async_refresh_after_advertisement(self.reconnect_backoff_remaining),
            f"{DOMAIN} reconnect {service_info.address}",
        )

    @callback
    def _async_bluetooth_unavailable(self, _service_info: BluetoothServiceInfoBleak) -> None:
        """Cancel a pending retry when no connectable scanner sees the kettle."""
        self._bluetooth_available = False
        if self.connected:
            return
        if self._reconnect_task is not None and not self._reconnect_task.done():
            reconnect_task = self._reconnect_task
            self._reconnect_task = None
            reconnect_task.cancel()

    async def _async_refresh_after_advertisement(self, delay: float) -> None:
        """Refresh now, or after backoff, for an advertisement already observed."""
        try:
            if delay:
                await asyncio.sleep(delay)
            if not self.connected:
                await self.async_request_refresh()
        finally:
            if self._reconnect_task is asyncio.current_task():
                self._reconnect_task = None

    def _disconnected_callback(self, client: BleakClientWithServiceCache) -> None:
        self.hass.loop.call_soon_threadsafe(self._async_schedule_disconnected, client)

    @callback
    def _async_schedule_disconnected(self, client: BleakClientWithServiceCache) -> None:
        if self._dropping or client is not self._client:
            return
        self.hass.async_create_task(
            self._async_handle_disconnected(client),
            f"{DOMAIN} disconnected {self.address}",
        )

    async def _async_handle_disconnected(self, client: BleakClientWithServiceCache) -> None:
        if client is not self._client:
            return
        dev = self._dev
        self._client = None
        self._dev = None
        self._record_disconnect()
        if dev is not None:
            await dev.async_stop()
        error = KettleError("BLE connection closed")
        self._last_error = error
        self._consecutive_failures += 1
        self._gatt_failure_streak = 0
        delay = min(
            TRANSIENT_RETRY_INTERVAL * 2 ** (self._consecutive_failures - 1),
            MAX_RECONNECT_BACKOFF,
        )
        self._retry_not_before = monotonic() + delay
        self.update_interval = None
        self._clear_advertisement_history()
        # Do not start a long connection attempt against a kettle whose smart
        # plug was just switched off. The next real advertisement reconnects it.
        if self.data is None:
            self.async_set_update_error(error)
            return
        self._start_disconnect_grace(error)

    def _start_disconnect_grace(self, error: Exception) -> None:
        """Debounce a brief weak-signal disconnect without hiding a real outage."""
        self._cancel_disconnect_grace()
        self._disconnect_grace_deadline = monotonic() + DISCONNECT_GRACE_PERIOD
        self._disconnect_grace_task = self.hass.async_create_task(
            self._async_expire_disconnect_grace(error),
            f"{DOMAIN} disconnect grace {self.address}",
        )

    def _cancel_disconnect_grace(self) -> None:
        """Cancel a pending availability downgrade after recovery or shutdown."""
        task = self._disconnect_grace_task
        self._disconnect_grace_task = None
        self._disconnect_grace_deadline = 0.0
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

    async def _async_expire_disconnect_grace(self, error: Exception) -> None:
        """Mark stale data unavailable if the session does not recover in time."""
        task = asyncio.current_task()
        try:
            await asyncio.sleep(DISCONNECT_GRACE_PERIOD)
            if not self.connected:
                self.async_set_update_error(self._last_error or error)
        finally:
            if self._disconnect_grace_task is task:
                self._disconnect_grace_task = None
                self._disconnect_grace_deadline = 0.0

    async def _ensure_session(self) -> YunmiKettle:
        if self.connected:
            assert self._dev is not None
            return self._dev
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise KettleError("device is not in range of a Bluetooth adapter or proxy")

        self._connection_attempt_count += 1
        client = await establish_connection(
            BleakClientWithServiceCache,
            ble_device,
            self.address,
            disconnected_callback=self._disconnected_callback,
            use_services_cache=True,
            ble_device_callback=lambda: bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            ),
        )
        dev = YunmiKettle(client, self._token, self._async_handle_push)
        try:
            await dev.start()
            await dev.login()
        except Exception as err:
            if isinstance(err, KettleGattError) and self._gatt_failure_streak == 0:
                await self._async_clear_service_cache()
            await dev.async_stop()
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            raise
        self._client = client
        self._dev = dev
        self._connection_started_at = datetime.now(UTC)
        self._connection_count += 1
        return dev

    async def _async_clear_service_cache(self) -> None:
        """Remove an incomplete GATT service table before the next connection."""
        try:
            if await clear_cache(self.address):
                self._service_cache_clear_count += 1
        except Exception as err:  # noqa: BLE001 - cache removal is best effort
            _LOGGER.debug("Could not clear kettle GATT service cache: %s", err)

    def _record_disconnect(self) -> None:
        """Record the end of a session without exposing adapter identifiers."""
        self._connection_started_at = None
        self._last_disconnect = datetime.now(UTC)
        self._disconnect_count += 1

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
        self._last_push = datetime.now(UTC)
        self._push_count += 1
        self._mark_success()
        self.async_set_updated_data(self._merge_properties(properties))

    def _mark_success(self) -> None:
        self._cancel_disconnect_grace()
        self._last_success = datetime.now(UTC)
        self._last_error = None
        self._consecutive_failures = 0
        self._gatt_failure_streak = 0
        self._retry_not_before = 0.0
        self.update_interval = timedelta(seconds=self.poll_interval)

    def _record_failure_backoff(self, err: Exception) -> tuple[float, bool]:
        """Record one failure and return its retry delay and quiet-mode state."""
        self._last_error = err
        self._consecutive_failures += 1
        if isinstance(err, KettleGattError):
            self._gatt_failure_streak += 1
            if self._gatt_failure_streak >= GATT_FAILURES_BEFORE_QUIET:
                quiet_exponent = self._gatt_failure_streak - GATT_FAILURES_BEFORE_QUIET
                delay = min(
                    GATT_QUIET_RETRY_INTERVAL * 2**quiet_exponent,
                    MAX_GATT_QUIET_BACKOFF,
                )
                quiet = True
            else:
                delay = GATT_RETRY_INTERVAL
                quiet = False
        else:
            self._gatt_failure_streak = 0
            delay = min(
                TRANSIENT_RETRY_INTERVAL * 2 ** (self._consecutive_failures - 1),
                MAX_RECONNECT_BACKOFF,
            )
            quiet = False
        self._retry_not_before = monotonic() + delay
        return delay, quiet

    def _handle_update_failure(self, err: Exception) -> dict[str, int]:
        delay, quiet = self._record_failure_backoff(err)
        # HA de-duplicates identical advertisements. Forget the last packet so
        # switching the kettle's smart plug back on always wakes this entry.
        self._clear_advertisement_history()
        if quiet:
            self.update_interval = None
            _LOGGER.warning(
                "Kettle GATT table is still incomplete; automatic BLE retries are quiet "
                "for %s seconds: %s",
                round(delay),
                err,
            )
            raise UpdateFailed(str(err)) from err
        if self.data is not None and self.disconnect_grace_active:
            self.update_interval = None
            _LOGGER.warning(
                "Temporary kettle reconnect failure; keeping the last confirmed data "
                "for %.0f more seconds and retrying from advertisements: %s",
                self.disconnect_grace_remaining,
                err,
            )
            return dict(self.data)
        if self.data is not None and self._consecutive_failures == 1:
            self.update_interval = timedelta(seconds=delay)
            _LOGGER.warning(
                "Temporary kettle communication failure; retrying in %s seconds: %s",
                round(delay),
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
            needs_full_snapshot = self.data is None or not self.connected
            try:
                dev = await self._ensure_session()
                if needs_full_snapshot:
                    properties = await dev.subscribe_and_read()
                    self._full_snapshot_count += 1
                else:
                    # The initial snapshot already subscribed every surfaced property.
                    # Pushes remain live, so one status read is enough to verify that the
                    # persistent GATT session still responds. Re-subscribing all fourteen
                    # properties on every watchdog tick needlessly disturbs the device.
                    properties = await dev.read_many([PROP_STATUS])
                    self._last_watchdog = datetime.now(UTC)
                    self._watchdog_count += 1
            except KettleAuthError as err:
                await self._drop(record_disconnect=True)
                self._record_auth_failure(err)
                raise ConfigEntryAuthFailed("Kettle rejected the BLE beaconkey") from err
            except Exception as err:  # noqa: BLE001
                await self._drop(record_disconnect=True)
                return self._handle_update_failure(err)
        self._mark_success()
        return self._merge_properties(properties)

    def _record_auth_failure(self, err: KettleAuthError) -> None:
        """Stop automatic retries until Home Assistant completes reauthentication."""
        self._cancel_disconnect_grace()
        self._last_error = err
        self._consecutive_failures += 1
        self._gatt_failure_streak = 0
        self._retry_not_before = 0.0
        self.update_interval = None

    async def _drop(self, *, record_disconnect: bool = False) -> None:
        client = self._client
        dev = self._dev
        self._client = None
        self._dev = None
        if record_disconnect and client is not None:
            self._record_disconnect()
        elif client is not None:
            self._connection_started_at = None
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
        if self._unregister_unavailable is not None:
            self._unregister_unavailable()
            self._unregister_unavailable = None
        if self._reconnect_task is not None and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            await asyncio.gather(self._reconnect_task, return_exceptions=True)
        grace_task = self._disconnect_grace_task
        self._cancel_disconnect_grace()
        if grace_task is not None:
            await asyncio.gather(grace_task, return_exceptions=True)
        await super().async_shutdown()
        await self._drop()

    async def _async_set_property(self, pair: tuple[int, int], value: int) -> None:
        async with self._operation_lock:
            try:
                dev = await self._ensure_session()
                echo = await dev.set_property(*pair, value, VTYPE_U8)
            except KettleAuthError as err:
                await self._drop(record_disconnect=True)
                self._record_auth_failure(err)
                self.entry.async_start_reauth(self.hass)
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="authentication_failed",
                ) from err
            except Exception as err:  # noqa: BLE001
                await self._drop(record_disconnect=True)
                _, quiet = self._record_failure_backoff(err)
                if quiet:
                    self.update_interval = None
                self._clear_advertisement_history()
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
