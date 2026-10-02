"""Send this computer's time and the current weather, and show the clock face.

usage: ./ditoo run client/examples/clock_weather.py LAT LON      (e.g. 51.51 -0.13)

Uses stock commands only (no remote mode): sets the time (0x18) and the current
weather (0x5F), then switches to the clock face keeping its settings. Whether
the weather is shown depends on the clock face pages (weather/temperature).
Weather comes from api.open-meteo.com (no key needed).
"""
import asyncio, json, os, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ditoo_api import CHANNEL_CLOCK, Ditoo

if len(sys.argv) != 3:
    sys.exit("usage: ./ditoo run client/examples/clock_weather.py LAT LON")
LAT, LON = float(sys.argv[1]), float(sys.argv[2])


def divoom_weather(wmo: int) -> int:
    """WMO weather code -> Divoom weather type."""
    if wmo in (0, 1): return 1            # clear
    if wmo in (2, 3): return 3            # cloudy
    if wmo in (45, 48): return 9          # fog
    if 51 <= wmo <= 67 or 80 <= wmo <= 82: return 6   # rain
    if 71 <= wmo <= 77 or wmo in (85, 86): return 8   # snow
    if wmo >= 95: return 5                # thunderstorm
    return 3


def fetch_weather():
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
           "&current=temperature_2m,weather_code")
    cur = json.load(urllib.request.urlopen(url, timeout=10))["current"]
    return round(cur["temperature_2m"]), int(cur["weather_code"])


async def main():
    temp, wmo = await asyncio.get_running_loop().run_in_executor(None, fetch_weather)
    kind = divoom_weather(wmo)
    print(f"weather at {LAT},{LON}: {temp} C, WMO {wmo} -> Divoom type {kind}", flush=True)
    async with Ditoo() as d:
        await d.set_time()
        await d.set_weather(temp, kind)
        # switch to the clock face with its current settings (pages, color, style)
        await d.set_channel(CHANNEL_CLOCK)
        st = await d.get_state()
        print(f"clock face: pages clock/weather/temp/calendar {st.clock_flags}, color {st.clock_color}; "
              "the weather shows only if its page is on (ditoo_pro.set_clock_face)", flush=True)

asyncio.run(main())
