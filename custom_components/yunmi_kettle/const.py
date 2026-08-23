"""Constants for the Yunmi Kettle (Mi Smart Kettle Pro, BLE) integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "yunmi_kettle"
PLATFORMS = [Platform.SENSOR, Platform.NUMBER, Platform.SWITCH, Platform.SELECT]

CONF_MAC = "mac"
CONF_TOKEN = "token"
CONF_NAME = "name"
CONF_POLL_INTERVAL = "poll_interval"
LEGACY_CONF_TARGET_TEMP = "target_temp"

DEFAULT_NAME = "Mi Kettle"
DEFAULT_POLL_INTERVAL = 300
TRANSIENT_RETRY_INTERVAL = 10
MAX_RECONNECT_BACKOFF = 300
MIN_TEMP = 40
MAX_TEMP = 95

PROFILE_COUNT = 5
# keep-warm duration is stored on-device as a uint8 in 30-minute units (1..24 = 0.5h..12h)
DURATION_UNIT_MIN = 30
MIN_DURATION = 30
MAX_DURATION = 720


# --- BLE GATT (service 0xFE95) characteristic UUIDs ---
def _u(short: str) -> str:
    return f"0000{short}-0000-1000-8000-00805f9b34fb"


UUID_UPNP = _u("0010")  # login / control channel
UUID_AUTH = _u("0019")  # framed handshake channel
UUID_MISERVICE_WRITE = _u("001a")  # app -> device property write/request (handle 0x0021)
UUID_MISERVICE_NOTIFY = _u("001b")  # device -> app property notify/response (handle 0x0025)
# Fallbacks tried at runtime if the above don't match the device's GATT layout.
UUID_NOTIFY_CANDIDATES = [_u("0018"), _u("0017"), _u("001a"), _u("001b")]
UUID_WRITE_CANDIDATES = [_u("0017"), _u("0018"), _u("001a"), _u("001b")]

# --- handshake commands (Xiaomi "mi" standard login) ---
CMD_LOGIN = bytes.fromhex("24000000")
HDR_SEND_KEY = bytes.fromhex("0000000b0100")  # rand_key = 16B = 1 frame
HDR_SEND_INFO = bytes.fromhex("0000000a0100")  # login_info = 32B as a single parcel (matches app)
RESP_KEY = 0x0D  # device->app header byte[3] for remote_key
RESP_INFO = 0x0C  # device->app header byte[3] for remote_info
RCV_RDY = bytes.fromhex("00000101")
RCV_OK = bytes.fromhex("00000100")
CFM_LOGIN_OK = bytes.fromhex("21000000")
ACK_DATA = bytes.fromhex("00000300")

# --- MIoT spec property map (siid, piid) ---
PROP_STATUS = (2, 1)  # uint8 0..5
PROP_CURRENT_TEMP = (3, 6)  # uint8 C
PROP_KEEPWARM_SWITCH = (3, 2)  # bool
PROP_KEEPWARM_MODE = (3, 1)  # uint8
PROP_ACTIVE_PROFILE = (3, 3)  # uint8 0..4


def PROP_PROFILE_TEMP(profile: int) -> tuple[int, int]:
    """Return the MIoT pair for a one-based profile temperature."""
    return (5, profile)


def PROP_PROFILE_DURATION(profile: int) -> tuple[int, int]:
    """Return the MIoT pair for a one-based profile duration."""
    return (5, 5 + profile)


STATUS_NAMES = {
    0: "standby",
    1: "heating",
    2: "boil_keepwarm",
    3: "heat_to_set",
    4: "cooling",
    5: "abnormal",
}

VTYPE_U8 = 0x10
VTYPE_U16 = 0x30
