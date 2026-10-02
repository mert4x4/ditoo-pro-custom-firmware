# Generated from client/ditoo_api.py by homeassistant/sync_core.sh. Do not edit here.
"""Core API for the Divoom Ditoo Pro (stock commands; remote mode with the custom firmware 24025-remote).

`Ditoo` covers the stock commands (channels, brightness, time, weather, tools,
games, audio, alarms, sleep); `DitooRemote` adds remote mode (opcode 0xF0),
where the host draws the screen and receives the front keys.

    import asyncio
    from ditoo_api import DitooRemote, Canvas

    async def main():
        async with DitooRemote() as dev:          # connect + remote mode on
            c = Canvas()
            c.text(1, 5, "HI", (255, 200, 0))
            await dev.show(c)
            async for ev in dev.keys():           # KeyEvent(key, event, name, kind)
                if ev.name == "little":
                    break
    asyncio.run(main())

The device is found by its Bluetooth name (DitooPro-Light...) unless an address
is given, either as `target` or in the DITOO_ADDRESS environment variable.

The custom firmware pushes key events as they happen and ends drawing mode and
key listening itself when the BLE link drops. While either is on, a background
task sends a command every `poll_interval` seconds (20 s) to keep the device's
60 s watchdog satisfied. Every 0xF0 reply carries queued key events as well. If a mode
ends on the device (key held 4 s, link lost, or the watchdog), `active` or
`listening` becomes False and `on_exit` or `on_listen_end` runs.
"""
import asyncio, datetime, inspect, os, time, traceback
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

from bleak import BleakClient, BleakScanner

DEFAULT_ADDRESS = os.environ.get("DITOO_ADDRESS") or None   # None: find the device by name
NAME_PREFIX = "DitooPro-Light"                             # BLE name of the light side
WRITE_CHAR = "49535343-8841-43f4-a8d4-ecbe34729bb3"       # DitooPro-Light UART
NOTIFY_CHAR = "49535343-1e4d-4bd9-ba61-23c647249616"
MAX_CHUNK = 509                                            # largest GATT write we use
OPCODE = 0xF0
SUB_PUSH = 0x80              # first data byte of a frame the device sends on its own (24025+)
REMOTE_FIRMWARE = 24025      # version of the custom firmware
POLL_KEEPALIVE = 20.0        # the device's watchdog is 60 s
KEY_NAMES = {0: "M", 1: "+", 2: "left", 3: "right", 4: "arm", 7: "brightness", 9: "-", 10: "little"}
EVENT_NAMES = {1: "down", 2: "up", 3: "long", 4: "repeat", 5: "long-up"}
STATUS_NAMES = {0: "ok", 1: "bad length", 2: "not in remote mode", 3: "unknown sub-command"}

RGB = Tuple[int, int, int]
BLACK: RGB = (0, 0, 0)


class RemoteError(Exception):
    pass


@dataclass(frozen=True)
class KeyEvent:
    key: int          # 0..10, see KEY_NAMES
    event: int        # 1 down, 2 up, 3 long, 4 repeat, 5 long-up
    time: float       # time.monotonic() when the reply arrived

    @property
    def name(self) -> str:
        return KEY_NAMES.get(self.key, str(self.key))

    @property
    def kind(self) -> str:
        return EVENT_NAMES.get(self.event, str(self.event))

    @property
    def pressed(self) -> bool:
        """True for a new press or an auto-repeat while held."""
        return self.event in (1, 4)

    def __str__(self):
        return f"{self.name}:{self.kind}"


@dataclass
class Reply:
    sub: int
    status: int
    active: bool            # drawing mode
    dropped: int
    keys: List[KeyEvent]
    listening: bool = False  # key listening


def _reply_from_raw(raw: bytes) -> Reply:
    if len(raw) < 5:
        raise RemoteError(f"short reply {raw.hex()}")
    now = time.monotonic()
    keys = [KeyEvent(raw[5 + 2 * i], raw[6 + 2 * i], now) for i in range(raw[4]) if 6 + 2 * i < len(raw)]
    # mode byte: bit 0 drawing, bit 1 key listening
    return Reply(raw[0], raw[1], bool(raw[2] & 1), raw[3], keys, bool(raw[2] & 2))


# ---- drawing ----------------------------------------------------------------

_FONT = {  # 3x5 glyphs, rows top to bottom
    "0": "111101101101111", "1": "010110010010111", "2": "111001111100111", "3": "111001111001111",
    "4": "101101111001001", "5": "111100111001111", "6": "111100111101111", "7": "111001010010010",
    "8": "111101111101111", "9": "111101111001111",
    "A": "010101111101101", "B": "110101110101110", "C": "011100100100011", "D": "110101101101110",
    "E": "111100110100111", "F": "111100110100100", "G": "011100101101011", "H": "101101111101101",
    "I": "111010010010111", "J": "001001001101010", "K": "101101110101101", "L": "100100100100111",
    "M": "101111111101101", "N": "110101101101101", "O": "010101101101010", "P": "110101110100100",
    "Q": "010101101110011", "R": "110101110101101", "S": "011100010001110", "T": "111010010010010",
    "U": "101101101101111", "V": "101101101101010", "W": "101101111111101", "X": "101101010101101",
    "Y": "101101010010010", "Z": "111001010100111",
    " ": "000000000000000", ":": "000010000010000", ".": "000000000000010", "-": "000000111000000",
    "+": "000010111010000", "/": "001001010100100", "%": "101001010100101", "°": "010101010000000",
    "!": "010010010000010", "?": "110001010000010", "=": "000111000111000", "_": "000000000000111",
}


class Canvas:
    """16x16 RGB image. (0, 0) is the top-left pixel."""
    W = H = 16

    def __init__(self, background: RGB = BLACK):
        self.pixels: List[RGB] = [background] * 256

    def clear(self, color: RGB = BLACK) -> "Canvas":
        self.pixels = [tuple(color)] * 256
        return self

    def set(self, x: int, y: int, color: RGB) -> None:
        if 0 <= x < 16 and 0 <= y < 16:
            self.pixels[y * 16 + x] = tuple(color)

    def get(self, x: int, y: int) -> RGB:
        return self.pixels[y * 16 + x]

    def rect(self, x: int, y: int, w: int, h: int, color: RGB, fill: bool = True) -> None:
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                if fill or yy in (y, y + h - 1) or xx in (x, x + w - 1):
                    self.set(xx, yy, color)

    def line(self, x0: int, y0: int, x1: int, y1: int, color: RGB) -> None:
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        while True:
            self.set(x0, y0, color)
            if x0 == x1 and y0 == y1:
                return
            e2 = 2 * err
            if e2 >= dy: err += dy; x0 += sx
            if e2 <= dx: err += dx; y0 += sy

    def text(self, x: int, y: int, s: str, color: RGB, spacing: int = 1) -> int:
        """Draw text in the 3x5 font; returns the x after the last glyph.
        Unknown characters are drawn as '?'. Lowercase is drawn as uppercase."""
        for ch in s.upper():
            glyph = _FONT.get(ch, _FONT["?"])
            for i, bit in enumerate(glyph):
                if bit == "1":
                    self.set(x + i % 3, y + i // 3, color)
            x += 3 + spacing
        return x

    @staticmethod
    def text_width(s: str, spacing: int = 1) -> int:
        return len(s) * (3 + spacing) - spacing if s else 0

    def blit(self, other: "Canvas", dx: int = 0, dy: int = 0, transparent: Optional[RGB] = BLACK) -> None:
        for y in range(16):
            for x in range(16):
                p = other.pixels[y * 16 + x]
                if p != transparent:
                    self.set(x + dx, y + dy, p)

    def copy(self) -> "Canvas":
        c = Canvas(); c.pixels = list(self.pixels); return c


def palette_payload(pixels: Sequence[RGB]) -> Optional[bytes]:
    """F0 04 frame, or None if the image has more than 16 colors."""
    pal: List[RGB] = []
    index = {}
    idx = []
    for p in pixels:
        if p not in index:
            if len(pal) == 16:
                return None
            index[p] = len(pal); pal.append(p)
        idx.append(index[p])
    return (bytes([OPCODE, 0x04, len(pal)]) + bytes(c for rgb in pal for c in rgb)
            + bytes((idx[i] << 4) | idx[i + 1] for i in range(0, 256, 2)))


def row_payloads(pixels: Sequence[RGB]) -> List[bytes]:
    """F0 03 packets of up to 5 rows; the last one presents the frame."""
    out = []
    for row in range(0, 16, 5):
        count = min(5, 16 - row)
        data = bytes(c for p in pixels[row * 16:(row + count) * 16] for c in p)
        out.append(bytes([OPCODE, 0x03, 1 if row + count == 16 else 0, row, count]) + data)
    return out


# ---- BLE link -----------------------------------------------------------------

async def find_device(timeout: float = 15.0):
    """Scan for the Ditoo Pro (name DitooPro-Light...) and return its BLEDevice.
    Raises DitooNotFound when nothing answers within `timeout` seconds."""
    def match(dev, adv):
        return (adv.local_name or dev.name or "").startswith(NAME_PREFIX)
    dev = await BleakScanner.find_device_by_filter(match, timeout=timeout)
    if dev is None:
        raise DitooNotFound(
            f"no {NAME_PREFIX} device found. Turn the Ditoo on, close the Divoom app or turn off "
            "Bluetooth on the phone, and make sure no other program is connected to it.")
    return dev


class DitooNotFound(Exception):
    pass


class BleLink:
    """Divoom BLE framing on top of bleak.

    Outgoing: FE EF AA 55, length (LE16, counts seq..crc), seq (BE16), 00 00 00,
    payload, crc (LE16 = byte sum of length and body). The device only acts on
    commands whose seq high byte is non-zero, so seq starts at 0x0101.
    Incoming notifications are passed to `on_data` unparsed.

    `target` is a Bluetooth address (AA:BB:CC:DD:EE:FF), a bleak BLEDevice, or None
    to scan for a device named DitooPro-Light. `connector`, if given, is an async function
    (target, disconnected_callback) -> connected BleakClient; Home Assistant passes
    bleak_retry_connector.establish_connection here."""

    def __init__(self, target, on_data: Callable[[bytes], None],
                 on_disconnect: Optional[Callable[[], None]] = None, connector=None):
        self.target = target
        self.on_data = on_data
        self.on_disconnect = on_disconnect
        self.connector = connector
        self._ble: Optional[BleakClient] = None
        self._seq = 0x0101
        self.chunk = 20

    @property
    def address(self):          # kept for older callers
        return self.target

    async def connect(self) -> None:
        def lost(_client):
            if self.on_disconnect:
                self.on_disconnect()
        if self.connector:
            self._ble = await self.connector(self.target, lost)
        else:
            target = self.target or await find_device()
            self._ble = BleakClient(target, disconnected_callback=lost)
            await self._ble.connect()
        # BlueZ reports MTU 23 until it is acquired explicitly.
        acquire = getattr(getattr(self._ble, "_backend", None), "_acquire_mtu", None)
        if acquire:
            try:
                await acquire()
            except Exception:
                pass
        # One write per packet when the negotiated MTU allows it.
        self.chunk = max(20, min(MAX_CHUNK, (self._ble.mtu_size or 23) - 3))
        await self._ble.start_notify(NOTIFY_CHAR, lambda _ch, data: self.on_data(bytes(data)))

    async def disconnect(self) -> None:
        ble, self._ble = self._ble, None
        if ble and ble.is_connected:
            try:
                await ble.stop_notify(NOTIFY_CHAR)
            except Exception:
                pass
            await ble.disconnect()

    @property
    def connected(self) -> bool:
        return bool(self._ble and self._ble.is_connected)

    async def set_connection_params(self, min_interval: int, max_interval: int,
                                    latency: int, timeout: int) -> bool:
        """Ask for BLE connection parameters: intervals in 1.25 ms units, latency in
        connection events the device may skip, supervision timeout in 10 ms units.
        Only Home Assistant's Bluetooth stack offers this (plain bleak does not);
        returns False when it is not available or not connected."""
        fn = getattr(self._ble, "set_connection_params", None)
        if fn is None or not self.connected:
            return False
        await fn(min_interval, max_interval, latency, timeout)
        return True

    def frame(self, payload: bytes) -> bytes:
        seq = self._seq
        self._seq = seq + 1 if seq < 0xFFFF else 0x0101
        body = bytes([seq >> 8, seq & 0xFF, 0, 0, 0]) + payload
        n = len(body) + 2
        crc = ((n & 0xFF) + (n >> 8) + sum(body)) & 0xFFFF
        return b"\xfe\xef\xaa\x55" + bytes([n & 0xFF, n >> 8]) + body + bytes([crc & 0xFF, crc >> 8])

    async def send(self, payload: bytes) -> None:
        """Write with response: back-to-back writes without it can overrun the link."""
        if not self._ble:
            raise RuntimeError("not connected")
        pkt = self.frame(payload)
        for i in range(0, len(pkt), self.chunk):
            await self._ble.write_gatt_char(WRITE_CHAR, pkt[i:i + self.chunk], response=True)


class FrameReader:
    """Splits the notify stream into device frames and calls on_frame(op, ack, data).

    Device frame: 01, length (LE16), 04, opcode, ack (0x55 = ok), data, crc (LE16), 02.
    `length` counts everything after the 01 except the crc and the 02."""

    def __init__(self, on_frame: Callable[[int, int, bytes], None]):
        self.on_frame = on_frame
        self.buf = bytearray()

    def feed(self, data: bytes) -> None:
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
            frame = bytes(self.buf[:n + 4])
            if frame[-1] != 0x02 or n < 4 or frame[3] != 0x04:
                del self.buf[:1]            # not a frame start, resync on the next 01
                continue
            del self.buf[:n + 4]
            self.on_frame(frame[4], frame[5], frame[6:-3])


# ---- stock device -------------------------------------------------------------

class DitooError(Exception):
    pass


# 0x45 channels (called "light modes" in Divoom's documentation)
# Channel 6 is the lyric display (scrolling text); Divoom's 0x45 page calls it scoreboard, but on the
# Ditoo Pro the scoreboard is the 0x72 tool 1, not a channel.
CHANNEL_CLOCK, CHANNEL_LIGHT, CHANNEL_CLOUD, CHANNEL_VJ, CHANNEL_MUSIC, CHANNEL_CUSTOM, CHANNEL_LYRIC = range(7)
CHANNEL_NAMES = {0: "clock", 1: "light", 2: "cloud", 3: "vj", 4: "music", 5: "custom", 6: "lyric"}
CUSTOM_PAGES = 3          # custom channel pages 0..2 (the app shows them as 1..3)

# Native scrolling text (the app's LED/text editor; divoom_light_word in the firmware).
# The device scrolls the text itself with its built-in 16x16 font, over a background
# frame; nothing has to be streamed. Speed values the firmware tells apart: a step every
# max(3, (s + 99) // s) timer ticks, so these 17 values are the distinct speeds, 50 the
# fastest (anything above 50 is the same).
NATIVE_TEXT_SPEEDS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 17, 20, 25, 34, 50)
NATIVE_TEXT_MAX = 64      # characters the firmware keeps
# Channels the Ditoo Pro actually switches to; 3 (VJ) is ignored.
DITOO_PRO_CHANNELS = (0, 1, 2, 4, 5, 6)
# 0x5F weather types
WEATHER = {"clear": 1, "cloudy": 3, "thunderstorm": 5, "rain": 6, "snow": 8, "fog": 9}
# 0x05 / 0x13 work modes
WORK_MODES = {0: "bluetooth", 1: "fm", 2: "line-in", 3: "sd", 4: "usb-host", 5: "record",
              6: "record-play", 7: "uac", 8: "phone", 9: "divoom-show", 10: "alarm-set", 11: "game"}
# 0x17 / 0x21 game keys
GAME_KEYS = {"left": 1, "right": 2, "up": 3, "down": 4, "ok": 5}


@dataclass
class LightState:
    """Reply to 0x46 (get light mode). Field order from Divoom's documentation."""
    channel: int
    fahrenheit: bool
    vj_effect: int
    light_color: RGB
    light_brightness: int
    light_effect: int
    light_on: bool
    music_mode: int
    brightness: int
    hour24: bool
    clock_color: RGB
    clock_style: int
    clock_flags: Tuple[int, ...]
    raw: bytes

    @property
    def channel_name(self) -> str:
        return CHANNEL_NAMES.get(self.channel, str(self.channel))


@dataclass
class Battery:
    """Remote-mode sub-command 06."""
    level: int              # 0..7, the device's own coarse measurement
    external_power: bool    # USB power connected
    charge_pin: bool        # charger status pin; only meaningful with external power

    MAX_LEVEL = 7

    @property
    def percent(self) -> int:
        """Approximate: the device only knows 8 steps."""
        return round(min(self.level, self.MAX_LEVEL) * 100 / self.MAX_LEVEL)

    @property
    def charging(self) -> bool:
        return self.external_power and self.charge_pin

    @property
    def full(self) -> bool:
        return self.external_power and not self.charge_pin


@dataclass
class PushStats:
    """Remote-mode sub-command 09. Counters
    since the remote state was created (cold boot or first 0xF0 command)."""
    pushed: int             # key events pushed
    low_stack: int          # not pushed because the key task's stack was short; queued
    push_failed: int        # the device's reply queue refused a push; queued
    min_free: Optional[int]      # smallest free stack at a push attempt (bytes), None before one
    high_water: Optional[int]    # key task stack never used since boot (bytes)
    blocked: Optional[int] = None  # not pushed: interrupts off, scheduler stopped or BLE down


def _parse_push_stats(d: bytes) -> PushStats:
    at = 5 + 2 * d[4] if len(d) >= 5 else len(d)
    if len(d) < at + 10 or d[1] != 0:
        raise DitooError("no push statistics: the custom firmware 24025-remote is needed (./ditoo install)")
    v = [d[at + i] | d[at + i + 1] << 8 for i in range(0, min(12, len(d) - at) - 1, 2)]
    return PushStats(v[0], v[1], v[2], None if v[3] == 0xFFFF else v[3],
                     None if v[4] == 0xFFFF else v[4], v[5] if len(v) > 5 else None)


def _sub_is(sub: int) -> Callable[[bytes], bool]:
    """Matches a 0xF0 reply to the sub-command that asked for it."""
    return lambda d: len(d) >= 1 and d[0] == sub


def _parse_battery(d: bytes) -> "Battery":
    at = 5 + 2 * d[4] if len(d) >= 5 else len(d)
    if len(d) < at + 3:
        raise DitooError("no battery data: the custom firmware 24025-remote is needed (./ditoo install)")
    return Battery(d[at], bool(d[at + 1]), bool(d[at + 2]))


@dataclass
class Alarm:
    index: int
    on: bool
    hour: int
    minute: int
    weekdays: int          # bit 0 Sunday .. bit 6 Saturday
    mode: int
    trigger: int
    fm: int
    volume: int

    def payload(self) -> bytes:
        return bytes([self.index, int(self.on), self.hour, self.minute, self.weekdays, self.mode,
                      self.trigger, self.fm & 0xFF, self.fm >> 8, self.volume])


@dataclass
class SleepSettings:
    minutes: int
    mode: int
    on: bool
    fm: int
    volume: int
    color: RGB
    light: int

    def payload(self) -> bytes:
        return bytes([self.minutes, self.mode, int(self.on), self.fm & 0xFF, self.fm >> 8,
                      self.volume, *self.color, self.light])


class Ditoo:
    """Connection to the Ditoo Pro light side (DitooPro-Light) and its stock commands.

    Every request waits for the device's reply with the same opcode. Frames that
    no request is waiting for (heartbeats, game results, SD card changes) go to
    `on_message(op, data)`. Commands the device does not answer are sent with
    `send()`. Everything here works without remote mode."""

    def __init__(self, target=DEFAULT_ADDRESS, reply_timeout: float = 2.0, connector=None):
        self.target = target
        self.reply_timeout = reply_timeout
        self.connector = connector
        self.on_message: Optional[Callable[[int, bytes], object]] = None
        self.on_disconnect: Optional[Callable[[], object]] = None
        self._link: Optional[BleLink] = None
        self._lock = asyncio.Lock()
        self._waiters: dict = {}
        self._collectors: dict = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def address(self):          # kept for older callers
        return self.target

    # connection ---------------------------------------------------------------
    async def connect(self) -> None:
        self._loop = asyncio.get_running_loop()
        reader = FrameReader(self._on_frame)

        def data(d: bytes):
            self._loop.call_soon_threadsafe(reader.feed, d)

        def lost():
            self._loop.call_soon_threadsafe(self._lost)

        self._link = BleLink(self.target, data, lost, self.connector)
        await self._link.connect()

    async def close(self) -> None:
        link, self._link = self._link, None
        if link:
            await link.disconnect()

    @property
    def connected(self) -> bool:
        return bool(self._link and self._link.connected)

    async def __aenter__(self):
        await self.connect()
        return self

    async def set_connection_params(self, min_interval: int, max_interval: int,
                                    latency: int, timeout: int) -> bool:
        """See BleLink.set_connection_params."""
        if not self._link:
            return False
        return await self._link.set_connection_params(min_interval, max_interval, latency, timeout)

    async def __aexit__(self, *exc) -> None:
        await self.close()

    def _lost(self) -> None:
        for waiters in self._waiters.values():
            for f, _ in waiters:
                if not f.done():
                    f.set_exception(DitooError("disconnected"))
                    f.exception()           # the request that awaits it re-raises; nobody else must log it
        self._waiters.clear()
        if self.on_disconnect:
            _call(self.on_disconnect)

    def _on_frame(self, op: int, ack: int, data: bytes) -> None:
        if op == 0x33:                      # per-packet ACK
            return
        if op == OPCODE and data[:1] == bytes([SUB_PUSH]):
            self._on_push(data)             # never the reply to a request
            return
        if op in self._collectors:
            self._collectors[op].append(data)
            return
        waiters = self._waiters.get(op) or []
        for k, (f, match) in enumerate(waiters):
            if not f.done() and (match is None or match(data)):
                del waiters[k]
                f.set_result(data)
                return
        if self.on_message:
            _call(self.on_message, op, data)

    def _on_push(self, data: bytes) -> None:
        """A frame the remote firmware sent on its own; DitooRemote handles it."""
        if self.on_message:
            _call(self.on_message, OPCODE, data)

    # raw access ---------------------------------------------------------------
    async def send(self, op: int, data: bytes = b"") -> None:
        """Send a command without waiting for a reply."""
        if not self._link:
            raise DitooError("not connected")
        async with self._lock:
            await self._link.send(bytes([op]) + bytes(data))

    async def request(self, op: int, data: bytes = b"", timeout: Optional[float] = None,
                      optional: bool = False,
                      match: Optional[Callable[[bytes], bool]] = None) -> Optional[bytes]:
        """Send a command and return the data of the device's reply with the same
        opcode. optional=True returns None instead of raising on timeout. `match`
        rejects frames that are not this request's reply, such as a late reply to
        an earlier request that timed out."""
        if not self._link:
            raise DitooError("not connected")
        async with self._lock:
            fut = self._loop.create_future()
            entry = (fut, match)
            self._waiters.setdefault(op, []).append(entry)
            try:
                await self._link.send(bytes([op]) + bytes(data))
                return await asyncio.wait_for(fut, timeout or self.reply_timeout)
            except asyncio.TimeoutError:
                if optional:
                    return None
                raise DitooError(f"no reply to 0x{op:02x}") from None
            finally:
                waiters = self._waiters.get(op)
                if waiters and entry in waiters:
                    waiters.remove(entry)

    async def request_all(self, op: int, data: bytes = b"", window: float = 1.0) -> List[bytes]:
        """Send a command and collect every reply with that opcode for `window`
        seconds (some queries answer with one frame per record)."""
        if not self._link:
            raise DitooError("not connected")
        async with self._lock:
            got = self._collectors[op] = []
            try:
                await self._link.send(bytes([op]) + bytes(data))
                await asyncio.sleep(window)
            finally:
                self._collectors.pop(op, None)
        return got

    # system -------------------------------------------------------------------
    async def battery(self) -> Battery:
        """Battery level and charging state. Works without remote mode."""
        return _parse_battery(await self.request(OPCODE, b"\x06", match=_sub_is(0x06)))

    async def firmware_version(self) -> int:
        d = await self.request(0x97)
        return d[1] | d[2] << 8

    async def set_time(self, when: Optional[datetime.datetime] = None) -> None:
        """0x18: year%100, year//100, month, day, hour, minute, second."""
        t = when or datetime.datetime.now()
        await self.request(0x18, bytes([t.year % 100, t.year // 100, t.month, t.day,
                                        t.hour, t.minute, t.second]), optional=True)

    async def set_brightness(self, value: int) -> None:
        await self.send(0x74, bytes([max(0, min(100, value))]))

    async def get_state(self) -> LightState:
        d = await self.request(0x46)
        d = d + bytes(max(0, 20 - len(d)))
        return LightState(d[0], bool(d[1]), d[2], tuple(d[3:6]), d[6], d[7], bool(d[8]), d[9],
                          d[10], bool(d[11]), tuple(d[12:15]), d[15], tuple(d[16:20]), d)

    async def get_name(self) -> str:
        d = await self.request(0x76)
        return d[1:1 + d[0]].decode("utf-8", "replace") if d else ""

    async def set_name(self, suffix: str) -> None:
        """Bluetooth name suffix, at most 16 bytes; "" restores the default name."""
        raw = suffix.encode()[:16]
        await self.send(0x75, bytes([len(raw)]) + raw)

    async def get_temperature_unit(self) -> str:
        d = await self.request(0x2B, b"\xff")
        return "F" if d and d[0] else "C"

    async def set_temperature_unit(self, unit: str) -> None:
        await self.request(0x2B, bytes([1 if unit.upper().startswith("F") else 0]), timeout=0.6, optional=True)

    async def get_low_power(self) -> bool:
        return bool((await self.request(0xB3))[0])

    async def set_low_power(self, on: bool) -> None:
        await self.send(0xB2, bytes([int(on)]))

    async def get_song_display(self) -> bool:
        return bool((await self.request(0x83, b"\xff"))[0])

    async def set_song_display(self, on: bool) -> None:
        await self.request(0x83, bytes([int(on)]), timeout=0.6, optional=True)

    async def get_sound_controls_screen(self) -> bool:
        return bool((await self.request(0xA8))[0])

    async def set_sound_controls_screen(self, on: bool) -> None:
        """Accepted but has no effect on the Ditoo Pro; the getter stays False."""
        await self.send(0xA7, bytes([int(on)]))

    async def get_boot_volume(self) -> int:
        return (await self.request(0xBB, b"\x00\x00"))[0]

    async def set_boot_volume(self, volume: int) -> None:
        await self.request(0xBB, bytes([1, max(0, min(100, volume))]), timeout=0.6, optional=True)

    async def get_boot_channel(self) -> int:
        return (await self.request(0x8A, b"\x00\x00"))[0]

    async def set_boot_channel(self, channel: int) -> None:
        await self.request(0x8A, bytes([1, channel]), timeout=0.6, optional=True)

    async def get_auto_power_off(self) -> int:
        d = await self.request(0xAC)
        return d[0] | d[1] << 8

    async def set_auto_power_off(self, minutes: int) -> None:
        await self.send(0xAB, bytes([minutes & 0xFF, minutes >> 8 & 0xFF]))

    async def get_work_mode(self) -> int:
        return (await self.request(0x13))[0]

    async def set_work_mode(self, mode: int) -> None:
        await self.send(0x05, bytes([mode]))

    async def get_sd_card(self) -> bool:
        return bool((await self.request(0x15))[0])

    async def get_password_status(self) -> bool:
        """True when a Bluetooth connection password is set."""
        return bool((await self.request(0x27, b"\x02"))[0])

    async def set_password(self, pin: Optional[str]) -> None:
        """Set a 4-digit Bluetooth connection password, or remove it with None.
        Every later connection then needs the password; keep it written down."""
        if pin is None:
            await self.request(0x27, b"\x00", timeout=0.6, optional=True)
        else:
            if len(pin) != 4 or not pin.isdigit():
                raise ValueError("pin must be 4 digits")
            await self.request(0x27, b"\x01" + bytes(int(c) for c in pin), timeout=0.6, optional=True)

    # channels (0x45) ------------------------------------------------------------
    async def show_clock(self, style: int = 0, hour24: bool = True, color: RGB = (255, 255, 255),
                         clock: bool = True, weather: bool = True, temperature: bool = True,
                         calendar: bool = False) -> None:
        await self.send(0x45, bytes([CHANNEL_CLOCK, int(hour24), style, int(clock), int(weather),
                                     int(temperature), int(calendar), *color]))

    async def show_light(self, color: RGB = (255, 255, 255), brightness: int = 100,
                         effect: int = 0, on: bool = True) -> None:
        await self.send(0x45, bytes([CHANNEL_LIGHT, *color, brightness, effect, int(on)]))

    async def show_cloud(self) -> None:
        await self.send(0x45, bytes([CHANNEL_CLOUD]))

    async def show_vj(self, effect: int = 0) -> None:
        """Documented by Divoom, but the Ditoo Pro ignores it: no VJ channel."""
        await self.send(0x45, bytes([CHANNEL_VJ, effect]))

    async def show_music(self, mode: int = 0) -> None:
        await self.send(0x45, bytes([CHANNEL_MUSIC, mode]))

    async def show_custom(self, page: Optional[int] = None) -> None:
        """Custom channel; page 0..2 selects one of its three pages (extended command
        BD 17, which also switches to the channel). The device does not report the
        current page, so a caller that shows it has to remember it."""
        if page is None:
            await self.send(0x45, bytes([CHANNEL_CUSTOM]))
            return
        if not 0 <= page < CUSTOM_PAGES:
            raise ValueError(f"custom page must be 0..{CUSTOM_PAGES - 1}")
        await self.send(0xBD, bytes([0x17, page]))

    async def show_lyric(self) -> None:
        await self.send(0x45, bytes([CHANNEL_LYRIC]))

    async def set_channel(self, channel: int) -> None:
        """Switch channel and keep that channel's current settings (clock face
        pages, colors, light effect, music mode), read from the device first."""
        st = await self.get_state()
        if channel == CHANNEL_CLOCK:
            await self.show_clock(st.clock_style, st.hour24, st.clock_color, *[bool(f) for f in st.clock_flags])
        elif channel == CHANNEL_LIGHT:
            await self.show_light(st.light_color, st.light_brightness, st.light_effect, st.light_on)
        elif channel == CHANNEL_MUSIC:
            await self.show_music(st.music_mode)
        elif channel in (CHANNEL_CUSTOM, CHANNEL_LYRIC, CHANNEL_CLOUD):
            await self.send(0x45, bytes([channel]))
        else:
            await self.send(0x45, bytes([channel, 0]))

    async def set_clock_face(self, style: Optional[int] = None, hour24: Optional[bool] = None,
                             color: Optional[RGB] = None, clock: Optional[bool] = None,
                             weather: Optional[bool] = None, temperature: Optional[bool] = None,
                             calendar: Optional[bool] = None) -> None:
        """Change some clock face settings and keep the rest; switches to the clock.
        clock/weather/temperature/calendar are the pages the face cycles through."""
        st = await self.get_state()
        flags = [bool(f) for f in st.clock_flags]
        for i, v in enumerate((clock, weather, temperature, calendar)):
            if v is not None:
                flags[i] = v
        await self.show_clock(st.clock_style if style is None else style,
                              st.hour24 if hour24 is None else hour24,
                              st.clock_color if color is None else tuple(color), *flags)

    async def keyboard_light(self, action: str) -> None:
        """Key backlight (undocumented opcode 0x23): "next" effect, "previous"
        effect, or "toggle" on/off. The device has no absolute on/off command and
        does not report the current state, so "toggle" flips whatever it is now.
        In the firmware (24020 @0x46a9c) 01 steps the effect index up and 00 down,
        through 50 effects with wrap-around; 02 toggles the light."""
        await self.send(0x23, bytes([{"next": 1, "previous": 0, "toggle": 2}[action]]))

    async def set_animation_speed(self, ms: int) -> None:
        await self.send(0x16, bytes([ms & 0xFF, ms >> 8 & 0xFF]))

    # weather ------------------------------------------------------------------
    async def set_weather(self, temperature: int, kind) -> None:
        """Current weather for the clock face. kind: a WEATHER name or number."""
        k = WEATHER[kind] if isinstance(kind, str) else int(kind)
        await self.send(0x5F, bytes([int(round(temperature)) & 0xFF, k]))

    async def set_forecast(self, entries: Sequence[Tuple[int, object]],
                           when: Optional[datetime.datetime] = None) -> None:
        """0x5D: list of (temperature, kind) starting at `when`."""
        t = when or datetime.datetime.now()
        data = bytes([t.year & 0xFF, t.year >> 8, t.month, t.day, t.hour, t.minute, len(entries)])
        for temp, kind in entries:
            data += bytes([int(round(temp)) & 0xFF, WEATHER[kind] if isinstance(kind, str) else int(kind)])
        await self.send(0x5D, data)

    # native scrolling text (0x6E / 0x86 / 0x6C) ---------------------------------
    async def show_native_text(self, text: str, color: RGB = (255, 255, 255),
                               background: RGB = BLACK, speed: int = len(NATIVE_TEXT_SPEEDS)) -> None:
        """Let the device scroll `text` by itself, over the current channel, until
        stop_native_text(). speed: 1 (slowest) to 17 (fastest), see NATIVE_TEXT_SPEEDS.
        At most NATIVE_TEXT_MAX characters.

        The firmware draws the text into a background frame of palette indexes by
        adding 127 to the index of every text pixel, so the palette holds the
        background color at 0..126 and the text color at 127..253. It needs two
        frames: one with the palette (0x407 bytes) and one with the indexes (0x107)."""
        if not text:
            raise ValueError("empty text")
        text = text[:NATIVE_TEXT_MAX]
        level = NATIVE_TEXT_SPEEDS[max(1, min(len(NATIVE_TEXT_SPEEDS), int(speed))) - 1]
        utf = text.encode("utf-16-le")
        palette = bytes(background) * 127 + bytes(color) * 127 + bytes(background) * 2
        first = bytes([0xAA, 0x07, 0x04, 60, 0, 0, 0]) + palette + bytes(256)
        frame = bytes([0xAA, 0x07, 0x01, 60, 0, 1, 0]) + bytes(256)
        await self.send(0x6E, b"\x01")
        await self.send(0x86, bytes([1, len(utf) // 2]) + utf)
        await self.send(0x86, bytes([0, level]))
        for i, f in enumerate((first, frame)):
            await self.send(0x6C, bytes([i, 0, len(f) & 0xFF, len(f) >> 8]) + f)

    async def stop_native_text(self) -> None:
        """End the native scrolling text and show the channel underneath again."""
        await self.send(0x6E, b"\x00")
        await asyncio.sleep(0.5)
        await self.set_channel((await self.get_state()).channel)

    # tools (0x71 / 0x72) --------------------------------------------------------
    async def get_tool(self, tool: int) -> bytes:
        """tool: 0 stopwatch, 1 scoreboard, 2 noise meter, 3 countdown. Returns the
        tool's data as documented (the device prefixes the reply with the tool
        number, which is removed here). Stopwatch: state; scoreboard: on, red LE16,
        blue LE16; noise: 1 running / 2 stopped; countdown: state, minutes, seconds."""
        d = await self.request(0x71, bytes([tool]))
        return d[1:] if d and d[0] == tool else d

    async def stopwatch(self, action: str) -> None:
        """action: "pause", "start" or "reset"."""
        await self.send(0x72, bytes([0, {"pause": 0, "start": 1, "reset": 2}[action]]))

    async def scoreboard_tool(self, red: int, blue: int, on: bool = True) -> None:
        await self.send(0x72, bytes([1, int(on), red & 0xFF, red >> 8 & 0xFF, blue & 0xFF, blue >> 8 & 0xFF]))

    async def noise_meter(self, on: bool) -> None:
        await self.send(0x72, bytes([2, 1 if on else 2]))

    async def countdown(self, minutes: int = 0, seconds: int = 0, action: str = "start") -> None:
        """action: "start", "cancel" or "toggle" (pause/resume)."""
        await self.send(0x72, bytes([3, {"start": 0, "cancel": 1, "toggle": 2}[action],
                                     minutes & 0xFF, min(59, seconds)]))

    # games ----------------------------------------------------------------------
    async def game(self, index: Optional[int]) -> None:
        """Start built-in game `index`, or leave game mode with None."""
        data = b"\x00" if index is None else bytes([1, index])
        await self.request(0xA0, data, timeout=0.6, optional=True)

    async def game_key(self, key: str, down: bool = True) -> None:
        await self.send(0x17 if down else 0x21, bytes([GAME_KEYS[key]]))

    async def game_shake(self) -> None:
        await self.send(0x88)

    # audio ----------------------------------------------------------------------
    async def get_volume(self) -> int:
        return (await self.request(0x09))[0]

    async def set_volume(self, volume: int) -> None:
        await self.send(0x08, bytes([max(0, min(15, volume))]))

    async def get_playing(self) -> bool:
        return bool((await self.request(0x0B))[0])

    async def set_playing(self, playing: bool) -> None:
        await self.send(0x0A, bytes([int(playing)]))

    async def next_track(self, forward: bool = True) -> None:
        await self.send(0x12, bytes([int(forward)]))

    async def sd_music_info(self) -> dict:
        d = await self.request(0xB4)
        return {"position": d[0] | d[1] << 8, "length": d[2] | d[3] << 8, "track": d[4] | d[5] << 8,
                "playing": bool(d[6]), "volume": d[7], "play_mode": d[8]}

    async def sd_track_count(self) -> int:
        d = await self.request(0x7D)
        return d[0] | d[1] << 8

    async def sd_play_track(self, track: int) -> None:
        await self.request(0x11, bytes([track & 0xFF, track >> 8]), timeout=0.6, optional=True)

    async def sd_seek(self, seconds: int) -> None:
        await self.send(0xB8, bytes([seconds & 0xFF, seconds >> 8 & 0xFF]))

    async def sd_play_mode(self, mode: int) -> None:
        """1 list loop, 2 single loop, 3 shuffle."""
        await self.send(0xB9, bytes([mode]))

    # alarms and memorial days ----------------------------------------------------
    async def get_alarms(self) -> List[Alarm]:
        d = await self.request(0x42)
        out = []
        for i in range(0, len(d) - 9, 10):
            r = d[i:i + 10]
            out.append(Alarm(r[0], bool(r[1]), r[2], r[3], r[4], r[5], r[6], r[7] | r[8] << 8, r[9]))
        return out

    async def set_alarm(self, alarm: Alarm) -> None:
        await self.send(0x43, alarm.payload())

    async def alarm_preview(self, on: bool, mode: int = 0, volume: int = 50) -> None:
        await self.send(0xA5, bytes([int(on), mode, volume]))

    async def get_memorials(self) -> List[dict]:
        """The device answers with one frame per record."""
        out = []
        for r in await self.request_all(0x53):
            if len(r) < 39:
                continue
            out.append({"index": r[0], "on": bool(r[1]), "month": r[2], "day": r[3], "hour": r[4],
                        "minute": r[5], "animation": bool(r[6]),
                        "title": r[7:39].split(b"\x00")[0].decode("utf-8", "replace")})
        return out

    async def set_memorial(self, index: int, on: bool, month: int, day: int, hour: int, minute: int,
                           title: str = "") -> None:
        name = title.encode()[:32].ljust(32, b"\x00")
        await self.send(0x54, bytes([index, int(on), month, day, hour, minute, 0]) + name)

    async def get_time_plan(self) -> bool:
        return bool((await self.request(0x57, b"\x00"))[0])

    # sleep ------------------------------------------------------------------------
    async def get_sleep(self) -> SleepSettings:
        d = await self.request(0xA2)
        return SleepSettings(d[0], d[1], bool(d[2]), d[3] | d[4] << 8, d[5], tuple(d[6:9]), d[9])

    async def set_sleep(self, settings: SleepSettings) -> None:
        await self.send(0x40, settings.payload())

    async def set_sleep_color(self, color: RGB) -> None:
        await self.send(0xAD, bytes(color))

    async def set_sleep_light(self, light: int) -> None:
        await self.send(0xAE, bytes([light]))

    # stock drawing pad ------------------------------------------------------------
    async def drawpad_enter(self, color: RGB = BLACK) -> None:
        await self.send(0x6F, bytes(color))

    async def drawpad_points(self, color: RGB, offsets: Sequence[int]) -> None:
        """Paint pixels (offset = y * 16 + x) on the stock drawing pad."""
        for i in range(0, len(offsets), 200):
            part = offsets[i:i + 200]
            await self.send(0x58, bytes([*color, len(part), *part]))

    async def drawpad_exit(self) -> None:
        await self.send(0x5A)


def _call(fn, *args):
    """Run a sync or async callback from the event loop without letting it raise."""
    try:
        res = fn(*args)
        if inspect.isawaitable(res):
            asyncio.ensure_future(res)
    except Exception:
        traceback.print_exc()


# ---- remote mode ----------------------------------------------------------------

class DitooRemote(Ditoo):
    """Ditoo plus the remote mode of the custom firmware (24025-remote).

    Drawing mode (`start`/`stop`): the host draws the screen and receives the keys.
    Key listening (`set_key_listening`): the host receives the keys
    in every menu while the device keeps showing its own screen. In both, keys go
    only to the host, not to the device's menus. They are independent: either or
    both can be on. `async with DitooRemote()` connects and starts drawing mode;
    leaving the block turns both off."""

    def __init__(self, target=DEFAULT_ADDRESS, poll_interval: Optional[float] = None,
                 reply_timeout: float = 1.5, connector=None):
        super().__init__(target, reply_timeout, connector)
        self.poll_interval = POLL_KEEPALIVE if poll_interval is None else poll_interval
        self.firmware: Optional[int] = None
        self.active = False          # drawing mode
        self.listening = False       # key listening
        self.dropped = 0
        self.on_key: Optional[Callable[[KeyEvent], object]] = None   # sync or async
        self.on_exit: Optional[Callable[[], object]] = None          # drawing ended on the device
        self.on_listen_end: Optional[Callable[[], object]] = None    # listening ended on the device
        self._events: "asyncio.Queue[KeyEvent]" = asyncio.Queue()
        self._poller: Optional[asyncio.Task] = None
        self._last_command = 0.0     # time.monotonic() of the last answered 0xF0 command
        self._last_frame: Optional[Tuple[RGB, ...]] = None

    async def connect(self) -> None:
        await super().connect()
        try:
            self.firmware = await self.firmware_version()
        except DitooError:
            self.firmware = None

    @property
    def pushes_keys(self) -> bool:
        """True when the device runs the custom firmware (it pushes key events)."""
        return self.firmware is not None and self.firmware >= REMOTE_FIRMWARE

    async def close(self) -> None:
        """Turn drawing mode and key listening off (if on) and disconnect."""
        try:
            if self.listening and self.connected:
                await self.set_key_listening(False)
            if self.active and self.connected:
                await self.stop()
        finally:
            await self._stop_poller()
            await super().close()

    async def __aenter__(self) -> "DitooRemote":
        await self.connect()
        await self.start()
        return self

    def _lost(self) -> None:
        self.active = self.listening = False
        super()._lost()

    def _on_frame(self, op: int, ack: int, data: bytes) -> None:
        # Every 0xF0 frame, reply or push, updates the state and delivers its keys
        # here, in the order the frames arrive, before any request sees it.
        if op == OPCODE and len(data) >= 5:
            self._apply(_reply_from_raw(data))
        super()._on_frame(op, ack, data)

    def _on_push(self, data: bytes) -> None:
        pass                                # already applied in _on_frame

    # remote mode --------------------------------------------------------------
    async def start(self) -> Reply:
        """Take the screen (black) and start polling for keys."""
        r = await self.command(bytes([OPCODE, 0x01]))
        self._last_frame = tuple([BLACK] * 256)
        self._ensure_poller()
        return r

    async def stop(self) -> Reply:
        """Give the screen back to the stock firmware."""
        if not self.listening:
            await self._stop_poller()
        return await self.command(bytes([OPCODE, 0x02]))

    async def set_key_listening(self, on: bool) -> Reply:
        """Receive the keys in every menu; the device's own menus stop reacting to
        them until listening is turned off again. Holding a key for 4 s, a lost BLE
        link or 60 s without any command turns listening off on the device."""
        if not on and not self.active:
            await self._stop_poller()
        r = await self.command(bytes([OPCODE, 0x07, int(on)]))
        if on and not r.listening:
            raise RemoteError("key listening not supported: the custom firmware 24025-remote is needed (./ditoo install)")
        if on:
            self._ensure_poller()
        return r

    def _ensure_poller(self) -> None:
        if self._poller is None or self._poller.done():
            self._poller = asyncio.create_task(self._poll_loop())

    async def poll(self) -> Reply:
        return await self.command(bytes([OPCODE, 0x00]))

    async def battery(self) -> Battery:
        """Battery level and charging state; key events in the reply are delivered as usual."""
        try:
            raw = await self.request(OPCODE, b"\x06", match=_sub_is(0x06))
        except DitooError as e:
            raise RemoteError(str(e)) from None
        return _parse_battery(raw)

    async def push_stats(self) -> PushStats:
        """Key push and stack counters; changes nothing on the device."""
        try:
            raw = await self.request(OPCODE, b"\x09", match=_sub_is(0x09))
        except DitooError as e:
            raise RemoteError(str(e)) from None
        try:
            return _parse_push_stats(raw)
        except DitooError as e:
            raise RemoteError(str(e)) from None

    # drawing ------------------------------------------------------------------
    async def show(self, image, force: bool = False) -> None:
        """Present a Canvas or 256 (r, g, b) tuples. Uses one palette packet when
        the image has at most 16 colors, otherwise 4 row packets. An image equal
        to the last one sent is skipped unless force=True."""
        pixels = tuple(tuple(p) for p in (image.pixels if isinstance(image, Canvas) else image))
        if len(pixels) != 256:
            raise ValueError("need 256 pixels")
        if not force and pixels == self._last_frame:
            return
        payload = palette_payload(pixels)
        for p in ([payload] if payload else row_payloads(pixels)):
            r = await self.command(p)
            if r.status:
                raise RemoteError(f"frame rejected: {STATUS_NAMES.get(r.status, r.status)}")
        self._last_frame = pixels

    async def fill(self, color: RGB) -> None:
        r = await self.command(bytes([OPCODE, 0x05, *color]))
        if r.status:
            raise RemoteError(f"fill rejected: {STATUS_NAMES.get(r.status, r.status)}")
        self._last_frame = tuple([tuple(color)] * 256)

    # keys ---------------------------------------------------------------------
    async def keys(self):
        """Async iterator over key events. Ends when drawing mode and key
        listening are both off."""
        while True:
            get = asyncio.ensure_future(self._events.get())
            while not get.done():
                if not self.active and not self.listening and self._events.empty():
                    get.cancel(); return
                await asyncio.wait([get], timeout=0.2)
            yield get.result()

    async def next_key(self, timeout: Optional[float] = None) -> Optional[KeyEvent]:
        try:
            return await asyncio.wait_for(self._events.get(), timeout)
        except asyncio.TimeoutError:
            return None

    # low level ----------------------------------------------------------------
    async def command(self, payload: bytes) -> Reply:
        """Send one 0xF0 payload (starting with F0) and wait for its reply. The
        reply's keys and mode were already applied when it arrived."""
        try:
            raw = await self.request(payload[0], payload[1:],
                                     match=_sub_is(payload[1]) if payload[0] == OPCODE and len(payload) > 1 else None)
            self._last_command = time.monotonic()
        except DitooError as e:
            raise RemoteError(str(e)) from None
        return _reply_from_raw(raw)

    def _apply(self, r: Reply) -> None:
        """Take the keys and mode from a reply or push. Sync callbacks run right
        here, async ones are started in order; neither may block the next frame."""
        self.dropped = r.dropped
        for ev in r.keys:
            self._events.put_nowait(ev)
            if self.on_key:
                _call(self.on_key, ev)
        was_drawing, self.active = self.active, r.active
        was_listening, self.listening = self.listening, r.listening
        if was_drawing and not r.active and r.sub != 0x02 and self.on_exit:
            _call(self.on_exit)
        if was_listening and not r.listening and r.sub != 0x07 and self.on_listen_end:
            _call(self.on_listen_end)

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self.poll_interval)
            if not (self.active or self.listening) or not self.connected:
                return
            if time.monotonic() - self._last_command < self.poll_interval:
                continue        # frames and other 0xF0 commands already feed the watchdog
            try:
                await self.poll()
            except RemoteError:
                pass            # a lost reply is retried on the next tick
            except Exception:
                if not self.connected:
                    return      # link gone; the next connection starts its own poller
                # anything else (a Bluetooth error while connected) is retried on the next tick

    async def _stop_poller(self) -> None:
        if self._poller and not self._poller.done() and self._poller is not asyncio.current_task():
            self._poller.cancel()
            try:
                await self._poller
            except asyncio.CancelledError:
                pass
        self._poller = None
