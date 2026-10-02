"""Constants for the Ditoo Pro integration."""
from homeassistant.const import Platform

DOMAIN = "ditoo_pro"
PLATFORMS = [
    Platform.BINARY_SENSOR, Platform.BUTTON, Platform.EVENT, Platform.NUMBER, Platform.SELECT,
    Platform.SENSOR, Platform.SWITCH, Platform.TEXT,
]
EVENT_KEY = f"{DOMAIN}_key"          # fired on the HA bus for every key event

# HA weather conditions -> Divoom weather type names (ditoo_api.WEATHER)
HA_WEATHER = {
    "sunny": "clear", "clear-night": "clear", "exceptional": "clear",
    "partlycloudy": "cloudy", "cloudy": "cloudy", "windy": "cloudy", "windy-variant": "cloudy",
    "fog": "fog", "rainy": "rain", "pouring": "rain",
    "snowy": "snow", "snowy-rainy": "snow", "hail": "snow",
    "lightning": "thunderstorm", "lightning-rainy": "thunderstorm",
}
