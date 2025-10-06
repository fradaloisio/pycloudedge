"""
Custom exceptions for the CloudEdge library.
"""


class CloudEdgeError(Exception):
    """Base exception for all CloudEdge-related errors."""
    
    def __init__(self, message: str, details: dict = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class AuthenticationError(CloudEdgeError):
    """Raised when authentication fails."""
    pass


class DeviceNotFoundError(CloudEdgeError):
    """Raised when a device cannot be found."""
    pass


class ConfigurationError(CloudEdgeError):
    """Raised when device configuration fails."""
    pass


class NetworkError(CloudEdgeError):
    """Raised when network requests fail."""
    pass


class ValidationError(CloudEdgeError):
    """Raised when input validation fails."""
    pass


class RateLimitError(CloudEdgeError):
    """Raised when API rate limit is exceeded."""
    pass