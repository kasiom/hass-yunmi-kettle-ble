"""Clean MISERVICE decoder: direction by ATT opcode, robust ct-offset + counter
brute force, decrypt and label the MIoT TLV op. Ground-truth for the app session."""
import os, struct, sys

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from extract_keys import sessions as _extract_sessions


def _load_env(path=".env"):
    """Minimal .env loader (no dependency): KEY=VALUE lines into os.environ."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass


_load_env()
# 24-hex BLE token (beaconkey) from a Xiaomi cloud token extractor; set KETTLE_TOKEN in .env
TOKEN = bytes.fromhex(os.environ.get("KETTLE_TOKEN", "00" * 12))
WRITE_H = 0x0021   # app -> device
NOTIFY_H = 0x0025  # device -> app
OPN = {0x00: 'SET', 0x01: 'READ-req', 0x02: 'SUBSCRIBE', 0x04: 'READ-resp', 0xf0: 'INIT-f0'}


def keys_for(rh, kh):
    salt = bytes.fromhex(rh) + bytes.fromhex(kh)
    d = HKDF(algorithm=hashes.SHA256(), length=64, salt=salt, info=b"mible-login-info").derive(TOKEN)
    return {'dev_key': d[:16], 'app_key': d[16:32], 'dev_iv': d[32:36], 'app_iv': d[36:40]}


def records(path):
    data = open(path, 'rb').read(); off = 16
    while off + 24 <= len(data):
        olen, ilen, flags, drops, ts = struct.unpack('>IIIIq', data[off:off+24]); off += 24
        yield flags, data[off:off+ilen]; off += ilen


def att_ops(path):
    reasm = {}
    for flags, pkt in records(path):
        if not pkt or pkt[0] != 0x02:
            continue
        body = pkt[1:]
        if len(body) < 4:
            continue
        h = body[0] | (body[1] << 8); pb = (h >> 12) & 3; ch = h & 0xFFF
        al = body[2] | (body[3] << 8); pl = body[4:4+al]
        reasm[ch] = pl if pb != 1 else reasm.get(ch, b'') + pl
        buf = reasm.get(ch, b'')
        if len(buf) < 4:
            continue
        l2len = buf[0] | (buf[1] << 8); cid = buf[2] | (buf[3] << 8)
        if len(buf) < 4 + l2len:
            continue
        l2 = buf[4:4+l2len]; reasm[ch] = b''
        if cid != 0x0004 or len(l2) < 3:
            continue
        op = l2[0]
        if op in (0x12, 0x52, 0x1b, 0x1d):
            ah = l2[1] | (l2[2] << 8)
            yield ch, op, ah, l2[3:]


def try_decrypt(key, iv, val):
    ccm = AESCCM(key, tag_length=4)
    for ofs in (4, 6, 0, 2):
        if len(val) <= ofs + 4:
            continue
        ct = val[ofs:]
        for c in range(0, 64):
            try:
                pt = ccm.decrypt(iv + b'\x00'*4 + struct.pack('<I', c), ct, None)
            except Exception:
                continue
            if len(pt) >= 5 and pt[1] == 0x20:
                return c, ofs, pt
    return None


def decode(path):
    keys = {h: keys_for(*v) for h, v in _extract_sessions(path).items()}
    for ch, op, ah, val in att_ops(path):
        if ch not in keys or ah not in (WRITE_H, NOTIFY_H):
            continue
        app2dev = op in (0x12, 0x52)
        k = keys[ch]
        key, iv = (k['app_key'], k['app_iv']) if app2dev else (k['dev_key'], k['dev_iv'])
        d = 'APP->DEV' if app2dev else 'DEV->APP'
        # raw short control frames (RCV_RDY/OK etc)
        if len(val) <= 6 and val[:2] in (b'\x00\x00', b'\x01\x00'):
            print(f"{d} ch{ch} h={ah:#06x} raw={val.hex()}")
            continue
        r = try_decrypt(key, iv, val)
        if not r:
            print(f"{d} ch{ch} h={ah:#06x} <nodec> {val.hex()}")
            continue
        ctr, ofs, pt = r
        opn = OPN.get(pt[4], hex(pt[4])) if len(pt) > 4 else '?'
        siid = pt[6] if len(pt) > 7 else None
        piid = pt[7] if len(pt) > 7 else None
        v = pt[10:].hex() if len(pt) > 10 else ''
        print(f"{d} ch{ch} ctr={ctr} op={opn:9} siid={siid} piid={piid} val={v}  PT={pt.hex()}")


if __name__ == '__main__':
    decode(sys.argv[1])
