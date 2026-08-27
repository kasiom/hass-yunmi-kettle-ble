"""Config flow for Yunmi Kettle (Mi Smart Kettle Pro, BLE)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from bleak_retry_connector import BleakClientWithServiceCache, clear_cache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .const import (
    CONF_MAC,
    CONF_NAME,
    CONF_POLL_INTERVAL,
    CONF_TOKEN,
    DEFAULT_NAME,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)
from .protocol import KettleAuthError, KettleGattError, YunmiKettle

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
FE95 = "0000fe95-0000-1000-8000-00805f9b34fb"
KETTLE_PRODUCT_ID = 0x1A4F  # MiBeacon product id of yunmi.kettle.v14

_INTERVAL = vol.All(vol.Coerce(int), vol.Range(min=15, max=600))
_TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _valid_token(t: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{24}", t))


def _token_format_error(token: str) -> str | None:
    """Return a config-flow error key for an invalid token format."""
    if re.fullmatch(r"[0-9a-fA-F]{32}", token):
        return "miot_token_not_supported"
    if not _valid_token(token):
        return "invalid_token"
    return None


def _is_kettle(info: BluetoothServiceInfoBleak) -> bool:
    sd = info.service_data.get(FE95)
    return bool(sd and len(sd) >= 4 and (sd[2] | (sd[3] << 8)) == KETTLE_PRODUCT_ID)


async def _test_connection(hass, mac: str, token: str) -> str | None:
    """Try a real connect + login. Returns an error key, or None on success."""
    ble_device = bluetooth.async_ble_device_from_address(hass, mac, connectable=True)
    if ble_device is None:
        return "cannot_connect"
    client: BleakClientWithServiceCache | None = None
    dev: YunmiKettle | None = None
    try:
        client = await establish_connection(
            BleakClientWithServiceCache,
            ble_device,
            mac,
            use_services_cache=True,
            ble_device_callback=lambda: bluetooth.async_ble_device_from_address(
                hass, mac, connectable=True
            ),
        )
        dev = YunmiKettle(client, bytes.fromhex(token))
        await dev.start()
        await dev.login()
    except KettleAuthError:
        return "invalid_auth"
    except KettleGattError:
        try:
            await clear_cache(mac)
        except Exception:  # noqa: BLE001 - cache removal is best effort
            pass
        return "cannot_connect"
    except Exception:  # noqa: BLE001
        return "cannot_connect"
    finally:
        if dev is not None:
            await dev.async_stop()
        if client is not None:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
    return None


class YunmiKettleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 2

    def __init__(self) -> None:
        self._discovered_mac: str | None = None
        self._discovered_name: str | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
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
            elif token_error := _token_format_error(token):
                errors[CONF_TOKEN] = token_error
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
                        },
                        options={CONF_POLL_INTERVAL: int(user_input[CONF_POLL_INTERVAL])},
                    )

        prev = user_input or {}
        fields: dict[Any, Any] = {}
        if not self._discovered_mac:
            fields[vol.Required(CONF_MAC, default=prev.get(CONF_MAC, ""))] = cv.string
        fields[vol.Required(CONF_TOKEN, default=prev.get(CONF_TOKEN, ""))] = _TOKEN_SELECTOR
        fields[
            vol.Required(
                CONF_NAME, default=prev.get(CONF_NAME, self._discovered_name or DEFAULT_NAME)
            )
        ] = cv.string
        fields[
            vol.Required(
                CONF_POLL_INTERVAL, default=prev.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
            )
        ] = _INTERVAL
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={"mac": self._discovered_mac or ""},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Ask for a replacement BLE beaconkey after authentication fails."""
        self.context["title_placeholders"] = {"name": str(entry_data.get(CONF_NAME, DEFAULT_NAME))}
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate and save a replacement BLE beaconkey."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip().lower()
            if token_error := _token_format_error(token):
                errors[CONF_TOKEN] = token_error
            else:
                entry = self._get_reauth_entry()
                error = await _test_connection(self.hass, entry.data[CONF_MAC], token)
                if error:
                    errors["base"] = error
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={CONF_TOKEN: token},
                    )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): _TOKEN_SELECTOR}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return YunmiKettleOptionsFlow()


class YunmiKettleOptionsFlow(OptionsFlow):
    """Edit settings after setup; the config-entry listener reloads it."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        cur = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_POLL_INTERVAL, default=cur.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
                ): _INTERVAL,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
