"""Extract the miauth handshake (rand_key + remote_key) per connection from a
btsnoop capture, so we can derive session keys for that capture."""
import struct, sys

AUTH_H = 0x001d  # AUTH/handshake value handle


def recs(path):
    d = open(path, 'rb').read(); o = 16
    while o + 24 <= len(d):
        ol, il, fl, dr, ts = struct.unpack('>IIIIq', d[o:o+24]); o += 24
        yield fl, d[o:o+il]; o += il


def att(path):
    reasm = {}
    for fl, pkt in recs(path):
        if not pkt or pkt[0] != 0x02:
            continue
        b = pkt[1:]
        if len(b) < 4:
            continue
        h = b[0] | (b[1] << 8); pb = (h >> 12) & 3; ch = h & 0xFFF
        al = b[2] | (b[3] << 8); pl = b[4:4+al]
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
            yield ch, ('TX' if op in (0x12, 0x52) else 'RX'), l2[1] | (l2[2] << 8), l2[3:]


def sessions(path):
    """Return {conn_handle: (rand_key_hex, remote_key_hex)} for each BLE session
    in the capture, extracted from the miauth handshake on the AUTH char."""
    tx = {}  # ch -> list of app->device payloads on AUTH
    rx = {}  # ch -> list of device->app payloads on AUTH
    for ch, d, ah, val in att(path):
        if ah != AUTH_H:
            continue
        (tx if d == 'TX' else rx).setdefault(ch, []).append(val)
    out = {}
    for ch in sorted(set(tx) | set(rx)):
        t = tx.get(ch, []); r = rx.get(ch, [])
        # app rand_key: the parcel(s) starting 01 00 after the 0000000b0100 header
        rand = b''
        for v in t:
            if v[:2] in (b'\x01\x00', b'\x02\x00'):
                rand += v[2:]
        # device frames: remote_key(16)+remote_info(32) in 00 00 02 0x .. frames
        devbuf = b''
        for v in r:
            if len(v) >= 4 and v[0] == 0 and v[1] == 0 and v[2] == 0x02:
                devbuf += v[4:]
            elif v[:2] in (b'\x01\x00', b'\x02\x00', b'\x03\x00'):
                devbuf += v[2:]
        if len(rand) >= 16 and len(devbuf) >= 16:
            out[ch] = (rand[:16].hex(), devbuf[:16].hex())
    return out


def main(path):
    s = sessions(path)
    if not s:
        print("no handshake sessions found")
    for ch, (rand, remote) in s.items():
        print(f"conn {ch}: rand_key = {rand}  remote_key = {remote}")


if __name__ == '__main__':
    main(sys.argv[1])
