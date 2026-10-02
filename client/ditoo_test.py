"""Test tool for the remote mode of the custom firmware (24025-remote, opcode 0xF0).

  ./ditoo run client/ditoo_test.py <command> [args]
The device is found by name; set DITOO_ADDRESS=AA:BB:.. to pick one.

Commands:
  auto                 automatic protocol checks (PASS/FAIL), watch the screen
  bench                round-trip latency and frame rate
  keys [s]             live key log, number of the pressed key on screen (default 60 s)
  listen [s]           key listening: keys in every menu, stock screen stays (default 60 s)
  escape               hold any key > 4 s, checks that remote mode switches off
  push [s]             key push: keys arrive without polling (default 60 s)
  stats                push and stack counters (sub-command 09)
  linkdrop             turns both modes on, drops the BLE link, reconnects, checks both are off
  queue                press keys fast for 4.5 s without polling, checks the 32-entry queue
  show <what> [s]      hold an image for s seconds (default 20):
                         pattern | gradient | fill R G B | number N
  play [s]             live paint with the device keys (default 300 s):
                         left/right move x, +/- move y (held keys repeat),
                         arm paints/erases, brightness next color, M clear,
                         little button quits
  raw <hex> [<hex>..]  send raw payloads, print replies (e.g. raw f000 f001 f005ff0000)

The device pushes key events (sub 0x80) instead of waiting for a poll. Every
reply returned by Remote.cmd() lists the pushed keys that arrived before it,
followed by its own queued keys.
"""
import asyncio, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ditoo_api import DEFAULT_ADDRESS, BleLink, FrameReader

ADDR = DEFAULT_ADDRESS
KEYS = {0: "M", 1: "+", 2: "left", 3: "right", 4: "arm", 7: "brightness", 9: "-", 10: "little"}
EVENTS = {1: "down", 2: "up", 3: "long", 4: "repeat", 5: "long-up"}
STATUS = {0: "ok", 1: "bad length", 2: "not in remote mode", 3: "unknown sub-command"}
SUB_PUSH = 0x80
REMOTE_FIRMWARE = 24025
FONT = {
    "0": ["111", "101", "101", "101", "111"], "1": ["010", "110", "010", "010", "111"],
    "2": ["111", "001", "111", "100", "111"], "3": ["111", "001", "111", "001", "111"],
    "4": ["101", "101", "111", "001", "001"], "5": ["111", "100", "111", "001", "111"],
    "6": ["111", "100", "111", "101", "111"], "7": ["111", "001", "010", "010", "010"],
    "8": ["111", "101", "111", "101", "111"], "9": ["111", "101", "111", "001", "111"],
}
T0 = time.monotonic()


def log(*a):
    print(f"{time.monotonic() - T0:7.2f}s", *a, flush=True)


# ---- frames ---------------------------------------------------------------

def palette_frame(px, pal):
    """px: 256 palette indexes (row-major), pal: list of (r, g, b), at most 16."""
    return (bytes([0xF0, 0x04, len(pal)]) + bytes(c for rgb in pal for c in rgb)
            + bytes((px[i] << 4) | px[i + 1] for i in range(0, 256, 2)))


def row_packets(rgb):
    """rgb: 256 (r, g, b) tuples. Packets of up to 5 rows; the last one presents."""
    out = []
    for row in range(0, 16, 5):
        count = min(5, 16 - row)
        data = bytes(c for p in rgb[row * 16:(row + count) * 16] for c in p)
        flags = 1 if row + count == 16 else 0
        out.append(bytes([0xF0, 0x03, flags, row, count]) + data)
    return out


def number_frame(n):
    px = [0] * 256
    text = str(n)
    x0 = (16 - (len(text) * 8 - 2)) // 2
    for i, ch in enumerate(text):
        for row, bits in enumerate(FONT[ch]):
            for col, bit in enumerate(bits):
                if bit == "1":
                    for dy in range(2):
                        for dx in range(2):
                            px[(3 + row * 2 + dy) * 16 + x0 + i * 8 + col * 2 + dx] = 1
    return palette_frame(px, [(0, 0, 0), (255, 255, 255)])


def corner_pattern():
    px = [0] * 256
    px[0], px[15], px[240], px[255] = 1, 2, 3, 4
    for i in range(16):
        px[7 * 16 + i] = px[i * 16 + 7] = 5
    return palette_frame(px, [(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255), (40, 40, 40)])


def gradient():
    return [(x * 17, y * 17, 96) for y in range(16) for x in range(16)]


# ---- device ---------------------------------------------------------------

class Remote:
    def __init__(self):
        self.c = None
        self.replies = asyncio.Queue()
        self.other = asyncio.Queue()     # (opcode, data) of non-0xF0 frames
        self.keys = []          # (key, event) in arrival order
        self.pending = []       # keys that arrived (pushed or in replies) since the last cmd()
        self.pushes = []        # (time, key, event, mode) of every pushed key event
        self.push_mode = None   # mode byte of the last push
        self.reply_keys = 0     # key events that came in replies (queued on the device)
        self.version = None

    async def open(self):
        loop = asyncio.get_running_loop()
        reader = FrameReader(self._frame)

        def feed(d):
            loop.call_soon_threadsafe(reader.feed, d)

        self.c = BleLink(ADDR, feed)
        log("connecting...")
        await self.c.connect()
        log("connected")
        self.version = await self.read_version()
        log(f"firmware {self.version}" + (" (key push)" if self.pushes_keys else ""))

    @property
    def pushes_keys(self):
        return self.version is not None and self.version >= REMOTE_FIRMWARE

    @property
    def watchdog_s(self):
        return 60.0

    def _frame(self, op, ack, data):
        if op != 0xF0:
            if op != 0x33:
                self.other.put_nowait((op, data))
            return
        keys = [(data[5 + 2 * k], data[6 + 2 * k]) for k in range(data[4])
                if 6 + 2 * k < len(data)] if len(data) >= 5 else []
        self.pending += keys
        if data[:1] == bytes([SUB_PUSH]):
            now = time.monotonic()
            self.push_mode = data[2] if len(data) > 2 else None
            self.pushes += [(now, k, e, self.push_mode) for k, e in keys]
            if not keys:
                self.pushes.append((now, None, None, self.push_mode))
            return
        self.reply_keys += len(keys)
        self.replies.put_nowait(data)

    async def read_version(self):
        while not self.other.empty():
            self.other.get_nowait()
        await self.c.send(b"\x97")
        end = time.monotonic() + 2
        while time.monotonic() < end:
            try:
                op, d = await asyncio.wait_for(self.other.get(), end - time.monotonic())
            except asyncio.TimeoutError:
                break
            if op == 0x97 and len(d) >= 3:
                return d[1] | d[2] << 8
        return None

    async def close(self):
        try:
            await self.cmd(b"\xf0\x02")
        finally:
            await self.c.disconnect()

    async def cmd(self, payload, timeout=1.5, quiet=True):
        """Send one 0xF0 payload, return the parsed reply or None. rep["keys"]
        holds every key that arrived since the previous cmd(), pushed or queued."""
        while not self.replies.empty():
            self.replies.get_nowait()
        await self.c.send(payload)
        try:
            r = await asyncio.wait_for(self.replies.get(), timeout)
        except asyncio.TimeoutError:
            if not quiet: log("TX", payload[:12].hex(" "), "-> no reply")
            return None
        keys, self.pending = self.pending, []
        rep = {"sub": r[0], "status": r[1], "active": r[2] & 1 if len(r) > 2 else 0,
               "mode": r[2] if len(r) > 2 else 0, "dropped": r[3] if len(r) > 3 else 0,
               "keys": keys, "raw": r}
        self.keys += keys
        if not quiet:
            log("TX", payload[:12].hex(" ") + (" ..." if len(payload) > 12 else ""),
                "->", f"status={STATUS.get(rep['status'], rep['status'])} mode={rep['mode']} "
                f"dropped={rep['dropped']} keys={fmt_keys(rep['keys'])}")
        return rep


def fmt_keys(keys):
    return " ".join(f"{KEYS.get(k, k)}:{EVENTS.get(e, e)}" for k, e in keys) or "-"


# ---- commands -------------------------------------------------------------

async def cmd_auto(d):
    results = []

    def check(name, ok, detail=""):
        results.append(ok)
        log("PASS" if ok else "FAIL", name, detail)

    r = await d.cmd(b"\xf0\x02")
    check("exit when not active", r is not None and r["status"] == 0 and r["active"] == 0)
    r = await d.cmd(b"\xf0\x00")
    check("poll outside remote mode", r is not None and r["active"] == 0)
    r = await d.cmd(b"\xf0\x05\xff\x00\x00")
    check("fill refused outside remote mode", r is not None and r["status"] == 2)

    r = await d.cmd(b"\xf0\x01")
    check("enter (screen black)", r is not None and r["status"] == 0 and r["active"] == 1)
    await asyncio.sleep(1)
    for name, rgb in [("red", (255, 0, 0)), ("green", (0, 255, 0)), ("blue", (0, 0, 255))]:
        r = await d.cmd(bytes([0xF0, 0x05, *rgb]))
        check(f"fill {name}", r is not None and r["status"] == 0)
        await asyncio.sleep(0.8)

    ok = True
    for p in row_packets(gradient()):
        r = await d.cmd(p)
        ok &= r is not None and r["status"] == 0
    check("row frame, 4 packets (red grows to the right, green grows downward)", ok)
    await asyncio.sleep(2)

    r = await d.cmd(corner_pattern())
    check("palette frame (red TL, green TR, blue BL, white BR, grey cross)",
          r is not None and r["status"] == 0)
    await asyncio.sleep(2)

    bad = [("rows past the bottom", bytes([0xF0, 0x03, 1, 14, 3]) + bytes(144), 1),
           ("row data too short", bytes([0xF0, 0x03, 1, 0, 2]) + bytes(50), 1),
           ("palette with 0 colors", b"\xf0\x04\x00" + bytes(128), 1),
           ("palette with 17 colors", b"\xf0\x04\x11" + bytes(51 + 128), 1),
           ("palette pixels too short", b"\xf0\x04\x02" + bytes(6 + 100), 1),
           ("fill without color", b"\xf0\x05", 1),
           ("unknown sub-command", b"\xf0\x7f", 3)]
    for name, p, want in bad:
        r = await d.cmd(p)
        check(f"rejects {name}", r is not None and r["status"] == want and r["active"] == 1,
              "" if r is None else f"(status {r['status']})")
    await asyncio.sleep(1)
    check("screen unchanged after rejected commands (pattern still visible)", True, "(visual)")

    r = await d.cmd(b"\xf0\x02")
    check("exit (stock screen returns)", r is not None and r["active"] == 0)
    await asyncio.sleep(2)

    r = await d.cmd(b"\xf0\x01")
    await d.cmd(b"\xf0\x05\x00\x00\x80")
    wait = d.watchdog_s + 1.5
    log(f"waiting {wait:.1f} s without commands (watchdog)...")
    await asyncio.sleep(wait)
    r = await d.cmd(b"\xf0\x00")
    check(f"watchdog switched remote mode off after {d.watchdog_s:.0f} s",
          r is not None and r["active"] == 0)

    passed = sum(results)
    log(f"{passed}/{len(results)} passed")
    if d.keys:
        log("keys seen during the run:", fmt_keys(d.keys))


async def cmd_bench(d):
    await d.cmd(b"\xf0\x01")
    times = []
    for _ in range(40):
        t = time.monotonic(); r = await d.cmd(b"\xf0\x00")
        if r: times.append(time.monotonic() - t)
    times.sort()
    log(f"poll round trip: min {times[0]*1000:.0f} ms, median {times[len(times)//2]*1000:.0f} ms, "
        f"max {times[-1]*1000:.0f} ms ({len(times)}/40 replies)")
    n, t = 40, time.monotonic()
    for i in range(n):
        await d.cmd(number_frame(i % 100))
    dt = time.monotonic() - t
    log(f"palette frames: {n / dt:.1f} fps ({dt / n * 1000:.0f} ms each)")
    packets = row_packets(gradient())
    t = time.monotonic()
    for _ in range(10):
        for p in packets:
            await d.cmd(p)
    dt = time.monotonic() - t
    log(f"full RGB frames: {10 / dt:.1f} fps ({dt / 10 * 1000:.0f} ms each, 4 packets)")


async def cmd_keys(d, secs=60.0):
    await d.cmd(b"\xf0\x01")
    log(f"press keys ({secs:.0f} s); the key number is shown on the device")
    end = time.monotonic() + secs
    while time.monotonic() < end:
        r = await d.cmd(b"\xf0\x00")
        for k, e in (r or {}).get("keys", []):
            log(f"key {k:2d} ({KEYS.get(k, '?')}): {EVENTS.get(e, e)}")
            if e == 1:
                await d.cmd(number_frame(k))
        await asyncio.sleep(0.1)


async def cmd_listen(d, secs=60.0):
    r = await d.cmd(b"\xf0\x07\x01")
    if not r or not (r["raw"][2] & 2):
        log("FAIL key listening not supported (the custom firmware 24025-remote is needed)"); return
    log(f"listening for {secs:.0f} s: change menus and press keys; the device ignores them meanwhile")
    end = time.monotonic() + secs
    while time.monotonic() < end:
        r = await d.cmd(b"\xf0\x00")
        if r:
            for k, e in r["keys"]:
                log(f"key {k:2d} ({KEYS.get(k, '?')}): {EVENTS.get(e, e)}")
            if not (r["raw"][2] & 2):
                log("listening ended on the device (key held 4 s)"); return
        await asyncio.sleep(0.15)
    await d.cmd(b"\xf0\x07\x00")
    log("listening off")


async def cmd_escape(d):
    await d.cmd(b"\xf0\x01")
    await d.cmd(corner_pattern())
    log("hold any key for about 5 s, then release it")
    end = time.monotonic() + 20
    while time.monotonic() < end:
        r = await d.cmd(b"\xf0\x00")
        if r and r["keys"]:
            log("events:", fmt_keys(r["keys"]))
        if r and not r["active"]:
            log("PASS remote mode switched off by the long hold; the stock screen should be back")
            return
        await asyncio.sleep(0.2)
    log("FAIL remote mode still active after 20 s")


async def cmd_push(d, secs=60.0):
    if not d.pushes_keys:
        log(f"FAIL key push needs the custom firmware {REMOTE_FIRMWARE}-remote"); return
    r = await d.cmd(b"\xf0\x07\x01")
    if not r or not (r["mode"] & 2):
        log("FAIL could not turn key listening on"); return
    log(f"listening for {secs:.0f} s without polling (keepalive every 20 s): press keys")
    seen, polled_before = 0, d.reply_keys
    end = next_keepalive = time.monotonic()
    end += secs
    next_keepalive += 20
    while time.monotonic() < end:
        for t, k, e, mode in d.pushes[seen:]:
            if k is None:
                log(f"push: capturing ended on the device (mode {mode})")
            else:
                log(f"push: key {k:2d} ({KEYS.get(k, '?')}): {EVENTS.get(e, e)}")
        seen = len(d.pushes)
        if d.push_mode is not None and not (d.push_mode & 2):
            break
        if time.monotonic() >= next_keepalive:
            r = await d.cmd(b"\xf0\x00")
            if r and not (r["mode"] & 2):
                log("listening ended on the device"); break
            next_keepalive += 20
        await asyncio.sleep(0.05)
    r = await d.cmd(b"\xf0\x07\x00")
    pushed = sum(1 for _, k, _, _ in d.pushes if k is not None)
    log(f"{pushed} key events pushed, {d.reply_keys - polled_before} came queued in a reply")
    await cmd_stats(d)


async def cmd_stats(d):
    r = await d.cmd(b"\xf0\x09")
    raw = r["raw"] if r else b""
    at = 5 + 2 * raw[4] if len(raw) >= 5 else 0
    if not r or r["status"] != 0 or len(raw) < at + 12:
        log("FAIL no statistics (firmware 24025-remote or later is needed)"); return
    v = [raw[at + i] | raw[at + i + 1] << 8 for i in range(0, 12, 2)]
    fmt = lambda x: "none yet" if x == 0xFFFF else f"{x} bytes"
    log(f"pushed {v[0]}, not pushed: low stack {v[1]}, refused by the reply queue {v[2]}, "
        f"interrupts/scheduler/link {v[5]}")
    log(f"smallest free stack at a push: {fmt(v[3])}; key task stack never used since boot: {fmt(v[4])}")


async def cmd_linkdrop(d):
    if not d.pushes_keys:
        log(f"FAIL the link check needs the custom firmware {REMOTE_FIRMWARE}-remote"); return
    await d.cmd(b"\xf0\x01")
    await d.cmd(corner_pattern())
    r = await d.cmd(b"\xf0\x07\x01")
    if not r or r["mode"] != 3:
        log("FAIL could not turn both modes on"); return
    log("drawing mode and key listening on (pattern on screen); dropping the BLE link")
    await d.c.disconnect()
    t = time.monotonic()
    log("disconnected: the stock screen should come back right away")
    await asyncio.sleep(3)
    await d.open()
    r = await d.cmd(b"\xf0\x00")
    if r is None:
        log("FAIL no reply after reconnecting"); return
    ok = r["mode"] == 0
    log(("PASS" if ok else "FAIL") + f" mode after reconnecting {time.monotonic() - t:.1f} s later: "
        f"{r['mode']} (0 = both off; the watchdog alone would need {d.watchdog_s:.0f} s)")


async def cmd_queue(d):
    await d.cmd(b"\xf0\x01")
    await d.cmd(number_frame(0))
    log("press keys as fast as you can for 4.5 s (more than 16 presses)")
    await asyncio.sleep(4.5)
    r1 = await d.cmd(b"\xf0\x00")
    r2 = await d.cmd(b"\xf0\x00")
    if not r1 or not r2:
        log("FAIL no reply"); return
    n1, n2 = len(r1["keys"]), len(r2["keys"])
    log(f"first poll {n1} events, second poll {n2} events, dropped {r2['dropped']}")
    log(f"total queued {n1 + n2} (queue holds 32, one reply carries at most 24)")
    if n1 + n2 >= 32:
        log("PASS queue full" + (", overflow counted" if r2["dropped"] else ", no overflow yet"))
    else:
        log("queue not filled; press faster to test overflow")


async def cmd_show(d, what, args):
    secs = 20.0
    if what == "pattern":
        packets = [corner_pattern()]
    elif what == "gradient":
        packets = row_packets(gradient())
    elif what == "fill":
        packets = [bytes([0xF0, 0x05, *(int(a) for a in args[:3])])]; args = args[3:]
    elif what == "number":
        packets = [number_frame(int(args[0]))]; args = args[1:]
    else:
        sys.exit(f"unknown image {what!r}")
    if args:
        secs = float(args[0])
    await d.cmd(b"\xf0\x01", quiet=False)
    for p in packets:
        await d.cmd(p, quiet=False)
    log(f"holding for {secs:.0f} s")
    end = time.monotonic() + secs
    while time.monotonic() < end:
        r = await d.cmd(b"\xf0\x00")
        if r and r["keys"]:
            log("keys:", fmt_keys(r["keys"]))
        await asyncio.sleep(1)


PAINT = [(0, 0, 0), (255, 0, 0), (255, 128, 0), (255, 230, 0), (0, 220, 0),
         (0, 200, 255), (0, 60, 255), (170, 0, 255), (255, 255, 255)]


async def cmd_play(d, secs=300.0):
    """Paint on the device with its own keys; every event is logged."""
    canvas = [0] * 256
    x = y = 7
    color = 1
    blink = True
    last_blink = time.monotonic()

    def frame():
        px = list(canvas)
        if blink:
            px[y * 16 + x] = 9 if canvas[y * 16 + x] == 0 else 10
        pal = PAINT + [(90, 90, 90), (0, 0, 0)]
        pal[9] = PAINT[color] if PAINT[color] != (0, 0, 0) else (90, 90, 90)
        return palette_frame(px, pal)

    await d.cmd(b"\xf0\x01")
    await d.cmd(frame())
    log(f"play for {secs:.0f} s: left/right x, +/- y, arm paint, brightness color, M clear, little quit")
    end = time.monotonic() + secs
    seen = len(d.keys)                   # d.keys collects events from every reply
    while time.monotonic() < end:
        r = await d.cmd(b"\xf0\x00")
        dirty = False
        events, seen = d.keys[seen:], len(d.keys)
        for k, e in events:
            log(f"key {k:2d} ({KEYS.get(k, '?')}): {EVENTS.get(e, e)}")
            if e not in (1, 4):          # act on press and auto-repeat only
                continue
            if k == 2: x = (x - 1) % 16
            elif k == 3: x = (x + 1) % 16
            elif k == 1: y = (y - 1) % 16
            elif k == 9: y = (y + 1) % 16
            elif k == 4:
                i = y * 16 + x
                canvas[i] = 0 if canvas[i] == color else color
            elif k == 7:
                color = color % (len(PAINT) - 1) + 1
                log("color", color, PAINT[color])
            elif k == 0:
                canvas = [0] * 256; log("cleared")
            elif k == 10:
                log("quit"); return
            blink, last_blink, dirty = True, time.monotonic(), True
        if time.monotonic() - last_blink > 0.5:
            blink, last_blink, dirty = not blink, time.monotonic(), True
        if r and not r["active"]:
            log("remote mode ended on the device (long hold or watchdog)"); return
        if dirty:
            await d.cmd(frame())
        await asyncio.sleep(0.05)


async def cmd_raw(d, hexes):
    for h in hexes:
        await d.cmd(bytes.fromhex(h), quiet=False)
        await asyncio.sleep(0.3)


async def main():
    if len(sys.argv) < 2 or sys.argv[1] not in {"auto", "bench", "keys", "escape", "queue", "show", "raw", "play", "listen",
                                                   "push", "stats", "linkdrop"}:
        print(__doc__); return
    what, args = sys.argv[1], sys.argv[2:]
    d = Remote()
    await d.open()
    try:
        if what == "auto": await cmd_auto(d)
        elif what == "bench": await cmd_bench(d)
        elif what == "keys": await cmd_keys(d, float(args[0]) if args else 60.0)
        elif what == "escape": await cmd_escape(d)
        elif what == "listen": await cmd_listen(d, float(args[0]) if args else 60.0)
        elif what == "queue": await cmd_queue(d)
        elif what == "push": await cmd_push(d, float(args[0]) if args else 60.0)
        elif what == "stats": await cmd_stats(d)
        elif what == "linkdrop": await cmd_linkdrop(d)
        elif what == "show": await cmd_show(d, args[0], args[1:])
        elif what == "raw": await cmd_raw(d, args)
        elif what == "play": await cmd_play(d, float(args[0]) if args else 300.0)
    finally:
        if what not in ("raw", "stats"):
            await d.close()
        else:
            await d.c.disconnect()
        log("done")

if __name__ == "__main__":
    asyncio.run(main())
