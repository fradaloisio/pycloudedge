"""
Utility functions for HTTP requests with retry logic
"""

import time
from typing import Optional, Dict, Any, Callable
from functools import wraps
import requests

from .exceptions import NetworkError


def retry_on_failure(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0):
    """
    Decorator to retry a function on failure with exponential backoff.
    
    Args:
        max_attempts: Maximum number of attempts
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay after each retry
        
    Returns:
        Decorated function
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None
            
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    
                    # Only retry on network-related exceptions
                    if isinstance(e, (
                        requests.exceptions.Timeout,
                        requests.exceptions.ConnectionError,
                        requests.exceptions.RequestException
                    )) and attempt < max_attempts - 1:
                        time.sleep(current_delay)
                        current_delay *= backoff
                        continue
                    # For other exceptions, raise immediately
                    raise
            
            # All attempts failed
            if last_exception:
                raise last_exception
        
        return wrapper
    return decorator


def safe_request(method: str, url: str, **kwargs) -> requests.Response:
    """
    Make a safe HTTP request with timeout and error handling.
    
    Args:
        method: HTTP method (GET, POST, etc.)
        url: URL to request
        **kwargs: Additional arguments for requests
        
    Returns:
        Response object
        
    Raises:
        NetworkError: If request fails
    """
    # Set default timeout if not provided
    if 'timeout' not in kwargs:
        kwargs['timeout'] = 30
    
    try:
        response = requests.request(method, url, **kwargs)
        response.raise_for_status()
        return response
    except requests.exceptions.Timeout:
        raise NetworkError(f"Request to {url} timed out")
    except requests.exceptions.ConnectionError:
        raise NetworkError(f"Failed to connect to {url}")
    except requests.exceptions.HTTPError as e:
        raise NetworkError(f"HTTP error {e.response.status_code}: {e}")
    except requests.exceptions.RequestException as e:
        raise NetworkError(f"Request failed: {e}")


class RateLimiter:
    """
    Simple rate limiter for API requests.
    """
    
    def __init__(self, calls_per_second: float = 1.0):
        """
        Initialize rate limiter.
        
        Args:
            calls_per_second: Maximum number of calls per second
        """
        self.min_interval = 1.0 / calls_per_second
        self.last_call = 0.0
    
    def wait(self):
        """Wait if necessary to respect rate limit."""
        now = time.time()
        time_since_last_call = now - self.last_call
        
        if time_since_last_call < self.min_interval:
            time.sleep(self.min_interval - time_since_last_call)
        
        self.last_call = time.time()
