# Firmware

The Divoom Ditoo Pro is hardware family 24 (MVSilicon chip, Andes NDS32 core, FreeRTOS). The custom
firmware **24025-remote** is stock **24020** with a remote mode added. It is the only image this project
installs; `./ditoo install` builds it on your computer and flashes it.

## Files

| Path | What it is |
|---|---|
| `cli.py` | The `./ditoo` command. |
| `supported.json` | Stock 24020 (SHA-1, Divoom download URLs, update API request) and the SHA-1 of the result. |
| `images.py` | Downloads stock 24020 and builds `out/24025-remote.MVA` from it. |
| `flash.py` | BLE flasher. |
| `remote/remote.c`, `entry.S`, `remote.ld` | Source of the remote mode. |
| `remote/prebuilt/` | The compiled remote mode (`remote.bin`) and the byte patches (`remote.json`). |
| `remote/tests/host_test.c` | Runs the logic of `remote.c` on a PC with the stock functions faked. |
| `tools/unlock.py` | Opens the version gate of stock 24020 (first build step). |
| `tools/mva_pack.py` | Puts code into an MVA image and fixes every length and CRC field. |
| `tools/verify.py` | Pre-flash checks of the built image. |
| `tools/build_remote.py` | Compiles the remote mode and refreshes `remote/prebuilt/`. |
| `tools/build_toolchain.sh` | Builds the nds32le-elf cross compiler. |

`out/` (created by the tool, ignored by git) holds the downloaded stock image and the built image.
No Divoom firmware is part of this repository.

## Build steps

1. **Stock image.** `images.py` downloads 24020 from `f.divoom-gz.com`, or asks Divoom's update API
   (`POST https://appin.divoom-gz.com/GetUpdateFileV3`, `{"Hardware":24,"IsTest":true,...}`) for its current
   location, and accepts it only with SHA-1 `ed4dba8470af6599523f204851a0c73162fd58af`. `--stock FILE` takes a
   local copy instead.
2. **Unlock** (`tools/unlock.py`, code offsets; MVA offset = code + 0x60f):
   - `0x476dc` version getter `44005dd4` -> `44005dd5`
   - `0x4b2f4` update gate `c816` -> `d516`: the "announced <= installed" reject is skipped, so stock firmware can
     be installed back later. The family check (version / 1000 == 24) is unchanged.
   - application change marker at `0x100cc`, code CRC at `0xcc` and the package CRC trailer recomputed.
     Bootloader untouched.
3. **Remote mode** (`mva_pack.pack` with `remote/prebuilt/`):
   - the code goes to free flash at `0x1ca000` (2086 bytes; must end before `0x1ee000`),
   - three 4-byte hooks: key scan `0x2d458` (`jal key_filter`), screen output `0x7550c` (`j screen_filter`),
     command jump-table slot for opcode 0xF0 at `0x39d70`,
   - heap end `0x84c08` lowered by 4 KiB: the remote mode state lives at `0x2004b000`,
   - version getter `0x476dc` -> 24025.
4. **Checks.** The result must have SHA-1 `78630429774642caf1c51ac9a1b756fba2041f40` (the released build), and
   `tools/verify.py` checks it against stock: container CRCs, bootloader unchanged, every difference from stock
   is one of the patches above, hooks point at the hook code.

Stock 24020 accepts 24025 directly (higher version, same family), so only this one image is flashed.

## Remote mode

New BLE opcode `0xF0`; the protocol is described at the top of `remote_command` in `remote/remote.c`.

| Sub | What it does |
|---|---|
| `00` | Poll: returns mode and queued key events |
| `01` / `02` | Drawing mode on / off |
| `03`, `04`, `05` | Frame: rows of RGB, a 16-colour palette frame, fill with one colour |
| `06` | Battery: level 0-7, USB power, charging |
| `07` | Key listening on / off |
| `09` | Push and stack statistics |
| `80` | (device to host) pushed key event |

Captured keys are pushed as they happen; if a push is not possible they are queued (32 entries) and come
with the next reply. Both modes switch themselves off when the BLE link drops, when no 0xF0 command arrives
for 60 s, or when any key is held for 4 s, so the device never stays without working keys.

## Flashing

`flash.py` reads the installed version (0x97) and stops before writing anything unless the device is family
24 and answers "ready" to the metadata (0x98: version, size, byte sum). It then sends 256-byte chunks (0x99),
resends chunks the device asks for again, and stops if the device reports failure or sends a stop request
(0x30 or 0x48). After the device reports success it reboots; the tool reconnects and reads the version.

`./ditoo restore` flashes stock 24020 the same way; the unlocked gate in 24025-remote allows the downgrade.

## Changing the remote mode

```sh
firmware/tools/build_toolchain.sh             # once, into ~/.local/nds32
python3 firmware/tools/build_remote.py        # compiles and refreshes remote/prebuilt/
./ditoo build                                 # builds out/24025-remote.MVA and runs the checks
```

With changed code the SHA-1 differs from the released build; `./ditoo build` says so, and `tools/verify.py`
(run with the toolchain present) also checks the disassembly: every call from the added code goes to it or to
a known stock function, and it only uses its own RAM page and known stock globals. If you add more than a few
bytes, raise the version number in `build_remote.py` and `supported.json`.

Compile flags:

    nds32le-elf-gcc -mcpu=d1088-spu -mabi=2 -mno-fp-as-gp -mcmodel=large -Os -ffreestanding -fno-builtin \
      -fno-tree-loop-distribute-patterns -fno-stack-protector -ffunction-sections -fdata-sections

The linker script places `.text` at `0x1ca000`, defines the stock addresses as symbols and sets
`_SDA_BASE_ = 0x2000d2f8` (the firmware's gp).

## Tests

```sh
cd firmware/remote && cc -std=c11 -Wall -Werror -o /tmp/host_test tests/host_test.c && /tmp/host_test
./ditoo run client/tests/test_client.py
```
