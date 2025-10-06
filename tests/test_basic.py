#!/usr/bin/env python3
"""
Basic tests for CloudEdge API Library
=====================================

Simple tests to verify library functionality.
"""

import unittest
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


class TestCloudEdgeClient(unittest.TestCase):
    """Test CloudEdge client functionality."""
    
    def setUp(self):
        """Set up test client."""
        self.client = CloudEdgeClient(
            username="test@example.com",
            password="testpass",
            country_code="US",
            phone_code="+1",
            debug=False
        )
    
    def test_client_initialization(self):
        """Test client initialization."""
        self.assertEqual(self.client.username, "test@example.com")
        self.assertEqual(self.client.country_code, "US")
        self.assertEqual(self.client.phone_code, "+1")
        self.assertFalse(self.client.debug)
        self.assertIsNone(self.client.session_data)
    
    def test_format_sn(self):
        """Test serial number formatting."""
        # Test 9-digit SN
        self.assertEqual(self.client._format_sn("123456789"), "0000000123456789")
        
        # Test longer SN
        self.assertEqual(self.client._format_sn("ABCD123456789"), "123456789")
        
        # Test empty SN
        self.assertEqual(self.client._format_sn(""), "")
    
    @patch('cloudedge.client.requests.Session.post')
    async def test_authenticate_success(self, mock_post):
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
        success = await self.client.authenticate()
        
        self.assertTrue(success)
        self.assertIsNotNone(self.client.session_data)
        self.assertEqual(self.client.session_data["userToken"], "test_token")
        self.assertEqual(self.client.session_data["userID"], "test_user_id")
    
    @patch('cloudedge.client.requests.Session.post')
    async def test_authenticate_failure(self, mock_post):
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
            await self.client.authenticate()
    
    async def test_get_devices_not_authenticated(self):
        """Test getting devices without authentication."""
        with self.assertRaises(AuthenticationError):
            await self.client.get_devices()
    
    @patch('cloudedge.client.requests.Session.post')
    async def test_get_devices_success(self, mock_post):
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
        devices = await self.client.get_devices()
        
        self.assertEqual(len(devices), 1)
        device = devices[0]
        self.assertEqual(device["device_id"], "device1")
        self.assertEqual(device["name"], "Test Camera")
        self.assertTrue(device["online"])
    
    async def test_find_device_by_name_not_authenticated(self):
        """Test finding device without authentication."""
        # Mock get_devices to raise AuthenticationError
        with patch.object(self.client, 'get_devices', side_effect=AuthenticationError("Not authenticated")):
            result = await self.client.find_device_by_name("Test Device")
            self.assertIsNone(result)


class TestAsyncMethods(unittest.IsolatedAsyncioTestCase):
    """Test async methods with proper async test support."""
    
    async def test_client_methods_with_mock(self):
        """Test client methods with mocked dependencies."""
        client = CloudEdgeClient("test", "test", "US", "+1")
        
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