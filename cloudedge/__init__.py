"""
CloudEdge Python Library
=================================

A Python library for interacting with CloudEdge cameras.
Provides authentication, device management, and configuration capabilities.

Author: Francesco D'Aloisio
Date: September 16, 2025
"""

from .client import CloudEdgeClient
from .exceptions import (
    CloudEdgeError, AuthenticationError, DeviceNotFoundError, 
    ConfigurationError, NetworkError, ValidationError, RateLimitError
)
from .iot_parameters import IOT_PARAMETERS, get_parameter_name, format_parameter_value

try:
    from ._version import version as __version__
except ImportError:
    __version__ = "unknown"
__author__ = "Francesco D'Aloisio"

__all__ = [
    'CloudEdgeClient',
    'CloudEdgeError', 
    'AuthenticationError',
    'DeviceNotFoundError',
    'ConfigurationError',
    'NetworkError',
    'ValidationError',
    'RateLimitError',
    'IOT_PARAMETERS',
    'get_parameter_name',
    'format_parameter_value'
]