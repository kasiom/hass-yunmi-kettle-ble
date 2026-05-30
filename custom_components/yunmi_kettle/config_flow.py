"""Config flow for Yunmi Kettle (Mi Smart Kettle Pro, BLE)."""
from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from bleak import BleakClient
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector

from .const import (
    CONF_MAC,
    CONF_NAME,
    CONF_POLL_INTERVAL,
    CONF_TARGET_TEMP,
    CONF_TOKEN,
    DEFAULT_NAME,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_TARGET_TEMP,
    DOMAIN,
    MAX_TEMP,
    MIN_TEMP,
)
from .protocol import KettleError, YunmiKettle

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
FE95 = "0000fe95-0000-1000-8000-00805f9b34fb"
KETTLE_PRODUCT_ID = 0x1A4F  # MiBeacon product id of yunmi.kettle.v14

_INTERVAL = vol.All(vol.Coerce(int), vol.Range(min=15, max=600))
_TEMP_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(min=MIN_TEMP, max=MAX_TEMP, step=1, mode=selector.NumberSelectorMode.BOX)
)


def _valid_token(t: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{24}", t))


def _is_kettle(info: BluetoothServiceInfoBleak) -> bool:
    sd = info.service_data.get(FE95)
    return bool(sd and len(sd) >= 4 and (sd[2] | (sd[3] << 8)) == KETTLE_PRODUCT_ID)


async def _test_connection(hass, mac: str, token: str) -> str | None:
    """Try a real connect + login. Returns an error key, or None on success."""
    ble_device = bluetooth.async_ble_device_from_address(hass, mac, connectable=True)
    if ble_device is None:
        return "cannot_connect"
    client: BleakClient | None = None
    try:
        client = await establish_connection(BleakClient, ble_device, mac)
        dev = YunmiKettle(client, bytes.fromhex(token))
        await dev.start()
        await dev.login()
    except KettleError:
        return "invalid_auth"
    except Exception:  # noqa: BLE001
        return "cannot_connect"
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
    return None


class YunmiKettleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_mac: str | None = None
        self._discovered_name: str | None = None

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        """Handle a kettle discovered over Bluetooth (MiBeacon fe95)."""
        if not _is_kettle(discovery_info):
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(discovery_info.address.upper())
        self._abort_if_unique_id_configured()
        self._discovered_mac = discovery_info.address.upper()
        self._discovered_name = discovery_info.name or DEFAULT_NAME
        self.context["title_placeholders"] = {"name": self._discovered_name}
        return await self.async_step_user()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            mac = (self._discovered_mac or user_input.get(CONF_MAC, "")).strip().upper()
            token = user_input[CONF_TOKEN].strip().lower()
            if not _MAC_RE.match(mac):
                errors[CONF_MAC] = "invalid_mac"
            elif not _valid_token(token):
                errors[CONF_TOKEN] = "invalid_token"
            else:
                await self.async_set_unique_id(mac, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                err = await _test_connection(self.hass, mac, token)
                if err:
                    errors["base"] = err
                else:
                    name = user_input.get(CONF_NAME, self._discovered_name or DEFAULT_NAME)
                    return self.async_create_entry(
                        title=name,
                        data={
                            CONF_MAC: mac,
                            CONF_TOKEN: token,
                            CONF_NAME: name,
                            CONF_TARGET_TEMP: int(user_input[CONF_TARGET_TEMP]),
                        },
                        options={CONF_POLL_INTERVAL: int(user_input[CONF_POLL_INTERVAL])},
                    )

        prev = user_input or {}
        fields: dict[Any, Any] = {}
        if not self._discovered_mac:
            fields[vol.Required(CONF_MAC, default=prev.get(CONF_MAC, ""))] = cv.string
        fields[vol.Required(CONF_TOKEN, default=prev.get(CONF_TOKEN, ""))] = cv.string
        fields[vol.Required(CONF_NAME, default=prev.get(CONF_NAME, self._discovered_name or DEFAULT_NAME))] = cv.string
        fields[vol.Required(CONF_TARGET_TEMP, default=prev.get(CONF_TARGET_TEMP, DEFAULT_TARGET_TEMP))] = _TEMP_SELECTOR
        fields[vol.Required(CONF_POLL_INTERVAL, default=prev.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))] = _INTERVAL
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={"mac": self._discovered_mac or ""},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return YunmiKettleOptionsFlow()


class YunmiKettleOptionsFlow(OptionsFlowWithReload):
    """Edit settings after setup (auto-reloads the entry on save)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        cur = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(CONF_POLL_INTERVAL, default=cur.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)): _INTERVAL,
                vol.Required(CONF_TARGET_TEMP, default=cur.get(CONF_TARGET_TEMP, DEFAULT_TARGET_TEMP)): _TEMP_SELECTOR,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
