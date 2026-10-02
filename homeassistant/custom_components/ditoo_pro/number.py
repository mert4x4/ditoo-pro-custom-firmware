"""Brightness and volume."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import DitooEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    add([Brightness(c), Volume(c)])


class Brightness(DitooEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:brightness-6"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "brightness")

    @property
    def native_value(self):
        st = self.coordinator.data.state if self.coordinator.data else None
        return st.brightness if st else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_brightness(int(value))


class Volume(DitooEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 15
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:volume-high"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "volume")

    @property
    def native_value(self):
        return self.coordinator.data.volume if self.coordinator.data else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_volume(int(value))
