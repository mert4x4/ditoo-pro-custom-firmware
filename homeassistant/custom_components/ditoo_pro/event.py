"""One event entity per front key. Keys are reported while drawing mode or key listening is on."""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ditoo_api import EVENT_NAMES, KEY_NAMES, KeyEvent
from .entity import DitooEntity

KEY_SLUGS = {0: "m", 1: "plus", 2: "left", 3: "right", 4: "arm", 7: "brightness", 9: "minus", 10: "little"}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    add([KeyEventEntity(c, key) for key in KEY_NAMES])


class KeyEventEntity(DitooEntity, EventEntity):
    _attr_event_types = list(EVENT_NAMES.values())     # down, up, long, repeat, long-up
    _attr_icon = "mdi:gesture-tap-button"

    def __init__(self, coordinator, key: int) -> None:
        super().__init__(coordinator, f"key_{KEY_SLUGS[key]}")
        self._key = key

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_key_listener(self._handle_key))

    @callback
    def _handle_key(self, ev: KeyEvent) -> None:
        if ev.key == self._key:
            self._trigger_event(ev.kind)
            self.async_write_ha_state()
