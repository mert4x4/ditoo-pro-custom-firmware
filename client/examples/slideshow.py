"""Show a folder of images (PNG/GIF, any size) one after another in remote mode.

usage: ./ditoo run client/examples/slideshow.py <folder> [seconds_per_image]
keys: right next, left previous, little button quit.
Transparent pixels become black; images are scaled to 16x16 (nearest neighbour).
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image
from ditoo_api import DitooRemote

FOLDER = sys.argv[1]
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 1.5


def load(path):
    im = Image.open(path).convert("RGBA")
    if im.size != (16, 16):
        im = im.resize((16, 16), Image.NEAREST)
    bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
    raw = Image.alpha_composite(bg, im).convert("RGB").tobytes()
    return [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]


async def main():
    files = sorted(f for f in os.listdir(FOLDER) if f.lower().endswith((".png", ".gif")))
    images = [(f, load(os.path.join(FOLDER, f))) for f in files]
    print(f"{len(images)} images, {SECS} s each", flush=True)
    async with DitooRemote() as dev:
        i = 0
        while dev.active:
            name, px = images[i % len(images)]
            await dev.show(px)
            print(f"{i % len(images) + 1:3d}/{len(images)} {name} ({len(set(px))} colors)", flush=True)
            ev = await dev.next_key(timeout=SECS)
            while ev and not ev.pressed:          # ignore releases
                ev = await dev.next_key(timeout=SECS)
            if ev and ev.name == "little":
                break
            i = i - 1 if ev and ev.name == "left" else i + 1

asyncio.run(main())
