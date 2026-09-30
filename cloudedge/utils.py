"""
Utility functions for HTTP requests with retry logic
"""

import time
from typing import Optional, Dict, Any, Callable
from functools import wraps
import requests

from .exceptions import NetworkError


def parse_retry_after(response: Any) -> Optional[float]:
    """Parse a ``Retry-After`` header into seconds.

    Supports both the delta-seconds form and the HTTP-date form. Returns
    ``None`` when the header is absent or malformed (never raises).
    """
    try:
        value = response.headers.get("Retry-After")
    except Exception:
        return None
    if value is None:
        return None
    value = str(value).strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime
            import datetime as _dt

            when = parsedate_to_datetime(value)
            return max(
                0.0,
                (when - _dt.datetime.now(_dt.timezone.utc)).total_seconds(),
            )
        except (TypeError, ValueError, OverflowError):
            return None
    if seconds >= 0:
        return seconds
    return None


def retry_on_failure(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0):
    """
    Decorator to retry a function on failure with exponential backoff.

    Only transient failures are retried:

    * transport errors (``Timeout``, ``ConnectionError``)
    * server errors that are plausibly transient (HTTP 502/503/504)

    Everything else — including other HTTP errors and rate limiting — is
    raised immediately: repeating a request the server permanently rejected
    only adds traffic.

    Args:
        max_attempts: Maximum number of attempts
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay after each retry

    Returns:
        Decorator
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None
            deadline = kwargs.pop("_retry_deadline", None)

            for attempt in range(max_attempts):
                if deadline is not None and time.monotonic() >= deadline:
                    raise requests.Timeout("Request deadline exceeded") from None
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e

                    retryable = isinstance(
                        e, (requests.exceptions.Timeout,
                            requests.exceptions.ConnectionError)
                    )
                    if not retryable and isinstance(e, requests.exceptions.HTTPError):
                        status = (
                            e.response.status_code
                            if e.response is not None
                            else None
                        )
                        retryable = status in (502, 503, 504)

                    if retryable and attempt < max_attempts - 1:
                        if deadline is not None and time.monotonic() + current_delay >= deadline:
                            raise
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
        NetworkError: If request fails. Messages never include the URL's
            query string: signed requests carry credentials there.
    """
    # Set default timeout if not provided
    if 'timeout' not in kwargs:
        kwargs['timeout'] = 30

    base_url = str(url).split('?', 1)[0]
    try:
        response = requests.request(method, url, **kwargs)
        response.raise_for_status()
        return response
    except requests.exceptions.Timeout:
        raise NetworkError(f"Request to {base_url} timed out") from None
    except requests.exceptions.ConnectionError:
        raise NetworkError(f"Failed to connect to {base_url}") from None
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        raise NetworkError(f"HTTP error {status} for {base_url}") from None
    except requests.exceptions.RequestException:
        raise NetworkError(f"Request failed for {base_url}") from None


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
