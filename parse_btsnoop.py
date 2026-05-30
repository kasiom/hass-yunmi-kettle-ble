"""Minimal btsnoop -> HCI ACL -> L2CAP(ATT) extractor, focused on a target BLE device.
Dumps ATT Write/Notify/Read ops with handles + values, and maps conn handle -> BD_ADDR."""
import os, struct, sys

# load .env if present (KETTLE_MAC=aa:bb:cc:dd:ee:ff)
try:
    with open(".env") as _f:
        for _l in _f:
            _l = _l.strip()
            if _l and not _l.startswith("#") and "=" in _l:
                _k, _v = _l.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())
except FileNotFoundError:
    pass

TARGET = os.environ.get("KETTLE_MAC", "aa:bb:cc:dd:ee:ff").lower()

def parse(path):
    data = open(path, "rb").read()
    assert data[:8] == b"btsnoop\x00", "not btsnoop"
    ver, dl = struct.unpack(">II", data[8:16])
    off = 16
    recs = []
    while off + 24 <= len(data):
        olen, ilen, flags, drops, ts = struct.unpack(">IIIIq", data[off:off+24])
        off += 24
        pkt = data[off:off+ilen]
        off += ilen
        recs.append((flags, ts, pkt))
    return dl, recs

def handle_uuid_map():
    # from our live GATT dump (fe95 service). handles unknown numerically; we match by value patterns instead.
    return {}

def main(path):
    dl, recs = parse(path)
    print(f"datalink={dl} records={len(recs)}")
    conn_addr = {}          # acl handle -> mac
    reasm = {}              # acl handle -> bytes (current L2CAP reassembly)
    att = []               # (ts, dir, acl_handle, opcode, att_handle, value)
    for flags, ts, pkt in recs:
        if not pkt:
            continue
        # H4: first byte = type (1002). Some logs use 1001 (no type byte) -> assume H4.
        typ = pkt[0]
        body = pkt[1:]
        direction = "RX" if (flags & 1) else "TX"   # 1 = controller->host
        if typ == 0x04:  # HCI event
            evt = body[0]
            if evt == 0x3e and len(body) >= 3:  # LE Meta
                sub = body[2]
                if sub in (0x01, 0x0a):  # LE Connection Complete / enhanced
                    # status, handle(2), role, peer_addr_type, peer_addr(6)
                    p = body[3:]
                    status = p[0]
                    chandle = p[1] | (p[2] << 8)
                    # enhanced (0x0a) has extra fields before addr
                    if sub == 0x01:
                        addr = p[5:11]
                    else:
                        addr = p[5:11]
                    mac = ":".join("%02x" % b for b in addr[::-1])
                    conn_addr[chandle] = mac
        elif typ == 0x02:  # ACL
            if len(body) < 4:
                continue
            h = body[0] | (body[1] << 8)
            pb = (h >> 12) & 0x3
            chandle = h & 0x0FFF
            acllen = body[2] | (body[3] << 8)
            payload = body[4:4+acllen]
            if pb == 0x01:  # continuation
                reasm[chandle] = reasm.get(chandle, b"") + payload
            else:  # 0x02 start (or 0x00)
                reasm[chandle] = payload
            buf = reasm.get(chandle, b"")
            if len(buf) < 4:
                continue
            l2len = buf[0] | (buf[1] << 8)
            cid = buf[2] | (buf[3] << 8)
            if len(buf) < 4 + l2len:
                continue  # wait for more
            l2pl = buf[4:4+l2len]
            reasm[chandle] = b""
            if cid == 0x0004 and l2pl:  # ATT
                op = l2pl[0]
                if op in (0x12, 0x52, 0x1b, 0x1d, 0x0b, 0x0a, 0x13):
                    if op in (0x12, 0x52, 0x1b, 0x1d):
                        ah = (l2pl[1] | (l2pl[2] << 8)) if len(l2pl) >= 3 else None
                        val = l2pl[3:]
                    else:
                        ah = None
                        val = l2pl[1:]
                    att.append((ts, direction, chandle, op, ah, val))
    print("conn handle -> mac:", {h: m for h, m in conn_addr.items()})
    # target conn handles
    tgt_handles = {h for h, m in conn_addr.items() if m == TARGET}
    print("target conn handles:", tgt_handles)
    OPN = {0x12:"WriteReq",0x52:"WriteCmd",0x1b:"Notify",0x1d:"Indicate",0x0a:"ReadReq",0x0b:"ReadResp",0x13:"WriteResp"}
    print("\n=== ATT ops (target if known, else all) ===")
    n=0
    for ts, d, ch, op, ah, val in att:
        if tgt_handles and ch not in tgt_handles:
            continue
        hs = ("h=0x%04x" % ah) if ah is not None else "h=?   "
        print(f"{d} ch{ch} {OPN.get(op,hex(op)):9} {hs} len={len(val):3} {val.hex()}")
        n+=1
    print(f"\ntotal att ops shown: {n} / {len(att)}")

if __name__ == "__main__":
    main(sys.argv[1])
