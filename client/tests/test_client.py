"""Offline tests for ditoo_api.py: a fake BLE link plays the device's side.

    client/.venv/bin/python client/tests/test_client.py
"""
import asyncio, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ditoo_api  # noqa: E402
from ditoo_api import Ditoo, DitooRemote, FrameReader, RemoteError  # noqa: E402

failures = checks = 0


def check(name, ok):
    global failures, checks
    checks += 1
    if not ok:
        failures += 1
        print("FAIL", name)


def device_frame(op, data, ack=0x55):
    """Bytes as the device sends them: 01 len 04 op ack data crc 02 (crc unchecked)."""
    n = 5 + len(data)
    return bytes([0x01, n & 0xFF, n >> 8, 0x04, op, ack]) + bytes(data) + b"\0\0\x02"


def reply(sub, mode=0, keys=(), extra=b"", status=0):
    return bytes([sub, status, mode, 0, len(keys)]) + bytes(b for k in keys for b in k) + extra


def push(mode, keys=()):
    return reply(0x80, mode, keys)


class FakeLink:
    """Replaces BleLink. Answers each command with the frames the script returns
    for it, delivered as notifications through the API's own data callback."""
    script = None
    version = 24025

    def __init__(self, target, data, lost=None, connector=None):
        self.data, self.sent, self.connected = data, [], False
        self.script, self.version = FakeLink.script, FakeLink.version

    async def connect(self):
        self.connected = True

    def notify(self, op, data):
        self.data(device_frame(op, data))

    async def send(self, payload):
        self.sent.append(payload)
        if payload == b"\x97":
            frames = [(0x97, bytes([0x55, self.version & 0xFF, self.version >> 8, 1]))]
        else:
            frames = self.script(payload)
        for op, data in frames:
            asyncio.get_running_loop().call_soon(self.notify, op, data)

    async def disconnect(self):
        self.connected = False


RealBleLink = ditoo_api.BleLink
ditoo_api.BleLink = FakeLink


def attach(dev, script, version=24025):
    """Only the link, no version read (for tests that set the state by hand)."""
    dev._loop = asyncio.get_running_loop()
    reader = FrameReader(dev._on_frame)
    dev._link = FakeLink(None, reader.feed)
    dev._link.script = script
    return dev._link


async def connect(dev, script, version=24025):
    """The real DitooRemote.connect over the fake link."""
    FakeLink.script, FakeLink.version = script, version
    await dev.connect()
    return dev._link


async def test_push_is_not_a_reply():
    """A push that arrives while a 0xF0 request waits must not be taken as its reply."""
    dev = DitooRemote()
    got = []
    dev.on_key = lambda ev: got.append((ev.key, ev.event))

    def script(p):
        if p == b"\xf0\x07\x01":
            return [(0xF0, push(2, [(3, 1)])), (0xF0, reply(7, 2, [(1, 1)]))]
        return [(0xF0, reply(p[1], 2))]
    await connect(dev, script)
    r = await dev.set_key_listening(True)
    check("request got the real reply", r.sub == 7 and [(k.key, k.event) for k in r.keys] == [(1, 1)])
    check("keys in arrival order", got == [(3, 1), (1, 1)])
    check("events queue in arrival order",
          [(e.key, e.event) for e in [dev._events.get_nowait(), dev._events.get_nowait()]] == [(3, 1), (1, 1)])
    await dev._stop_poller()


async def test_push_between_commands():
    dev = DitooRemote()
    got = []
    dev.on_key = lambda ev: got.append(ev.name)
    link = attach(dev, lambda p: [(0xF0, reply(p[1], 2))])
    dev.listening = True
    link.notify(0xF0, push(2, [(2, 1)]))
    link.notify(0xF0, push(2, [(2, 2)]))
    check("pushed keys delivered without a command", got == ["left", "left"])
    ev = await dev.next_key(0.1)
    check("next_key gets the pushed key", ev is not None and ev.name == "left" and ev.kind == "down")


async def test_escape_push_ends_listening():
    dev = DitooRemote()
    ended = []
    dev.on_listen_end = lambda: ended.append(1)
    link = attach(dev, lambda p: [])
    dev.listening = True
    link.notify(0xF0, push(0))
    check("escape push turns listening off", not dev.listening and ended == [1])


async def test_async_callback_can_send_commands():
    """An async on_key that draws must not deadlock the frame handling."""
    dev = DitooRemote()
    drawn = asyncio.Event()

    async def on_key(ev):
        await dev.fill((255, 0, 0))
        drawn.set()
    dev.on_key = on_key
    link = attach(dev, lambda p: [(0xF0, reply(p[1], 1))])
    dev.active = True
    link.notify(0xF0, push(1, [(4, 1)]))
    try:
        await asyncio.wait_for(drawn.wait(), 1.0)
        ok = True
    except asyncio.TimeoutError:
        ok = False
    check("async callback command completes", ok and link.sent[-1] == b"\xf0\x05\xff\x00\x00")


async def test_poll_interval_by_version():
    dev = DitooRemote()
    await connect(dev, lambda p: [], 24025)
    check("24025 keepalive 20 s", dev.pushes_keys and dev.poll_interval == 20.0)
    dev = DitooRemote()
    await connect(dev, lambda p: [], 24020)
    check("stock firmware: no push", not dev.pushes_keys)
    dev = DitooRemote(poll_interval=1.0)
    await connect(dev, lambda p: [], 24025)
    check("explicit interval kept", dev.poll_interval == 1.0)


async def test_push_stats():
    dev = DitooRemote()
    stats = bytes([7, 0, 1, 0, 0, 0, 0xF4, 0x01, 0xB0, 0x04, 2, 0])
    await connect(dev, lambda p: [(0xF0, reply(9, 2, [(1, 2)], stats))])
    s = await dev.push_stats()
    check("stats parsed", (s.pushed, s.low_stack, s.push_failed, s.min_free, s.high_water, s.blocked)
          == (7, 1, 0, 500, 1200, 2))
    check("stats reply keys delivered", dev._events.qsize() == 1)
    dev = DitooRemote()
    await connect(dev, lambda p: [(0xF0, reply(9, 0, status=3))], 24020)
    try:
        await dev.push_stats()
        check("no stats without the custom firmware", False)
    except RemoteError:
        check("no stats without the custom firmware", True)
    dev = DitooRemote()
    none = bytes([0, 0, 0, 0, 0, 0, 0xFF, 0xFF, 0xFF, 0xFF])
    await connect(dev, lambda p: [(0xF0, reply(9, 0, extra=none))])
    s = await dev.push_stats()
    check("0xffff means unknown", s.min_free is None and s.high_water is None)
    check("10-byte stats: blocked unknown", s.blocked is None)


async def test_battery_with_push_before():
    dev = DitooRemote()
    await connect(dev, lambda p: [(0xF0, push(2, [(9, 1)])),
                                  (0xF0, reply(6, 2, extra=bytes([5, 1, 0])))])
    b = await dev.battery()
    check("battery read despite a push first", b.level == 5 and b.external_power and not b.charging)
    check("pushed key kept", dev._events.qsize() == 1)


async def test_plain_ditoo_ignores_push():
    dev = Ditoo()
    msgs = []
    dev.on_message = lambda op, data: msgs.append((op, data[:1]))
    attach(dev, lambda p: [(0xF0, push(2, [(1, 1)])), (0xF0, reply(6, 0, extra=bytes([3, 0, 0])))])
    b = await dev.battery()
    check("plain Ditoo battery gets the reply", b.level == 3)
    check("push passed to on_message", msgs == [(0xF0, b"\x80")])


async def test_connection_params():
    """BleLink passes the parameters to a client that supports them (HA's), and
    reports False for plain bleak or no connection."""
    calls = []

    class HaClient:
        is_connected = True

        async def set_connection_params(self, *args):
            calls.append(args)

    class PlainClient:
        is_connected = True

    link = RealBleLink("x", lambda d: None)
    check("not connected -> False", await link.set_connection_params(12, 24, 9, 200) is False)
    link._ble = PlainClient()
    check("plain bleak -> False", await link.set_connection_params(12, 24, 9, 200) is False)
    link._ble = HaClient()
    check("HA client -> True", await link.set_connection_params(12, 24, 9, 200) is True)
    check("arguments passed in order", calls == [(12, 24, 9, 200)])
    dev = DitooRemote()
    check("Ditoo without link -> False", await dev.set_connection_params(12, 24, 9, 200) is False)


async def test_late_reply_is_not_taken_for_the_next_request():
    """A frame reply that arrives after its request timed out must not answer the next request."""
    dev = DitooRemote(reply_timeout=0.2)

    def script(p):
        if p[:2] == b"\xf0\x04":
            return []                                   # frame: the reply is late
        if p == b"\xf0\x06":
            return [(0xF0, reply(4, 1)), (0xF0, reply(6, 1, extra=bytes([5, 1, 0])))]
        return [(0xF0, reply(p[1], 0))]
    await connect(dev, script)
    try:
        await dev.command(b"\xf0\x04\x01" + bytes(3 + 128))
        check("frame without reply times out", False)
    except RemoteError:
        check("frame without reply times out", True)
    b = await dev.battery()
    check("battery ignores the late frame reply", b.level == 5 and b.external_power)


async def test_poll_loop_stops_when_link_is_gone():
    dev = DitooRemote(poll_interval=0.01)
    link = await connect(dev, lambda p: [(0xF0, reply(p[1], 2))])
    dev.listening = True

    async def broken(payload):
        link.connected = False
        raise RuntimeError("Service Discovery has not been performed yet")
    link.send = broken
    dev._ensure_poller()
    await asyncio.sleep(0.1)
    check("poller ended quietly", dev._poller.done() and dev._poller.exception() is None)


async def test_poll_skipped_after_recent_command():
    sent = []
    dev = DitooRemote(poll_interval=0.05)

    def script(p):
        sent.append(p)
        return [(0xF0, reply(p[1], 1))]
    await connect(dev, script)
    dev.active = True
    dev._ensure_poller()
    for _ in range(8):                                  # a frame every 20 ms, like a scrolling text
        await dev.fill((1, 2, 3))
        await asyncio.sleep(0.02)
    polls_while_busy = sum(1 for p in sent if p == b"\xf0\x00")
    await asyncio.sleep(0.2)                            # idle: the poller has to keep the watchdog fed
    polls_idle = sum(1 for p in sent if p == b"\xf0\x00") - polls_while_busy
    await dev._stop_poller()
    check("no polls while frames flow", polls_while_busy == 0)
    check("polls resume when idle", polls_idle >= 1)


async def test_native_text_bytes():
    """The sequence that worked on the device on 2026-10-01."""
    dev = Ditoo()
    link = attach(dev, lambda p: [])
    await dev.show_native_text("HI", color=(255, 0, 0), background=(0, 0, 9), speed=17)
    s = link.sent
    check("mode on", s[0] == b"\x6e\x01")
    check("text as UTF-16LE with length", s[1] == b"\x86\x01\x02H\x00I\x00")
    check("fastest speed is 50", s[2] == b"\x86\x00\x32")
    first, frame = s[3], s[4]
    check("palette frame is 0x407 bytes", first[:5] == b"\x6c\x00\x00\x07\x04" and len(first) == 5 + 0x407)
    pal = first[5 + 7:5 + 7 + 768]          # opcode, index, length, then the frame
    check("background at 0, text color at 127", pal[0:3] == b"\x00\x00\x09" and pal[127 * 3:128 * 3] == b"\xff\x00\x00")
    check("index frame is 0x107 bytes, reuses the palette",
          frame[:5] == b"\x6c\x01\x00\x07\x01" and len(frame) == 5 + 0x107 and frame[5 + 5] == 1)
    await dev.show_native_text("x" * 100, speed=1)
    check("text cut to 64 characters, slowest speed 1", link.sent[6] == b"\x86\x01\x40" + "x".encode("utf-16-le") * 64
          and link.sent[7] == b"\x86\x00\x01")


async def main():
    global failures
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                await asyncio.wait_for(fn(), 5)
            except Exception as e:
                failures += 1
                print("FAIL", name, "raised", type(e).__name__, e)
    print(f"{checks} checks, {failures} failed")
    return failures


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
