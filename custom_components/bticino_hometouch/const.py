"""Constants for the BTicino HOMETOUCH integration."""

DOMAIN = "bticino_hometouch"
DEFAULT_PORT = 8790
# Private data of the integration's own phone, under the configuration folder.
STORAGE_DIR = "bticino_hometouch"
CONF_STORAGE = "storage"
CONF_ENTRANCES = "entrances"
MANUFACTURER = "BTicino"
MODEL = "HOMETOUCH (via listener)"

EVENT_RING = "ring"
# Fired on the Home Assistant bus for automations and blueprints.
BUS_EVENT = f"{DOMAIN}_event"

RECONNECT_MIN_SECONDS = 2
RECONNECT_MAX_SECONDS = 60
