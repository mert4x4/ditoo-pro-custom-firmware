# Ditoo Pro for Home Assistant

Custom integration for a Divoom Ditoo Pro over Bluetooth LE. Drawing mode, messages, keys and battery need the custom firmware from this repository (`./ditoo install`, see the main README); channel, brightness, volume, time, weather and native text also work on stock firmware. It is a thin layer over the core API in `../client/ditoo_api.py`; all device protocol code lives there.

## Layout

- `custom_components/ditoo_pro/`: the integration. `ditoo_api.py` inside it is a generated copy of the core API.
- `sync_core.sh`: refreshes that copy. Run it after every change to `client/ditoo_api.py`.

## Install

1. Copy `custom_components/ditoo_pro` to `/config/custom_components/ditoo_pro` on the Home Assistant host.
2. Restart Home Assistant.
3. Home Assistant needs a working Bluetooth adapter (Settings > Devices & services > Bluetooth), and the Ditoo must be in its range.
4. The Ditoo accepts one BLE connection at a time. Close the Divoom phone app (or turn off Bluetooth on the phone) and stop any other program connected to it. Later, turn the Connection switch off whenever something else needs the device.
5. The device is discovered as `DitooPro-Light`. Accept the discovery, or add "Divoom Ditoo Pro" under Settings > Devices & services.

No extra Python packages are installed: bleak, bleak-retry-connector and Pillow already ship with Home Assistant.

## Entities

| Entity | What it does |
|---|---|
| `select.ditoo_pro_channel` | Stock channel: clock, light, cloud, music, custom 1-3, lyric display. Turns drawing mode off. Keeps each channel's own settings (clock face pages and color, light color and effect, music mode). Custom pages are selected with the extended command `BD 17 <page>`; the device does not report the current page, so the select shows a custom page only when it was chosen here since Home Assistant started. |
| `number.ditoo_pro_brightness` | 0 to 100 |
| `number.ditoo_pro_volume` | 0 to 15 |
| `switch.ditoo_pro_drawing_mode` | On: Home Assistant owns the screen and receives the keys. |
| `switch.ditoo_pro_key_listening` | On: every key goes to Home Assistant in every menu while the device keeps its own screen; the device's menus ignore the keys meanwhile. Remembered across restarts. |
| `switch.ditoo_pro_connection` | Off: disconnects and stops reconnecting, so the phone app or another program can connect. On: connects again. Remembered across restarts; stays available while the device is off. |
| `text.ditoo_pro_message` | Setting it shows the text (remote mode on). Empty text turns remote mode off. |
| `event.ditoo_pro_<key>_key` | One per key: m, plus, minus, left, right, arm, brightness, little. Event types: down, up, long, repeat, long-up. |
| `button.ditoo_pro_sync_time` | Sets the device clock to Home Assistant's time. |
| `sensor.ditoo_pro_firmware` | Firmware version (diagnostic). |
| `sensor.ditoo_pro_battery` | Battery in %. Approximate: the device measures 8 steps (attribute `level`, 0 to 7), so values move in steps of about 14 %. |
| `binary_sensor.ditoo_pro_charging` | On while charging. |
| `binary_sensor.ditoo_pro_external_power` | On while USB power is connected. With power on and charging off, the battery is full. |

State (channel, brightness, volume, battery) is read from the device every 60 seconds and after each change. After a disconnect the integration reconnects by itself and turns remote mode back on if it was on. The only exception is the Connection switch: while it is off, nothing reconnects and the other entities show as unavailable.

## Keys

Keys reach Home Assistant while drawing mode or key listening is on. In both cases they go only to Home Assistant: the device's own menus do not react to them. With both off, the keys work on the device as usual.

Each key event is also fired on the event bus as `ditoo_pro_key` with `key` (name), `key_number`, `event` (down, up, long, repeat, long-up), `event_number` and `address`.

Holding any key for 4 seconds turns drawing mode and key listening off on the device (escape hatch), and the switches follow. If Home Assistant stops or loses the connection, both end on the device within 5 seconds; after reconnecting, the integration turns them back on if their switches are still on.

## Services

| Service | Fields |
|---|---|
| `ditoo_pro.show_text` | `text`, `color`, `background`, `speed` (px/s, text wider than 16 px scrolls; the position follows the clock, so when the device cannot draw a frame per pixel the text moves several pixels per frame and keeps the speed; at most one frame per 60 ms), `duration` (s, then the stock screen returns), `y` (top row), `start` (`right`: each pass enters from the right edge, the default; `left`: each pass starts with the first letter at the left edge), `mode` (`scroll`: the whole text scrolls, the default; `words`: one word at a time, a word that fits is centered for `word_seconds`, a longer word scrolls, then the next word) |
| `ditoo_pro.show_native_text` | `text` (up to 64 characters), `color`, `background`, `speed` (1-17, the device's own speed steps; 17 is the fastest), `duration` (s; empty: until stopped). The device scrolls the text itself with its built-in font over the current channel, so nothing is streamed and the scroll is smooth. It loops until stopped; a channel, drawing or text action also ends it. |
| `ditoo_pro.stop_native_text` | Ends the native text and shows the channel again. |
| `ditoo_pro.show_image` | `path` (PNG or GIF, first frame, scaled to 16x16; the folder must be in `allowlist_external_dirs`), `duration` |
| `ditoo_pro.fill` | `color` |
| `ditoo_pro.set_weather` | `weather_entity`, or `temperature` and `condition` (clear, cloudy, rain, snow, fog, thunderstorm). Shown on the stock clock face. |
| `ditoo_pro.sync_time` | none |
| `ditoo_pro.connect` | none (same as turning the Connection switch on) |
| `ditoo_pro.disconnect` | none (same as turning it off) |

All services take an optional `device_id`, needed only with more than one Ditoo Pro.

The font is 3x5 pixels: letters (shown as uppercase), digits and `: . - + / % ° ! ? = _`.

## Examples

Toggle a lamp with the left key:

```yaml
triggers:
  - trigger: event
    event_type: ditoo_pro_key
    event_data: { key: left, event: down }
actions:
  - action: light.toggle
    target: { entity_id: light.desk }
```

Show the outside temperature whenever it changes:

```yaml
triggers:
  - trigger: state
    entity_id: sensor.outside_temperature
actions:
  - action: ditoo_pro.show_text
    data:
      text: "{{ states('sensor.outside_temperature') | round(0) | int }}°"
      color: [255, 160, 0]
```

Release the Ditoo while the phone app is used (turn it back on with `ditoo_pro.connect`):

```yaml
actions:
  - action: ditoo_pro.disconnect
```

Keep the clock face weather up to date:

```yaml
triggers:
  - trigger: time_pattern
    minutes: "/30"
actions:
  - action: ditoo_pro.set_weather
    data: { weather_entity: weather.home }
```

## Tested

- Home Assistant 2026.9 on a Raspberry Pi (Docker, BlueZ) with the device on the custom firmware 24025-remote.

## Device notes

- Channel 3 (VJ) and the "screen follows ambient sound" setting (0xA7) are documented by Divoom but have no effect on the Ditoo Pro, so they are left out.
- Bluetooth link: Home Assistant connects with a 10 ms interval and no latency, so the link wakes 100 times a second even when idle. After connecting, the integration asks for 15-30 ms with latency 9 and a 6 s supervision timeout (`LINK_IDLE` in `coordinator.py`): the device can sleep through quiet periods, key pushes still go out at the next event, and a lost link is noticed in 6 s instead of 8 s (2 s was too short: a fast frame stream can stall the device for about 2 s). While drawing mode is on it asks for 7.5-15 ms without latency (`LINK_DRAWING`) so frames are not delayed. Checked with `btmon`: the device accepts these parameters.
