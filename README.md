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

UI is localised in **English** and **Russian**.

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
3. **Copy the component.** Put `custom_components/yunmi_kettle/` into your HA
   `config/custom_components/` and restart Home Assistant.
4. **Add the integration.** Settings → Devices & Services → **Add Integration** →
   *"Yunmi Kettle"*. Enter the MAC, the token, a name, a default keep-warm temperature and
   a poll interval.

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

---

## Connection & message lifecycle (byte-level)

### GATT map (service `0xFE95`)

| Characteristic | Value handle | Role |
|---|---|---|
| `0010` | `0x11` | UPNP / login command (write `CMD_LOGIN`, `a4`; notify `CFM_LOGIN_OK`) |
| `0019` | `0x1d` | AUTH — the framed secure-login handshake |
| `001a` | `0x21` | MISERVICE **write** — app→device commands (+ device→app flow-control acks) |
| `001b` | `0x25` | MISERVICE **notify** — device→app property data (+ app→device acks) |

### Transport framing (on the encrypted MISERVICE channels)

- **Control frames** (4 bytes, plaintext): `00 00 01 01` = RCV_RDY, `00 00 01 00` = RCV_OK,
  `00 00 03 00` = ACK. They never consume an AES counter.
- **App → device command** is a *framed transfer*: write a header `00 00 00 00 <cnt_le16>`,
  wait for RCV_RDY, write `cnt` parcels of `<parcel_no_le16> <≤18-byte chunk>`, wait for RCV_OK.
  The reassembled payload is `<aes_counter_le16> <ciphertext + 4-byte tag>`.
- **Device → app data** comes in one of two forms:
  - **Direct** `00 00 02 00 <counter_le16> <ct>` when it fits a single notification
    (≤ MTU − 3 bytes); the client ACKs with `00 00 03 00`.
  - **Framed** `00 00 00 00 <cnt>` + parcels (RCV_RDY / RCV_OK) when larger — **this form is not
    reliably delivered over an ESPHome proxy**, which is why reads are kept small (see step E).
- **AES-CCM**: TX uses `app_key`/`app_iv`, RX uses `dev_key`/`dev_iv`;
  `nonce = iv + 00 00 00 00 + uint32_le(counter)`, tag = 4 bytes, no AAD. The counter increments
  per *encrypted* frame, per direction; the first app command (the `f0` below) uses counter `0`.

### The whole session, step by step

`→` = app→device, `←` = device→app.

```
A. Link
   →  connect (BLE, through the proxy); MTU is negotiated (~96)
   →  enable notifications (CCCD) on 0x11, 0x1d, 0x21, 0x25

B. Auth-init   (this is what flips the device into "direct notify" mode)
   →  0x11   A4
   ←  0x1d   00 00 04 00 06 5B
   →  0x1d   00 00 05 00 06 5B           (echo back, byte[3] 04→05)
   ←  0x1d   00 00 04 01 <89×5B>
   →  0x1d   00 00 05 01 <89×5B>         (echo back)

C. Secure login (miauth)
   →  0x11   24 00 00 00                 (CMD_LOGIN)
   →  0x1d   00 00 00 0B 01 00           (send-key header, 1 parcel)
   ←  0x1d   00 00 01 01                 (RCV_RDY)
   →  0x1d   01 00 <rand_key:16>         (our 16 random bytes)
   ←  0x1d   00 00 01 00                 (RCV_OK)
   ←  0x1d   00 00 02 0D <remote_key:16>
   →  0x1d   00 00 03 00                 (ACK)
   ←  0x1d   00 00 02 0C <remote_info:32>
   →  0x1d   00 00 03 00                 (ACK)
   --- derive session keys, locally ---
       salt       = rand_key + remote_key
       k          = HKDF-SHA256(token, salt, "mible-login-info", 64)
       dev_key=k[0:16]  app_key=k[16:32]  dev_iv=k[32:36]  app_iv=k[36:40]
       verify  HMAC(dev_key, remote_key + rand_key) == remote_info
       login_info = HMAC(app_key, salt)
   →  0x1d   00 00 00 0A 01 00           (send-info header, 1 parcel)
   ←  0x1d   00 00 01 01                 (RCV_RDY)
   →  0x1d   01 00 <login_info:32>
   ←  0x1d   00 00 01 00                 (RCV_OK)
   ←  0x11   21 00 00 00                 (CFM_LOGIN_OK)

D. Open the property session (op 0xF0)
   →  0x21   framed write of  05 20 <tid> 00 F0    (encrypted, counter 0)
   ←  0x25   00 00 02 00 <ctr> <ct>       (device echoes F0, direct)
   →  0x25   00 00 03 00                  (ACK the echo)

E. Read a property   (repeat once per siid/piid; counter++ each)
   →  0x21   framed write of  <len> 20 <tid> 00  02 01 <siid> <piid> 00   (op=0x02 subscribe, ONE pair)
   ←  0x25   00 00 02 00 <ctr> <ct>       → decrypt → op=0x03, one entry → value
   →  0x25   00 00 03 00                  (ACK)

F. Write a property   (e.g. set a preset; counter++)
   →  0x21   framed write of  <len> 20 <tid> 00  00 01 <siid> <piid> 00 01 <vtype> <value>  (op=0x00 SET)
   ←  0x25   00 00 02 00 <ctr> <ct>       → device echoes the changed value (op=0x04)
   →  0x25   00 00 03 00                  (ACK)
```

### Lifecycle of the integration

- **One persistent BLE session per kettle.** `_ensure_session()` connects and runs steps A–D
  once; every later poll and command reuses the same authenticated session.
- **Each poll** (default 60 s): read the surfaced properties one at a time (step E) and update
  the entities. Reading ~14 single-property subscribes takes a few seconds.
- **Each command** (number / select / switch): one SET (step F); the device's echo is folded
  into HA state immediately (optimistic update), and corrected on the next poll.
- **On any error or disconnect:** the session is dropped and steps A–D are re-run on the next
  poll. `bleak-retry-connector` handles the reconnect/backoff.
- **Single client only:** the kettle accepts one BLE central at a time, so keep the phone's
  Bluetooth off (or Mi Home closed) while Home Assistant holds the connection.

---

## Capturing & decrypting the BLE traffic (how this was reverse-engineered)

If you want to verify, extend, or port this to another Xiaomi BLE device, here is the whole
workflow. The helper scripts (`parse_btsnoop.py`, `extract_keys.py`, `decode2.py`) are in this repo.

### 1. Capture an HCI snoop of Mi Home talking to the device (Android)

- **Developer options → enable "Bluetooth HCI snoop log"** (set to *Enabled* / *Filtered*).
- Toggle Bluetooth off and on so logging starts cleanly.
- Open **Mi Home** and do the thing you want to capture (open the device, change a preset, …).
- Pull the log. On modern Android the snoop file isn't world-readable, so grab it via a bugreport:
  ```
  adb bugreport bugreport.zip
  ```
  Inside the zip: `FS/data/misc/bluetooth/logs/btsnoop_hci.log` (current) and `…log.last` (previous).
  > Gotcha: `adb shell dumpsys bluetooth_manager` may report `sSnoopLogSettingAtEnable=EMPTY`.
  > That's misleading — the log **is** being written and comes out in the bugreport.

### 2. Look at the raw ATT traffic

```
python parse_btsnoop.py btsnoop_hci.log
```
Walks btsnoop → HCI ACL → L2CAP and prints every ATT operation (CID `0x0004`) with
direction / handle / bytes — this is how you see the handshake and the framing.

### 3. Derive the session keys

You need the device **token** (24-hex *beaconkey*, from the cloud token extractor) plus, per BLE
connection, the **rand_key** (the 16 bytes the app sends) and the **remote_key** (the first 16
bytes the device returns) from the miauth handshake on the AUTH char. Pull the latter two
automatically:
```
python extract_keys.py btsnoop_hci.log
```
Then the keys are:
```
salt = rand_key + remote_key
k    = HKDF-SHA256(ikm=token, salt=salt, info="mible-login-info", length=64)
dev_key = k[0:16]   app_key = k[16:32]   dev_iv = k[32:36]   app_iv = k[36:40]
```
(This is the standard Xiaomi "mi" key derivation; `decode2.py` implements it with the
`cryptography` library. Reference: [dnandha/miauth](https://github.com/dnandha/miauth).)

### 4. Decrypt the property frames

```
python decode2.py btsnoop_hci.log
```
Patch its `SESSIONS = {conn_handle: (rand_key_hex, remote_key_hex)}` with the values from step 3
and it decrypts + labels every MISERVICE frame: direction, AES counter, `op`, `siid/piid`, value.
The crypto:
```
RX (device→app): dev_key / dev_iv        TX (app→device): app_key / app_iv
AES-CCM, nonce = iv + 00 00 00 00 + uint32_le(counter), tag = 4 bytes, aad = none
(the scripts brute-force the counter over a small range when it isn't known)
```

### 5. Debug the live integration

- **HA side:** set the `custom_components.yunmi_kettle` logger to `debug`
  (Developer Tools → Actions → `logger.set_level`) to log every GATT write/notify and every
  decrypted push.
- **ESP-proxy side** (to see the raw radio *before* it reaches HA): temporarily set the proxy's
  `logger: { level: VERY_VERBOSE }`, reflash, and run `esphome logs btproxy.yaml`. Look for
  `ESP_GATTC_NOTIFY_EVT` and the `bluetooth_gatt_notify_data_response` byte dumps — that's how we
  proved the big framed snapshot never leaves the kettle for the proxy connection.

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
