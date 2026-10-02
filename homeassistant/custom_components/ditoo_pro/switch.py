"""Drawing mode, key listening and the connection itself."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import DitooEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    add([RemoteSwitch(c), KeyListeningSwitch(c), ConnectionSwitch(c)])


class RemoteSwitch(DitooEntity, SwitchEntity):
    _attr_icon = "mdi:remote"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "remote_mode")

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data and self.coordinator.data.remote)

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_remote(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_remote(False)


class ConnectionSwitch(DitooEntity, SwitchEntity):
    """Off disconnects and stops reconnecting, so the phone app or another program
    can connect. Stays available while the device is unreachable."""
    _attr_icon = "mdi:bluetooth-connect"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "connection")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.enabled

    @property
    def extra_state_attributes(self) -> dict:
        return {"connected": bool(self.coordinator.data and self.coordinator.data.connected)}

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_connection(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_connection(False)


class KeyListeningSwitch(DitooEntity, SwitchEntity):
    """On: every key press goes to Home Assistant (ditoo_pro_key events and the
    key event entities) in every menu, and the device's own menus ignore the
    keys. Remembered across restarts. Holding a key for 4 s on the device turns
    it off. Needs the custom firmware."""
    _attr_icon = "mdi:gesture-tap"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "key_listening")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.listen_supported is not False

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data and self.coordinator.data.listening)

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_listening(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_listening(False)
