"""
Configuration constants for CloudEdge API
"""

# API Endpoints
# By default we use EU endpoints. Support for additional regions (e.g. US) can
# be configured by using the region mapping below or by passing explicit
# base URLs to the client initializer.
TYPE_REGION_EU = "EU"
TYPE_REGION_US = "US"

REGION_URLS = {
    TYPE_REGION_EU: {
        "BASE_URL": "https://apis-eu-frankfurt.cloudedge360.com",
        "OPENAPI_BASE_URL": "https://openapi-euce.mearicloud.com",
    },
    # These URLs are the commonly expected US endpoints. If these differ for a
    # given account, pass explicit base_url/openapi_base_url to the client.
    TYPE_REGION_US: {
        # Confirmed base URL for US region (discovered by user)
        "BASE_URL": "https://apis.cloudedge360.com",
        # We use the best-effort OpenAPI URL for the US region. This can be
        # overridden when the client is initialized with explicit values.
        "OPENAPI_BASE_URL": "https://openapi-us.mearicloud.com",
    },
}

def get_urls_for_region(region: str):
    """Return the base URLs for the provided region code.

    Args:
        region: Case-insensitive region code such as 'EU' or 'US'.

    Returns:
        A dict containing 'BASE_URL' and 'OPENAPI_BASE_URL'.
    """
    if not region:
        region = TYPE_REGION_EU
    return REGION_URLS.get(region.upper(), REGION_URLS[TYPE_REGION_EU])

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
