# Client

Python library for the Ditoo Pro over Bluetooth LE (Linux, BlueZ). The only runtime dependency is
bleak; `slideshow.py` also needs Pillow. Both are installed by `./setup.sh`.

- `ditoo_api.py`: the core API. `Ditoo` covers the stock commands (channels, custom pages, brightness,
  time, weather, tools, games, audio, alarms, native scrolling text). `DitooRemote` adds the remote mode
  of the custom firmware (drawing, keys, battery). `Canvas` draws 16x16 frames. `BleLink` is the framing
  layer used by everything else, including the flasher. The Home Assistant integration uses a copy of this file.
- `examples/`: `example.py` (draw and react to keys), `clock_weather.py` (time and weather on the stock
  clock face), `slideshow.py` (a folder of images).
- `ditoo_test.py`: test tool for the remote mode (`auto`, `bench`, `keys`, `listen`, `escape`, `push`, `stats`,
  `linkdrop`, `queue`, `show`, `play`, `raw`).
- `tests/test_client.py`: offline tests with a fake BLE link.

Run scripts from the repository root with the launcher, which uses the virtual environment:

    ./ditoo run client/examples/example.py
    ./ditoo run client/ditoo_test.py keys

The device is found by its Bluetooth name (`DitooPro-Light`). With more than one, pick it with
`DITOO_ADDRESS=AA:BB:CC:DD:EE:FF`.

Minimal program:

```python
import asyncio
from ditoo_api import DitooRemote, Canvas

async def main():
    async with DitooRemote() as dev:          # connect + drawing mode on
        c = Canvas()
        c.text(1, 5, "HI", (255, 200, 0))
        await dev.show(c)
        async for ev in dev.keys():           # KeyEvent: ev.name, ev.kind
            print(ev)
            if ev.name == "little":
                break

asyncio.run(main())
```

The device only acts on commands whose sequence number has a non-zero high byte, so `BleLink` starts at
0x0101. No other session setup is needed. The custom firmware pushes key events as they happen;
`DitooRemote` sends a command every 20 s while a mode is on to keep the device's 60 s watchdog satisfied.
