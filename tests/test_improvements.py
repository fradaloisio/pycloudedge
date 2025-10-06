"""
Test suite for Phase 1-3 improvements to PyCloudEdge library.
Tests input validation, logging, error handling, and utility modules.
"""

import pytest
import logging
from unittest.mock import Mock, patch, MagicMock
import requests

from cloudedge import (
    CloudEdgeClient, 
    ValidationError, 
    AuthenticationError,
    NetworkError,
    ConfigurationError
)
from cloudedge.validators import validate_email, validate_country_code, validate_phone_code
from cloudedge.constants import CA_KEY, DEFAULT_TIMEOUT
from cloudedge.utils import retry_on_failure


class TestPhase1InputValidation:
    """Test Phase 1: Input validation in CloudEdgeClient.__init__"""
    
    def test_invalid_email_raises_validation_error(self):
        """Test that invalid email format raises ValidationError"""
        with pytest.raises(ValidationError) as exc_info:
            CloudEdgeClient("not-an-email", "password", "US", "+1")
        
        assert "Invalid email format" in str(exc_info.value)
        assert exc_info.value.details["field"] == "username"
    
    def test_invalid_country_code_raises_validation_error(self):
        """Test that invalid country code raises ValidationError"""
        with pytest.raises(ValidationError) as exc_info:
            CloudEdgeClient("user@example.com", "password", "USA", "+1")
        
        assert "Invalid country code" in str(exc_info.value)
        assert exc_info.value.details["field"] == "country_code"
    
    def test_invalid_phone_code_raises_validation_error(self):
        """Test that invalid phone code raises ValidationError"""
        with pytest.raises(ValidationError) as exc_info:
            CloudEdgeClient("user@example.com", "password", "US", "1234567")
        
        assert "Invalid phone code" in str(exc_info.value)
        assert exc_info.value.details["field"] == "phone_code"
    
    def test_valid_inputs_success(self):
        """Test that valid inputs create client successfully"""
        client = CloudEdgeClient(
            "user@example.com",
            "password",
            "US",
            "+1"
        )
        assert client.username == "user@example.com"
        assert client.country_code == "US"
        assert client.phone_code == "+1"


class TestPhase1Logging:
    """Test Phase 1: Proper logging implementation"""
    
    def test_logger_configured_correctly(self):
        """Test that logger is properly configured"""
        client = CloudEdgeClient("user@example.com", "password", "US", "+1", debug=True)
        
        assert hasattr(client, 'logger')
        assert isinstance(client.logger, logging.Logger)
        assert client.logger.name == "pycloudedge.client"
    
    def test_debug_mode_sets_log_level(self):
        """Test that debug mode sets DEBUG log level"""
        client = CloudEdgeClient("user@example.com", "password", "US", "+1", debug=True)
        assert client.logger.level == logging.DEBUG
    
    def test_log_method_uses_logger(self, caplog):
        """Test that _log method uses logger.debug"""
        client = CloudEdgeClient("user@example.com", "password", "US", "+1", debug=True)
        
        with caplog.at_level(logging.DEBUG):
            client._log("Test message")
        
        assert "Test message" in caplog.text


class TestPhase1Constants:
    """Test Phase 1: Externalized constants"""
    
    def test_ca_key_constant_exists(self):
        """Test that CA_KEY constant is available"""
        assert CA_KEY == "bc29be30292a4309877807e101afbd51"
    
    def test_default_timeout_constant_exists(self):
        """Test that DEFAULT_TIMEOUT constant is available"""
        assert DEFAULT_TIMEOUT == 30


class TestPhase1RetryLogic:
    """Test Phase 1: Retry logic with @retry_on_failure decorator"""
    
    def test_retry_decorator_retries_on_network_failure(self):
        """Test that retry decorator retries on network failures"""
        attempt_count = {"count": 0}
        
        @retry_on_failure(max_attempts=3, delay=0.01)
        def test_func():
            attempt_count["count"] += 1
            if attempt_count["count"] < 3:
                raise requests.exceptions.ConnectionError("Network error")
            return "Success"
        
        result = test_func()
        assert result == "Success"
        assert attempt_count["count"] == 3
    
    def test_retry_decorator_gives_up_after_max_attempts(self):
        """Test that retry decorator gives up after max attempts"""
        attempt_count = {"count": 0}
        
        @retry_on_failure(max_attempts=3, delay=0.01)
        def test_func():
            attempt_count["count"] += 1
            raise requests.exceptions.ConnectionError("Always fails")
        
        with pytest.raises(requests.exceptions.ConnectionError):
            test_func()
        
        assert attempt_count["count"] == 3


class TestPhase2ErrorHandling:
    """Test Phase 2: Standardized error handling with details"""
    
    def test_authentication_error_includes_details(self):
        """Test that AuthenticationError includes structured details"""
        error = AuthenticationError(
            "Login failed",
            details={"error_code": "1002", "message": "Invalid credentials"}
        )
        
        assert error.message == "Login failed"
        assert error.details["error_code"] == "1002"
        assert error.details["message"] == "Invalid credentials"
    
    def test_configuration_error_includes_details(self):
        """Test that ConfigurationError includes structured details"""
        error = ConfigurationError(
            "Unknown parameter",
            details={"parameter_name": "INVALID_PARAM", "device": "Camera1"}
        )
        
        assert "Unknown parameter" in error.message
        assert error.details["parameter_name"] == "INVALID_PARAM"
        assert error.details["device"] == "Camera1"


class TestPhase2Validators:
    """Test Phase 2: Input validation functions"""
    
    def test_validate_email_valid(self):
        """Test validate_email with valid emails"""
        assert validate_email("user@example.com") is True
        assert validate_email("test.user+tag@domain.co.uk") is True
    
    def test_validate_email_invalid(self):
        """Test validate_email with invalid emails"""
        assert validate_email("not-an-email") is False
        assert validate_email("missing@domain") is False
        assert validate_email("@nodomain.com") is False
    
    def test_validate_country_code_valid(self):
        """Test validate_country_code with valid codes"""
        assert validate_country_code("US") is True
        assert validate_country_code("IT") is True
        assert validate_country_code("GB") is True
    
    def test_validate_country_code_invalid(self):
        """Test validate_country_code with invalid codes"""
        assert validate_country_code("USA") is False
        assert validate_country_code("1") is False
        assert validate_country_code("") is False
    
    def test_validate_phone_code_valid(self):
        """Test validate_phone_code with valid codes"""
        assert validate_phone_code("+1") is True
        assert validate_phone_code("+39") is True
        assert validate_phone_code("+1234") is True
    
    def test_validate_phone_code_invalid(self):
        """Test validate_phone_code with invalid codes"""
        assert validate_phone_code("1") is False
        assert validate_phone_code("+12345") is False
        assert validate_phone_code("invalid") is False


class TestPhase3CodeDuplication:
    """Test Phase 3: Helper methods to reduce duplication"""
    
    def test_generate_xca_headers_method_exists(self):
        """Test that _generate_xca_headers helper method exists"""
        client = CloudEdgeClient("user@example.com", "password", "US", "+1")
        assert hasattr(client, '_generate_xca_headers')
    
    @patch('time.time')
    def test_generate_xca_headers_returns_correct_structure(self, mock_time):
        """Test that _generate_xca_headers returns proper header dict"""
        mock_time.return_value = 1000000.0
        
        client = CloudEdgeClient("user@example.com", "password", "US", "+1")
        client.session_data = {"userToken": "test_token"}
        
        headers = client._generate_xca_headers("test_params", "test_token")
        
        assert "X-Ca-Timestamp" in headers
        assert "X-Ca-Sign" in headers
        assert "X-Ca-Key" in headers
        assert "X-Ca-Nonce" in headers
        assert headers["X-Ca-Key"] == "test_token"


class TestIntegration:
    """Integration tests for all improvements"""
    
    def test_make_request_with_retry_on_network_error(self):
        """Test that _make_request retries on network errors"""
        with patch.object(requests.Session, 'request') as mock_request:
            # Simulate network error then success
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"result": "success"}
            mock_response.raise_for_status = Mock()  # Don't raise on success
            
            mock_request.side_effect = [
                requests.exceptions.ConnectionError("Network error"),
                requests.exceptions.ConnectionError("Network error 2"),
                mock_response
            ]
            
            client = CloudEdgeClient("user@example.com", "password", "US", "+1")
            
            # Should retry and succeed on 3rd attempt
            response = client._make_request('GET', 'https://test.com')
            assert response.status_code == 200
            assert mock_request.call_count == 3
    
    def test_client_initialization_with_all_features(self):
        """Test that client initializes with all new features"""
        client = CloudEdgeClient(
            "user@example.com",
            "SecurePassword123",
            "US",
            "+1",
            debug=True,
            enable_network_ping=True
        )
        
        # Verify validation passed
        assert client.username == "user@example.com"
        
        # Verify logging configured
        assert client.logger.name == "pycloudedge.client"
        assert client.logger.level == logging.DEBUG
        
        # Verify session configured with user agent
        assert 'User-Agent' in client._session.headers
        
        # Verify retry logic method exists
        assert hasattr(client, '_make_request')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
