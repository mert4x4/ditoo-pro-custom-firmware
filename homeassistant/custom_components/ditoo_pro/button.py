"""Clock sync and key backlight buttons."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import DitooEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    add([SyncTimeButton(c)] + [KeyboardLightButton(c, a) for a in ("toggle", "next", "previous")])


class SyncTimeButton(DitooEntity, ButtonEntity):
    _attr_icon = "mdi:clock-check"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "sync_time")

    async def async_press(self) -> None:
        await self.coordinator.async_sync_time()


class KeyboardLightButton(DitooEntity, ButtonEntity):
    """Key backlight: toggle on/off, or step through the effects. The device has
    no absolute on/off and does not report the state, so this is a toggle."""
    _attr_icon = "mdi:keyboard-variant"

    def __init__(self, coordinator, action: str) -> None:
        super().__init__(coordinator, f"keyboard_light_{action}")
        self._action = action

    async def async_press(self) -> None:
        await self.coordinator.async_keyboard_light(self._action)
