"""
CloudEdge Python Library
=================================

A Python library for interacting with CloudEdge cameras.
Provides authentication, device management, and configuration capabilities.

Author: Francesco D'Aloisio
Date: September 16, 2025
"""

from .client import (
    CloudEdgeClient,
    DEVICE_STATUS_ONLINE,
    DEVICE_STATUS_DORMANCY,
    DEVICE_STATUS_OFFLINE,
)
from .mqtt import CloudEdgeMqttListener, ALARM_TYPE_NAMES, MOTION_ALARM_TYPES
from .image_decrypt import decrypt_jpgx3, decrypt_jpgx3_from_url, verify_licence_for_url
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
    'DEVICE_STATUS_ONLINE',
    'DEVICE_STATUS_DORMANCY',
    'DEVICE_STATUS_OFFLINE',
    'CloudEdgeMqttListener',
    'ALARM_TYPE_NAMES',
    'MOTION_ALARM_TYPES',
    'CloudEdgeError', 
    'AuthenticationError',
    'DeviceNotFoundError',
    'ConfigurationError',
    'NetworkError',
    'ValidationError',
    'RateLimitError',
    'IOT_PARAMETERS',
    'get_parameter_name',
    'format_parameter_value',
    'decrypt_jpgx3',
    'decrypt_jpgx3_from_url',
    'verify_licence_for_url',
]