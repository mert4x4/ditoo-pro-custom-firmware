#!/usr/bin/env python3
"""Pre-flash checks for out/24025-remote.MVA against stock 24020 (out/24020.MVA).

Run by `ditoo-fw build` and `ditoo-fw install`; can also be run on its own.
Checks: container CRCs, bootloader unchanged, every difference from stock is an
expected one, hooks point at the hook code, version and gates. If remote/build/
exists (tools/build_remote.py was run) and the toolchain is found, it also checks
the disassembly: every call from the added code goes to it or to a known stock
function, and it only touches its own RAM page and known stock globals.
"""
import hashlib, json, re, struct, subprocess, sys
from pathlib import Path

FW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FW / "tools"))
import mva_pack  # noqa: E402

CODE = 0x60F
STOCK_END = 0x1C8250       # end of the stock 24020 code
BLOB_ADDR = 0x1CA000
KNOWN_STOCK = {0x3B2B6, 0x2BF78, 0x7550C, 0x75510, 0x39014, 0x77BEC, 0x84A88, 0x84A54}


def verify(img_path=FW / "out/24025-remote.MVA", stock_path=FW / "out/24020.MVA", quiet=False) -> bool:
    ok_all = True

    def check(name, ok, detail=""):
        nonlocal ok_all
        ok_all &= bool(ok)
        if not quiet or not ok:
            print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))

    supported = json.loads((FW / "supported.json").read_text())
    meta = json.loads((FW / "remote/prebuilt/remote.json").read_text())
    syms = {k: int(v, 16) for k, v in meta["symbols"].items()}
    img = Path(img_path).read_bytes()
    stock_img = Path(stock_path).read_bytes()
    new, stock = img[CODE:-4], stock_img[CODE:-4]
    blob = (FW / "remote/prebuilt" / meta["blob"]).read_bytes()
    blob_end = BLOB_ADDR + len(blob)

    # 1. container and inputs
    try:
        mva_pack.check_image(img)
        check("container: magic, lengths, bootloader CRC, code CRC, package CRC, user-data field", True)
    except AssertionError as e:
        check("container", False, str(e))
    check("stock image is 24020", hashlib.sha1(stock_img).hexdigest() == supported["stock"]["sha1"])

    # 2. bootloader untouched
    boot_diff = [i for i in range(mva_pack.BOOT_LEN) if new[i] != stock[i]]
    check("bootloader identical to stock (except the code CRC/length fields 0xcc-0xd3)",
          all(0xCC <= i < 0xD4 for i in boot_diff), ", ".join(hex(i) for i in boot_diff))

    # 3. every difference from stock is an expected one
    expected = {  # address: length
        0xBC: 4, 0xCC: 4, 0xD0: 4, 0x100CC: 2, 0x476DC: 4, 0x4B2F4: 2,
        0x84C08: 4, 0x2D458: 4, 0x7550C: 4, 0x39D70: 4,
    }
    n = min(len(new), len(stock))
    diffs, i = [], 0
    while i < n:
        if new[i] != stock[i]:
            j = i
            while j < n and new[j] != stock[j]:
                j += 1
            diffs.append((i, j)); i = j
        else:
            i += 1
    bad = [(hex(s), hex(e)) for s, e in diffs
           if s < STOCK_END and not any(a <= s and e <= a + ln for a, ln in expected.items())]
    check("all changes vs stock are expected ones", not bad, f"unexpected {bad}" if bad else f"{len(diffs)} ranges")
    check("added code stays in the free area", len(new) <= 0x1EE000 and blob_end <= 0x1EE000,
          f"0x1ca000-{blob_end:#x}")
    check("gap between stock code end and added code is padding only",
          set(new[len(stock):BLOB_ADDR]) <= {0, 0xFF})
    check("added code in image equals remote/prebuilt", new[BLOB_ADDR:blob_end] == blob)

    # 4. version and gates
    check("version getter returns 24025", new[0x476DC:0x476E0].hex() == "44005dd9", new[0x476DC:0x476E0].hex())
    check("update gate unlocked (way back to stock 24020 stays open)", new[0x4B2F4:0x4B2F6].hex() == "d516")
    check("heap end lowered (state RAM at 0x2004b000 reserved)", new[0x84C08:0x84C0C].hex() == "4602004b")

    # 5. hooks point at the right code
    def jal_target(addr):
        be = int.from_bytes(new[addr:addr + 4], "big")
        off = be & 0xFFFFFF
        if off & 0x800000:
            off -= 1 << 24
        return addr + off * 2, be >> 24
    t, op = jal_target(0x2D458)
    check("key hook: jal key_filter", op == 0x49 and t == syms["key_filter"], hex(t))
    t, op = jal_target(0x7550C)
    check("screen hook: j screen_filter", op == 0x48 and t == syms["screen_filter"], hex(t))
    slot = struct.unpack_from("<I", new, 0x39D70)[0]
    check("0xF0 jump-table slot -> command_entry", slot == syms["command_entry"], hex(slot))
    for name, a in syms.items():
        check(f"{name} inside the added code", BLOB_ADDR <= a < blob_end, hex(a))
    for a, want in {0x39014: "3bfffcbc", 0x84A88: "3c1de38d", 0x84A54: "c804"}.items():
        check(f"stock function at {a:#x} unchanged", new[a:a + len(want) // 2].hex() == want)

    # 6. optional: disassembly of a local build
    elf = FW / "remote/build/remote.elf"
    objdump = None
    if elf.exists():
        try:
            from build_remote import toolchain_dir
            objdump = toolchain_dir() / "nds32le-elf-objdump"
        except SystemExit:
            pass
    if objdump and (FW / "remote/build/text.bin").read_bytes() == blob:
        dis = subprocess.run([objdump, "-d", elf], capture_output=True, text=True).stdout
        lines = dis.splitlines()
        targets, ram = set(), set()
        for k, line in enumerate(lines):
            m = re.search(r"\t(jal|j)\t([0-9a-f]+)", line)
            if m:
                targets.add(int(m.group(2), 16))
            m = re.search(r"sethi\t\$r15, #0x([0-9a-f]+)", line)
            if m and k + 1 < len(lines):
                m2 = re.search(r"ori\t\$r15, \$r15, #0x([0-9a-f]+)", lines[k + 1])
                if m2:
                    targets.add(int(m.group(1), 16) << 12 | int(m2.group(1), 16))
            m = re.search(r"sethi\t\$r(\d+), #0x(2[0-9a-f]{4})\b", line)
            if m:
                ram.add(int(m.group(2), 16) << 12)
        outside = sorted(t for t in targets if not (BLOB_ADDR <= t < blob_end) and t not in KNOWN_STOCK)
        check("all calls go to the added code or verified stock functions", not outside,
              ", ".join(hex(t) for t in outside) or f"{len(targets)} targets")
        check("RAM pages used: 0x2004b000 and stock globals 0x20004000-0x20006fff only",
              ram <= {0x2004B000, 0x20004000, 0x20005000, 0x20006000})
        check("no gp-relative access", ".gp" not in dis)

    if not quiet:
        print("\nALL CHECKS PASSED" if ok_all else "\nSOME CHECKS FAILED")
    return ok_all


if __name__ == "__main__":
    sys.exit(0 if verify(*sys.argv[1:3]) else 1)
