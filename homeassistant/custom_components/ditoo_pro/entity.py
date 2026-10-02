"""Base entity for the Ditoo Pro."""
from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DitooCoordinator


class DitooEntity(CoordinatorEntity[DitooCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: DitooCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            manufacturer="Divoom", model="Ditoo Pro", name="Ditoo Pro",
            sw_version=str(coordinator.firmware) if coordinator.firmware else None,
        )

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.data and self.coordinator.data.connected)
