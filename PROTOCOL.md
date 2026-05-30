# Xiaomi Mi Smart Kettle Pro (yunmi.kettle.v14 / MJHWSH02YM) — BLE protocol

Fully reverse-engineered 2026-05-29 from an Android HCI snoop (Mi Home) of the real
device, decrypted with session keys derived from the device token. Both the auth
handshake and the encrypted MIoT property read/write are confirmed.

## Device
- model `yunmi.kettle.v14`, spec `urn:miot-spec-v2:device:kettle:0000A009:yunmi-v14:1`
- BLE only (no Wi-Fi). DID `blt.x.xxxxxxxxxxx`, MAC `AA:BB:CC:DD:EE:FF`.
- **token (beaconkey, 12 bytes)** from Xiaomi cloud token extractor: `<YOUR_24_HEX_TOKEN>`.
- Advertises MiBeacon under service `0xFE95` (idle beacon carries no sensor data).

## GATT (service 0xFE95)
Characteristics (16-bit UUIDs under fe95): `0004`(read), `0010`(write-no-resp,notify),
`0017`(write,notify), `0018`(wnr,notify), `0019`(wnr,notify), `001a`(wnr,notify),
`001b`(wnr,notify). Plus generic `1800/1801`.

Value handles seen in the capture (resolve by UUID at runtime; handles may differ):
- `0x0011` = UPNP/login channel (UUID `0010`)
- `0x001d` = AUTH/framed-handshake channel (UUID `0019`)
- `0x0021` = MISERVICE **write** channel (app→device)
- `0x0025` = MISERVICE **notify** channel (device→app)

## Auth handshake (= Xiaomi "mi" standard login; ref: dnandha/miauth)
Only **login** is needed (device already bound → we have the token). Flow:
1. Enable notifications (CCCDs) on the notify chars.
2. Write `CMD_LOGIN = 24 00 00 00` to UPNP (`0x0011`).
3. Framed transfer on AUTH (`0x001d`) using the **parcel** framing
   (`write_parcel`: 18-byte chunks, each prefixed `[frame_no, 0x00]`, frame_no from 1):
   - send header `00 00 00 0b 01 00` (CMD_SEND_KEY), device replies `00 00 01 01` (RCV_RDY)
   - send `rand_key` (16 random bytes) as parcel → `01 00 <rand_key>`
   - device returns its `remote_key` (16B) then `remote_info` (32B) in `00 00 02 0x ...` frames
   - send header `00 00 00 0a 02 00` (CMD_SEND_INFO), device `RCV_RDY`
   - send `login_info` (32B) as parcel → `01 00 <login_info>`
4. Device sends `21 00 00 00` (CFM_LOGIN_OK) on UPNP. (errors: `e0..e3`, `23`=login err)

## Session keys (validated: both HMACs matched byte-exact)
```
salt      = rand_key + remote_key
salt_inv  = remote_key + rand_key
derived   = HKDF-SHA256(ikm=token, salt=salt, info="mible-login-info", L=64)
dev_key = derived[0:16]   app_key = derived[16:32]
dev_iv  = derived[32:36]  app_iv  = derived[36:40]
login_info           = HMAC-SHA256(app_key, salt)          # we send this
expected_remote_info = HMAC-SHA256(dev_key, salt_inv)       # must equal device's remote_info
```

## Encrypted MIoT property channels (AES-CCM, tag=4, aad=None)
```
nonce   = iv + b"\x00\x00\x00\x00" + uint32_le(counter)     # counter increments per frame, per direction
encrypt/RX(device→app) uses dev_key/dev_iv ; TX(app→device) uses app_key/app_iv
```
- **Write/request → handle 0x0021** (ATT Write Command 0x52), framed:
  `00 00 00 00 01 00` (frame-0 header, 1 frame) then `01 00 <idx_le16> <ciphertext>`.
- **Notify/response → handle 0x0025**: `00 00 02 00 <idx_le16> <ciphertext>`.
  App ACKs each notify with `00 00 03 00`.

### Plaintext TLV (inside the ciphertext)
`[len][0x20][tid][0x00][op][0x01][siid][piid][0x00][0x01][vtype][value...]`
- `len` = total bytes after this byte; `tid` increments.
- `op`: `0x00`=SET (write), `0x01`=read request, `0x02`=subscribe-batch (then list of
  `siid,piid` pairs), `0x04`=read response (carries value).
- `vtype`: `0x10`=uint8 (1-byte value), `0x30`=uint16 (2-byte LE value).

## Property map (siid/piid) — confirmed from decrypted traffic
| Function | siid/piid | type | notes |
|---|---|---|---|
| current water temp | 3 / 6 | uint8 °C | read/notify |
| status | 2 / 1 | uint8 | 0 standby,1 heating,2 boil+keepwarm,3 heat-to-set,4 cooling,5 abnormal |
| insulation switch (keep-warm on/off) | 3 / 2 | bool | read/write |
| insulation mode | 3 / 1 | uint8 | 0=keepwarm-after-boil,1=keepwarm-no-boil |
| **active profile selector** | 3 / 3 | uint8 | 0..4 (which of the 5 profiles is active) |
| profile target temps | 5 / 1..5 | uint8 °C | the 5 presets (write to set target) |
| profile keep-warm durations | 5 / 6..10 | uint16 | 30-min units (1=30m .. 24=12h) |

(siid3/piid4 also appears as a uint16 duration-like value; siid3/piid5 a uint16.)

## Goal command: keep-warm at 90 °C
1. `SET siid5/piid1 = 0x5A` (90 °C) for profile 1 — plaintext `0c 20 <tid> 00 00 01 05 01 00 01 10 5a`
2. `SET siid3/piid3 = 0` (select profile 1)
3. `SET siid3/piid2 = 1` (enable keep-warm)
Device should go to status=3 (heat-to-set) then keep warm at 90. Boil the last ~10° manually.

## References
- dnandha/miauth (login handshake + HKDF/AES-CCM crypto reference).
- danielkucera/mi-standardauth (token/login crypto origin).
- Capture + decode tooling (in this repo): `parse_btsnoop.py`, `extract_keys.py`, `decode2.py`
  — see the README's "Capturing & decrypting the BLE traffic" guide. You supply your own
  HCI capture and token (via `.env`); none are shipped here.
