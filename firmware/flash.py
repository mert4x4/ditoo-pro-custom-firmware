"""BLE firmware flasher for the Ditoo Pro (opcode 0x98 metadata, 0x99 256-byte chunks).

Used by `./ditoo install` and `./ditoo restore`. It reads the installed version
first and writes nothing unless the device is a Ditoo Pro (family 24) and answers
"ready". The device checks the size and byte sum of the image before it
reports success and reboots.
"""
import asyncio, hashlib, struct, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
from ditoo_api import BleLink  # noqa: E402

FAMILY = 24


class Log:
    def __init__(self, path=None):
        self.f = open(path, "a", buffering=1) if path else None
        self.t0 = time.monotonic()

    def __call__(self, *a):
        line = f"[{time.strftime('%H:%M:%S')} +{time.monotonic() - self.t0:7.1f}s] " + " ".join(str(x) for x in a)
        print(line, flush=True)
        if self.f:
            self.f.write(line + "\n")


class Rx:
    """Split the notify stream into 01 <lenLE16> ... 02 frames; queue (cmd, payload)."""
    def __init__(self, log):
        self.buf = bytearray(); self.q = asyncio.Queue(); self.acks = 0; self.log = log
        self.loop = asyncio.get_running_loop()

    def feed(self, data):
        self.buf += data
        while True:
            i = self.buf.find(b"\x01")
            if i < 0:
                self.buf.clear(); return
            del self.buf[:i]
            if len(self.buf) < 3:
                return
            n = self.buf[1] | self.buf[2] << 8
            if len(self.buf) < n + 4:
                return
            fr = bytes(self.buf[:n + 4]); del self.buf[:n + 4]
            if fr[-1] != 0x02 or len(fr) < 8:
                self.log("RX ?", fr.hex(" ")); continue
            cmd, payload = fr[4], fr[6:-3]
            if cmd == 0x33:
                self.acks += 1
            elif cmd not in (0x99,):
                self.log("RX", f"cmd=0x{cmd:02x}", payload.hex(" "))
            self.loop.call_soon_threadsafe(self.q.put_nowait, (cmd, payload))


async def wait_for(rx, cmds, timeout):
    end = time.monotonic() + timeout
    while (left := end - time.monotonic()) > 0:
        try:
            cmd, p = await asyncio.wait_for(rx.q.get(), left)
        except asyncio.TimeoutError:
            break
        if cmd in cmds:
            return cmd, p
    return None, None


async def _open(address, log):
    rx = Rx(log)
    c = BleLink(address, rx.feed)
    await c.connect()
    c.chunk = 138   # GATT write size every tested flash used
    await asyncio.sleep(1)
    while not rx.q.empty():
        rx.q.get_nowait()
    return c, rx


async def _version(c, rx):
    await c.send(b"\x97")
    _, p = await wait_for(rx, {0x97}, 5)
    return struct.unpack_from("<H", p, 1)[0] if p and len(p) >= 3 else None


async def read_version(address=None, log=print):
    c, rx = await _open(address, log)
    try:
        return await _version(c, rx)
    finally:
        await c.disconnect()


async def flash(image: bytes, version: int, address=None, allow_downgrade=False, log=print) -> bool:
    """Send `image` as firmware `version`. True when the device reports success."""
    size, csum = len(image), sum(image) & 0xFFFFFFFF
    nchunks = (size + 255) // 256
    padded = image + b"\x00" * (nchunks * 256 - size)
    meta = bytes([0]) + struct.pack("<H", version) + struct.pack("<II", size, csum)
    log(f"image v{version}: {size} bytes, {nchunks} chunks, sha1 {hashlib.sha1(image).hexdigest()}")

    log("connecting...")
    c, rx = await _open(address, log)
    try:
        cur = await _version(c, rx)
        log("installed version:", cur)
        if cur is None:
            log("ABORT: the device did not report its version, nothing written"); return False
        if cur // 1000 != FAMILY or version // 1000 != FAMILY:
            log(f"ABORT: not a Ditoo Pro (family {cur // 1000}), nothing written"); return False
        if cur == version:
            log(f"the device already runs {version}, nothing to do"); return True
        if cur > version and not allow_downgrade:
            log("ABORT: this would be a downgrade, nothing written"); return False

        await c.send(b"\x98" + meta)
        _, p = await wait_for(rx, {0x98}, 10)
        if p is None or len(p) < 2 or p[1] != 0:
            log("ABORT: device not ready (0x98 reply", p.hex(" ") if p else None, "), nothing written")
            return False
        idx = struct.unpack_from("<H", p, 2)[0] if len(p) >= 4 else 0
        log(f"device ready, start at chunk {idx}. Keep the device on USB power and close to this computer.")

        t_start = time.monotonic(); sent = 0; result = None
        while result is None:
            if idx < nchunks:
                await c.send(b"\x99" + struct.pack("<H", idx) + padded[idx * 256:(idx + 1) * 256])
                sent += 1; idx += 1
                if idx % 100 == 0 or idx == nchunks:
                    el = time.monotonic() - t_start
                    log(f"chunk {idx}/{nchunks} ({idx * 100 / nchunks:5.1f}%)  {sent * 256 / el / 1024:5.2f} KB/s  "
                        f"eta {(nchunks - idx) * el / max(sent, 1) / 60:4.1f} min")
            timeout = 0 if idx < nchunks else 90
            while True:
                try:
                    cmd, p = (rx.q.get_nowait() if timeout == 0 else await asyncio.wait_for(rx.q.get(), timeout))
                except (asyncio.QueueEmpty, asyncio.TimeoutError):
                    if timeout:
                        log("ABORT: no answer from the device after the last chunk"); result = False
                    break
                if cmd == 0x99 and len(p) >= 2:
                    if p[1] == 1:
                        log("device: update complete, it reboots now"); result = True; break
                    if p[1] == 2:
                        log("device: update FAILED. Keep it on power and run the install again."); result = False; break
                    if p[1] == 0 and len(p) >= 4:
                        idx = struct.unpack_from("<H", p, 2)[0]
                        log(f"device asks for chunk {idx} again"); break
                elif cmd in (0x30, 0x48):
                    log(f"device: stop request (0x{cmd:02x}). Keep it on power and run the install again."); result = False; break
    finally:
        try:
            await c.disconnect()
        except Exception as e:
            log("disconnect:", e)
    if not result:
        return False

    log("waiting 20 s for the reboot...")
    await asyncio.sleep(20)
    for attempt in range(6):
        try:
            v = await read_version(address, log)
            log("version after the update:", v)
            return v == version
        except Exception as e:
            log(f"reconnect attempt {attempt + 1} failed: {e}")
            await asyncio.sleep(10)
    log("could not reconnect; check the version later with ./ditoo version")
    return True
