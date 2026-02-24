"""Constants for Emulated Hue + integration."""

DOMAIN = "ha_emulated_hue"

# Bridge identity — matches real Philips Hue bridge 2015 (BSB002)
HUE_SERIAL_NUMBER = "001788FFFE23BFC2"
HUE_UUID = "2f402f80-da50-11e1-9b23-001788255acc"

# Hue API
HUE_API_USERNAME = "nouser"

# Configuration keys
CONF_LISTEN_PORT = "listen_port"
CONF_ADVERTISE_IP = "advertise_ip"
CONF_ADVERTISE_PORT = "advertise_port"

# Default values
DEFAULT_LISTEN_PORT = 80

# Storage keys
STORAGE_KEY = f"{DOMAIN}_storage"
STORAGE_VERSION = 1

# Service names
SERVICE_RELOAD = "reload"
SERVICE_TEST_CREATE_DEVICE = "test_create_device"
SERVICE_TEST_LIST_DEVICES = "test_list_devices"

# Device types supported
SUPPORTED_DOMAINS = [
    "light",
    "switch",
    "fan",
    "cover",
    "climate",
    "media_player",
    "script",
    "scene",
    "input_boolean",
]

# Hue API min/max values — https://developers.meethue.com/develop/hue-api/lights-api/
HUE_API_STATE_BRI_MIN = 1
HUE_API_STATE_BRI_MAX = 254
HUE_API_STATE_HUE_MIN = 0
HUE_API_STATE_HUE_MAX = 65535
HUE_API_STATE_SAT_MIN = 0
HUE_API_STATE_SAT_MAX = 254
HUE_API_STATE_CT_MIN = 153
HUE_API_STATE_CT_MAX = 500

# How long a cached state entry is valid (seconds)
CACHE_TIMEOUT = 2.0
# How long to wait for a state change after a service call
STATE_CHANGE_TIMEOUT = 5.0

# Off-maps-to-on domains: "off" commands still trigger "on" for these
OFF_MAPS_TO_ON_DOMAINS = {"script", "scene"}
