"""Family Finance integration constants."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "family_finance"

CONF_URL: Final = "url"
CONF_TOKEN: Final = "token"
CONF_HOUSEHOLD_ID: Final = "household_id"

DEFAULT_SCAN_INTERVAL: Final = timedelta(seconds=60)
API_TIMEOUT: Final = 10

ATTR_VERSION: Final = "app_version"

PLATFORMS: Final = ["sensor", "event"]
