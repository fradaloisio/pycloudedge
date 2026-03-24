"""
Configuration constants for CloudEdge API
"""

# Region identifiers
REGION_EU = "eu"
REGION_US = "us"
REGION_AP = "ap"

# Per-region API endpoints (static fallback when redirect discovery fails)
REGION_ENDPOINTS = {
    REGION_EU: {
        "base_url": "https://apis-eu-frankfurt.cloudedge360.com",
        "openapi_base_url": "https://openapi-euce.mearicloud.com",
    },
    REGION_US: {
        "base_url": "https://apis-us-west.cloudedge360.com",
        "openapi_base_url": "https://openapi-uswe.mearicloud.com",
    },
    REGION_AP: {
        "base_url": "https://apis-as-singapore.cloudedge360.com",
        "openapi_base_url": "https://openapi-usce.mearicloud.com",
    },
}

# Global redirect endpoint — used to discover the correct regional server
REDIRECT_URL = "https://apis.cloudedge360.com/ppstrongs/redirect"

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


# Asia-Pacific / Oceania / Middle East / Africa country codes
# These are routed through the Singapore gateway
AP_COUNTRY_CODES = {
    # East Asia
    "JP", "KR", "TW", "HK", "MO",
    # Southeast Asia
    "SG", "MY", "TH", "VN", "PH", "ID", "MM", "KH", "LA", "BN", "TL",
    # South Asia
    "IN", "PK", "BD", "LK", "NP", "BT", "MV",
    # Oceania
    "AU", "NZ", "FJ", "PG", "WS", "TO", "VU", "SB", "KI", "MH", "FM",
    "PW", "NR", "TV", "CK", "NU",
    # Middle East
    "AE", "SA", "QA", "KW", "BH", "OM", "JO", "LB", "IQ", "IR", "YE",
    "SY", "PS", "IL",
    # Africa
    "ZA", "EG", "NG", "KE", "GH", "TZ", "UG", "ET", "MA", "DZ", "TN",
    "SN", "CI", "CM", "AO", "MZ", "ZW", "BW", "NA", "MU", "MG", "RW",
}


def region_for_country(country_code: str) -> str:
    """Return the region key for a given ISO 3166-1 alpha-2 country code."""
    cc = country_code.upper()
    if cc in EU_COUNTRY_CODES:
        return REGION_EU
    if cc in AP_COUNTRY_CODES:
        return REGION_AP
    return REGION_US

# API Keys (these are public keys from the mobile app)
CA_KEY = "bc29be30292a4309877807e101afbd51"
CA_SECRET = "35a69fd1-6527-4566-b190-921f9a651488"

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
