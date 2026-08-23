"""Unit tests for notification-driven kettle protocol behavior."""

from __future__ import annotations

import asyncio
import importlib.util
import struct
import sys
import types
import unittest
from enum import StrEnum
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESCCM

ROOT = Path(__file__).parents[1]
PACKAGE = "yunmi_kettle_test"


class _Platform(StrEnum):
    SENSOR = "sensor"
    NUMBER = "number"
    SWITCH = "switch"
    SELECT = "select"


class _FakeBleakClient:
    def __init__(self) -> None:
        self.writes: list[tuple[str, bytes, bool]] = []

    async def write_gatt_char(self, uuid: str, data: bytes, *, response: bool = False) -> None:
        self.writes.append((uuid, bytes(data), response))


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_protocol():
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT / "custom_components" / "yunmi_kettle")]
    sys.modules[PACKAGE] = package

    homeassistant = types.ModuleType("homeassistant")
    homeassistant_const = types.ModuleType("homeassistant.const")
    homeassistant_const.Platform = _Platform
    homeassistant.const = homeassistant_const
    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.const"] = homeassistant_const

    bleak = types.ModuleType("bleak")
    bleak.BleakClient = _FakeBleakClient
    sys.modules["bleak"] = bleak

    component = ROOT / "custom_components" / "yunmi_kettle"
    _load_module(f"{PACKAGE}.const", component / "const.py")
    return _load_module(f"{PACKAGE}.protocol", component / "protocol.py")


protocol = _load_protocol()


class _FakeDataUpdateCoordinator:
    @classmethod
    def __class_getitem__(cls, _item):
        return cls

    def __init__(
        self,
        hass,
        _logger,
        *,
        config_entry,
        name,
        update_interval,
        always_update,
    ) -> None:
        self.hass = hass
        self.config_entry = config_entry
        self.name = name
        self.update_interval = update_interval
        self.always_update = always_update
        self.data = None
        self.last_update_success = True
        self.last_exception = None

    def async_set_update_error(self, error: Exception) -> None:
        self.last_exception = error
        self.last_update_success = False

    def async_set_updated_data(self, data) -> None:
        self.data = data
        self.last_update_success = True

    async def async_request_refresh(self) -> None:
        raise AssertionError("disconnect must wait for a real advertisement")

    async def async_shutdown(self) -> None:
        return None


class _UpdateFailed(Exception):
    pass


def _load_coordinator():
    bleak_retry = types.ModuleType("bleak_retry_connector")

    async def _establish_connection(*_args, **_kwargs):
        raise AssertionError("not used by these tests")

    bleak_retry.establish_connection = _establish_connection
    sys.modules["bleak_retry_connector"] = bleak_retry

    bluetooth = types.ModuleType("homeassistant.components.bluetooth")
    bluetooth.BluetoothChange = object
    bluetooth.BluetoothCallbackMatcher = lambda **kwargs: kwargs
    bluetooth.BluetoothScanningMode = types.SimpleNamespace(PASSIVE="passive")
    bluetooth.BluetoothServiceInfoBleak = object
    bluetooth.async_register_callback = lambda *_args, **_kwargs: lambda: None
    bluetooth.cleared_addresses = []
    bluetooth.async_clear_advertisement_history = lambda _hass, address: (
        bluetooth.cleared_addresses.append(address)
    )
    components = types.ModuleType("homeassistant.components")
    components.bluetooth = bluetooth
    sys.modules["homeassistant.components"] = components
    sys.modules["homeassistant.components.bluetooth"] = bluetooth

    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    sys.modules["homeassistant.config_entries"] = config_entries

    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    core.callback = lambda function: function
    sys.modules["homeassistant.core"] = core

    exceptions = types.ModuleType("homeassistant.exceptions")
    exceptions.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})
    exceptions.HomeAssistantError = Exception
    sys.modules["homeassistant.exceptions"] = exceptions

    helpers = types.ModuleType("homeassistant.helpers")
    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")
    update_coordinator.DataUpdateCoordinator = _FakeDataUpdateCoordinator
    update_coordinator.UpdateFailed = _UpdateFailed
    helpers.update_coordinator = update_coordinator
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator

    component = ROOT / "custom_components" / "yunmi_kettle"
    return _load_module(f"{PACKAGE}.coordinator", component / "coordinator.py")


coordinator_module = _load_coordinator()


class ProtocolParsingTests(unittest.TestCase):
    def test_parse_single_property(self) -> None:
        payload = bytes.fromhex("0c20b0000401030600011019")
        self.assertEqual(protocol.YunmiKettle._parse_all(payload), [(3, 6, 25)])

    def test_parse_subscribe_batch(self) -> None:
        payload = bytes.fromhex("1620b00003020306000000011019050100000001105a")
        self.assertEqual(
            protocol.YunmiKettle._parse_all(payload),
            [(3, 6, 25), (5, 1, 90)],
        )


class ProtocolNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = _FakeBleakClient()
        self.received: list[dict[tuple[int, int], int]] = []
        self.kettle = protocol.YunmiKettle(
            self.client,
            bytes.fromhex("00112233445566778899aabb"),
            self.received.append,
        )

    async def test_direct_notification_is_acked_and_published(self) -> None:
        key = bytes(range(16))
        iv = bytes.fromhex("01020304")
        self.kettle._keys = {"dev_key": key, "dev_iv": iv}
        self.kettle._live_updates = True
        plaintext = bytes.fromhex("0c20b0000401030600011019")
        counter = 7
        encrypted = AESCCM(key, tag_length=4).encrypt(
            protocol._ccm_nonce(iv, counter), plaintext, None
        )
        sender = types.SimpleNamespace(uuid=self.kettle._notify_uuid)

        self.kettle._on_notify(
            sender,
            bytearray(b"\x00\x00\x02\x00" + struct.pack("<H", counter) + encrypted),
        )
        await asyncio.gather(*tuple(self.kettle._notify_tasks))

        self.assertEqual(self.received, [{(3, 6): 25}])
        self.assertEqual(self.kettle._property_values[(3, 6)], 25)
        self.assertEqual(
            self.client.writes,
            [(self.kettle._notify_uuid, protocol.ACK_DATA, False)],
        )

    async def test_wait_for_properties_requires_a_new_version(self) -> None:
        pair = (3, 6)
        self.kettle._property_values[pair] = 20
        self.kettle._property_versions[pair] = 1
        waiter = asyncio.create_task(self.kettle._wait_for_properties({pair}, {pair: 1}, timeout=1))
        await asyncio.sleep(0)
        self.kettle._property_values[pair] = 21
        self.kettle._property_versions[pair] = 2
        self.kettle._property_event.set()

        self.assertEqual(await waiter, {pair: 21})

    async def test_write_confirmation_ignores_an_old_value(self) -> None:
        pair = (5, 5)
        waiter = asyncio.create_task(self.kettle._wait_for_confirmed_value(pair, 90, 0, timeout=1))
        await asyncio.sleep(0)
        self.kettle._property_values[pair] = 80
        self.kettle._property_versions[pair] = 1
        self.kettle._property_event.set()
        await asyncio.sleep(0)
        self.kettle._property_values[pair] = 90
        self.kettle._property_versions[pair] = 2
        self.kettle._property_event.set()

        self.assertEqual(await waiter, {pair: 90})

    async def test_write_confirmation_times_out_without_echo(self) -> None:
        with self.assertRaisesRegex(protocol.KettleError, "did not confirm|timeout waiting"):
            await self.kettle._wait_for_confirmed_value((5, 1), 90, 0, timeout=0.01)


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def _coordinator(self):
        entry = types.SimpleNamespace(
            data={
                "mac": "AA:BB:CC:DD:EE:FF",
                "token": "00112233445566778899aabb",
                "name": "Kettle",
            },
            options={},
        )
        hass = types.SimpleNamespace(
            loop=asyncio.get_running_loop(),
            async_create_task=lambda coro, name: asyncio.create_task(coro, name=name),
        )
        return coordinator_module.YunmiKettleCoordinator(hass, entry)

    async def test_property_mapping_preserves_profiles_and_units(self) -> None:
        coordinator = self._coordinator()
        data = coordinator._merge_properties(
            {
                (2, 1): 0,
                (3, 6): 25,
                (3, 3): 4,
                (5, 5): 90,
                (5, 10): 24,
            }
        )
        self.assertEqual(data["status"], 0)
        self.assertEqual(data["current_temp"], 25)
        self.assertEqual(data["active_profile"], 4)
        self.assertEqual(data["profile_temp_5"], 90)
        self.assertEqual(data["profile_temp"], 90)
        self.assertEqual(data["profile_duration_5"], 720)

    async def test_disconnect_marks_unavailable_and_waits_for_advertisement(self) -> None:
        coordinator = self._coordinator()
        client = types.SimpleNamespace(is_connected=False)

        class _Device:
            stopped = False

            async def async_stop(self) -> None:
                self.stopped = True

        device = _Device()
        coordinator._client = client
        coordinator._dev = device

        await coordinator._async_handle_disconnected(client)

        self.assertTrue(device.stopped)
        self.assertIsNone(coordinator._client)
        self.assertIsNone(coordinator.update_interval)
        self.assertFalse(coordinator.last_update_success)
        self.assertIsInstance(coordinator.last_exception, protocol.KettleError)
        self.assertIn(
            coordinator.address,
            sys.modules["homeassistant.components.bluetooth"].cleared_addresses,
        )

    async def test_advertisement_during_backoff_schedules_delayed_refresh(self) -> None:
        coordinator = self._coordinator()
        refreshed = asyncio.Event()

        async def _refresh() -> None:
            refreshed.set()

        coordinator.async_request_refresh = _refresh
        coordinator._retry_not_before = coordinator_module.monotonic() + 0.02
        coordinator._async_bluetooth_callback(
            types.SimpleNamespace(address=coordinator.address),
            None,
        )

        await asyncio.wait_for(refreshed.wait(), timeout=0.5)
        self.assertIsNone(coordinator._reconnect_task)

    async def test_start_schedules_initial_refresh_without_cached_advertisement(self) -> None:
        coordinator = self._coordinator()
        refreshed = asyncio.Event()

        async def _refresh() -> None:
            refreshed.set()

        coordinator.async_request_refresh = _refresh
        coordinator.async_start()

        await asyncio.wait_for(refreshed.wait(), timeout=0.5)
        self.assertIsNone(coordinator._reconnect_task)
        await coordinator.async_shutdown()

    async def test_invalid_beaconkey_triggers_config_entry_reauth(self) -> None:
        coordinator = self._coordinator()

        async def _invalid_session():
            raise protocol.KettleAuthError("wrong key")

        coordinator._ensure_session = _invalid_session
        with self.assertRaises(sys.modules["homeassistant.exceptions"].ConfigEntryAuthFailed):
            await coordinator._async_update_data()
        self.assertIsNone(coordinator.update_interval)
        self.assertIsInstance(coordinator.last_error, protocol.KettleAuthError)


if __name__ == "__main__":
    unittest.main()
