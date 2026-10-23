"""
Utility functions for HTTP requests with retry logic
"""

import time
from typing import Optional, Dict, Any, Callable
from functools import wraps
import requests

from .exceptions import NetworkError


def _url_without_query(url: Any) -> str:
    """Return the scheme/host/path of a URL, dropping the query string.

    CloudEdge query strings carry signatures and tokens: they must never
    reach exception messages or logs.
    """
    text = str(url or "")
    return text.split("?", 1)[0]


def _sanitize_request_exception(exc: Exception) -> str:
    """Request failure text that is safe to expose.

    Uses method-free facts only: HTTP status plus URL without query, or the
    exception class for transport failures. The response body is never
    included.
    """
    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        return f"HTTP {status} for {_url_without_query(getattr(response, 'url', ''))}"
    request = getattr(exc, "request", None)
    if request is not None:
        return f"{type(exc).__name__} for {_url_without_query(getattr(request, 'url', ''))}"
    return type(exc).__name__


def _safe_exception_text(exc: Exception) -> str:
    """Exception text safe for messages/logs: sanitized for HTTP requests."""
    if isinstance(exc, requests.exceptions.RequestException):
        return _sanitize_request_exception(exc)
    return str(exc)


def _payload_shape(value: Any, depth: int = 0) -> Any:
    """Structure of a decoded JSON payload for debug logs, never its values.

    Dicts keep their keys, lists become ``list[<length>]`` and scalars their
    type name: API replies and MQTT pushes carry host keys, device
    signatures and signed snapshot URLs.
    """
    if isinstance(value, dict):
        if depth >= 4:
            return f"dict[{len(value)}]"
        return {str(key): _payload_shape(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return f"list[{len(value)}]"
    return type(value).__name__


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
