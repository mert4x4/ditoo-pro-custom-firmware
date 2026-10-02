#!/usr/bin/env python3
"""Compile remote mode (remote/remote.c, remote/entry.S) and refresh remote/prebuilt/.

Only needed when you change the C code: `ditoo-fw build` uses the files in
remote/prebuilt/ and does not need a compiler. Needs the nds32le-elf toolchain
(tools/build_toolchain.sh); it is looked up in $NDS32_BIN, then
~/.local/nds32/bin, then PATH. Intermediate files go to remote/build/.

prebuilt/remote.bin is the code placed at 0x1ca000; prebuilt/remote.json lists the
byte patches for the stock code (three hooks, heap end, version number) and the
addresses of the hook targets, which tools/verify.py checks.
"""
import hashlib, json, os, shutil, subprocess, sys
from pathlib import Path

FW = Path(__file__).resolve().parents[1]
R = FW / "remote"
OUT = R / "build"
PREBUILT = R / "prebuilt"
sys.path.insert(0, str(FW / "tools"))
import unlock  # noqa: E402

FLAGS = ["-mcpu=d1088-spu", "-mabi=2", "-mno-fp-as-gp", "-mcmodel=large", "-Os",
         "-ffreestanding", "-fno-builtin", "-fno-tree-loop-distribute-patterns",
         "-fno-stack-protector", "-ffunction-sections", "-fdata-sections", "-Wall", "-Werror"]

# (section, code address, stock bytes it replaces)
HOOKS = [
    ("key_hook", 0x2d458, "49fff590"),      # jal adc_scan in the key scan loop
    ("screen_hook", 0x7550c, "3a6f98bc"),   # first instruction of the present function
    ("command_slot", 0x39d70, "b6b20300"),  # jump-table entry for opcode 0xF0 (was the default handler)
]
# Plain byte patches: heap ends 4 KiB lower (0x2004b000 is ours), version 24021 -> 24025.
PATCHES = ["0x84c08:4602004c:4602004b", "0x476dc:44005dd5:44005dd9"]
VERSION = 24025
SYMBOLS = ["command_entry", "key_filter", "screen_filter"]


def toolchain_dir():
    for d in [os.environ.get("NDS32_BIN"), str(Path.home() / ".local/nds32/bin")]:
        if d and (Path(d) / "nds32le-elf-gcc").exists():
            return Path(d)
    gcc = shutil.which("nds32le-elf-gcc")
    if gcc:
        return Path(gcc).parent
    sys.exit("nds32le-elf-gcc not found: run tools/build_toolchain.sh or set NDS32_BIN")


def run(*args, **kw):
    return subprocess.run([str(a) for a in args], check=True, **kw)


def main():
    bin_dir = toolchain_dir()
    tool = lambda name: bin_dir / f"nds32le-elf-{name}"
    OUT.mkdir(exist_ok=True)
    objs = []
    for src in ["remote.c", "entry.S"]:
        obj = OUT / (src + ".o")
        run(tool("gcc"), *FLAGS, "-c", R / src, "-o", obj)
        objs.append(obj)
    elf = OUT / "remote.elf"
    run(tool("gcc"), "-mcpu=d1088-spu", "-mabi=2", "-nostdlib", "-nostartfiles",
        "-Wl,--gc-sections,--no-relax,-T," + str(R / "remote.ld") + ",-Map," + str(OUT / "remote.map"),
        "-o", elf, *objs, "-lgcc")
    run(tool("size"), "-A", elf)

    patches = list(PATCHES)
    for name, addr, before in HOOKS:
        path = OUT / f"{name}.bin"
        run(tool("objcopy"), "-O", "binary", "-j", "." + name, elf, path)
        after = path.read_bytes()
        assert len(after) == 4, name
        patches.append(f"{addr:#x}:{before}:{after.hex()}")
    text = OUT / "text.bin"
    run(tool("objcopy"), "-O", "binary", "-j", ".text", elf, text)
    with open(OUT / "remote.dis", "w") as f:
        run(tool("objdump"), "-d", elf, stdout=f)

    syms = {}
    for line in run(tool("nm"), elf, capture_output=True, text=True).stdout.splitlines():
        a, _, n = line.split()
        if n in SYMBOLS:
            syms[n] = hex(int(a, 16))

    blob = text.read_bytes()
    PREBUILT.mkdir(exist_ok=True)
    (PREBUILT / "remote.bin").write_bytes(blob)
    meta = {
        "version": VERSION,
        "base": "24021-unlock",
        "base_sha1": unlock.OUT_SHA1,
        "blob": "remote.bin",
        "blob_addr": "0x1ca000",
        "blob_sha1": hashlib.sha1(blob).hexdigest(),
        "patches": patches,
        "symbols": syms,
    }
    (PREBUILT / "remote.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"remote/prebuilt updated: {len(blob)} bytes, sha1 {meta['blob_sha1']}")


if __name__ == "__main__":
    main()
