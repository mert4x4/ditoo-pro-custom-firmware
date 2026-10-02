"""Smallest use of ditoo_api: take the screen, draw, react to keys, give it back.

run: ./ditoo run client/examples/example.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ditoo_api import DitooRemote, Canvas


async def main():
    async with DitooRemote() as dev:
        x = 7
        while True:
            c = Canvas()
            c.text(1, 1, "HI", (255, 200, 0))
            c.rect(x, 12, 2, 2, (0, 200, 255))
            await dev.show(c)
            ev = await dev.next_key(timeout=10)
            if ev is None or ev.name == "little" or not dev.active:
                break
            print(ev, flush=True)
            if ev.pressed and ev.name == "left": x = max(0, x - 1)
            if ev.pressed and ev.name == "right": x = min(14, x + 1)

asyncio.run(main())
