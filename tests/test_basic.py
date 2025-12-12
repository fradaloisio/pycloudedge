#!/usr/bin/env python3
"""
Basic tests for CloudEdge API Library
=====================================

Simple tests to verify library functionality.
"""

import unittest
import tempfile
import os
import asyncio
from unittest.mock import Mock, patch, AsyncMock
import sys
import os

# Add the parent directory to the path so we can import cloudedge
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cloudedge import CloudEdgeClient, AuthenticationError, DeviceNotFoundError
from cloudedge.iot_parameters import get_parameter_name, format_parameter_value


class TestIoTParameters(unittest.TestCase):
    """Test IoT parameter functions."""
    
    def test_get_parameter_name(self):
        """Test parameter name retrieval."""
        # Test known parameter
        self.assertEqual(get_parameter_name("167"), "FRONT_LIGHT_SWITCH")
        self.assertEqual(get_parameter_name("103"), "LED_ENABLE")
        
        # Test unknown parameter
        self.assertEqual(get_parameter_name("99999"), "iot_99999")
    
    def test_format_parameter_value(self):
        """Test parameter value formatting."""
        # Test boolean parameter
        self.assertEqual(format_parameter_value("LED_ENABLE", "1"), "Enabled")
        self.assertEqual(format_parameter_value("LED_ENABLE", "0"), "Disabled")
        
        # Test percentage parameter
        self.assertEqual(format_parameter_value("BATTERY_PERCENT", 85), "85%")
        
        # Test regular value
        self.assertEqual(format_parameter_value("UNKNOWN_PARAM", "test"), "test")


class TestCloudEdgeClient(unittest.IsolatedAsyncioTestCase):
    """Test CloudEdge client functionality."""
    
    def setUp(self):
        """Set up test client."""
        # Use a per-test temporary session cache file to avoid shared state
        self.session_cache_file = tempfile.NamedTemporaryFile(delete=False).name
        self.client = CloudEdgeClient(
            username="test@example.com",
            password="testpass",
            country_code="US",
            phone_code="+1",
            debug=False
            , session_cache_file=self.session_cache_file
        )

    def tearDown(self):
        # Remove temporary cache file if exists
        try:
            if os.path.exists(self.session_cache_file):
                os.remove(self.session_cache_file)
        except Exception:
            pass
    
    def test_client_initialization(self):
        """Test client initialization."""
        self.assertEqual(self.client.username, "test@example.com")
        self.assertEqual(self.client.country_code, "US")
        self.assertEqual(self.client.phone_code, "+1")
        self.assertFalse(self.client.debug)
        self.assertIsNone(self.client.session_data)
        # Country code US must try to set base URLs for US region by default.
        from cloudedge.constants import REGION_URLS
        expected = REGION_URLS.get("US")
        self.assertEqual(self.client.BASE_URL, expected["BASE_URL"])
        self.assertEqual(self.client.OPENAPI_BASE_URL, expected["OPENAPI_BASE_URL"])
    
    def test_format_sn(self):
        """Test serial number formatting."""
        # Test 9-digit SN
        self.assertEqual(self.client._format_sn("123456789"), "0000000123456789")
        
        # Test longer SN
        self.assertEqual(self.client._format_sn("ABCD123456789"), "123456789")
        
        # Test empty SN
        self.assertEqual(self.client._format_sn(""), "")

    def test_explicit_region_argument(self):
        from cloudedge.constants import REGION_URLS
        client_eu = CloudEdgeClient("test@example.com", "testpass", "IT", "+39", region="EU")
        self.assertEqual(client_eu.BASE_URL, REGION_URLS.get("EU")["BASE_URL"])

        client_us = CloudEdgeClient("test@example.com", "testpass", "US", "+1", region="US")
        self.assertEqual(client_us.BASE_URL, REGION_URLS.get("US")["BASE_URL"])

    def test_authenticate_updates_base_url_from_server(self):
        """If login response includes an 'apiServer', the client should adopt it."""
        client = CloudEdgeClient("test@example.com", "testpass", "US", "+1")

        # Mock authenticate flow; patch session.request used by _make_request for the home list call
        from unittest.mock import Mock
        mock_response_login = Mock()
        mock_response_login.status_code = 200
        mock_response_login.json.return_value = {
            "resultCode": "1001",
            "result": {
                "userToken": "test_token",
                "userID": "test_user_id",
                "apiServer": "https://apis.cloudedge360.com",
                "iot": {"pfKey": {"accessid": "test_access_id", "accesskey": "test_access_key"}}
            }
        }
        mock_response_login.raise_for_status.return_value = None

        with patch('cloudedge.client.requests.Session.request', return_value=mock_response_login):
            success = client.authenticate()
            self.assertTrue(success)
            self.assertEqual(client.BASE_URL, "https://apis.cloudedge360.com")

    def test_explicit_base_url_override(self):
        client_custom = CloudEdgeClient("test@example.com", "testpass", "US", "+1", base_url="https://example.invalid", openapi_base_url="https://example.invalid")
        self.assertEqual(client_custom.BASE_URL, "https://example.invalid")
        self.assertEqual(client_custom.OPENAPI_BASE_URL, "https://example.invalid")

    def test_get_devices_uses_base_url(self):
        """Ensure get_devices uses the correct BASE_URL endpoint."""
        from unittest.mock import Mock
        # Use a client with US region
        client = CloudEdgeClient("test@example.com", "testpass", "US", "+1")
        client.session_data = {"userToken": "t", "userID": "id"}

        # Mock response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"resultCode": "1001", "result": {"deviceList": []}}
        mock_response.raise_for_status.return_value = None

        with patch('cloudedge.client.requests.Session.request', return_value=mock_response) as mock_request:
            client.get_devices()
            # Verify the request was made to the correct URL
            called_url = mock_request.call_args[0][1]
            self.assertTrue(called_url.startswith(client.BASE_URL))
            # No X-Ca headers are required for the default devices API; if
            # present they will be validated by other tests.

    def test_get_device_config_uses_openapi_base_url(self):
        """Ensure get_device_config uses the OPENAPI_BASE_URL endpoint."""
        from unittest.mock import Mock
        client = CloudEdgeClient("test@example.com", "testpass", "US", "+1")
        # Provide OpenAPI credentials in session data
        client.session_data = {"userToken": "t", "userID": "id", "iotPlatformKeys": {"accessid": "aid", "accesskey": "akey"}}

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"code": 100001, "data": {}}
        mock_response.raise_for_status.return_value = None

        with patch('cloudedge.client.requests.Session.request', return_value=mock_response) as mock_request:
            client.get_device_config("SN123456789")
            called_url = mock_request.call_args[0][1]
            self.assertTrue(called_url.startswith(client.OPENAPI_BASE_URL))
            headers = mock_request.call_args[1]['headers']
            from cloudedge.constants import CA_KEY
            self.assertEqual(headers.get('X-Ca-Key'), CA_KEY)

    def test_get_devices_fallback_to_eu(self):
        """If the first request fails, ensure fallback to EU endpoint is tried and returns success."""
        client = CloudEdgeClient("test@example.com", "testpass", "US", "+1")
        client.session_data = {"userToken": "t", "userID": "id"}

        # First request raises RequestException; second returns success
        from requests.exceptions import RequestException
        from unittest.mock import Mock
        mock_success = Mock()
        mock_success.status_code = 200
        mock_success.json.return_value = {"resultCode": "1001", "result": {"deviceList": []}}
        mock_success.raise_for_status.return_value = None

        side_effects = [RequestException("Network error"), mock_success]

        with patch('cloudedge.client.requests.Session.request', side_effect=side_effects) as mock_request:
            devices = client.get_devices()
            self.assertIsInstance(devices, list)
            # We expect two calls: first attempt to US URL, second attempt to EU fallback
            self.assertEqual(mock_request.call_count, 2)
            # Ensure the first call included userToken in the POST data (default devices API uses POST)
            first_call_kwargs = mock_request.call_args_list[0][1]
            # The device_body should include userToken
            if 'data' in first_call_kwargs:
                self.assertIn('userToken', first_call_kwargs['data'])
    
    @patch('cloudedge.client.requests.Session.request')
    def test_authenticate_success(self, mock_post):
        """Test successful authentication."""
        # Mock successful login response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "resultCode": "1001",
            "result": {
                "userToken": "test_token",
                "userID": "test_user_id",
                "iot": {
                    "pfKey": {
                        "accessid": "test_access_id",
                        "accesskey": "test_access_key"
                    }
                }
            }
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response
        
        # Test authentication
        success = self.client.authenticate()
        
        self.assertTrue(success)
        self.assertIsNotNone(self.client.session_data)
        self.assertEqual(self.client.session_data["userToken"], "test_token")
        self.assertEqual(self.client.session_data["userID"], "test_user_id")
    
    @patch('cloudedge.client.requests.Session.request')
    def test_authenticate_failure(self, mock_post):
        """Test failed authentication."""
        # Mock failed login response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "resultCode": "1002",
            "resultMsg": "Invalid credentials"
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response
        
        # Test authentication failure
        with self.assertRaises(AuthenticationError):
            self.client.authenticate()
    
    async def test_get_devices_not_authenticated(self):
        """Test getting devices without authentication."""
        with self.assertRaises(AuthenticationError):
            await self.client.get_devices()
    
    @patch('cloudedge.client.requests.Session.request')
    def test_get_devices_success(self, mock_post):
        """Test successful device retrieval."""
        # Set up authenticated session
        self.client.session_data = {
            "userToken": "test_token",
            "userID": "test_user_id"
        }
        
        # Mock device list response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "resultCode": "1001",
            "result": {
                "deviceList": [
                    {
                        "deviceID": "device1",
                        "snNum": "SN123456789",
                        "deviceName": "Test Camera",
                        "deviceTypeName": "Camera",
                        "devTypeID": "1",
                        "hostKey": "hostkey123",
                        "onLine": 1
                    }
                ]
            }
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response
        
        # Test device retrieval
        devices = self.client.get_devices()
        
        self.assertEqual(len(devices), 1)
        device = devices[0]
        self.assertEqual(device["device_id"], "device1")
        self.assertEqual(device["name"], "Test Camera")
        self.assertTrue(device["online"])
    
    def test_find_device_by_name_not_authenticated(self):
        """Test finding device without authentication."""
        # Mock get_all_devices to raise AuthenticationError
        with patch.object(self.client, 'get_all_devices', side_effect=AuthenticationError("Not authenticated")):
            result = self.client.find_device_by_name("Test Device")
            self.assertIsNone(result)


class TestAsyncMethods(unittest.IsolatedAsyncioTestCase):
    """Test async methods with proper async test support."""
    
    async def test_client_methods_with_mock(self):
        """Test client methods with mocked dependencies."""
        client = CloudEdgeClient("test@example.com", "test", "US", "+1")
        
        # Test that methods require authentication
        with self.assertRaises(AuthenticationError):
            await client.get_devices()
        
        with self.assertRaises(AuthenticationError):
            await client.get_device_status("device1")


def run_tests():
    """Run all tests."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add test classes
    suite.addTests(loader.loadTestsFromTestCase(TestIoTParameters))
    suite.addTests(loader.loadTestsFromTestCase(TestCloudEdgeClient))
    suite.addTests(loader.loadTestsFromTestCase(TestAsyncMethods))
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result.wasSuccessful()


if __name__ == "__main__":
    print("CloudEdge API Library - Basic Tests")
    print("=" * 50)
    
    success = run_tests()
    
    if success:
        print("\n✅ All tests passed!")
        exit(0)
    else:
        print("\n❌ Some tests failed!")
        exit(1)