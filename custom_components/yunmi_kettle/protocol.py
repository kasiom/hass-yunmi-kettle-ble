"""Xiaomi Mi Smart Kettle Pro (yunmi.kettle.v14) BLE protocol.

Implements the Xiaomi "mi" standard secure login (token -> session keys) and the
encrypted MIoT property read/write over the FE95 MISERVICE channels. Designed to
run over Home Assistant's Bluetooth stack (incl. ESPHome proxies) via a
BleakClient obtained from bleak-retry-connector.

See PROTOCOL.md for the full reverse-engineered spec.
"""
from __future__ import annotations

import asyncio
import logging
import struct
from collections import deque

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from cryptography.hazmat.primitives.hmac import HMAC
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from bleak import BleakClient

from .const import (
    ACK_DATA,
    CFM_LOGIN_OK,
    CMD_LOGIN,
    HDR_SEND_INFO,
    HDR_SEND_KEY,
    PROP_CURRENT_TEMP,
    RCV_OK,
    RCV_RDY,
    RESP_INFO,
    RESP_KEY,
    UUID_AUTH,
    UUID_MISERVICE_NOTIFY,
    UUID_MISERVICE_WRITE,
    UUID_UPNP,
    VTYPE_U8,
    VTYPE_U16,
)

_LOGGER = logging.getLogger(__name__)

_DEF_TIMEOUT = 8.0


class KettleError(Exception):
    """Protocol error."""


# --- crypto helpers -------------------------------------------------------

def _hkdf(ikm: bytes, salt: bytes, info: bytes, length: int = 64) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def _hmac(key: bytes, data: bytes) -> bytes:
    h = HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def _ccm_nonce(iv: bytes, counter: int) -> bytes:
    return iv + b"\x00\x00\x00\x00" + struct.pack("<I", counter)


# --- protocol -------------------------------------------------------------

class YunmiKettle:
    """A connected, authenticated session to the kettle."""

    def __init__(self, client: BleakClient, token: bytes) -> None:
        self._c = client
        self._token = token
        # buffer of received notifications; _await scans it WITHOUT discarding
        # non-matching frames, so an interleaved device push isn't lost while we
        # wait for an ack on another characteristic.
        self._inbox: deque[tuple[str, bytes]] = deque(maxlen=256)
        self._evt = asyncio.Event()
        self._keys: dict[str, bytes] = {}
        self._tx_ctr = -1         # app->device AES counter; first command uses 0
        self._tid = 0xb0          # plaintext transaction id
        self._notify_uuid = UUID_MISERVICE_NOTIFY
        self._write_uuid = UUID_MISERVICE_WRITE

    # ---- notification plumbing ----

    def _on_notify(self, sender, data: bytearray) -> None:
        uuid = getattr(sender, "uuid", str(sender)).lower()
        _LOGGER.debug("notify %s <- %s", uuid, bytes(data).hex())
        self._inbox.append((uuid, bytes(data)))
        self._evt.set()

    def _scan(self, predicate) -> tuple[str, bytes] | None:
        for i, (u, d) in enumerate(self._inbox):
            if predicate(u, d):
                del self._inbox[i]
                return u, d
        return None

    async def _await(self, predicate, timeout: float = _DEF_TIMEOUT) -> tuple[str, bytes]:
        """Wait for a buffered notification matching predicate(uuid, data).

        Non-matching frames stay in the inbox so a different consumer (e.g. the
        push collector) can still pick them up — critical because the device
        interleaves pushes on the notify char with acks on the write char.
        """
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while True:
            hit = self._scan(predicate)
            if hit is not None:
                return hit
            remaining = end - loop.time()
            if remaining <= 0:
                raise KettleError("timeout waiting for device response")
            self._evt.clear()
            try:
                await asyncio.wait_for(self._evt.wait(), timeout=remaining)
            except (asyncio.TimeoutError, TimeoutError):
                hit = self._scan(predicate)
                if hit is not None:
                    return hit
                raise KettleError("timeout waiting for device response") from None

    async def start(self) -> None:
        """Subscribe to the device's notify characteristics."""
        try:
            _LOGGER.debug("negotiated MTU = %s", self._c.mtu_size)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("mtu_size unavailable: %s", err)
        for s in self._c.services:
            for ch in s.characteristics:
                _LOGGER.debug("GATT char %s handle=0x%04x props=%s", ch.uuid, ch.handle, ch.properties)
        for uuid in (UUID_UPNP, UUID_AUTH, UUID_MISERVICE_NOTIFY, UUID_MISERVICE_WRITE):
            try:
                await self._c.start_notify(uuid, self._on_notify)
            except Exception as err:  # noqa: BLE001 - char may not be notifiable
                _LOGGER.debug("start_notify %s failed: %s", uuid, err)

    async def _write(self, uuid: str, data: bytes, response: bool = False) -> None:
        _LOGGER.debug("write %s -> %s", uuid, data.hex())
        await self._c.write_gatt_char(uuid, data, response=response)

    async def _write_parcel(self, uuid: str, payload: bytes) -> None:
        for i in range(0, len(payload), 18):
            chunk = bytes([i // 18 + 1, 0]) + payload[i:i + 18]
            await self._write(uuid, chunk)

    # ---- login ----

    async def _auth_init(self) -> None:
        """Pre-login auth-init the official Mi Home app performs before CMD_LOGIN.

        Captured from the device: write 0xA4 to UPNP, then the device sends
        ``00 00 04 <n> <payload>`` frames on AUTH which the app echoes back as
        ``00 00 05 <n> <payload>``. Without this the device appears to enter a
        limited session class and never volunteers the property snapshot.
        """
        await self._write(UUID_UPNP, b"\xa4")
        echoed = 0
        while True:
            try:
                _, data = await self._await(
                    lambda u, d: len(d) >= 4 and d[0] == 0 and d[1] == 0 and d[2] == 0x04,
                    timeout=2.0,
                )
            except KettleError:
                break
            await self._write(UUID_AUTH, b"\x00\x00\x05" + data[3:])
            echoed += 1
        _LOGGER.debug("auth-init: echoed %s device frame(s)", echoed)

    async def login(self) -> None:
        import os

        rand = os.urandom(16)
        # 0) pre-login auth-init (a4 + echo device 00 00 04 frames) — primes the
        #    device to serve full state; the official app always does this first.
        await self._auth_init()
        # 1) CMD_LOGIN on UPNP
        await self._write(UUID_UPNP, CMD_LOGIN)
        # 2) announce key transfer, wait ready, send rand_key
        await self._write(UUID_AUTH, HDR_SEND_KEY)
        await self._await(lambda u, d: d == RCV_RDY)
        await self._write_parcel(UUID_AUTH, rand)
        await self._await(lambda u, d: d == RCV_OK)

        # 3) device sends remote_key (00 00 02 0d) then remote_info (00 00 02 0c)
        #    as direct frames (post auth-init mode); ack each with 00 00 03 00.
        _, rk = await self._await(
            lambda u, d: len(d) >= 4 and d[0] == 0 and d[1] == 0 and d[2] == 0x02 and d[3] == RESP_KEY
        )
        remote_key = rk[4:4 + 16]
        await self._write(UUID_AUTH, ACK_DATA)
        _, ri = await self._await(
            lambda u, d: len(d) >= 4 and d[0] == 0 and d[1] == 0 and d[2] == 0x02 and d[3] == RESP_INFO
        )
        remote_info = ri[4:4 + 32]
        await self._write(UUID_AUTH, ACK_DATA)

        # 4) derive + verify session keys
        salt = rand + remote_key
        salt_inv = remote_key + rand
        d = _hkdf(self._token, salt, b"mible-login-info", 64)
        self._keys = {
            "dev_key": d[0:16], "app_key": d[16:32],
            "dev_iv": d[32:36], "app_iv": d[36:40],
        }
        if _hmac(self._keys["dev_key"], salt_inv) != remote_info:
            raise KettleError("remote_info mismatch - wrong token?")
        login_info = _hmac(self._keys["app_key"], salt)

        # 5) send login_info as a single parcel (matches the app), wait OK + CFM_LOGIN_OK
        await self._write(UUID_AUTH, HDR_SEND_INFO)
        await self._await(lambda u, d: d == RCV_RDY)
        await self._write(UUID_AUTH, b"\x01\x00" + login_info)
        await self._await(lambda u, d: d == RCV_OK)
        await self._await(lambda u, d: d == CFM_LOGIN_OK)
        _LOGGER.info("kettle login successful")
        # post-login init (op 0xf0). After auth-init the device echoes it as a
        # DIRECT notify (00 00 02 00 <idx> <ct>); the app ACKs that echo with
        # 00 00 03 00 and only then is the session fully "open" for the snapshot.
        hello = bytes([0x05, 0x20, self._next_tid(), 0x00, 0xF0])
        hctr, hct = self._encrypt(hello)
        await self._send_request(struct.pack("<H", hctr) + hct)
        try:
            _, echo = await self._await(
                lambda u, d: len(d) >= 4 and d[0] == 0 and d[1] == 0 and d[2] == 0x02,
                timeout=4.0,
            )
            _LOGGER.debug("f0 echo (direct): %s", echo.hex())
            await self._write(self._notify_uuid, ACK_DATA)   # 00 00 03 00
        except KettleError:
            _LOGGER.debug("no f0 echo")

    async def _receive(self, channel: str = UUID_AUTH) -> bytes:
        """Receive a framed transfer from the device on ``channel``.

        Device sends a 6-byte header frame (00 00 .. <cnt_le16>); we ack with
        RCV_RDY; device sends <cnt> data frames (<frm_le16> <payload>); we ack
        with RCV_OK. Returns the reassembled payload (frame counters stripped).
        """
        _, hdr = await self._await(
            lambda u, d: len(d) == 6 and d[0] == 0 and d[1] == 0
        )
        expected = hdr[4] | (hdr[5] << 8)
        await self._write(channel, RCV_RDY)
        buf = b""
        got = 0
        while got < expected:
            _, frm = await self._await(lambda u, d: len(d) >= 2 and d[0] != 0)
            buf += frm[2:]
            got += 1
        await self._write(channel, RCV_OK)
        _LOGGER.debug("received on %s: %s", channel[4:8], buf.hex())
        return buf

    # ---- MIoT property read / write ----

    def _next_tid(self) -> int:
        self._tid = (self._tid + 1) & 0xFF
        return self._tid

    def _encrypt(self, plaintext: bytes) -> tuple[int, bytes]:
        self._tx_ctr += 1
        ctr = self._tx_ctr
        ct = AESCCM(self._keys["app_key"], tag_length=4).encrypt(
            _ccm_nonce(self._keys["app_iv"], ctr), plaintext, None
        )
        return ctr, ct

    def _decrypt(self, counter: int, ct: bytes) -> bytes:
        return AESCCM(self._keys["dev_key"], tag_length=4).decrypt(
            _ccm_nonce(self._keys["dev_iv"], counter), ct, None
        )

    def _build_tlv(self, op: int, siid: int, piid: int, value: bytes = b"", vtype: int = 0) -> bytes:
        body = bytes([op, 0x01, siid, piid, 0x00])
        if value:
            body += bytes([0x01, vtype]) + value
        else:
            body += bytes([0x00, 0x00])
        # length byte = total frame length (len byte + 0x20 + tid + 0x00 + body)
        return bytes([len(body) + 4, 0x20, self._next_tid(), 0x00]) + body

    async def set_property(
        self, siid: int, piid: int, value: int, vtype: int = VTYPE_U8
    ) -> dict[tuple[int, int], int]:
        vbytes = struct.pack("<B", value) if vtype == VTYPE_U8 else struct.pack("<H", value)
        tlv = self._build_tlv(0x00, siid, piid, vbytes, vtype)
        ctr, ct = self._encrypt(tlv)
        await self._send_request(struct.pack("<H", ctr) + ct)
        _LOGGER.info("set siid=%s piid=%s value=%s", siid, piid, value)
        # the device echoes the changed property back; collect it (confirms write)
        echo = await self._collect(3.0, stop_when={(siid, piid)})
        _LOGGER.debug("set echo: %s", echo)
        return echo

    async def _send_request(self, payload: bytes) -> None:
        """Send a framed encrypted command on the write channel."""
        await self._write(self._write_uuid, b"\x00\x00\x00\x00\x01\x00")
        await self._await(lambda u, d: d == RCV_RDY)
        await self._write_parcel(self._write_uuid, payload)
        await self._await(lambda u, d: d == RCV_OK)

    def _decrypt_auto(self, blob: bytes) -> bytes | None:
        """Decrypt a response blob, trying plausible ct offsets + frame counters."""
        ccm = AESCCM(self._keys["dev_key"], tag_length=4)
        for off in (6, 4, 2, 0):
            ct = blob[off:]
            if len(ct) < 5:
                continue
            for ctr in range(0, 64):
                try:
                    return ccm.decrypt(_ccm_nonce(self._keys["dev_iv"], ctr), ct, None)
                except Exception:  # noqa: BLE001
                    pass
        return None

    # the properties we actually surface (read one-at-a-time; see SUBSCRIBE_CHUNK)
    SUBSCRIBE_PAIRS = [
        (2, 1),                                       # status
        (3, 6),                                       # current temp
        (3, 2),                                       # keep-warm switch
        (3, 3),                                       # active profile
        (5, 1), (5, 2), (5, 3), (5, 4), (5, 5),       # preset temps
        (5, 6), (5, 7), (5, 8), (5, 9), (5, 10),      # preset keep-warm durations
    ]

    @staticmethod
    def _val(vlen: int, raw: bytes) -> int | None:
        if vlen == 1 and len(raw) >= 1:
            return raw[0]
        if vlen == 2 and len(raw) >= 2:
            return struct.unpack("<H", raw[:2])[0]
        if vlen >= 4 and len(raw) >= 4:
            return struct.unpack("<I", raw[:4])[0]
        return None

    @classmethod
    def _parse_one(cls, tlv: bytes) -> tuple[int, int, int] | None:
        # single read-resp: [len][20][tid][00][op][01][siid][piid][00][vlen][vtype][value]
        if len(tlv) < 11:
            return None
        siid, piid, vlen, val = tlv[6], tlv[7], tlv[9], tlv[11:]
        v = cls._val(vlen, val)
        return None if v is None else (siid, piid, v)

    @classmethod
    def _parse_all(cls, pt: bytes) -> list[tuple[int, int, int]]:
        """Parse a decrypted payload: either a subscribe-batch (op 0x03 with N
        inline entries) or a single read-response (op 0x04)."""
        out: list[tuple[int, int, int]] = []
        if len(pt) >= 6 and pt[4] == 0x03:
            # [len][20][tid][00][03][count] then count× [siid][piid][00 00 00][vlen][vtype][value]
            pos = 6
            while pos + 7 <= len(pt):
                siid, piid, vlen = pt[pos], pt[pos + 1], pt[pos + 5]
                val = pt[pos + 7:pos + 7 + vlen]
                v = cls._val(vlen, val)
                if v is not None:
                    out.append((siid, piid, v))
                pos += 7 + vlen
            return out
        sp = cls._parse_one(pt)
        if sp:
            out.append(sp)
        return out

    # kept for back-compat with read_property
    _parse_sp = _parse_one

    async def _send_subscribe(self, pairs: list[tuple[int, int]]) -> None:
        body = bytes([0x02, len(pairs)])
        for s, p in pairs:
            body += bytes([s, p, 0x00])
        tlv = bytes([len(body) + 4, 0x20, self._next_tid(), 0x00]) + body
        ctr, ct = self._encrypt(tlv)
        await self._send_request(struct.pack("<H", ctr) + ct)

    # ≤ this many (siid,piid) per subscribe so the op=0x03 response fits in ONE
    # direct notify (MTU-3 ≈ 93 B; ~8 B/entry). The full 17-prop batch would need
    # a framed multi-parcel transfer the device does not deliver over the proxy.
    SUBSCRIBE_CHUNK = 1

    async def subscribe_and_read(self, collect: float = 4.0) -> dict[tuple[int, int], int]:
        """Read current property values by subscribing in small chunks, so each
        response arrives as a single direct op=0x03 notify (which works over the
        proxy) rather than the un-deliverable big framed snapshot."""
        result: dict[tuple[int, int], int] = {}
        pairs = self.SUBSCRIBE_PAIRS
        for i in range(0, len(pairs), self.SUBSCRIBE_CHUNK):
            chunk = pairs[i:i + self.SUBSCRIBE_CHUNK]
            await self._send_subscribe(chunk)
            got = await self._collect(collect, stop_when=set(chunk))
            result.update(got)
        _LOGGER.debug("subscribe read %s props: %s", len(result), result)
        return result

    async def _collect(
        self, duration: float, stop_when: set[tuple[int, int]] | None = None
    ) -> dict[tuple[int, int], int]:
        """Drain device->app pushes for ``duration`` s, decrypt + parse them.

        Handles both framed transfers (00 00 00 00 <cnt>) and direct notifies
        (00 00 02 00 <idx>). Returns the latest value seen per (siid, piid).
        Stops early once every (siid, piid) in ``stop_when`` has a value.
        """
        result: dict[tuple[int, int], int] = {}
        nuuid = self._notify_uuid
        loop = asyncio.get_running_loop()
        end = loop.time() + duration
        while loop.time() < end:
            if stop_when and stop_when <= result.keys():
                break
            remaining = end - loop.time()
            if remaining <= 0:
                break
            try:
                _, data = await self._await(lambda u, d: len(d) >= 6, timeout=min(2.5, remaining))
            except KettleError:
                continue
            pts: list[bytes] = []
            if data[0:2] == b"\x00\x00" and data[2:4] == b"\x00\x00":
                expected = data[4] | (data[5] << 8)
                await self._write(nuuid, RCV_RDY)
                buf = b""
                got = 0
                while got < expected:
                    try:
                        _, frm = await self._await(lambda u, d: len(d) >= 3 and d[0] != 0, timeout=2.5)
                    except KettleError:
                        break
                    buf += frm[2:]
                    got += 1
                await self._write(nuuid, RCV_OK)
                dec = self._decrypt_auto(buf)
                if dec:
                    pts.append(dec)
            elif data[0:3] == b"\x00\x00\x02":
                idx = struct.unpack("<H", data[4:6])[0]
                try:
                    dec = self._decrypt(idx, data[6:])
                except Exception:  # noqa: BLE001
                    dec = self._decrypt_auto(data[6:])
                await self._write(nuuid, ACK_DATA)
                if dec:
                    pts.append(dec)
            else:
                continue
            for pt in pts:
                _LOGGER.debug("push pt=%s", pt.hex())
                for siid, piid, val in self._parse_all(pt):
                    result[(siid, piid)] = val
        return result

    async def read_property(self, siid: int, piid: int, timeout: float = 4.0) -> int | None:
        """Read one property via op=0x01; the device answers with an op=0x04 push."""
        res = await self.read_many([(siid, piid)], timeout=timeout)
        return res.get((siid, piid))

    async def read_many(
        self, pairs: list[tuple[int, int]], timeout: float = 4.0
    ) -> dict[tuple[int, int], int]:
        """Send an op=0x01 read for each pair, collecting every value the device
        pushes back (it answers reads/sets with op=0x04 notifies)."""
        result: dict[tuple[int, int], int] = {}
        for siid, piid in pairs:
            if (siid, piid) in result:
                continue
            tlv = self._build_tlv(0x01, siid, piid)
            ctr, ct = self._encrypt(tlv)
            await self._send_request(struct.pack("<H", ctr) + ct)
            got = await self._collect(timeout, stop_when={(siid, piid)})
            result.update(got)
        return result

    @staticmethod
    def _parse_value(pt: bytes) -> int | None:
        # [len][20][tid][00][op][01][siid][piid][00][01][vtype][value...]
        if len(pt) < 12:
            return None
        vtype = pt[10]
        val = pt[11:]
        if vtype == VTYPE_U8 and len(val) >= 1:
            return val[0]
        if vtype == VTYPE_U16 and len(val) >= 2:
            return struct.unpack("<H", val[:2])[0]
        return None
