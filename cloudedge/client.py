"""
CloudEdge API Client
============================

Main client class for interacting with CloudEdge cameras.
Provides authentication, device management, and configuration capabilities.
"""

import os
import json
import time
import base64
import hmac
import hashlib
import datetime
import subprocess
import socket
import ipaddress
import logging
from typing import Dict, List, Optional, Union, Any
from urllib.parse import quote, urlencode

import requests

from .exceptions import (
    CloudEdgeError, 
    AuthenticationError, 
    DeviceNotFoundError, 
    ConfigurationError,
    NetworkError,
    ValidationError
)
from .iot_parameters import (
    get_parameter_name, 
    get_parameter_code_by_name, 
    format_parameter_value
)
from .logging_config import get_logger
from .validators import validate_email, validate_country_code, validate_phone_code
from .constants import (
    CA_KEY,
    DEFAULT_HEADERS,
    DEFAULT_TIMEOUT,
    TYPE_REGION_EU,
    get_urls_for_region,
)
from .utils import retry_on_failure


class CloudEdgeClient:
    """
    CloudEdge API Client
    
    A client for interacting with CloudEdge cameras.
    Handles authentication, device discovery, status monitoring, and configuration.
    
    Args:
        username (str): CloudEdge account username
        password (str): CloudEdge account password  
        country_code (str): Country code (e.g., "US", "IT")
        phone_code (str): Phone country code (e.g., "+1", "+39")
        debug (bool): Enable debug logging
        session_cache_file (str): Path to session cache file
        
    Example:
        >>> client = CloudEdgeClient("user@example.com", "password", "US", "+1")
        >>> client.authenticate()
        >>> devices = client.get_devices()
        >>> for device in devices:
        ...     print(f"Device: {device['name']} - Status: {device['online']}")
    """
    
    BASE_URL = "https://apis-eu-frankfurt.cloudedge360.com"
    OPENAPI_BASE_URL = "https://openapi-euce.mearicloud.com"
    
    def __init__(
        self, 
        username: str, 
        password: str, 
        country_code: str, 
        phone_code: str,
        debug: bool = False,
        session_cache_file: str = ".cloudedge_session_cache",
        enable_network_ping: bool = True,
        ping_timeout: float = 2.0,
        region: Optional[str] = None,
        base_url: Optional[str] = None,
        openapi_base_url: Optional[str] = None,
        log_signature_debug: bool = False,
        use_epoch_timestamp: bool = False,
    ):
        """
        Initialize CloudEdge API client.
        
        Args:
            username (str): CloudEdge account username
            password (str): CloudEdge account password
            country_code (str): Country code (e.g., "US", "IT")
            phone_code (str): Phone country code (e.g., "+1", "+39")
            debug (bool): Enable debug logging
            session_cache_file (str): Path to session cache file
            enable_network_ping (bool): Enable ping-based online status when on same network
            ping_timeout (float): Ping timeout in seconds
            region (Optional[str]): Region code ("US" or "EU"). If not provided, inferred from country_code.
            base_url (Optional[str]): Override base URL for API requests
            openapi_base_url (Optional[str]): Override OpenAPI base URL
            log_signature_debug (bool): Log signature debug information (masked)
            use_epoch_timestamp (bool): Use epoch milliseconds for timestamp fields
            
        Raises:
            ValidationError: If input validation fails
        """
        # Validate inputs
        if not validate_email(username):
            raise ValidationError(
                f"Invalid email format: {username}",
                details={"field": "username", "value": username}
            )
        
        if not validate_country_code(country_code):
            raise ValidationError(
                f"Invalid country code (use 2-letter code like 'US'): {country_code}",
                details={"field": "country_code", "value": country_code}
            )
        
        if not validate_phone_code(phone_code):
            raise ValidationError(
                f"Invalid phone code (use format like '+1'): {phone_code}",
                details={"field": "phone_code", "value": phone_code}
            )
        
        self.username = username
        self.password = password
        self.country_code = country_code.upper()
        self.phone_code = phone_code if phone_code.startswith('+') else f'+{phone_code}'
        
        # Setup proper logging
        self.logger = get_logger("client")
        if debug:
            self.logger.setLevel(logging.DEBUG)
        self.debug = debug
        
        # Signature logging flag (masked output)
        self.log_signature_debug = log_signature_debug
        # Use epoch milliseconds for 'timestamp' fields on v1 endpoints
        self.use_epoch_timestamp = use_epoch_timestamp
        
        self.session_cache_file = session_cache_file
        self.enable_network_ping = enable_network_ping
        self.ping_timeout = ping_timeout
        
        self.session_data: Optional[Dict] = None
        self._session = requests.Session()
        self._session.headers.update({'User-Agent': DEFAULT_HEADERS['User-Agent']})
        
        # Network detection cache
        self._local_network = None
        self._network_detected = False
        
        # Resolve base URLs for region; priority: explicit args -> region mapping -> defaults
        if base_url:
            self.BASE_URL = base_url
        else:
            # region may be explicitly provided by user or inferred from country_code
            resolved_region = region or ("US" if self.country_code == "US" else TYPE_REGION_EU)
            urls = get_urls_for_region(resolved_region)
            self.BASE_URL = urls.get("BASE_URL")

        if openapi_base_url:
            self.OPENAPI_BASE_URL = openapi_base_url
        else:
            # Use the same region resolution
            urls = get_urls_for_region(region or ("US" if self.country_code == "US" else TYPE_REGION_EU))
            self.OPENAPI_BASE_URL = urls.get("OPENAPI_BASE_URL")

        if self.debug:
            self._log(f"Using BASE_URL={self.BASE_URL} OPENAPI_BASE_URL={self.OPENAPI_BASE_URL}")
        
    def _detect_local_network(self) -> Optional[str]:
        """Detect the local network subnet."""
        if self._network_detected:
            return self._local_network
            
        try:
            # Get local IP address by connecting to a remote address
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                
            # Assume /24 subnet (common for home networks)
            network = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
            self._local_network = str(network)
            self._network_detected = True
            
            if self.debug:
                self._log(f"Detected local network: {self._local_network}")
                
            return self._local_network
            
        except Exception as e:
            if self.debug:
                self._log(f"Failed to detect local network: {e}")
            self._network_detected = True  # Don't retry
            return None
    
    def _is_device_on_local_network(self, device_ip: str) -> bool:
        """Check if device IP is on the same local network."""
        if not self.enable_network_ping:
            return False
            
        local_network = self._detect_local_network()
        if not local_network:
            return False
            
        try:
            device_addr = ipaddress.IPv4Address(device_ip)
            network = ipaddress.IPv4Network(local_network)
            is_local = device_addr in network
            
            if self.debug:
                self._log(f"Device {device_ip} on local network {local_network}: {is_local}")
                
            return is_local
            
        except Exception as e:
            if self.debug:
                self._log(f"Error checking if {device_ip} is local: {e}")
            return False
    
    def _check_ping_availability(self) -> bool:
        """Check if ping command is available on the system."""
        try:
            # Just try to run ping command with --help to check if it exists
            result = subprocess.run(["ping", "--help"], capture_output=True, text=True, timeout=5)
            return True
            
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False
        except Exception:
            return False

    def _ping_device(self, ip_address: str) -> Optional[bool]:
        """
        Ping a device to check if it's online.
        
        Returns:
            True if device responds to ping
            False if device doesn't respond to ping
            None if ping command is not available (status unknown)
        """
        if not ip_address or not self.enable_network_ping:
            return False
            
        # Check if ping command is available
        if not self._check_ping_availability():
            if self.debug:
                self._log(f"Ping command not available on this system - cannot determine status for {ip_address}")
            return None
            
        try:
            # Use platform-appropriate ping command
            import platform
            if platform.system().lower() == "windows":
                cmd = ["ping", "-n", "1", "-w", str(int(self.ping_timeout * 1000)), ip_address]
            else:
                cmd = ["ping", "-c", "1", "-W", str(int(self.ping_timeout)), ip_address]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.ping_timeout + 1)
            is_online = result.returncode == 0
            
            if self.debug:
                self._log(f"Ping {ip_address}: {'success' if is_online else 'failed'}")
                
            return is_online
            
        except Exception as e:
            if self.debug:
                self._log(f"Ping error for {ip_address}: {e}")
            return False

    def _get_enhanced_device_status(self, device: Dict) -> bool:
        """Get enhanced device online status using ping when on local network."""
        # Start with API status as fallback
        api_status = device.get('online', False)
        
        # If ping is disabled, return API status
        if not self.enable_network_ping:
            if self.debug:
                self._log(f"Device {device.get('name', 'Unknown')}: Ping disabled, using API status={api_status}")
            return api_status
            
        # Try to get device IP from configuration or device info
        device_ip = None
        
        # First try to get IP from device configuration if available
        if 'ip_address' in device:
            device_ip = device['ip_address']
        else:
            # Try to get configuration to find IP
            try:
                config = self.get_device_config(device.get('serial_number', ''))
                if config and 'iot' in config:  # Fixed: use correct path
                    iot_data = config['iot']
                    if isinstance(iot_data, dict):
                        # Parameter 126 is IP_ADDRESS
                        device_ip = iot_data.get('126')
                        if self.debug:
                            self._log(f"Device {device.get('name', 'Unknown')}: Found IP {device_ip} in config")
            except Exception as e:
                if self.debug:
                    self._log(f"Device {device.get('name', 'Unknown')}: Failed to get config: {e}")
                pass
        
        # If we have an IP and it's on local network, use ping
        if device_ip and self._is_device_on_local_network(device_ip):
            ping_result = self._ping_device(device_ip)
            if ping_result is None:
                # Ping command not available - status unknown, fall back to API
                if self.debug:
                    self._log(f"Device {device.get('name', 'Unknown')} ({device_ip}): Ping unavailable, using API status={api_status}")
                return api_status
            elif ping_result is not None:
                if self.debug:
                    self._log(f"Device {device.get('name', 'Unknown')} ({device_ip}): API={api_status}, Ping={ping_result}, Using=Ping")
                return ping_result
        elif device_ip:
            if self.debug:
                self._log(f"Device {device.get('name', 'Unknown')} ({device_ip}): Not on local network, using API status={api_status}")
        else:
            if self.debug:
                self._log(f"Device {device.get('name', 'Unknown')}: No IP found, using API status={api_status}")
        
        # Fallback to API status
        return api_status
    
    def _log(self, message: str) -> None:
        """Log debug messages."""
        self.logger.debug(message)
            
    def _error(self, message: str) -> None:
        """Log error messages."""
        self.logger.error(message)
    
    def _generate_xca_headers(self, params_str: str = "", user_token: Optional[str] = None) -> Dict[str, str]:
        """Generate X-Ca headers for authenticated API requests.
        
        The CA key used will prefer the per-session caKey if it was provided by
        the login response (session_data['caKey']), otherwise it falls back to
        the default CA_KEY constant from the library.
        """
        if user_token is None:
            user_token = self.session_data.get('userToken', '') if self.session_data else ''
        
        ca_timestamp = str(int(time.time() * 1000))
        ca_nonce = str(int(time.time() * 1000000) % 100000000)
        ca_signature = base64.b64encode(
            hmac.new(
                user_token.encode(), 
                params_str.encode(), 
                hashlib.sha1
            ).digest()
        ).decode()
        
        # Prefer server-provided caKey if the server returned one at login
        ca_key = None
        if self.session_data and isinstance(self.session_data, dict):
            ca_key = self.session_data.get('caKey')
        if not ca_key:
            ca_key = CA_KEY
        
        # Debug logs: masked signature and CA key
        if (self.debug or getattr(self, 'log_signature_debug', False)) and params_str:
            try:
                masked = ca_signature[:8] + "..."
                self._log(f"X-Ca-Sign (masked): {masked}")
                self._log(f"X-Ca-Key used: {ca_key}")
            except Exception:
                pass
        
        return {
            "X-Ca-Timestamp": ca_timestamp,
            "X-Ca-Sign": ca_signature,
            "X-Ca-Key": ca_key,
            "X-Ca-Nonce": ca_nonce
        }
    
    @retry_on_failure(max_attempts=3, delay=1.0)
    def _make_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make HTTP request with retry logic and error handling."""
        try:
            response = self._session.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except requests.exceptions.Timeout as e:
            self.logger.error(f"Request timeout: {url}")
            raise  # Let retry decorator handle it
        except requests.exceptions.ConnectionError as e:
            self.logger.error(f"Connection error: {url}")
            raise  # Let retry decorator handle it
        except requests.exceptions.HTTPError as e:
            self.logger.error(f"HTTP error {e.response.status_code}: {url}")
            raise  # HTTP errors shouldn't be retried
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Request failed: {url}")
            raise  # Let retry decorator handle it
        
    def _generate_timestamp(self) -> str:
        """Generate timestamp in CloudEdge format."""
        return datetime.datetime.now().astimezone().isoformat(timespec='seconds')
        
    def _generate_url_timestamp(self) -> str:
        """Generate URL-encoded timestamp."""
        if getattr(self, 'use_epoch_timestamp', False):
            # Return epoch ms as string (not quoted)
            return str(int(time.time() * 1000))
        return quote(self._generate_timestamp())
        
    def _get_timeout(self) -> str:
        """Get timeout timestamp for API requests (60 seconds from now)."""
        return str(int(time.time()) + 60)
        
    def _format_sn(self, sn: str) -> str:
        """Format serial number according to CloudEdge requirements."""
        if not sn:
            return ""
        if len(sn) == 9:
            return "0000000" + sn
        return sn[4:] if len(sn) > 4 else sn
        
    def _des_encode(self, password: str) -> str:
        """Encrypt password using 3DES.
        
        Note: TripleDES is required for CloudEdge protocol compatibility.
        Using the new decrepit module path to avoid deprecation warnings.
        """
        key = "123456781234567812345678".encode('utf-8')
        iv = "01234567".encode('utf-8')
        
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, modes
            from cryptography.hazmat.primitives import padding
            # Import from the new decrepit module location
            from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES

            algorithm = TripleDES(key)
            cipher = Cipher(algorithm, modes.CBC(iv))
            encryptor = cipher.encryptor()
            
            padder = padding.PKCS7(64).padder()
            padded_data = padder.update(password.encode('utf-8')) + padder.finalize()
            
            encrypted = encryptor.update(padded_data) + encryptor.finalize()
            return base64.b64encode(encrypted).decode('utf-8')
            
        except Exception as e:
            raise CloudEdgeError(f"Error during password encryption: {e}")
            
    def _aes_encode_param(self, user_account: str, api_endpoint: str, partner_id: int = 8, 
                         ttid: str = "", timestamp: Optional[int] = None) -> str:
        """Encrypt userAccount using AES encryption."""
        if timestamp is None:
            timestamp = int(time.time() * 1000)
        
        key_material = f"{api_endpoint}{partner_id}{ttid}{timestamp}"
        key_b64 = base64.b64encode(key_material.encode('utf-8')).decode('utf-8')
        aes_key = key_b64[:16].encode('utf-8')
        
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.primitives import padding

            algorithm = algorithms.AES(aes_key)
            cipher = Cipher(algorithm, modes.CBC(aes_key))
            encryptor = cipher.encryptor()
            
            padder = padding.PKCS7(128).padder()
            padded_data = padder.update(user_account.encode('utf-8')) + padder.finalize()
            
            encrypted = encryptor.update(padded_data) + encryptor.finalize()
            return base64.b64encode(encrypted).decode('utf-8')
            
        except Exception as e:
            raise CloudEdgeError(f"Error during username encryption: {e}")
            
    def _generate_api_signature(self, params_str: str, secret: str) -> str:
        """Generate HMAC-SHA1 signature."""
        from urllib.parse import unquote
        
        params = {}
        if params_str:
            for pair in params_str.split('&'):
                if '=' in pair:
                    key, value = pair.split('=', 1)
                    params[key] = unquote(value)
        
        sorted_keys = sorted(params.keys())
        parts = [f"{key}={params[key]}" for key in sorted_keys]
        string_to_sign = "&".join(parts)
        
        signature_bytes = hmac.new(
            secret.encode('utf-8'),
            string_to_sign.encode('utf-8'),
            hashlib.sha1
        ).digest()
        
        signature = base64.b64encode(signature_bytes).decode('utf-8')
        
        # Masked logging of signature and partial string-to-sign for debugging
        if (self.debug or getattr(self, 'log_signature_debug', False)) and string_to_sign:
            try:
                masked_sig = signature[:8] + '...'
                masked_str = string_to_sign[:80] + '...'
                self._log(f"Generated API signature (masked): {masked_sig}")
                self._log(f"String to sign (partial): {masked_str}")
            except Exception:
                pass
        
        return signature
        
    def _get_signature_for_openapi(self, url_path: str, action_type: str, secret: str) -> tuple:
        """Generate signature for OpenAPI requests."""
        timeout = self._get_timeout()
        params = [
            "GET",
            "",
            "",
            timeout,
            url_path,
            action_type
        ]
        string_to_sign = "\n".join(params)
        
        signature = base64.b64encode(
            hmac.new(secret.encode('utf-8'), string_to_sign.encode('utf-8'), hashlib.sha1).digest()
        ).decode('utf-8')
        
        return signature, timeout
        
    def _load_session_cache(self) -> Optional[Dict]:
        """Load session data from cache file."""
        if not os.path.exists(self.session_cache_file):
            return None
            
        try:
            with open(self.session_cache_file, 'r') as f:
                session_data = json.load(f)
                
            # Check if session is still valid (not older than 24 hours)
            login_time = session_data.get('loginTime', 0)
            if time.time() - login_time > 86400:  # 24 hours
                self._log("Session cache expired")
                return None
                
            return session_data
        except (json.JSONDecodeError, KeyError):
            self._log("Invalid session cache file")
            return None
            
    def _save_session_cache(self, session_data: Dict) -> None:
        """Save session data to cache file."""
        try:
            with open(self.session_cache_file, 'w') as f:
                json.dump(session_data, f)
        except Exception as e:
            self._log(f"Failed to save session cache: {e}")
            
    def authenticate(self) -> bool:
        """
        Authenticate with CloudEdge API.
        
        Returns:
            bool: True if authentication successful, False otherwise
            
        Raises:
            AuthenticationError: If authentication fails
            NetworkError: If network request fails
        """
        # Try to load cached session first
        self.session_data = self._load_session_cache()
        if self.session_data:
            self._log("Using cached session")
            return True
            
        self._log("Performing CloudEdge login...")
        
        # Encrypt credentials
        timestamp = int(time.time() * 1000)
        try:
            encrypted_username = self._aes_encode_param(
                self.username, "/meari/app/login", timestamp=timestamp
            )
            encrypted_password = self._des_encode(self.password)
        except Exception as e:
            raise AuthenticationError(f"Failed to encrypt credentials: {e}")
            
        # Generate headers
        ca_timestamp = str(timestamp)
        ca_nonce = str(int(time.time() * 1000000) % 100000000)
        ca_key = "bc29be30292a4309877807e101afbd51"
        
        # Create signature
        ca_sign_data = (
            f"phoneType=a&sourceApp=8&appVer=5.5.1&iotType=4&equipmentNo=&"
            f"appVerCode=551&localTime={timestamp}&password={encrypted_password}&"
            f"t={timestamp}&lngType=en&countryCode={self.country_code}&"
            f"userAccount={encrypted_username}&phoneCode={self.phone_code}"
        )
        ca_signature = base64.b64encode(
            hmac.new(ca_key.encode(), ca_sign_data.encode(), hashlib.sha1).digest()
        ).decode()
        
        login_data = {
            "phoneType": "a",
            "sourceApp": "8",
            "appVer": "5.5.1",
            "iotType": "4",
            "equipmentNo": "",
            "appVerCode": "551",
            "localTime": timestamp,
            "password": encrypted_password,
            "t": timestamp,
            "lngType": "en",
            "countryCode": self.country_code,
            "userAccount": encrypted_username,
            "phoneCode": self.phone_code
        }
        
        headers = {
            "Accept-Language": "en-US,en;q=0.8",
            "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us; Android SDK built for arm64 Build/QSR1.211112.002) AppleWebKit/533.1 (KHTML, like Gecko) Version/5.0 Mobile Safari/533.1",
            "X-Ca-Timestamp": ca_timestamp,
            "X-Ca-Sign": ca_signature,
            "X-Ca-Key": ca_key,
            "X-Ca-Nonce": ca_nonce,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Encoding": "gzip, deflate, br"
        }
        
        try:
            response = self._session.post(
                f"{self.BASE_URL}/meari/app/login", 
                headers=headers, 
                data=login_data,
                timeout=30
            )
            response.raise_for_status()
            response_data = response.json()
            
            if response_data.get("resultCode") == "1001":
                self._log("Authentication successful!")
                
                result = response_data.get("result", {})
                user_token = result.get("userToken")
                user_id = result.get("userID")
                
                if not user_token or not user_id:
                    raise AuthenticationError(
                        "Missing user token or ID in response",
                        details={"response": response_data}
                    )
                
                # Extract IoT platform keys if available
                iot_platform_keys = {}
                if 'iot' in result and 'pfKey' in result['iot']:
                    iot_platform_keys = result['iot']['pfKey']
                
                self.session_data = {
                    "userToken": user_token,
                    "userID": user_id,
                    "caKey": ca_key,
                    "loginTime": int(time.time()),
                    "apiServer": self.BASE_URL,
                    "iotPlatformKeys": iot_platform_keys
                }
                
                self._save_session_cache(self.session_data)
                return True
            else:
                error_msg = response_data.get('resultMsg', 'Unknown error')
                error_code = response_data.get('resultCode', 'unknown')
                raise AuthenticationError(
                    f"Login failed: {error_msg}",
                    details={"error_code": error_code, "message": error_msg}
                )
                
        except requests.exceptions.RequestException as e:
            raise NetworkError(f"Login request failed: {e}")
        except json.JSONDecodeError:
            raise AuthenticationError("Failed to parse login response")
            
    def _generate_device_body(self, extra_params: Optional[Dict] = None) -> Dict:
        """Generate device body for API requests."""
        if not self.session_data:
            raise AuthenticationError("Not authenticated")
            
        body = {
            'appVer': '5.5.1',
            'appVerCode': '551',
            'lngType': 'en',
            'phoneType': 'a',
            'sdkVer': '1.0.0',
            'sourceApp': '8',
            'userID': self.session_data['userID'],
            'userToken': self.session_data['userToken']
        }
        
        if extra_params:
            body.update(extra_params)
            
        return body
    
    def get_homes(self) -> List[Dict]:
        """
        Get list of homes associated with the account.
        
        Returns:
            List[Dict]: List of home information dictionaries
            
        Raises:
            AuthenticationError: If not authenticated
            NetworkError: If network request fails
        """
        if not self.session_data:
            raise AuthenticationError("Not authenticated - call authenticate() first")
            
        self._log("Getting homes from API...")
        
        timestamp = self._generate_url_timestamp()
        nonce = int(time.time())
        
        # Include userToken in params_str - this is required for the simple signature to work
        params_str = (
            f"appVer=5.5.1&appVerCode=551&lngType=en&phoneType=a&"
            f"signatureMethod=HMAC-SHA1&signatureNonce={nonce}&"
            f"signatureVersion=1.0&sourceApp=8&timestamp={timestamp}&"
            f"userID={self.session_data['userID']}&userToken={self.session_data['userToken']}"
        )
        
        # Generate X-Ca headers for authenticated requests
        xca_headers = self._generate_xca_headers(params_str, self.session_data['userToken'])
        
        headers = {
            "Accept-Language": "en-US,en;q=0.8",
            "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us; Android SDK built for arm64 Build/QSR1.211112.002) AppleWebKit/533.1 (KHTML, like Gecko) Version/5.0 Mobile Safari/533.1",
            "Accept-Encoding": "gzip, deflate, br"
        }
        headers.update(xca_headers)
        
        # Use the simple signature with userToken
        signature = self._generate_api_signature(params_str, self.session_data.get('userToken'))
        signature_encoded = quote(signature)
        url = f"{self.BASE_URL}/v1/app/home/list?{params_str}&signature={signature_encoded}"
        
        try:
            response = self._make_request('GET', url, headers=headers, timeout=DEFAULT_TIMEOUT)
            response_data = response.json()
            
            if response_data.get("resultCode") == "1001":
                self._log("Homes retrieved successfully!")
                
                homes = []
                home_list = response_data.get('result', {}).get('homes', [])
                
                for home in home_list:
                    rooms = home.get('rooms', [])
                    device_count = sum(len(room.get('devices', [])) for room in rooms)
                    # Normalize home name: some backends return empty string for homeName
                    home_name = home.get('homeName') or home.get('name') or 'Unnamed'
                    homes.append({
                        'home_id': home.get('homeID'),
                        'name': home_name,
                        'owner': home.get('owner'),
                        'rooms': len(rooms),
                        'device_count': device_count
                    })
                    
                return homes
            else:
                error_msg = response_data.get('resultMsg', 'Unknown error')
                error_code = response_data.get('resultCode', 'unknown')
                self._log(f"API error - Code: {error_code}, Message: {error_msg}")
                raise CloudEdgeError(
                    f"Failed to retrieve homes: {error_msg} (Code: {error_code})",
                    details={"error_code": error_code, "message": error_msg}
                )
                
        except requests.exceptions.RequestException as e:
            # Fallback to EU endpoint if request failed
            try:
                eu_urls = get_urls_for_region(TYPE_REGION_EU)
                eu_url = f"{eu_urls['BASE_URL']}/v1/app/home/list?{params_str}&signature={signature_encoded}"
                self._log(f"Home list request failed on {self.BASE_URL}: {e}; retrying on EU URL")
                response = self._make_request('GET', eu_url, headers=headers, timeout=DEFAULT_TIMEOUT)
                response_data = response.json()
                if response_data.get("resultCode") == "1001":
                    homes = []
                    home_list = response_data.get('result', {}).get('homes', [])
                    for home in home_list:
                        rooms = home.get('rooms', [])
                        device_count = sum(len(room.get('devices', [])) for room in rooms)
                        home_name = home.get('homeName') or home.get('name') or 'Unnamed'
                        homes.append({
                            'home_id': home.get('homeID'),
                            'name': home_name,
                            'owner': home.get('owner'),
                            'rooms': len(rooms),
                            'device_count': device_count
                        })
                    return homes
            except Exception:
                pass
            raise NetworkError(f"Home list request failed: {e}")
        except json.JSONDecodeError:
            raise CloudEdgeError("Failed to parse home list response")
            
    def get_devices_by_home(self, home_id: str) -> List[Dict]:
        """
        Get list of devices in a specific home.
        
        Args:
            home_id (str): Home ID to get devices from
            
        Returns:
            List[Dict]: List of device information dictionaries
            
        Raises:
            AuthenticationError: If not authenticated
            NetworkError: If network request fails
        """
        if not self.session_data:
            raise AuthenticationError("Not authenticated - call authenticate() first")
            
        self._log(f"Getting devices from home {home_id}...")
        
        timestamp = self._generate_url_timestamp()
        nonce = int(time.time())
        
        # Include userToken in params_str - required for the simple signature to work
        params_str = (
            f"appVer=5.5.1&appVerCode=551&homeID={home_id}&lngType=en&phoneType=a&"
            f"signatureMethod=HMAC-SHA1&signatureNonce={nonce}&"
            f"signatureVersion=1.0&sourceApp=8&timestamp={timestamp}&"
            f"userID={self.session_data['userID']}&userToken={self.session_data['userToken']}"
        )
        
        # Generate X-Ca headers for authenticated requests
        xca_headers = self._generate_xca_headers(params_str, self.session_data['userToken'])
        
        headers = {
            "Accept-Language": "en-US,en;q=0.8",
            "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us; Android SDK built for arm64 Build/QSR1.211112.002) AppleWebKit/533.1 (KHTML, like Gecko) Version/5.0 Mobile Safari/533.1",
            "Accept-Encoding": "gzip, deflate, br"
        }
        headers.update(xca_headers)
        
        # Use the simple signature with userToken
        signature = self._generate_api_signature(params_str, self.session_data.get('userToken'))
        signature_encoded = quote(signature)
        url = f"{self.BASE_URL}/v1/app/home/join/device/list?{params_str}&signature={signature_encoded}"
        
        def _parse_devices(response_data: Dict) -> List[Dict]:
            """Parse devices from response data."""
            devices = []
            device_types = ['snap', 'nvr', 'ipc', 'chime', 'doorbell']
            
            # Check both top-level and result for device lists
            sources = [response_data]
            if isinstance(response_data.get('result'), dict):
                sources.append(response_data.get('result'))
            
            raw_devices = []
            for src in sources:
                for device_type in device_types:
                    if device_type in src and isinstance(src[device_type], list):
                        raw_devices.extend(src[device_type])
            
            # Also check for deviceList format
            if not raw_devices:
                device_list = response_data.get('result', {}).get('deviceList', [])
                if isinstance(device_list, list) and device_list:
                    raw_devices.extend(device_list)
            
            # Normalize and deduplicate
            seen = set()
            for device in raw_devices:
                serial = device.get('snNum') or device.get('devUid') or device.get('productSN') or str(device.get('deviceID'))
                if not serial or serial in seen:
                    continue
                seen.add(serial)
                device_name = device.get('deviceName') or 'Unnamed'
                device_dict = {
                    'device_id': device.get('deviceID'),
                    'serial_number': serial,
                    'name': device_name,
                    'type': device.get('deviceTypeName', 'Unknown'),
                    'type_id': device.get('devTypeID'),
                    'host_key': device.get('hostKey'),
                    'online': (
                        (device.get('onLine') == 1) if 'onLine' in device else
                        (device.get('devStatus') == 1) if 'devStatus' in device else
                        (device.get('online') is True) if 'online' in device else False
                    ),
                    'home_id': home_id
                }
                device_dict['online'] = self._get_enhanced_device_status(device_dict)
                devices.append(device_dict)
            
            return devices
        
        try:
            response = self._make_request('GET', url, headers=headers, timeout=DEFAULT_TIMEOUT)
            response_data = response.json()
            
            if response_data.get("resultCode") in ["1001", "1107"]:
                self._log("Home devices retrieved successfully!")
                return _parse_devices(response_data)
            else:
                error_msg = response_data.get('resultMsg', 'Unknown error')
                error_code = response_data.get('resultCode', 'unknown')
                raise CloudEdgeError(
                    f"Failed to retrieve home devices: {error_msg}",
                    details={"error_code": error_code, "message": error_msg, "home_id": home_id}
                )
                
        except requests.exceptions.RequestException as e:
            # Fallback to EU endpoint if request failed
            try:
                eu_urls = get_urls_for_region(TYPE_REGION_EU)
                eu_url = f"{eu_urls['BASE_URL']}/v1/app/home/join/device/list?{params_str}&signature={signature_encoded}"
                self._log(f"Home device list request failed on {self.BASE_URL}: {e}; retrying on EU URL")
                response = self._make_request('GET', eu_url, headers=headers, timeout=DEFAULT_TIMEOUT)
                response_data = response.json()
                if response_data.get("resultCode") in ["1001", "1107"]:
                    return _parse_devices(response_data)
            except Exception:
                pass
            raise NetworkError(f"Home device list request failed: {e}")
        except json.JSONDecodeError:
            raise CloudEdgeError("Failed to parse home device list response")
            
    def get_all_devices(self) -> List[Dict]:
        """
        Get all devices from all homes associated with the account.
        
        Returns:
            List[Dict]: List of all device information dictionaries
            
        Raises:
            AuthenticationError: If not authenticated
            NetworkError: If network request fails
        """
        if not self.session_data:
            raise AuthenticationError("Not authenticated - call authenticate() first")
            
        self._log("Getting all devices from all homes...")
        
        all_devices = []
        
        # First try the default home API (works for device owners)
        try:
            default_devices = self.get_devices()
            if default_devices:
                self._log(f"Found {len(default_devices)} devices via default home API")
                all_devices.extend(default_devices)
                return all_devices
        except Exception as e:
            self._log(f"Default home API failed: {e}, trying home-based approach...")
        
        # Fallback to home-based approach
        try:
            homes = self.get_homes()
            self._log(f"Found {len(homes)} homes")
            
            for home in homes:
                home_id = home['home_id']
                home_name = home['name']
                self._log(f"Getting devices from home '{home_name}' ({home_id})")
                
                try:
                    home_devices = self.get_devices_by_home(home_id)
                    self._log(f"Found {len(home_devices)} devices in home '{home_name}'")
                    all_devices.extend(home_devices)
                except Exception as e:
                    self._log(f"Failed to get devices from home '{home_name}': {e}")
                    
            return all_devices
            
        except Exception as e:
            raise CloudEdgeError(f"Failed to get devices from homes: {e}")
            
    def get_devices(self, home_id: Optional[str] = None) -> List[Dict]:
        """
        Get list of devices associated with the account.
        
        Args:
            home_id (Optional[str]): Specific home ID to get devices from. 
                                   If None, uses default home API.
        
        Returns:
            List[Dict]: List of device information dictionaries
            
        Raises:
            AuthenticationError: If not authenticated
            NetworkError: If network request fails
        """
        if not self.session_data:
            raise AuthenticationError("Not authenticated - call authenticate() first")
            
        # If home_id is specified, use home-based API
        if home_id:
            return self.get_devices_by_home(home_id)
            
        # Otherwise use default home API (default behavior)
        self._log("Getting devices from API...")
        
        device_body = self._generate_device_body()
        
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Encoding": "gzip, deflate, br",
            "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us; Android SDK built for arm64 Build/QSR1.211112.002) AppleWebKit/533.1 (KHTML, like Gecko) Version/5.0 Mobile Safari/533.1",
            "Accept-Language": "en-US,en;q=1"
        }
        
        try:
            response = self._make_request(
                'POST',
                f"{self.BASE_URL}/ppstrongs/getDevice.action",
                headers=headers, 
                data=device_body,
                timeout=DEFAULT_TIMEOUT
            )
            response_data = response.json()
            
            if response_data.get("resultCode") == "1001":
                self._log("Devices retrieved successfully!")
                
                # Debug: Log the actual response structure
                if self.debug:
                    import json
                    self._log(f"API Response structure: {json.dumps(response_data, indent=2)}")
                
                devices = []
                
                # Check for devices in different device type keys (working format)
                device_types = ['nvr', 'ipc', 'chime', 'doorbell', 'snap']
                
                for device_type in device_types:
                    if device_type in response_data and response_data[device_type]:
                        device_list = response_data[device_type]
                        if isinstance(device_list, list):
                            self._log(f"Found {len(device_list)} devices under '{device_type}' key")
                            devices.extend(device_list)
                
                # Fallback: check for devices in result.deviceList (older format)
                if not devices:
                    device_list = response_data.get("result", {}).get("deviceList", [])
                    if isinstance(device_list, list) and device_list:
                        self._log(f"Found {len(device_list)} devices under 'result.deviceList' key")
                        devices.extend(device_list)
                
                # Convert to standardized format
                standardized_devices = []
                for device in devices:
                    # Debug: log all status-related fields from the raw device data
                    if self.debug:
                        status_fields = {k: v for k, v in device.items() if 'status' in k.lower() or 'online' in k.lower() or k in ['onLine', 'devStatus']}
                        self._log(f"Device '{device.get('deviceName')}' status fields: {status_fields}")
                    
                    # Determine online status from multiple possible fields
                    online_status = False
                    if 'onLine' in device:
                        online_status = device.get('onLine') == 1
                        if self.debug:
                            self._log(f"  Using onLine={device.get('onLine')} -> {online_status}")
                    elif 'devStatus' in device:
                        online_status = device.get('devStatus') == 1
                        if self.debug:
                            self._log(f"  Using devStatus={device.get('devStatus')} -> {online_status}")
                    elif 'online' in device:
                        online_status = device.get('online') is True or device.get('online') == 1
                        if self.debug:
                            self._log(f"  Using online={device.get('online')} -> {online_status}")
                    
                    device_dict = {
                        'device_id': device.get('deviceID'),
                        'serial_number': device.get('snNum'),
                        'name': device.get('deviceName', 'Unnamed'),
                        'type': device.get('deviceTypeName', 'Unknown'),
                        'type_id': device.get('devTypeID'),
                        'host_key': device.get('hostKey'),
                        'online': online_status
                    }
                    
                    # Get enhanced online status (may override with ping result)
                    device_dict['online'] = self._get_enhanced_device_status(device_dict)
                    
                    standardized_devices.append(device_dict)
                    
                return standardized_devices
            else:
                error_msg = response_data.get('resultMsg', 'Unknown error')
                error_code = response_data.get('resultCode', 'unknown')
                raise CloudEdgeError(
                    f"Failed to retrieve devices: {error_msg}",
                    details={"error_code": error_code, "message": error_msg}
                )
                
        except requests.exceptions.RequestException as e:
            raise NetworkError(f"Device list request failed: {e}")
        except json.JSONDecodeError:
            raise CloudEdgeError("Failed to parse device list response")
            
    def get_device_status(self, device_id: str) -> Optional[Dict]:
        """
        Get online status for a specific device.
        
        Args:
            device_id (str): Device ID
            
        Returns:
            Optional[Dict]: Device status information or None if failed
            
        Raises:
            AuthenticationError: If not authenticated
            NetworkError: If network request fails
        """
        if not self.session_data:
            raise AuthenticationError("Not authenticated - call authenticate() first")
            
        self._log(f"Getting device status for ID: {device_id}")
        
        device_body = self._generate_device_body({'deviceID': device_id})
        
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Encoding": "gzip, deflate, br",
            "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us; Android SDK built for arm64 Build/QSR1.211112.002) AppleWebKit/533.1 (KHTML, like Gecko) Version/5.0 Mobile Safari/533.1",
        }
        
        try:
            response = self._make_request(
                'POST',
                f"{self.BASE_URL}/ppstrongs/getDeviceOnLine.action",
                headers=headers, 
                data=device_body,
                timeout=DEFAULT_TIMEOUT
            )
            response_data = response.json()
            
            if response_data.get("resultCode") == "1001":
                result = response_data.get("result", {})
                
                # Debug: log the raw result for status debugging
                if self.debug:
                    self._log(f"Device status API response result: {result}")
                
                # Check multiple possible status fields
                online_status = False
                if 'onLine' in result:
                    online_status = result.get('onLine') == 1
                elif 'devStatus' in result:
                    online_status = result.get('devStatus') == 1
                elif 'online' in result:
                    online_status = result.get('online') is True or result.get('online') == 1
                
                if self.debug:
                    self._log(f"Device status determined: {online_status}")
                
                return {
                    'online': online_status,
                    'last_seen': result.get('lastOnLineTime')
                }
            else:
                error_msg = response_data.get('resultMsg', 'Unknown error')
                error_code = response_data.get('resultCode', 'unknown')
                self._log(f"Failed to get device status: {error_msg}")
                raise CloudEdgeError(
                    f"Failed to get device status: {error_msg}",
                    details={"error_code": error_code, "device_id": device_id}
                )
                
        except requests.exceptions.RequestException as e:
            raise NetworkError(f"Device status request failed: {e}")
        except json.JSONDecodeError:
            raise CloudEdgeError("Failed to parse device status response")
            
    def get_device_config(self, device_serial: str, 
                          parameter_codes: Optional[List[str]] = None) -> Optional[Dict]:
        """
        Get device configuration parameters.
        
        Args:
            device_serial (str): Device serial number
            parameter_codes (Optional[List[str]]): Specific parameter codes to retrieve
            
        Returns:
            Optional[Dict]: Device configuration data or None if failed
            
        Raises:
            AuthenticationError: If not authenticated
            ConfigurationError: If configuration retrieval fails
        """
        if not self.session_data:
            raise AuthenticationError("Not authenticated - call authenticate() first")
            
        self._log(f"Getting device configuration for SN: {device_serial}")
        
        # Check if we have OpenAPI credentials
        iot_keys = self.session_data.get('iotPlatformKeys', {})
        if not iot_keys or 'accessid' not in iot_keys or 'accesskey' not in iot_keys:
            raise ConfigurationError(
                "No OpenAPI credentials available. Device may not support remote configuration.",
                details={"device_serial": device_serial}
            )
            
        access_id = iot_keys['accessid']
        access_key = iot_keys['accesskey']
        
        # Generate signature for OpenAPI
        signature, timeout = self._get_signature_for_openapi('/openapi/device/config', 'get', access_key)
        
        # Format device SN
        formatted_sn = self._format_sn(device_serial)
        
        # Prepare IoT parameters
        iot_params = {
            'code': 100001,
            'action': 'get',
            'name': 'iot'
        }
        
        if parameter_codes:
            iot_params['iot'] = parameter_codes
        
        # Build request parameters
        params = {
            'accessid': access_id,
            'expires': timeout,
            'signature': signature,
            'action': 'get',
            'deviceid': formatted_sn,
            'target': 'server',
            'params': base64.b64encode(json.dumps(iot_params).encode()).decode()
        }
        
        headers = {
            "Accept": "*/*",
            "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us; Android SDK built for arm64 Build/QSR1.211112.002) AppleWebKit/533.1 (KHTML, like Gecko) Version/5.0 Mobile Safari/533.1",
            "Accept-Language": "en-US,en;q=1",
            "X-Ca-Key": CA_KEY
        }
        
        try:
            response = self._make_request(
                'GET',
                f"{self.OPENAPI_BASE_URL}/openapi/device/config",
                headers=headers, 
                params=params, 
                timeout=DEFAULT_TIMEOUT
            )
            
            if response.status_code == 200:
                response_data = response.json()
                self._log("Device configuration retrieved successfully")
                return response_data
            else:
                raise ConfigurationError(f"Config request failed: {response.status_code}")
                
        except requests.exceptions.RequestException as e:
            raise NetworkError(f"Config request failed: {e}")
        except json.JSONDecodeError as e:
            raise ConfigurationError(f"Failed to parse config response: {e}")
            
    def set_device_config(self, device_serial: str, parameters: Dict[str, Any]) -> bool:
        """
        Set device configuration parameters.
        
        Args:
            device_serial (str): Device serial number
            parameters (Dict[str, Any]): Parameter codes and values to set
            
        Returns:
            bool: True if successful, False otherwise
            
        Raises:
            AuthenticationError: If not authenticated
            ConfigurationError: If configuration setting fails
        """
        if not self.session_data:
            raise AuthenticationError("Not authenticated - call authenticate() first")
            
        self._log(f"Setting device configuration for SN: {device_serial}")
        
        # Check if we have OpenAPI credentials
        iot_keys = self.session_data.get('iotPlatformKeys', {})
        if not iot_keys or 'accessid' not in iot_keys or 'accesskey' not in iot_keys:
            raise ConfigurationError(
                "No OpenAPI credentials available. Device may not support remote configuration.",
                details={"device_serial": device_serial, "parameters": list(parameters.keys())}
            )
            
        access_id = iot_keys['accessid']
        access_key = iot_keys['accesskey']
        
        # Generate signature for OpenAPI
        signature, timeout = self._get_signature_for_openapi('/openapi/device/config', 'set', access_key)
        
        # Format device SN
        formatted_sn = self._format_sn(device_serial)
        
        # Prepare IoT parameters for SET operation
        iot_params = {
            'code': 100001,
            'action': 'set',
            'name': 'iot',
            'iot': parameters
        }
        
        # Encode parameters to base64
        params_json = json.dumps(iot_params)
        params_b64 = base64.b64encode(params_json.encode()).decode()
        
        # Build request parameters
        params = {
            'accessid': access_id,
            'expires': timeout,
            'signature': signature,
            'action': 'set',
            'deviceid': formatted_sn,
            'params': params_b64
        }
        
        headers = {
            "Accept": "*/*",
            "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us; Android SDK built for arm64 Build/QSR1.211112.002) AppleWebKit/533.1 (KHTML, like Gecko) Version/5.0 Mobile Safari/533.1",
            "Accept-Language": "en-US,en;q=1"
        }
        
        try:
            response = self._make_request(
                'GET',
                f"{self.OPENAPI_BASE_URL}/openapi/device/config",
                headers=headers, 
                params=params, 
                timeout=DEFAULT_TIMEOUT
            )
            
            if response.status_code == 200:
                response_data = response.json()
                
                # Check if the operation was successful
                if response_data.get('code') == 100001 or response_data.get('resultCode') == '1001':
                    self._log("Device configuration set successfully")
                    return True
                else:
                    raise ConfigurationError(f"Set config failed: {response_data}")
            else:
                raise ConfigurationError(f"Set config request failed: {response.status_code}")
                
        except requests.exceptions.RequestException as e:
            raise NetworkError(f"Set config request failed: {e}")
        except json.JSONDecodeError as e:
            raise ConfigurationError(f"Failed to parse set config response: {e}")
            
    def find_device_by_name(self, device_name: str) -> Optional[Dict]:
        """
        Find a device by its name.
        
        Args:
            device_name (str): Device name to search for
            
        Returns:
            Optional[Dict]: Device information or None if not found
        """
        # First try the get_all_devices method for comprehensive search
        try:
            devices = self.get_all_devices()
        except AuthenticationError:
            # If we're not authenticated, return None to indicate no device
            # found (caller can decide to authenticate first)
            return None
        
        # Search for exact match first
        for device in devices:
            if device.get('name', '').lower() == device_name.lower():
                return device
                
        # If exact match not found, try partial match
        for device in devices:
            if device_name.lower() in device.get('name', '').lower():
                return device
                
        return None
        
    def set_device_parameter(self, device_name: str, parameter_name: str, 
                             value: Union[int, str, float]) -> bool:
        """
        Set a single device parameter by name.
        
        Args:
            device_name (str): Device name
            parameter_name (str): Parameter name (e.g., "FRONT_LIGHT_SWITCH")
            value (Union[int, str, float]): Parameter value
            
        Returns:
            bool: True if successful, False otherwise
            
        Raises:
            DeviceNotFoundError: If device not found
            ConfigurationError: If parameter is invalid or setting fails
        """
        # Find device
        device = self.find_device_by_name(device_name)
        if not device:
            raise DeviceNotFoundError(f"Device '{device_name}' not found")
            
        # Get parameter code
        parameter_code = get_parameter_code_by_name(parameter_name)
        if not parameter_code:
            raise ConfigurationError(
                f"Unknown parameter: {parameter_name}",
                details={"parameter_name": parameter_name, "device": device_name}
            )
            
        # Set parameter
        parameters = {parameter_code: value}
        success = self.set_device_config(device['serial_number'], parameters)
        
        if success:
            param_display = get_parameter_name(parameter_code)
            formatted_value = format_parameter_value(param_display, value)
            self._log(f"Set {param_display} = {formatted_value} on device '{device_name}'")
            
        return success
        
    def get_device_info(self, device_name: str, include_config: bool = True) -> Optional[Dict]:
        """
        Get comprehensive device information including status and configuration.
        
        Args:
            device_name (str): Device name
            include_config (bool): Whether to include device configuration
            
        Returns:
            Optional[Dict]: Complete device information or None if not found
            
        Raises:
            DeviceNotFoundError: If device not found
        """
        # Find device
        device = self.find_device_by_name(device_name)
        if not device:
            raise DeviceNotFoundError(f"Device '{device_name}' not found")
            
        # Try to get updated device status, but don't overwrite if API returns empty
        try:
            status = self.get_device_status(device['device_id'])
            if status:
                # Only update if we got a valid status response
                # Some regions/APIs return empty result - preserve existing status in that case
                prev_online = device.get('online', False)
                new_online = status.get('online', False)
                
                # Prefer the more positive status (if either shows online, use online)
                # This handles cases where one API call succeeds and another fails
                if new_online or prev_online:
                    device['online'] = True
                else:
                    device['online'] = False
                    
                if status.get('last_seen'):
                    device['last_seen'] = status['last_seen']
                    
                if self.debug:
                    self._log(f"Updated device status: prev={prev_online}, new={new_online}, final={device['online']}")
        except Exception as e:
            if self.debug:
                self._log(f"Failed to get updated device status (using existing): {e}")
            # Keep existing status from device list API
            pass
            
        # Get device configuration if requested
        if include_config:
            try:
                config = self.get_device_config(device['serial_number'])
                if config and 'result' in config and 'iot' in config['result']:
                    # Process IoT parameters for display
                    iot_data = config['result']['iot']
                    processed_config = {}
                    
                    for param_code, value in iot_data.items():
                        param_name = get_parameter_name(param_code)
                        formatted_value = format_parameter_value(param_name, value)
                        # Use parameter CODE as key (not name) for consistent lookups
                        processed_config[param_code] = {
                            'name': param_name,
                            'code': param_code,
                            'value': value,
                            'formatted': formatted_value
                        }
                        
                    device['configuration'] = processed_config
            except Exception as e:
                self._log(f"Failed to get device configuration: {e}")
                device['configuration'] = None
                
        return device
    
    def refresh_device_status(self, device: Dict) -> bool:
        """
        Refresh the online status of a device using enhanced checking.
        
        Args:
            device (Dict): Device dictionary to refresh
            
        Returns:
            bool: Updated online status
        """
        updated_status = self._get_enhanced_device_status(device)
        device['online'] = updated_status
        return updated_status
    
    def get_network_info(self) -> Dict:
        """
        Get network configuration information.
        
        Returns:
            Dict: Network configuration info
        """
        return {
            'ping_enabled': self.enable_network_ping,
            'ping_timeout': self.ping_timeout,
            'local_network': self._detect_local_network(),
            'network_detected': self._network_detected
        }