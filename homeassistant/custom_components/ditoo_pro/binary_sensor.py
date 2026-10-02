"""Charging state."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import DitooEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    add([ChargingSensor(c), PowerSensor(c)])


class _BatteryFlag(DitooEntity, BinarySensorEntity):
    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.battery is not None


class ChargingSensor(_BatteryFlag):
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "charging")

    @property
    def is_on(self):
        b = self.coordinator.data.battery if self.coordinator.data else None
        return b.charging if b else None


class PowerSensor(_BatteryFlag):
    _attr_device_class = BinarySensorDeviceClass.PLUG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "external_power")

    @property
    def is_on(self):
        b = self.coordinator.data.battery if self.coordinator.data else None
        return b.external_power if b else None
