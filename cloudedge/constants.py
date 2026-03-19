"""
Configuration constants for CloudEdge API
"""

# Region identifiers
REGION_EU = "eu"
REGION_US = "us"

# Per-region API endpoints
REGION_ENDPOINTS = {
    REGION_EU: {
        "base_url": "https://apis-eu-frankfurt.cloudedge360.com",
        "openapi_base_url": "https://openapi-euce.mearicloud.com",
    },
    REGION_US: {
        "base_url": "https://apis-us-west.cloudedge360.com",
        "openapi_base_url": "https://openapi-uswe.mearicloud.com",
    },
}

# Default endpoints (EU) – kept for backward compatibility
BASE_URL = REGION_ENDPOINTS[REGION_EU]["base_url"]
OPENAPI_BASE_URL = REGION_ENDPOINTS[REGION_EU]["openapi_base_url"]

# European country codes (ISO 3166-1 alpha-2)
EU_COUNTRY_CODES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
    "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
    "PL", "PT", "RO", "SK", "SI", "ES", "SE",
    # EEA + EFTA + UK
    "IS", "LI", "NO", "CH", "GB",
    # Other European countries
    "AL", "AD", "AM", "AZ", "BA", "BY", "GE", "GI", "XK", "MD",
    "MC", "ME", "MK", "RS", "RU", "SM", "TR", "UA", "VA",
}


def region_for_country(country_code: str) -> str:
    """Return the region key for a given ISO 3166-1 alpha-2 country code."""
    if country_code.upper() in EU_COUNTRY_CODES:
        return REGION_EU
    return REGION_US

# API Keys (these are public keys from the mobile app)
CA_KEY = "bc29be30292a4309877807e101afbd51"

# Default Headers
DEFAULT_HEADERS = {
    "Accept-Language": "en-US,en;q=0.8",
    "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us; Android SDK built for arm64 Build/QSR1.211112.002) AppleWebKit/533.1 (KHTML, like Gecko) Version/5.0 Mobile Safari/533.1",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept-Encoding": "gzip, deflate, br"
}

# API Constants
PHONE_TYPE = "a"
SOURCE_APP = "8"
APP_VERSION = "5.5.1"
IOT_TYPE = "4"
APP_VERSION_CODE = "551"
DEFAULT_LANGUAGE = "en"

# Timeout values (seconds)
DEFAULT_TIMEOUT = 30
PING_TIMEOUT = 2.0

# Cache settings
DEFAULT_CACHE_FILE = ".cloudedge_session_cache"
