"""Message shown on the screen (turns remote mode on)."""
from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import DitooEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([MessageText(entry.runtime_data)])


class MessageText(DitooEntity, TextEntity):
    _attr_native_max = 255
    _attr_icon = "mdi:message-text"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "message")

    @property
    def native_value(self) -> str:
        return self.coordinator.message

    async def async_set_value(self, value: str) -> None:
        if value:
            await self.coordinator.async_show_text(value)
        else:
            self.coordinator.message = ""
            await self.coordinator.async_set_remote(False)
