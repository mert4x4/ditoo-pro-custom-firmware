"""Divoom Ditoo Pro (custom firmware 24025-remote) for Home Assistant."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID, CONF_ADDRESS
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import DOMAIN, HA_WEATHER, PLATFORMS
from .coordinator import DitooCoordinator
from .ditoo_api import NATIVE_TEXT_MAX, NATIVE_TEXT_SPEEDS, WEATHER

COLOR = vol.All(vol.ExactSequence((cv.byte, cv.byte, cv.byte)), vol.Coerce(tuple))
BASE = {vol.Optional(ATTR_DEVICE_ID): cv.string}
SCHEMAS = {
    "show_text": vol.Schema({**BASE,
        vol.Required("text"): cv.string,
        vol.Optional("color", default=[255, 255, 255]): COLOR,
        vol.Optional("background", default=[0, 0, 0]): COLOR,
        vol.Optional("speed", default=12): vol.All(vol.Coerce(float), vol.Range(min=1, max=30)),
        vol.Optional("duration"): vol.All(vol.Coerce(float), vol.Range(min=1)),
        vol.Optional("y", default=5): vol.All(vol.Coerce(int), vol.Range(min=0, max=11)),
        vol.Optional("start", default="right"): vol.In(["right", "left"]),
        vol.Optional("mode", default="scroll"): vol.In(["scroll", "words"]),
        vol.Optional("word_seconds", default=1.0): vol.All(vol.Coerce(float), vol.Range(min=0.2, max=60)),
    }),
    "show_image": vol.Schema({**BASE,
        vol.Required("path"): cv.string,
        vol.Optional("duration"): vol.All(vol.Coerce(float), vol.Range(min=1)),
    }),
    "fill": vol.Schema({**BASE, vol.Required("color"): COLOR}),
    "show_native_text": vol.Schema({**BASE,
        vol.Required("text"): vol.All(cv.string, vol.Length(min=1, max=NATIVE_TEXT_MAX)),
        vol.Optional("color", default=[255, 255, 255]): COLOR,
        vol.Optional("background", default=[0, 0, 0]): COLOR,
        vol.Optional("speed", default=len(NATIVE_TEXT_SPEEDS)):
            vol.All(vol.Coerce(int), vol.Range(min=1, max=len(NATIVE_TEXT_SPEEDS))),
        vol.Optional("duration"): vol.All(vol.Coerce(float), vol.Range(min=1)),
    }),
    "stop_native_text": vol.Schema(BASE),
    "set_weather": vol.Schema({**BASE,
        vol.Optional("weather_entity"): cv.entity_id,
        vol.Optional("temperature"): vol.Coerce(float),
        vol.Optional("condition"): vol.In(list(WEATHER) + list(HA_WEATHER)),
    }),
    "sync_time": vol.Schema(BASE),
    "connect": vol.Schema(BASE),
    "disconnect": vol.Schema(BASE),
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = DitooCoordinator(hass, entry, entry.data[CONF_ADDRESS])
    await coordinator.async_config_entry_first_refresh()      # raises ConfigEntryNotReady if unreachable
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok and (coordinator := getattr(entry, "runtime_data", None)):
        await coordinator.async_shutdown()
    return ok


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> DitooCoordinator:
    coordinators = {e.entry_id: e.runtime_data for e in hass.config_entries.async_loaded_entries(DOMAIN)}
    device_id = call.data.get(ATTR_DEVICE_ID)
    if device_id:
        device = dr.async_get(hass).async_get(device_id)
        for entry_id in (device.config_entries if device else ()):
            if entry_id in coordinators:
                return coordinators[entry_id]
        raise ServiceValidationError(f"{device_id} is not a Ditoo Pro")
    if len(coordinators) != 1:
        raise ServiceValidationError("more than one Ditoo Pro: pass device_id")
    return next(iter(coordinators.values()))


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "show_text"):
        return

    async def show_text(call: ServiceCall) -> None:
        d = call.data
        await _coordinator(hass, call).async_show_text(
            d["text"], d["color"], d["background"], d["speed"], d.get("duration"), d["y"], d["start"],
            d["mode"], d["word_seconds"])

    async def show_image(call: ServiceCall) -> None:
        path = call.data["path"]
        if not hass.config.is_allowed_path(path):
            raise ServiceValidationError(f"{path} is not in allowlist_external_dirs")
        await _coordinator(hass, call).async_show_image(path, call.data.get("duration"))

    async def show_native_text(call: ServiceCall) -> None:
        d = call.data
        await _coordinator(hass, call).async_show_native_text(
            d["text"], d["color"], d["background"], d["speed"], d.get("duration"))

    async def stop_native_text(call: ServiceCall) -> None:
        await _coordinator(hass, call).async_stop_native_text()

    async def fill(call: ServiceCall) -> None:
        await _coordinator(hass, call).async_fill(call.data["color"])

    async def set_weather(call: ServiceCall) -> None:
        temp, cond = call.data.get("temperature"), call.data.get("condition")
        if ent := call.data.get("weather_entity"):
            state = hass.states.get(ent)
            if state is None:
                raise ServiceValidationError(f"{ent} not found")
            cond = cond or state.state
            temp = temp if temp is not None else state.attributes.get("temperature")
        if temp is None or cond is None:
            raise ServiceValidationError("give weather_entity, or temperature and condition")
        kind = HA_WEATHER.get(cond, cond)
        if kind not in WEATHER:
            raise ServiceValidationError(f"unknown condition {cond}")
        await _coordinator(hass, call).async_set_weather(float(temp), kind)

    async def sync_time(call: ServiceCall) -> None:
        await _coordinator(hass, call).async_sync_time()

    async def connect(call: ServiceCall) -> None:
        await _coordinator(hass, call).async_set_connection(True)

    async def disconnect(call: ServiceCall) -> None:
        await _coordinator(hass, call).async_set_connection(False)

    for name, handler in [("show_text", show_text), ("show_native_text", show_native_text),
                          ("stop_native_text", stop_native_text), ("show_image", show_image), ("fill", fill),
                          ("set_weather", set_weather), ("sync_time", sync_time),
                          ("connect", connect), ("disconnect", disconnect)]:
        hass.services.async_register(DOMAIN, name, _wrap(handler), schema=SCHEMAS[name])


def _wrap(handler):
    async def run(call: ServiceCall) -> None:
        try:
            await handler(call)
        except ServiceValidationError:
            raise
        except Exception as err:        # BLE and device errors
            raise HomeAssistantError(f"Ditoo Pro: {err}") from err
    return run
