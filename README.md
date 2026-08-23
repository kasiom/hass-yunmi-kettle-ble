# yunmi_kettle — Home Assistant integration for the Mi Smart Kettle Pro (yunmi.kettle.v14)

A custom Home Assistant integration that talks to the **Xiaomi Mi Smart Kettle Pro
(MJHWSH02YM / `yunmi.kettle.v14`)** over Bluetooth LE — directly or **through an
ESPHome Bluetooth proxy** — using the Xiaomi "mi" secure login + MIoT-spec property
RPC. No cloud, no gateway, no root.

This is, as far as we know, the **first working BLE driver for this exact model**: every
existing project targets the older Mi Kettle GATT (`aa0x` characteristics) which the
v14 does not have — it speaks MIoT-spec over the `0xFE95` service instead.

> ⚠️ **Vibe-coded, use at your own risk.** This integration was reverse-engineered and
> written end-to-end in an exploratory, "vibe coding" session (human + LLM, lots of HCI
> snoop staring). It works on the author's kettle and is shared in case it's useful, but
> it is **not** an official or hardened component. Expect rough edges; read the code
> before trusting it. PRs welcome.

## What changed in 0.3

Version 0.3 is maintained as an offline-aware fork while the changes are offered
upstream. It keeps the existing `yunmi_kettle` domain, device identifier and entity
unique IDs, so upgrading does not require recreating dashboards, history or automations.

- A kettle disconnected from mains power is treated as normally offline. The config
  entry still loads, entities become unavailable, and the next BLE advertisement after
  power returns triggers reconnection.
- BLE property notifications update Home Assistant immediately. The polling option is
  now a watchdog interval (300 seconds by default), not the primary update mechanism.
- All GATT commands are serialized. A changed number, select or switch is accepted only
  after the kettle echoes the requested value; otherwise Home Assistant reports the
  command as failed.
- Profile controls are configuration entities with temperature/duration device classes.
  Redacted diagnostics and Czech translations are included.
- The unused legacy `target_temp` option is removed automatically during config-entry
  migration. The actual active target remains the read-only keep-warm target sensor.

---

## ⚠️ The one hard limitation — read this first

**This kettle cannot be told to start heating/boiling over Bluetooth. There is no such
command — not in this integration, and not in the official Mi Home app.** This was proven
by capturing Mi Home's own BLE traffic: the app only ever sends *profile-select* and
*preset* writes; heating only ever begins after the **physical button** on the kettle is
pressed. Xiaomi gates the heater behind the button for safety. The older Mi Kettle had a
remote-heat command; the newer MIoT `yunmi.kettle.v14` does not expose one.

So this integration is a **monitor + configurator**, not a remote on/off:

| Capability | Works? | Property |
|---|---|---|
| Read current water temperature (even idle) | ✅ | siid 3 / piid 6 |
| Read status (standby / heating / keep-warm / …) | ✅ | siid 2 / piid 1 |
| Read & **edit all 5 keep-warm presets** (temperature) | ✅ | siid 5 / piid 1–5 |
| Read & **edit all 5 keep-warm durations** | ✅ | siid 5 / piid 6–10 |
| **Select** the active keep-warm profile | ✅ | siid 3 / piid 3 |
| Toggle "keep warm after boil" | ✅ | siid 3 / piid 2 |
| **Start heating / boil remotely** | ❌ | hardware-blocked — press the kettle button |

**Realistic use:** pre-set a profile to e.g. 90 °C and enable "keep warm after boil"; in the
morning you physically press boil and the kettle then holds your target temperature, which
HA monitors.

---

## Entities

- **Sensors** — water temperature, status (enum), keep-warm **target** temperature
  (read-only; mirrors the active profile's preset).
- **Numbers** — per-profile keep-warm temperature ×5, per-profile keep-warm time ×5.
- **Select** — active keep-warm profile (1–5).
- **Switch** — keep warm after boil.

The **status** sensor (siid 2 / piid 1) decodes these device states:

| Value | Meaning |
|---|---|
| 0 | standby |
| 1 | heating |
| 2 | boiling / keep-warm |
| 3 | heating to target |
| 4 | cooling |
| 5 | error / abnormal |

UI is localised in **Czech**, **English** and **Russian**.

---

## Installation

1. **Bluetooth reach.** The kettle must be in range of a Bluetooth adapter on your HA host,
   or of an **ESPHome `bluetooth_proxy`** (recommended — see the example below). The kettle
   accepts only **one** BLE client at a time, so while HA owns the connection keep your
   phone's Bluetooth off / Mi Home closed.
2. **Get the MAC and the BLE token (beaconkey).** Use a *Xiaomi cloud token extractor*
   (e.g. `PiotrMachowski/Xiaomi-cloud-tokens-extractor`). Log into your Xiaomi account in
   the extractor (do this yourself — never paste your Xiaomi password anywhere else), find
   the kettle, and copy its **MAC** (`AA:BB:CC:DD:EE:FF`) and its **token / beaconkey**
   (24 hex characters). The kettle must already be bound to your Mi Home account once.
3. **Install the component.** Home Assistant 2025.3 or newer is required. Add this
   repository as a custom **Integration** repository in HACS, install *Yunmi Kettle*,
   and restart Home Assistant. A manual copy of `custom_components/yunmi_kettle/` into
   `config/custom_components/` works as well.
4. **Add the integration.** Settings → Devices & Services → **Add Integration** →
   *"Yunmi Kettle"*. Enter the MAC, the token, a name and the push-watchdog interval.
   The kettle must be powered during this one-time login check.

## Power switching and recovery

It is safe for the integration if a smart plug cuts power to the kettle. While the
kettle is off, its entities become unavailable. When power is restored, Home Assistant
2026.5 and newer forgets the previous duplicate-advertisement cache and reconnects as
soon as a connectable BLE advertisement is received. No reload or Home Assistant restart
should be necessary.

Do not hard-code a particular smart-plug entity into this integration. Keep plug control
in a Home Assistant script or automation, wait for the kettle entities to become
available, and then change a profile if needed. This keeps the component reusable for
direct adapters, ESPHome proxies and any brand of smart plug.

## Upgrading from 0.2

Install 0.3 over the existing component and restart Home Assistant. The config entry is
migrated in place. The same devices and entity IDs remain registered; the ten profile
number entities merely move to the **Configuration** section of the device page. The
default watchdog changes from 60 to 300 seconds only for new entries—an explicitly saved
poll interval remains unchanged.

If recovery does not occur after switching power back on, download diagnostics from
Settings → Devices & Services → Yunmi Kettle. MAC address and token are redacted.

---

## Example ESPHome Bluetooth proxy

Any ESP32 (incl. a cheap ESP32-C3) works. Active connections are required so HA can write
to the kettle, not just read advertisements. Secrets are placeholders — use your own.

```yaml
esphome:
  name: btproxy
  friendly_name: BT Proxy

esp32:
  board: lolin_c3_mini        # any ESP32 / ESP32-C3 board with BLE
  framework:
    type: esp-idf             # recommended for bluetooth_proxy (stabler BLE)

wifi:
  ssid: "YOUR_WIFI"
  password: "YOUR_WIFI_PASSWORD"

logger:
api:
  encryption:
    key: "YOUR_API_ENCRYPTION_KEY"
ota:
  - platform: esphome
    password: "YOUR_OTA_PASSWORD"

esp32_ble_tracker:
  scan_parameters:
    interval: 1100ms
    window: 1100ms
    active: true

bluetooth_proxy:
  active: true                # allow HA to open GATT connections (control), not just scan
  cache_services: false       # the kettle's cached GATT came back empty -> force fresh discovery
```

> Tip for debugging: temporarily set `logger: {level: VERY_VERBOSE}` and run
> `esphome logs btproxy.yaml` to see the raw GATT notifications the proxy forwards.

---

## How it works (protocol)

Full byte-level spec is in [`PROTOCOL.md`](PROTOCOL.md). In short:

1. **Secure login (Xiaomi "mi" / miauth).** GATT service `0xFE95`. After enabling
   notifications, the client does a pre-login **auth-init** (write `0xA4` to the UPNP char,
   echo the device's `00 00 04 …` frames back as `00 00 05 …`), then `CMD_LOGIN` (`24000000`)
   + a framed key exchange on the AUTH char using the token. Session keys are derived with
   `HKDF-SHA256(token, salt = rand_key + remote_key, info = "mible-login-info")` →
   `dev_key / app_key / dev_iv / app_iv`. Both HMAC proofs are verified.
2. **Encrypted MIoT property channel.** AES-CCM (tag 4, `nonce = iv + 0x00000000 +
   uint32_le(counter)`), app→device on one characteristic, device→app on another. Plaintext
   is a MIoT TLV `[len][0x20][tid][00][op][01][siid][piid][00][vlen][vtype][value]` with
   `op` = SET / read / subscribe / subscribe-response. Properties are addressed by
   `siid`/`piid` per the `yunmi-v14` MIoT spec.
3. **Two non-obvious tricks that make reads work over a BLE proxy:**
   - The **pre-login auth-init** above; without it the device stays in a session mode that
     never volunteers full state.
   - **Read one property per subscribe.** A multi-property subscribe makes the device answer
     with a large `op=0x03` snapshot that exceeds the negotiated MTU and is sent as a *framed
     multi-parcel* transfer which (over an ESPHome proxy) is not delivered. A single-property
     subscribe answers in **one small notify** that arrives fine — so the integration reads
     each property one at a time.

For the **complete byte-level protocol** (full session handshake, transport framing,
property map, and capture/decrypt tooling) see [`PROTOCOL.md`](PROTOCOL.md).

---

## Sources & credits

This was built by standing on a few shoulders:

- **[asruda/mi_kettle_pro](https://github.com/asruda/mi_kettle_pro)** — a HA integration for
  the *older* Mi Kettle. It doesn't fit the v14 data layer, but its **auth-init** sequence
  (`a4` + `OP_ALREADY_LOGIN`) was the clue that unlocked full state reporting here.
- **[dnandha/miauth](https://github.com/dnandha/miauth)** — the reference for the Xiaomi "mi"
  secure login / token → session-key derivation (HKDF + AES-CCM). This is where the auth
  handshake was understood and validated.
- **[danielkucera/mi-standardauth](https://github.com/danielkucera/mi-standardauth)** — origin
  of the standard-auth token/login crypto.
- **[MiEcosystem/mijia_ble_api](https://github.com/MiEcosystem/mijia_ble_api)** — the official
  Mijia BLE SDK primitives, confirming the property-RPC model.
- The MIoT `yunmi-v14` device spec
  (`urn:miot-spec-v2:device:kettle:0000A009:yunmi-v14:1`) for the siid/piid map.
- The rest is reverse-engineering from Android HCI snoop captures of Mi Home talking to the
  real kettle (decrypted with the session keys). The capture/decode helper scripts
  (`extract_keys.py`, `decode2.py`, `parse_btsnoop.py`) live in this repo.

---

## Disclaimer

Not affiliated with or endorsed by Xiaomi. "Mi", "Xiaomi" and product names are trademarks
of their owners. Provided as-is, no warranty. You are responsible for your own kettle, your
own account credentials, and not leaving water heating unattended.
