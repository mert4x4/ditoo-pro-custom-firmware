#!/usr/bin/env python3
"""Package code into a Ditoo Pro (family 24) MVA image.

Takes a base image (pinned by SHA-1), optionally appends a binary blob in the
free flash area after the stock code, applies byte patches that must match the
expected "before" bytes, then fixes every length and CRC field and writes a
report listing each changed byte.

Refuses to touch the bootloader, the user-data area (>= 0x1f0000) or anything
outside the code record.

usage:
  mva_pack.py --base 24021-unlock.MVA --base-sha1 <sha1> --out new.MVA
              [--blob code.bin --blob-addr 0x1ca000]
              [--patch 0x476dc:44005dd5:44005dd6 ...]
"""
import argparse, binascii, hashlib, json, os, struct, sys

CODE = 0x60F            # code record payload (after its 4-byte address) in the MVA
REC2_LEN = 0x607        # type-2 record length field (code + 4)
BOOT_LEN = 0xA488       # 24020 bootloader length
FREE_START = 0x1CA000   # first address used for added code
USER_DATA = 0x1F0000    # stock user-data boundary, header field 0xb4


def crc_code(c, end):
    crc = 0
    for s, e in [(0, 0xA4), (0xA8, 0xBC), (0xC0, 0xCC), (0xD4, 0xE4), (0xEC, end)]:
        crc = binascii.crc_hqx(bytes(c[s:e]), crc)
    return crc


def packed_len(n):
    return n | ((sum(n.to_bytes(3, "little")) & 0xFF) << 24)


def check_image(img):
    code = img[CODE:-4]
    assert img[:4] == b"MV\xb1X", "bad magic"
    assert struct.unpack_from("<I", img, REC2_LEN)[0] == len(code) + 4, "record length"
    assert struct.unpack_from("<I", code, 0xD0)[0] == packed_len(len(code)), "code length field"
    assert crc_code(code, BOOT_LEN) == struct.unpack_from("<I", code, 0xBC)[0], "bootloader CRC"
    assert crc_code(code, len(code)) == struct.unpack_from("<I", code, 0xCC)[0], "code CRC"
    assert binascii.crc_hqx(bytes(img[:-4]), 0) == struct.unpack_from("<I", img, len(img) - 4)[0], "package CRC"
    assert struct.unpack_from("<I", code, 0xB4)[0] == USER_DATA, "user-data field"
    assert len(code) <= USER_DATA, "code crosses user data"


def pack(base, patches=(), blob=None, blob_addr=FREE_START):
    """Apply `patches` ("offset:before:after" strings) and `blob` to the base image bytes.
    Returns (image bytes, report dict). Raises ValueError on anything unexpected."""
    check_image(base)
    stock_code = base[CODE:-4]
    code = bytearray(stock_code)
    stock_len = len(code)

    done = []
    for p in patches:
        off, before, after = p.split(":")
        off, before, after = int(off, 0), bytes.fromhex(before), bytes.fromhex(after)
        if len(before) != len(after):
            raise ValueError(f"patch {p}: length differs")
        if off < BOOT_LEN or off + len(before) > stock_len:
            raise ValueError(f"patch {p}: outside the application code")
        if bytes(code[off:off + len(before)]) != before:
            raise ValueError(f"patch {p}: expected {before.hex()}, found {code[off:off+len(before)].hex()}")
        code[off:off + len(before)] = after
        done.append({"code_offset": hex(off), "before": before.hex(), "after": after.hex()})

    blob_info = None
    if blob is not None:
        if blob_addr < max(FREE_START, stock_len) or blob_addr + len(blob) > USER_DATA - 0x2000:
            raise ValueError("blob address outside the free area")
        code.extend(bytes(blob_addr - len(code)))
        code.extend(blob)
        blob_info = {"addr": hex(blob_addr), "bytes": len(blob),
                     "end": hex(blob_addr + len(blob)), "sha1": hashlib.sha1(blob).hexdigest()}
    code.extend(bytes(-len(code) % 4))

    struct.pack_into("<I", code, 0xD0, packed_len(len(code)))
    old_marker = struct.unpack_from("<I", stock_code, 0x100CC)[0]
    marker = crc_code(code[0x10000:], len(code) - 0x10000)
    if marker == old_marker:
        marker ^= 0x5A5A
    struct.pack_into("<I", code, 0x100CC, marker)
    struct.pack_into("<I", code, 0xCC, crc_code(code, len(code)))

    img = bytearray(base[:CODE])
    struct.pack_into("<I", img, REC2_LEN, len(code) + 4)
    img.extend(code)
    img.extend(struct.pack("<I", binascii.crc_hqx(bytes(img), 0)))
    check_image(img)
    assert img[CODE:CODE + BOOT_LEN][:0xCC] == base[CODE:CODE + 0xCC], "bootloader head changed"
    assert img[CODE + 0xD4:CODE + BOOT_LEN] == base[CODE + 0xD4:CODE + BOOT_LEN], "bootloader body changed"

    changed = [i for i in range(min(len(base), len(img)) - 4) if base[i] != img[i]]
    report = {
        "base_sha1": hashlib.sha1(base).hexdigest(),
        "out_sha1": hashlib.sha1(img).hexdigest(), "bytes": len(img),
        "code_bytes": len(code), "patches": done, "blob": blob_info,
        "app_marker": {"old": hex(old_marker), "new": hex(marker)},
        "code_crc16": hex(crc_code(code, len(code))),
        "package_crc16": hex(binascii.crc_hqx(bytes(img[:-4]), 0)),
        "byte_sum": hex(sum(img) & 0xFFFFFFFF),
        "changed_bytes_in_stock_range": len(changed),
        "changed_offsets": [hex(i - CODE) for i in changed][:64],
    }
    return bytes(img), report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--base-sha1", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--blob")
    ap.add_argument("--blob-addr", type=lambda s: int(s, 0), default=FREE_START)
    ap.add_argument("--patch", action="append", default=[],
                    help="code_offset:before_hex:after_hex")
    a = ap.parse_args()

    base = open(a.base, "rb").read()
    if hashlib.sha1(base).hexdigest() != a.base_sha1:
        sys.exit("base image SHA-1 mismatch")
    blob = open(a.blob, "rb").read() if a.blob else None
    try:
        img, report = pack(base, a.patch, blob, a.blob_addr)
    except ValueError as e:
        sys.exit(str(e))
    open(a.out, "wb").write(img)
    report = {"base": os.path.basename(a.base), "out": os.path.basename(a.out), **report}
    open(a.out.rsplit(".", 1)[0] + ".json", "w").write(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "changed_offsets"}, indent=2))


if __name__ == "__main__":
    main()
