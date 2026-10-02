# Ditoo Pro custom firmware + Home Assistant

Unofficial firmware patch and Home Assistant integration for the **Divoom Ditoo Pro** (the pixel-art
Bluetooth speaker with a keyboard, Bluetooth name `DitooPro-Light`). Linux only.

The custom firmware adds a **remote mode** to the stock firmware:

- **Drawing mode**: your computer or Home Assistant draws the 16x16 screen.
- **Keys**: all 8 front keys (M, +, -, left, right, arm, brightness, the little button) reach Home Assistant as
  events (down, up, long, repeat, long-up), in drawing mode or in every stock menu. While they are captured the
  device's own menus ignore them.
- **Battery**: charge level, charging and USB power.

Everything else on the device stays stock: clock, music, games, the Divoom app.

> **Not affiliated with Divoom.** This project is not made, endorsed or supported by Divoom. "Divoom" and
> "Ditoo" are their trademarks. **No Divoom firmware is in this repository.** The installer downloads the
> stock firmware from Divoom's own update server and patches it on your computer.

> **Use at your own risk.** Flashing modified firmware may void your warranty, and a failed update can in the
> worst case leave the device unusable. The flasher checks the image byte for byte before it writes anything,
> and the way back to stock is built in (`./ditoo restore`), but there is no guarantee.

## What you need

- A **Divoom Ditoo Pro** (not the Ditoo Plus, Ditoo Mic or Ditoo-5Pro: their firmware is different). The
  flasher refuses any device whose firmware family is not 24.
- A **Linux** computer with Bluetooth (BlueZ) and Python 3.10 or newer. A Raspberry Pi works.
- A USB cable to keep the Ditoo powered while it updates.

## Install the firmware

```sh
git clone https://github.com/<you>/ditoo-pro-custom-firmware.git
cd ditoo-pro-custom-firmware
./setup.sh             # one-time: makes .venv and installs bleak
./ditoo scan           # finds the Ditoo Pro
./ditoo install        # downloads stock 24020, builds 24025-remote, checks it, flashes it
```

Before `./ditoo install`:

- plug the Ditoo into USB power and leave it plugged in,
- close the Divoom app or turn off Bluetooth on your phone (the Ditoo takes one connection at a time),
- if Home Assistant is already connected to it, turn its **Connection** switch off,
- keep the computer close to the Ditoo and do not let it sleep.

The update takes about **15 minutes**. The device checks the size and checksum of the whole image before it
reports success and reboots. If the transfer stops halfway, keep the device on power and run `./ditoo install`
again. Afterwards `./ditoo version` shows `24025`.

Only one image is ever flashed: `24025-remote`. It is stock 24020 plus the remote mode code, with the version
gate opened so that stock firmware can be installed back.

### Other commands

| Command | What it does |
|---|---|
| `./ditoo version` | Reads the installed firmware version. |
| `./ditoo build` | Downloads stock 24020 and builds `firmware/out/24025-remote.MVA` without flashing. |
| `./ditoo restore` | Flashes stock firmware 24020 back. |
| `./ditoo run SCRIPT` | Runs a Python script with the client library (examples in `client/examples/`). |

Options: `--address AA:BB:CC:DD:EE:FF` (or `DITOO_ADDRESS=...`) picks the device instead of scanning,
`--stock FILE` uses a `24020.MVA` you already have instead of downloading it, `--log FILE` keeps the flash log.

If Divoom's server ever stops serving the file, any copy of stock 24020 with SHA-1
`ed4dba8470af6599523f204851a0c73162fd58af` works with `--stock`. The tool rejects every other file.

## Home Assistant

See [`homeassistant/README.md`](homeassistant/README.md). In short: copy
`homeassistant/custom_components/ditoo_pro` to `/config/custom_components/`, restart Home Assistant, and accept
the discovered `DitooPro-Light`. You get entities for channel, brightness, volume, drawing mode, key listening,
battery, a message text, one event entity per key, and services such as `show_text`, `show_image`,
`set_weather` and `sync_time`.

Example: toggle a lamp with the left key.

```yaml
triggers:
  - trigger: event
    event_type: ditoo_pro_key
    event_data: { key: left, event: down }
actions:
  - action: light.toggle
    target: { entity_id: light.desk }
```

## If something goes wrong

- **Keys do nothing on the device**: the keys are being sent to Home Assistant. Hold any key for 4 seconds:
  drawing mode and key listening switch off. They also switch off as soon as the Bluetooth connection drops.
- **`./ditoo scan` finds nothing**: the Ditoo is off, out of range, or connected to the phone or Home Assistant.
- **The update stopped**: keep the device on power and run `./ditoo install` again.
- **Back to stock**: `./ditoo restore`.

## How it works

1. `firmware/images.py` downloads stock 24020 from Divoom's update server and checks its SHA-1.
2. `firmware/tools/unlock.py` opens the version gate (two small patches, CRCs recomputed).
3. The remote mode code (`firmware/remote/remote.c`, compiled into `firmware/remote/prebuilt/`) is placed in
   free flash at `0x1ca000`, and three 4-byte hooks are written into stock code: the key scan, the screen output
   and the BLE command table slot for opcode `0xF0`.
4. `firmware/tools/verify.py` checks that the bootloader is untouched, every difference from stock is an
   expected one, and every CRC is right. The result must equal the released build (SHA-1
   `78630429774642caf1c51ac9a1b756fba2041f40`).
5. `firmware/flash.py` sends it over Bluetooth LE with the same update protocol the Divoom app uses.

Details: [`firmware/README.md`](firmware/README.md). The BLE commands of the stock firmware are listed in
[`docs/opcodes.md`](docs/opcodes.md).

## Layout

| Folder | Contents |
|---|---|
| `firmware/` | Installer (`cli.py`), flasher, image builder, remote mode source, tests. |
| `client/` | Python library for the device (`ditoo_api.py`), test tool and examples. |
| `homeassistant/` | The `ditoo_pro` Home Assistant integration. |
| `docs/` | BLE opcode table. |

## License

GPL-3.0, see [LICENSE](LICENSE). This covers the code in this repository only, not Divoom's firmware.
