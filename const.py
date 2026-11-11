"""Constants for the Tüke Busz integration."""
from datetime import timedelta

from .gtfs import GTFS_URL  # noqa: F401  (re-exported)

DOMAIN = "tukebusz"
VERSION = "0.1.0"

CONF_ORIGIN = "origin"
CONF_DESTINATION = "destination"
CONF_COUNT = "count"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_LINES = "lines"
CONF_GTFS_REFRESH_HOURS = "gtfs_refresh_hours"

DEFAULT_COUNT = 5
DEFAULT_SCAN_INTERVAL = 60  # seconds
DEFAULT_GTFS_REFRESH_HOURS = 24

GTFS_DIR = ".storage/tukebusz"
GTFS_FILE = "gtfs.zip"
GTFS_MIN_RETRY = timedelta(minutes=30)

CARD_URL = "/tukebusz/tukebusz-card.js"

DATA_STORE = "store"
DATA_HUB = "hub"

SIGNAL_FEED_UPDATED = f"{DOMAIN}_feed_updated"
