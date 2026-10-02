"""Firmware version (diagnostic)."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import DitooEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([FirmwareSensor(entry.runtime_data), BatterySensor(entry.runtime_data)])


class FirmwareSensor(DitooEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:chip"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "firmware")

    @property
    def native_value(self):
        return self.coordinator.firmware


class BatterySensor(DitooEntity, SensorEntity):
    """Approximate percentage: the device measures the battery in 8 steps (0..7)."""
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "battery")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.battery is not None

    @property
    def native_value(self):
        b = self.coordinator.data.battery if self.coordinator.data else None
        return b.percent if b else None

    @property
    def extra_state_attributes(self) -> dict:
        b = self.coordinator.data.battery if self.coordinator.data else None
        return {"level": b.level, "max_level": b.MAX_LEVEL, "external_power": b.external_power,
                "full": b.full} if b else {}
