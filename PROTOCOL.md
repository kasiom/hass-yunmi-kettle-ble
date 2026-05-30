# Xiaomi Mi Smart Kettle Pro (yunmi.kettle.v14 / MJHWSH02YM) — BLE protocol

The complete, reverse-engineered protocol for talking to this kettle over Bluetooth LE
(the Xiaomi "mi" secure login + MIoT-spec property RPC on service `0xFE95`). Derived from
Android HCI snoops of the official Mi Home app and validated live — login, encrypted
property reads and writes all work, including **through an ESPHome Bluetooth proxy**.

This file is the authoritative spec; the README is the user-facing overview.

---

## Device

- Model `yunmi.kettle.v14` (MJHWSH02YM), spec
  `urn:miot-spec-v2:device:kettle:0000A009:yunmi-v14:1`. BLE only — no Wi-Fi.
- **Token** (beaconkey, 12 bytes / 24 hex) from a Xiaomi cloud token extractor. The device
  must already be bound to your Mi account once.
- Advertises MiBeacon under service `0xFE95`, **product id `0x1A4F`** (the idle advert carries
  no sensor data — temperature is only on the encrypted channel).

## ⚠️ Hard limitation: heating cannot be started over BLE

The kettle **cannot be told to start heating/boiling** over Bluetooth. This was confirmed from
Mi Home's *own* traffic: the app only ever sends profile-select and preset writes; actual
heating begins **only after the physical button is pressed**. So this protocol lets you *read*
state and *configure* keep-warm presets, but not remotely power the heater. (Older Mi Kettles
had a remote-heat command; the newer MIoT `yunmi.kettle.v14` does not expose one.)

---

## GATT (service `0xFE95`)

16-bit characteristic UUIDs under `fe95` (resolve to value handles at runtime; handles below are
what we observed):

| UUID | Value handle | Role |
|---|---|---|
| `0010` | `0x0011` | UPNP / login command (`CMD_LOGIN`, `a4`; notifies `CFM_LOGIN_OK`) |
| `0019` | `0x001d` | AUTH — the framed secure-login handshake |
| `001a` | `0x0021` | MISERVICE **write** — app→device commands (+ device→app flow-control acks) |
| `001b` | `0x0025` | MISERVICE **notify** — device→app property data (+ app→device acks) |
| `0004` | `0x000e` | read-only firmware string (e.g. `2.1.0_0029`) |

Enable notifications (write the CCCD) on `0x0011`, `0x001d`, `0x0021`, `0x0025` before login.

---

## Transport

Two framings ride on the MISERVICE characteristics.

**Control frames** (4 bytes, plaintext, never consume an AES counter):
`00 00 01 01` = RCV_RDY, `00 00 01 00` = RCV_OK, `00 00 03 00` = ACK.

**App → device** (commands, on `0x0021`) is always a *framed transfer*:
write header `00 00 00 00 <cnt_le16>`, wait RCV_RDY, write `cnt` parcels
`<parcel_no_le16> <≤18-byte chunk>`, wait RCV_OK. The reassembled payload is
`<aes_counter_le16> <ciphertext + 4-byte tag>`.

**Device → app** (on `0x0025`) comes in one of two forms:
- **Direct** `00 00 02 00 <counter_le16> <ct>` when it fits one notification (≤ MTU − 3 bytes);
  the client ACKs with `00 00 03 00`.
- **Framed** `00 00 00 00 <cnt>` + parcels (driven with RCV_RDY / RCV_OK) when larger.
  **This larger framed form is not reliably delivered over an ESPHome proxy** — see "reads" below.

**AES-CCM**: TX (app→device) uses `app_key`/`app_iv`; RX (device→app) uses `dev_key`/`dev_iv`.
`nonce = iv + 00 00 00 00 + uint32_le(counter)`, tag = 4 bytes, AAD = none. The counter increments
per *encrypted* frame, per direction; the first app command (the `f0` below) uses counter `0`.

---

## The whole session, byte by byte

`→` = app→device, `←` = device→app.

```
A. Link
   →  connect (BLE, possibly through a proxy); MTU is negotiated (~96 over a proxy)
   →  enable notifications (CCCD) on 0x11, 0x1d, 0x21, 0x25

B. Auth-init   (REQUIRED — without it the device stays in a limited session and
                never serves full state; this is what the official app does first)
   →  0x11   A4
   ←  0x1d   00 00 04 00 06 5B
   →  0x1d   00 00 05 00 06 5B           (echo back, byte[3] 04→05)
   ←  0x1d   00 00 04 01 <89×5B>
   →  0x1d   00 00 05 01 <89×5B>         (echo back)

C. Secure login (Xiaomi "mi" / miauth)
   →  0x11   24 00 00 00                 (CMD_LOGIN)
   →  0x1d   00 00 00 0B 01 00           (send-key header, 1 parcel)
   ←  0x1d   00 00 01 01                 (RCV_RDY)
   →  0x1d   01 00 <rand_key:16>         (16 random bytes you generate)
   ←  0x1d   00 00 01 00                 (RCV_OK)
   ←  0x1d   00 00 02 0D <remote_key:16>
   →  0x1d   00 00 03 00                 (ACK)
   ←  0x1d   00 00 02 0C <remote_info:32>
   →  0x1d   00 00 03 00                 (ACK)
   --- derive + verify session keys (below) ---
   →  0x1d   00 00 00 0A 01 00           (send-info header, 1 parcel)
   ←  0x1d   00 00 01 01                 (RCV_RDY)
   →  0x1d   01 00 <login_info:32>       (single 34-byte parcel)
   ←  0x1d   00 00 01 00                 (RCV_OK)
   ←  0x11   21 00 00 00                 (CFM_LOGIN_OK; errors: e0..e3, 23)

D. Open the property session (op 0xF0)
   →  0x21   framed write of  05 20 <tid> 00 F0    (encrypted, counter 0)
   ←  0x25   00 00 02 00 <ctr> <ct>       (device echoes F0, DIRECT)
   →  0x25   00 00 03 00                  (ACK the echo)

E. Read a property   (one (siid,piid) at a time — see "reads" below)
   →  0x21   framed write of  <len> 20 <tid> 00  02 01 <siid> <piid> 00   (op=0x02 subscribe, ONE pair)
   ←  0x25   00 00 02 00 <ctr> <ct>       → decrypt → op=0x03 with one entry → value
   →  0x25   00 00 03 00                  (ACK)

F. Write a property   (e.g. set a preset; counter++)
   →  0x21   framed write of  <len> 20 <tid> 00  00 01 <siid> <piid> 00 01 <vtype> <value>  (op=0x00 SET)
   ←  0x25   00 00 02 00 <ctr> <ct>       → device echoes the changed value (op=0x04)
   →  0x25   00 00 03 00                  (ACK)
```

Note that after auth-init the device is in **"direct notify" mode**: `remote_key`/`remote_info`
and the `f0` echo arrive as **direct** `00 00 02 0x …` frames (not framed transfers), each ACKed
with `00 00 03 00`.

## Session keys

```
salt       = rand_key + remote_key
k          = HKDF-SHA256(ikm = token, salt = salt, info = "mible-login-info", length = 64)
dev_key = k[0:16]   app_key = k[16:32]   dev_iv = k[32:36]   app_iv = k[36:40]
login_info           = HMAC-SHA256(app_key, salt)               # you send this
expected_remote_info = HMAC-SHA256(dev_key, remote_key + rand_key)  # must equal the device's remote_info
```

(Both HMAC proofs were validated byte-exact against captures.)

---

## MIoT property TLV (the decrypted plaintext)

`[len][0x20][tid][0x00][op][0x01][siid][piid][0x00][vlen][vtype][value…]`

- `len` = total length of this TLV (including the length byte); `tid` increments.
- `op`: `0x00` = SET (write) · `0x01` = read-request · `0x02` = subscribe · `0x03` = subscribe
  response (a snapshot; for N>1 it inlines N entries `[siid][piid][00 00 00][vlen][vtype][value]`)
  · `0x04` = read response / change push · `0xF0` = session-open init.
- `vtype`: `0x10` = uint8, `0x30` = uint16 (LE), `0x00` = bool (1 byte). Parse the value by `vlen`.

> The app **never** sends an app-initiated `op=0x01` read — the device ignores those. Reads are
> done by `op=0x02` subscribe, which the device answers with `op=0x03`, then keeps pushing `op=0x04`.

## Reads over a proxy — read one property at a time

A subscribe for *many* properties makes the device answer with a large `op=0x03` snapshot
(~143 bytes for 17 props) that exceeds MTU − 3 and is therefore sent as a **framed multi-parcel**
transfer — which an ESPHome proxy does not reliably deliver (verified at the ESP radio level: the
snapshot bytes never leave the kettle for the proxy connection). A subscribe for a **single**
property answers in **one small direct notify**, which arrives fine. So: subscribe to each
`(siid, piid)` individually and merge the results.

---

## Property map (siid / piid) — confirmed from decrypted traffic

| Function | siid/piid | type | notes |
|---|---|---|---|
| status | 2 / 1 | uint8 | 0 standby · 1 heating · 2 boiling/keep-warm · 3 heat-to-target · 4 cooling · 5 abnormal |
| insulation mode | 3 / 1 | uint8 | 0 = keep-warm after boil · 1 = keep-warm without boil |
| keep-warm switch | 3 / 2 | bool | on/off (read/write) |
| **active profile selector** | 3 / 3 | uint8 | `0..4` — which of the 5 profiles is active |
| heartbeat | 3 / 5 | uint16 | constant `0`, pushed continuously (keepalive) |
| current water temp | 3 / 6 | uint8 °C | read/notify |
| profile target temps | 5 / 1..5 | uint8 °C | the 5 keep-warm presets (write to set) |
| profile keep-warm durations | 5 / 6..10 | uint8 | in 30-minute units (`1`=30 min … `24`=12 h) |

## Practical goal: keep-warm at 90 °C

1. `SET siid5/piidN = 0x5A` (90 °C) for some profile N (plaintext e.g.
   `0c 20 <tid> 00 00 01 05 01 00 01 10 5a` for profile 1).
2. `SET siid3/piid3 = N-1` to make that profile active.
3. `SET siid3/piid2 = 1` to enable keep-warm.

The kettle will then hold 90 °C **after you physically boil it** (remember: heating can't be
started over BLE). Each SET is echoed back as an `op=0x04` notify, which confirms the write.

---

## Capturing & decrypting the traffic yourself

The repo includes tooling to reproduce/extend this work (you supply your own capture + token;
none are shipped). Set `KETTLE_TOKEN` / `KETTLE_MAC` in a `.env` (see `.env.example`).

1. **Capture** an HCI snoop on Android: Developer options → enable *Bluetooth HCI snoop log*,
   reproduce in Mi Home, then `adb bugreport bugreport.zip`. The log is inside the zip at
   `FS/data/misc/bluetooth/logs/btsnoop_hci.log[.last]`.
   (`adb shell dumpsys bluetooth_manager` may report `EMPTY` — misleading; the log *is* written.)
2. **`python parse_btsnoop.py <btsnoop>`** — dump raw ATT ops (handles + bytes).
3. **`python extract_keys.py <btsnoop>`** — pull `rand_key`/`remote_key` per BLE session from the
   handshake (no token needed).
4. **`python decode2.py <btsnoop>`** — using `KETTLE_TOKEN`, derive the session keys and decrypt +
   label every MISERVICE frame (direction, AES counter, op, siid/piid, value).

## References

- [dnandha/miauth](https://github.com/dnandha/miauth) — the Xiaomi "mi" secure-login reference
  (token → HKDF session keys + AES-CCM). The login here is an independent implementation of it.
- [danielkucera/mi-standardauth](https://github.com/danielkucera/mi-standardauth) — token/login
  crypto origin.
- [asruda/mi_kettle_pro](https://github.com/asruda/mi_kettle_pro) — its pre-login auth-init
  (`a4`) sequence was the clue that unlocked full state reporting.
- MIoT spec `urn:miot-spec-v2:device:kettle:0000A009:yunmi-v14:1` — the siid/piid map.
