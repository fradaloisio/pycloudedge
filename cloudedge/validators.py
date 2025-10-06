"""
Validation utilities for CloudEdge API inputs
"""

import re
from typing import Optional


def validate_email(email: str) -> bool:
    """
    Validate email format.
    
    Args:
        email: Email address to validate
        
    Returns:
        True if valid email format
    """
    if not email:
        return False
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def validate_phone_code(phone_code: str) -> bool:
    """
    Validate phone code format.
    
    Args:
        phone_code: Phone code to validate (e.g., "+1", "+39")
        
    Returns:
        True if valid phone code format
    """
    if not phone_code:
        return False
    # Phone code must start with + and contain 1-4 digits
    pattern = r'^\+\d{1,4}$'
    return bool(re.match(pattern, phone_code))


def validate_country_code(country_code: str) -> bool:
    """
    Validate country code format.
    
    Args:
        country_code: Country code to validate (e.g., "US", "IT")
        
    Returns:
        True if valid country code format
    """
    if not country_code:
        return False
    # Country code should be 2 letters
    pattern = r'^[A-Z]{2}$'
    return bool(re.match(pattern, country_code.upper()))


def validate_parameter_value(parameter_name: str, value: any) -> tuple[bool, Optional[str]]:
    """
    Validate parameter value based on parameter type.
    
    Args:
        parameter_name: Name of the parameter
        value: Value to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    from .iot_parameters import BOOLEAN_PARAMETERS, PERCENTAGE_PARAMETERS
    
    # Boolean parameters should be 0 or 1
    if parameter_name in BOOLEAN_PARAMETERS:
        if value not in [0, 1, "0", "1"]:
            return False, f"{parameter_name} must be 0 or 1"
    
    # Percentage parameters should be 0-100
    if parameter_name in PERCENTAGE_PARAMETERS:
        try:
            val = int(value)
            if not 0 <= val <= 100:
                return False, f"{parameter_name} must be between 0 and 100"
        except ValueError:
            return False, f"{parameter_name} must be a number between 0 and 100"
    
    return True, None


def sanitize_device_name(name: str) -> str:
    """
    Sanitize device name for use in searches.
    
    Args:
        name: Device name to sanitize
        
    Returns:
        Sanitized device name
    """
    if not name:
        return ""
    return name.strip().lower()
