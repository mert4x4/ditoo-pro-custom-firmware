"""Connection, state and display handling for one Ditoo Pro.

Everything device-specific is in ditoo_api (the core API, copied here from
client/ditoo_api.py by sync_core.sh). This module only adapts it to Home
Assistant: Bluetooth connection through HA, periodic state refresh, remote mode
as a switch, key events on the HA bus, and a small display manager that keeps
text scrolling while HA owns the screen.
"""
from __future__ import annotations

import asyncio
import functools
import math
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Optional

import bleak_retry_connector
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, EVENT_KEY
from .ditoo_api import (
    BLACK, CHANNEL_CUSTOM, Battery, Canvas, DitooError, DitooRemote, KeyEvent, LightState, RemoteError,
)

_LOGGER = logging.getLogger(__name__)
ERRORS = (DitooError, RemoteError, BleakError, asyncio.TimeoutError)

# BLE connection parameters: min and max interval (1.25 ms units), latency (events
# the device may skip while it has nothing to send) and supervision timeout (10 ms
# units). Home Assistant connects with 10 ms and no latency, which wakes the link
# 100 times a second even when nothing is sent.
# Idle and key listening: the device wakes every 150-300 ms when quiet, but a key
# push still goes out at the next event (15-30 ms).
# Supervision timeout 6 s: under a fast frame stream the device can stop answering for
# about 2 s (seen on 2026-10-01: Connection Timeout with a 2 s timeout); Home
# Assistant's own default is 8 s.
LINK_IDLE = (12, 24, 9, 600)
# Drawing mode: every frame is a command that waits for its reply, so no latency.
LINK_DRAWING = (6, 12, 0, 600)
# Shortest time per frame when scrolling text; the Divoom app streams at 60 ms too.
MIN_FRAME_S = 0.06


def ha_errors(fn):
    """Report device and Bluetooth failures of user actions as HomeAssistantError."""
    @functools.wraps(fn)
    async def run(self, *args, **kwargs):
        try:
            return await fn(self, *args, **kwargs)
        except ERRORS as err:
            raise HomeAssistantError(f"Ditoo Pro: {err}") from err
    return run


@dataclass
class DitooData:
    connected: bool = False
    remote: bool = False                   # drawing mode
    listening: bool = False                # key listening
    state: Optional[LightState] = None
    volume: Optional[int] = None
    battery: Optional[Battery] = None      # None without the custom firmware


class DitooCoordinator(DataUpdateCoordinator[DitooData]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, address: str) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} {address}",
                         update_interval=timedelta(seconds=60))
        self.address = address
        self.dev: Optional[DitooRemote] = None
        self.firmware: Optional[int] = None
        self.message = ""
        # Connection switch; off releases the device for the phone app or other hosts.
        self.enabled: bool = entry.options.get("connect", True)
        self.listen_wanted: bool = entry.options.get("listen", False)
        self.listen_supported: Optional[bool] = None   # known after the first connect
        self._remote_wanted = False
        self._connect_lock = asyncio.Lock()
        self._display_task: Optional[asyncio.Task] = None
        self._key_listeners: list[Callable[[KeyEvent], None]] = []
        self._link_params: Optional[tuple] = None      # last connection parameters asked for
        self._text_options: dict = {}                  # options of the current message, for redraws
        self._frame_s = 0.09                           # average time the device takes per frame
        self.native_text = False                       # the device scrolls a native text
        self._native_task: Optional[asyncio.Task] = None   # ends a native text after its duration
        # Custom channel page (0..2) last chosen here. The device does not report it,
        # so None means unknown (after a restart, or when it was changed on the device).
        self.custom_page: Optional[int] = None

    # connection -------------------------------------------------------------------
    async def _connector(self, target, lost):
        # Looked up at connect time: Home Assistant replaces this class with its own
        # wrapper (which can set connection parameters) when Bluetooth starts.
        return await establish_connection(
            bleak_retry_connector.BleakClientWithServiceCache, target, "Ditoo Pro",
            disconnected_callback=lost,
            ble_device_callback=lambda: bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True) or target)

    async def async_connect(self) -> DitooRemote:
        if self.dev and self.dev.connected:
            return self.dev
        if not self.enabled:
            raise DitooError("connection is switched off")
        async with self._connect_lock:
            if self.dev and self.dev.connected:
                return self.dev
            ble_device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
            if ble_device is None:
                raise UpdateFailed(f"Ditoo Pro {self.address} is not in range of a Bluetooth adapter")
            dev = DitooRemote(ble_device, connector=self._connector)
            dev.on_key = self._on_key
            dev.on_exit = self._on_remote_exit
            dev.on_listen_end = self._on_listen_end
            dev.on_disconnect = self._on_disconnect
            await dev.connect()
            self.dev = dev
            self._link_params = None
            self.firmware = await dev.firmware_version()
            _LOGGER.debug("connected to %s, firmware %s", self.address, self.firmware)
            if self.listen_wanted:
                await self._apply_listening(dev, True)
            if self._remote_wanted:
                await dev.start()
                await self._redraw()
            await self._apply_link_params(dev)
            return dev

    async def _apply_link_params(self, dev: Optional[DitooRemote] = None) -> None:
        """Fast link while drawing, slow and quiet otherwise. Asked only on change."""
        dev = dev or self.dev
        if not dev or not dev.connected:
            return
        params = LINK_DRAWING if dev.active else LINK_IDLE
        if params == self._link_params:
            return
        self._link_params = params              # asked once per change, also if unsupported
        try:
            ok = await dev.set_connection_params(*params)
            _LOGGER.debug("Ditoo Pro %s: connection parameters %s %s", self.address, params,
                          "requested" if ok else "not supported by this Bluetooth stack")
        except Exception as err:                # never let this break a user action
            _LOGGER.debug("Ditoo Pro %s: connection parameters not set: %s", self.address, err)

    async def async_shutdown(self) -> None:
        self._cancel_display()
        if self.dev:
            try:
                await self.dev.close()      # leaves remote mode first
            except ERRORS:
                pass
            self.dev = None
        await super().async_shutdown()

    @ha_errors
    async def async_set_connection(self, on: bool) -> None:
        """Connect (and keep reconnecting), or disconnect and stay disconnected.
        The choice is stored in the config entry, so it survives restarts."""
        self.enabled = on
        self.hass.config_entries.async_update_entry(
            self.config_entry, options={**self.config_entry.options, "connect": on})
        if on:
            await self.async_refresh()
        else:
            self._cancel_display()
            dev, self.dev = self.dev, None
            if dev:
                try:
                    await dev.close()       # leaves remote mode first; remembers it for later
                except ERRORS:
                    pass
            self.async_set_updated_data(DitooData(connected=False))

    @callback
    def _on_disconnect(self) -> None:
        _LOGGER.debug("Ditoo Pro %s disconnected", self.address)
        self._cancel_display()
        self.async_set_updated_data(DitooData(connected=False))
        if self.enabled:
            # reconnect soon; keeps remote mode if it was on
            self.hass.loop.call_later(5, lambda: self.hass.async_create_task(self.async_request_refresh()))

    async def _async_update_data(self) -> DitooData:
        if not self.enabled:
            return DitooData(connected=False)
        if self._display_task and not self._display_task.done() and self.data and self.dev and self.dev.connected:
            # A text or image is being streamed: queries would stall its frames.
            return self.data
        try:
            dev = await self.async_connect()
            state = await dev.get_state()
            volume = await dev.get_volume()
            battery = await self._read_battery(dev)
        except ERRORS as err:
            raise UpdateFailed(f"Ditoo Pro {self.address}: {err}") from err
        return DitooData(connected=True, remote=dev.active, listening=dev.listening,
                         state=state, volume=volume, battery=battery)

    async def _read_battery(self, dev: DitooRemote) -> Optional[Battery]:
        try:
            return await dev.battery()
        except RemoteError as err:
            if "firmware" in str(err):       # stock firmware: no battery readout
                return None
            raise

    def _publish(self, **changes) -> None:
        data = self.data or DitooData()
        for k, v in changes.items():
            setattr(data, k, v)
        data.remote = bool(self.dev and self.dev.active)
        data.listening = bool(self.dev and self.dev.listening)
        self.async_set_updated_data(data)
        if self.dev and (LINK_DRAWING if self.dev.active else LINK_IDLE) != self._link_params:
            self.hass.async_create_task(self._apply_link_params())

    # key listening ------------------------------------------------------------------
    def _store(self, **options) -> None:
        self.hass.config_entries.async_update_entry(
            self.config_entry, options={**self.config_entry.options, **options})

    async def _apply_listening(self, dev: DitooRemote, on: bool) -> None:
        try:
            await dev.set_key_listening(on)
            self.listen_supported = True
        except RemoteError as err:
            if "firmware" not in str(err):
                raise
            self.listen_supported = False
            _LOGGER.warning("Ditoo Pro %s: %s", self.address, err)

    @ha_errors
    async def async_set_listening(self, on: bool) -> None:
        """Keys go to Home Assistant in every menu (and not to the device's menus)."""
        dev = await self.async_connect()
        await self._apply_listening(dev, on)
        if on and self.listen_supported is False:
            self._publish()                 # the switch becomes unavailable
            raise DitooError("key listening needs the custom firmware 24025-remote")
        self.listen_wanted = on
        self._store(listen=on)
        self._publish()

    def _on_listen_end(self) -> None:
        # Listening ended on the device while connected: a key was held for 4 s.
        self.listen_wanted = False
        self._store(listen=False)
        self._publish()

    # keys ---------------------------------------------------------------------------
    @callback
    def async_add_key_listener(self, listener: Callable[[KeyEvent], None]) -> Callable[[], None]:
        self._key_listeners.append(listener)
        return lambda: self._key_listeners.remove(listener)

    def _on_key(self, ev: KeyEvent) -> None:
        self.hass.bus.async_fire(EVENT_KEY, {
            "address": self.address, "key": ev.name, "key_number": ev.key,
            "event": ev.kind, "event_number": ev.event})
        for listener in list(self._key_listeners):
            listener(ev)

    def _on_remote_exit(self) -> None:
        # remote mode ended on the device (key held 4 s or watchdog)
        self._remote_wanted = False
        self._cancel_display()
        self._publish()

    # remote mode --------------------------------------------------------------------
    @ha_errors
    async def async_set_remote(self, on: bool) -> None:
        dev = await self.async_connect()
        self._remote_wanted = on
        if on:
            await self._end_native_text(dev)
            if not dev.active:
                await dev.start()
            await self._redraw()
        else:
            self._cancel_display()
            if dev.active:
                await dev.stop()
        await self._apply_link_params(dev)
        self._publish()

    async def _ensure_remote(self) -> DitooRemote:
        dev = await self.async_connect()
        await self._end_native_text(dev)
        self._remote_wanted = True
        if not dev.active:
            await dev.start()
        await self._apply_link_params(dev)
        return dev

    # native scrolling text ------------------------------------------------------------
    @ha_errors
    async def async_show_native_text(self, text: str, color=(255, 255, 255), background=BLACK,
                                     speed: int = 17, duration: Optional[float] = None) -> None:
        """The device scrolls the text by itself (no streaming); see
        DitooRemote.show_native_text. Ends after `duration` seconds, or when another
        channel, drawing or text action takes the screen."""
        dev = await self.async_connect()
        if dev.active:
            await self.async_set_remote(False)
        self._cancel_native_task()
        await dev.show_native_text(text, tuple(color), tuple(background), speed)
        self.native_text = True
        if duration:
            async def end_later():
                await asyncio.sleep(duration)
                await self.async_stop_native_text()
            self._native_task = self.config_entry.async_create_background_task(
                self.hass, end_later(), f"{DOMAIN} native text {self.address}")
        self._publish()

    @ha_errors
    async def async_stop_native_text(self) -> None:
        dev = await self.async_connect()
        if self._native_task is not asyncio.current_task():
            self._cancel_native_task()
        self._native_task = None
        await dev.stop_native_text()
        self.native_text = False
        self._publish(state=await dev.get_state())

    def _cancel_native_task(self) -> None:
        task, self._native_task = self._native_task, None
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()

    async def _end_native_text(self, dev: DitooRemote) -> None:
        """Another action takes the screen: switch the native text off (that action
        then shows what it wants, so the channel is not restored here)."""
        if self.native_text:
            self._cancel_native_task()
            self.native_text = False
            await dev.send(0x6E, b"\x00")

    # display ------------------------------------------------------------------------
    def _cancel_display(self) -> None:
        task, self._display_task = self._display_task, None
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()

    def _start_display(self, coro) -> None:
        self._cancel_display()
        self._display_task = self.config_entry.async_create_background_task(
            self.hass, coro, f"{DOMAIN} display {self.address}")

    async def _redraw(self) -> None:
        """Show the current message again (after reconnect or turning remote on)."""
        if self.message:
            await self.async_show_text(self.message, **self._text_options)
        elif self.dev and self.dev.active:
            await self.dev.fill(BLACK)

    @ha_errors
    async def async_show_text(self, text: str, color=(255, 255, 255), background=BLACK,
                              speed: float = 12.0, duration: Optional[float] = None, y: int = 5,
                              start: str = "right", mode: str = "scroll",
                              word_seconds: float = 1.0) -> None:
        """mode "scroll": text that fits is centered, wider text scrolls at `speed` px/s.
        mode "words": one word at a time; a word that fits is centered for
        `word_seconds`, a wider one scrolls; then the next word, then from the start.
        start "right": each scroll pass enters from the right edge; "left": it starts
        with the first letter at the left edge. After `duration` seconds remote mode
        ends and the stock screen returns."""
        self.message = text
        self._text_options = dict(color=color, background=background, speed=speed, y=y,
                                  start=start, mode=mode, word_seconds=word_seconds)
        dev = await self._ensure_remote()
        parts = text.split() if mode == "words" else [text]

        def frame(s: str, x: int) -> Canvas:
            c = Canvas(tuple(background))
            c.text(x, y, s, tuple(color))
            return c

        def centered(s: str) -> Canvas:
            return frame(s, (16 - Canvas.text_width(s)) // 2)

        async def run():
            loop = asyncio.get_running_loop()
            end = loop.time() + duration if duration else None

            def over() -> bool:
                return end is not None and loop.time() >= end

            async def scroll(s: str) -> None:
                """One pass of `s` across the screen at `speed` px/s with an even rhythm:
                the same number of pixels every frame and the same time between frames.
                The frame period is chosen once per pass from the measured frame time,
                so a slow device gets bigger steps instead of uneven ones; a late frame
                only shifts the rhythm, it never makes the text jump."""
                period = max(MIN_FRAME_S, self._frame_s * 1.15)
                px = max(1, math.ceil(speed * period))
                period = px / speed
                x, x_end = (16 if start == "right" else 0), -Canvas.text_width(s) - 1
                due = loop.time()
                while x >= x_end:
                    t = loop.time()
                    await dev.show(frame(s, x))
                    self._frame_s = 0.8 * self._frame_s + 0.2 * (loop.time() - t)
                    x -= px
                    due += period
                    if due > loop.time():
                        await asyncio.sleep(due - loop.time())
                    else:
                        due = loop.time()
                    if over():
                        return

            async def hold(s: str) -> None:
                await dev.show(centered(s))
                await asyncio.sleep(word_seconds if end is None else max(0.0, min(word_seconds, end - loop.time())))

            try:
                if len(parts) <= 1 and Canvas.text_width(parts[0] if parts else "") <= 16:
                    await dev.show(centered(parts[0] if parts else ""))
                    if duration:
                        await asyncio.sleep(duration)
                else:
                    while not over():
                        for s in parts:
                            if Canvas.text_width(s) <= 16 and len(parts) > 1:
                                await hold(s)
                            else:
                                await scroll(s)
                            if over():
                                break
                if duration:
                    self.message = ""
                    await self.async_set_remote(False)
            except ERRORS as err:
                _LOGGER.debug("display stopped: %s", err)

        self._start_display(run())
        self._publish()

    @ha_errors
    async def async_show_image(self, path: str, duration: Optional[float] = None) -> None:
        pixels = await self.hass.async_add_executor_job(_load_image, path)
        dev = await self._ensure_remote()
        self._cancel_display()
        self.message = ""
        await dev.show(pixels)
        self._publish()
        if duration:
            async def later():
                await asyncio.sleep(duration)
                await self.async_set_remote(False)
            self._start_display(later())

    @ha_errors
    async def async_fill(self, color) -> None:
        dev = await self._ensure_remote()
        self._cancel_display()
        self.message = ""
        await dev.fill(tuple(color))
        self._publish()

    # stock features -----------------------------------------------------------------
    @ha_errors
    async def async_set_channel(self, channel: int, page: Optional[int] = None) -> None:
        """page 0..2 picks a page of the custom channel."""
        dev = await self.async_connect()
        await self._end_native_text(dev)
        if dev.active:
            await self.async_set_remote(False)
        if channel == CHANNEL_CUSTOM and page is not None:
            await dev.show_custom(page)
            self.custom_page = page
        else:
            await dev.set_channel(channel)
            if channel == CHANNEL_CUSTOM:
                self.custom_page = None
        await asyncio.sleep(0.5)
        self._publish(state=await dev.get_state())

    @ha_errors
    async def async_set_brightness(self, value: int) -> None:
        dev = await self.async_connect()
        await dev.set_brightness(value)
        self._publish(state=await dev.get_state())

    @ha_errors
    async def async_set_volume(self, value: int) -> None:
        dev = await self.async_connect()
        await dev.set_volume(value)
        self._publish(volume=await dev.get_volume())

    @ha_errors
    async def async_keyboard_light(self, action: str) -> None:
        dev = await self.async_connect()
        await dev.keyboard_light(action)

    @ha_errors
    async def async_sync_time(self) -> None:
        dev = await self.async_connect()
        await dev.set_time()

    @ha_errors
    async def async_set_weather(self, temperature: float, kind: str) -> None:
        dev = await self.async_connect()
        await dev.set_weather(round(temperature), kind)


def _load_image(path: str):
    """Load an image file as 256 RGB tuples (runs in the executor)."""
    from PIL import Image
    im = Image.open(path).convert("RGBA")
    if im.size != (16, 16):
        im = im.resize((16, 16), Image.NEAREST)
    bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
    raw = Image.alpha_composite(bg, im).convert("RGB").tobytes()
    return [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]
