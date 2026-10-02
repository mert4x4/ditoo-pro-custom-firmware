#!/usr/bin/env python3
"""Stock Ditoo Pro 24020 -> 24021-unlock.

- the version getter returns 24021 instead of 24020
- the "announced <= installed -> reject" branch in the 0x98 update gate becomes an
  unconditional jump, so same-version reinstalls and downgrades are accepted. The
  hardware-family gate (version / 1000 == 24) stays.
- application change marker, code CRC and package CRC are recomputed.

Every remote build starts from this image, and the unlocked gate is what makes the
way back to stock 24020 possible.

usage: unlock.py <24020.MVA> <out.MVA>
"""
import binascii, hashlib, struct, sys

STOCK_SHA1 = "ed4dba8470af6599523f204851a0c73162fd58af"
OUT_SHA1 = "251c52e514d47891da1a4494970fbd5f72d5c94a"
CODE = 0x60F
BOOT_LEN = 0xA488
BOOT_CRC = 0x35B9

PATCHES = [
    # (code offset, before, after, note)
    (0x476DC, "44005dd4", "44005dd5", "version getter: movi a0, 24020 -> 24021"),
    (0x4B2F4, "c816", "d516", "update gate: bnez38 a0,0x4b320 -> j8 0x4b320 (skip <= check)"),
]


def crc_code(c, end):
    crc = 0
    for s, e in [(0, 0xA4), (0xA8, 0xBC), (0xC0, 0xCC), (0xD4, 0xE4), (0xEC, end)]:
        crc = binascii.crc_hqx(bytes(c[s:e]), crc)
    return crc


def unlock(stock: bytes) -> bytes:
    if hashlib.sha1(stock).hexdigest() != STOCK_SHA1:
        raise ValueError("input is not the stock 24020 image")
    p = bytearray(stock)
    for off, before, after, _ in PATCHES:
        b, a = bytes.fromhex(before), bytes.fromhex(after)
        assert p[CODE + off:CODE + off + len(b)] == b, hex(off)
        p[CODE + off:CODE + off + len(b)] = a

    old_marker = struct.unpack_from("<I", stock, CODE + 0x100CC)[0]
    app = p[CODE + 0x10000:-4]
    marker = crc_code(app, len(app))
    assert marker not in (old_marker, 0, 0xFFFF)
    struct.pack_into("<I", p, CODE + 0x100CC, marker)

    code = p[CODE:-4]
    struct.pack_into("<I", p, CODE + 0xCC, crc_code(code, len(code)))
    struct.pack_into("<I", p, len(p) - 4, binascii.crc_hqx(bytes(p[:-4]), 0))

    # checks: CRCs valid, bootloader untouched, only the expected bytes changed
    code = p[CODE:-4]
    assert len(p) == len(stock)
    assert crc_code(code, BOOT_LEN) == struct.unpack_from("<I", code, 0xBC)[0] == BOOT_CRC
    assert crc_code(code, len(code)) == struct.unpack_from("<I", code, 0xCC)[0]
    assert binascii.crc_hqx(bytes(p[:-4]), 0) == struct.unpack_from("<I", p, len(p) - 4)[0]
    allowed = set()
    for off, size in [(CODE + 0x476DC, 4), (CODE + 0x4B2F4, 2), (CODE + 0x100CC, 4),
                      (CODE + 0xCC, 4), (len(p) - 4, 4)]:
        allowed.update(range(off, off + size))
    assert all(i in allowed for i, (a, b) in enumerate(zip(stock, p)) if a != b)
    out = bytes(p)
    assert hashlib.sha1(out).hexdigest() == OUT_SHA1, "unlock result differs from the known 24021-unlock"
    return out


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__.strip().splitlines()[-1])
    with open(sys.argv[1], "rb") as f:
        data = unlock(f.read())
    with open(sys.argv[2], "wb") as f:
        f.write(data)
    print(sys.argv[2], hashlib.sha1(data).hexdigest())
