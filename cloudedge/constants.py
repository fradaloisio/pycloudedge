"""
Configuration constants for CloudEdge API
"""

# Global redirect endpoint — used to discover the correct regional server.
# This is the single source of truth: the redirect API returns the correct
# ``apiServer`` and ``openapi`` domain for the given account/country.
REDIRECT_URL = "https://apis.cloudedge360.com/ppstrongs/redirect"

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
