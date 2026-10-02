"""Channel select (stock screen)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ditoo_api import (
    CHANNEL_CLOCK, CHANNEL_CLOUD, CHANNEL_CUSTOM, CHANNEL_LIGHT, CHANNEL_LYRIC, CHANNEL_MUSIC,
)
from .entity import DitooEntity

# option -> (channel, custom page or None)
OPTIONS = {
    "clock": (CHANNEL_CLOCK, None),
    "light": (CHANNEL_LIGHT, None),
    "cloud": (CHANNEL_CLOUD, None),
    "music": (CHANNEL_MUSIC, None),
    "custom_1": (CHANNEL_CUSTOM, 0),
    "custom_2": (CHANNEL_CUSTOM, 1),
    "custom_3": (CHANNEL_CUSTOM, 2),
    "lyric": (CHANNEL_LYRIC, None),
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([ChannelSelect(entry.runtime_data)])


class ChannelSelect(DitooEntity, SelectEntity):
    _attr_options = list(OPTIONS)

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "channel")

    @property
    def current_option(self) -> str | None:
        st = self.coordinator.data.state if self.coordinator.data else None
        if not st:
            return None
        if st.channel == CHANNEL_CUSTOM:
            # The device does not report the page; only the one chosen here is known.
            page = self.coordinator.custom_page
            return None if page is None else f"custom_{page + 1}"
        for option, (channel, _) in OPTIONS.items():
            if channel == st.channel:
                return option
        return None

    async def async_select_option(self, option: str) -> None:
        channel, page = OPTIONS[option]
        await self.coordinator.async_set_channel(channel, page)
