# PyCloudEdge

> **Disclaimer**: This library is currently in **beta**. While it provides an interface for interacting with CloudEdge cameras, there are some known and unknown issues (see the Beta Notice section) that will be addressed in future versions.

A Python library for interacting with CloudEdge cameras. This library provides a simple and intuitive interface for authentication, device management, status monitoring, and configuration of CloudEdge cameras.

## Support the Project

If you find this library useful, consider supporting its development! Your contributions help maintain and improve the project.

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/I3I71LBUUU)

## Features

- 🔐 **Secure Authentication** - Handle CloudEdge API authentication with automatic session management
- 🏠 **Multi-Home Support** - Manage devices across multiple homes/locations
- 📱 **Device Discovery** - List and discover devices from specific homes or all homes
- 📊 **Status Monitoring** - Check device online status and connectivity
- ⚙️ **Configuration Management** - Get and set device parameters (LED, motion detection, etc.)
- 🔍 **Device Search** - Find devices by name with fuzzy matching across all homes
- 📝 **Comprehensive Logging** - Debug mode for troubleshooting
- 💾 **Session Caching** - Automatic session persistence to avoid repeated logins

## Installation

### From PyPI (Recommended)

```bash
# Install the library with core dependencies
pip install pycloudedge

# Or install with example dependencies (includes python-dotenv)
pip install pycloudedge[examples]

# For development (includes testing and formatting tools)
pip install pycloudedge[dev]
```

### From Source

```bash
git clone https://github.com/fradaloisio/pycloudedge.git
cd pycloudedge
pip install -e .

# Or with optional dependencies
pip install -e .[examples,dev]
```

### Dependencies

The library requires:
- **requests** (≥2.25.0) - For HTTP API communication
- **cryptography** (≥3.4.0) - For credential encryption

Optional dependencies:
- **python-dotenv** (≥0.19.0) - For loading environment variables in examples

## Quick Start

### Basic Usage

```python
import os
from dotenv import load_dotenv
from pycloudedge import CloudEdgeClient

def main():
    # Initialize the client
    client = CloudEdgeClient(
        username="your-email@example.com",
        password="your-password",
        country_code="US",  # Your country code
        phone_code="+1",    # Your phone country code
        debug=True          # Enable debug logging
    )
    
    # Authenticate
    success = client.authenticate()
    if not success:
        print("Authentication failed!")
        return
    
    # Get all homes
    homes = client.get_homes()
    for home in homes:
        print(f"Home: {home['name']} (ID: {home['home_id']})")
        print(f"  Devices: {home['device_count']}, Rooms: {home['rooms']}")
    
    # Get devices from all homes (recommended for multi-home setups)
    all_devices = client.get_all_devices()
    for device in all_devices:
        home_info = f" (Home: {device.get('home_id', 'Default')})" if device.get('home_id') else ""
        print(f"Device: {device['name']} - Online: {device['online']}{home_info}")
    
    # Get devices from specific home
    if homes:
        home_devices = client.get_devices_by_home(homes[0]['home_id'])
        print(f"Devices in {homes[0]['name']}: {len(home_devices)}")
    
    # Get devices from default home API
    default_devices = client.get_devices()
    print(f"Default home devices: {len(default_devices)}")

if __name__ == "__main__":
    main()
```

### Multi-Home Device Management

```python
from pycloudedge import CloudEdgeClient

def manage_multi_home():
    client = CloudEdgeClient("user@example.com", "password", "US", "+1")
    success = client.authenticate()
    if not success:
        print("Authentication failed!")
        return
    
    # Method 1: Get all devices from all homes (recommended)
    all_devices = client.get_all_devices()
    print(f"Total devices across all homes: {len(all_devices)}")
    
    # Method 2: Get devices by specific home
    homes = client.get_homes()
    for home in homes:
        home_devices = client.get_devices_by_home(home['home_id'])
        print(f"Home '{home['name']}': {len(home_devices)} devices")
    
    # Method 3: Get devices from default home API
    default_devices = client.get_devices()
    print(f"Default home devices: {len(default_devices)}")

manage_multi_home()
```

### Device Control

```python
from pycloudedge import CloudEdgeClient

def control_devices():
    client = CloudEdgeClient("user@example.com", "password", "US", "+1")
    success = client.authenticate()
    if not success:
        print("Authentication failed!")
        return
    
    # Turn on front light
    success = client.set_device_parameter("Front Door Camera", "FRONT_LIGHT_SWITCH", 1)
    if success:
        print("Front light turned on")
    
    # Enable motion detection
    success = client.set_device_parameter("Front Door Camera", "MOTION_DET_ENABLE", 1)
    if success:
        print("Motion detection enabled")
    
    # Set speaker volume to 50%
    success = client.set_device_parameter("Front Door Camera", "SPEAK_VOLUME", 50)
    if success:
        print("Volume set to 50%")

control_devices()
```

### Environment Variables

Create a `.env` file in your project root:

```env
CLOUDEDGE_USERNAME=your-email@example.com
CLOUDEDGE_PASSWORD=your-password
CLOUDEDGE_COUNTRY_CODE=US
CLOUDEDGE_PHONE_CODE=+1
```

Then use environment variables in your code:

```python
import os
from dotenv import load_dotenv
from pycloudedge import CloudEdgeClient

load_dotenv()

client = CloudEdgeClient(
    username=os.getenv("CLOUDEDGE_USERNAME"),
    password=os.getenv("CLOUDEDGE_PASSWORD"),
    country_code=os.getenv("CLOUDEDGE_COUNTRY_CODE"),
    phone_code=os.getenv("CLOUDEDGE_PHONE_CODE")
)
```

## Command Line Interface

The library includes a command-line interface for common operations:

### Installation and Setup

After installing the library, the `cloudedge` command will be available:

```bash
# Set up your credentials in environment variables
export CLOUDEDGE_USERNAME="your-email@example.com"
export CLOUDEDGE_PASSWORD="your-password"
export CLOUDEDGE_COUNTRY_CODE="IT"
export CLOUDEDGE_PHONE_CODE="+39"
```

### CLI Commands

#### List Homes

```bash
# List all homes in your account
cloudedge homes
```

#### List Devices

```bash
# List devices from default home
cloudedge list

# List devices from all homes (recommended for multi-home setups)
cloudedge list --all-homes

# List devices from specific home
cloudedge list --home-id YOUR_HOME_ID
```

#### Device Information

```bash
# Get detailed device information
cloudedge info "Front Door Camera"
```

#### Device Control

```bash
# Set device parameters
cloudedge set "Front Door Camera" FRONT_LIGHT_SWITCH 1
cloudedge set "Front Door Camera" MOTION_DET_ENABLE 1
cloudedge set "Front Door Camera" SPEAK_VOLUME 75
```

#### CLI Help

```bash
# Show all available commands
cloudedge --help

# Show help for specific command
cloudedge list --help
```

## API Reference

### CloudEdgeClient

The main client class for interacting with CloudEdge devices.

#### Constructor

```python
CloudEdgeClient(
    username: str,
    password: str,
    country_code: str,
    phone_code: str,
    debug: bool = False,
    session_cache_file: str = ".cloudedge_session_cache"
)
```

**Parameters:**
- `username`: CloudEdge account email
- `password`: CloudEdge account password
- `country_code`: ISO country code (e.g., "US", "IT", "DE")
- `phone_code`: International phone code (e.g., "+1", "+39", "+49")
- `debug`: Enable debug logging (default: False)
- `session_cache_file`: Path to session cache file (default: ".cloudedge_session_cache")

#### Methods

##### `authenticate() -> bool`

Authenticate with the CloudEdge API. Returns `True` if successful.

```python
success = client.authenticate()
if success:
    print("Authentication successful!")
```

##### `get_devices() -> List[Dict]`

Get a list of all devices from the default home. For multi-home setups, use `get_all_devices()` instead.

```python
devices = client.get_devices()
for device in devices:
    print(f"Name: {device['name']}")
    print(f"Serial: {device['serial_number']}")
    print(f"Online: {device['online']}")
```

**Returns:** List of device dictionaries with keys:
- `device_id`: Internal device ID
- `serial_number`: Device serial number
- `name`: Device name
- `type`: Device type description
- `type_id`: Device type ID
- `host_key`: Device host key
- `online`: Boolean indicating if device is online

##### `get_device_status(device_id: str) -> Optional[Dict]`

Get the online status of a specific device.

```python
status = client.get_device_status("device123")
if status:
    print(f"Online: {status['online']}")
    print(f"Last seen: {status['last_seen']}")
```

##### `get_device_config(device_serial: str, parameter_codes: Optional[List[str]] = None) -> Optional[Dict]`

Get device configuration parameters.

```python
# Get all configuration
config = client.get_device_config("ABC123456789")

# Get specific parameters
config = client.get_device_config("ABC123456789", ["167", "103", "106"])
```

##### `set_device_config(device_serial: str, parameters: Dict[str, Any]) -> bool`

Set device configuration parameters using parameter codes.

```python
# Set multiple parameters
success = client.set_device_config("ABC123456789", {
    "167": 1,  # Front light on
    "103": 0,  # LED off
    "106": 1   # Motion detection on
})
```

##### `find_device_by_name(device_name: str) -> Optional[Dict]`

Find a device by its name (supports partial matching across all homes).

```python
device = client.find_device_by_name("Front Door")
if device:
    print(f"Found: {device['name']}")
```

##### `set_device_parameter(device_name: str, parameter_name: str, value: Union[int, str, float]) -> bool`

Set a single device parameter by name (high-level method).

```python
# Turn on front light
success = client.set_device_parameter("Front Door Camera", "FRONT_LIGHT_SWITCH", 1)

# Set motion sensitivity
success = client.set_device_parameter("Front Door Camera", "MOTION_DET_SENSITIVITY", 75)
```

##### `get_device_info(device_name: str, include_config: bool = True) -> Optional[Dict]`

Get comprehensive device information including status and configuration.

```python
info = client.get_device_info("Front Door Camera")
if info:
    print(f"Name: {info['name']}")
    print(f"Online: {info['online']}")
    if info['configuration']:
        for param, details in info['configuration'].items():
            print(f"{param}: {details['formatted']}")
```

##### `get_homes() -> List[Dict]`

Get a list of all homes associated with your account.

```python
homes = client.get_homes()
for home in homes:
    print(f"Home: {home['name']} (ID: {home['home_id']})")
    print(f"Devices: {home['device_count']}, Rooms: {home['rooms']}")
```

##### `get_devices_by_home(home_id: str) -> List[Dict]`

Get devices from a specific home.

```python
homes = client.get_homes()
if homes:
    devices = client.get_devices_by_home(homes[0]['home_id'])
    print(f"Found {len(devices)} devices in {homes[0]['name']}")
```

##### `get_all_devices() -> List[Dict]`

Get all devices from all homes (recommended for multi-home setups).

```python
all_devices = client.get_all_devices()
for device in all_devices:
    home_info = f" (Home: {device.get('home_id', 'N/A')})" if device.get('home_id') else ""
    print(f"Device: {device['name']}{home_info}")
```

## IoT Parameters

The library includes comprehensive IoT parameter definitions for CloudEdge devices. Parameters can be referenced by name or code.

### Common Parameters

| Parameter Name | Code | Description | Values |
|----------------|------|-------------|---------|
| `FRONT_LIGHT_SWITCH` | 167 | Front light control | 0=Off, 1=On |
| `MOTION_DET_ENABLE` | 106 | Motion detection | 0=Disabled, 1=Enabled |
| `LED_ENABLE` | 103 | Status LED | 0=Off, 1=On |
| `SPEAK_VOLUME` | 152 | Speaker volume | 0-100 |
| `WIFI_STRENGTH` | 1007 | WiFi signal strength | 0-100% |
| `BATTERY_PERCENT` | 154 | Battery level | 0-100% |
| `DEVICE_RESOLUTION` | 332 | Video resolution | 0=720P, 1=1080P, 2=2K, 3=4K |

### Parameter Helper Functions

```python
from pycloudedge import get_parameter_name, format_parameter_value

# Get human-readable name from code
name = get_parameter_name("167")  # Returns "FRONT_LIGHT_SWITCH"

# Format parameter value for display
formatted = format_parameter_value("BATTERY_PERCENT", 85)  # Returns "85%"
```

## Error Handling

The library provides specific exception types for different error conditions:

```python
from pycloudedge import (
    CloudEdgeClient, 
    AuthenticationError, 
    DeviceNotFoundError, 
    ConfigurationError,
    NetworkError
)

try:
    client = CloudEdgeClient("user@example.com", "password", "US", "+1")
    success = client.authenticate()
    if not success:
        raise AuthenticationError("Authentication failed")
    
    success = client.set_device_parameter("My Camera", "FRONT_LIGHT_SWITCH", 1)
    if not success:
        raise ConfigurationError("Failed to set parameter")
except AuthenticationError:
    print("Failed to authenticate - check credentials")
except DeviceNotFoundError:
    print("Device not found - check device name")
except ConfigurationError:
    print("Failed to configure device - check parameter")
except NetworkError:
    print("Network error - check connection")
```

## Development

### Setting up Development Environment

```bash
# Clone the repository
git clone https://github.com/fradaloisio/pycloudedge.git
cd pycloudedge

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install in development mode
pip install -e ".[dev]"
```

### Running Tests

```bash
pytest tests/
```

### Code Formatting

```bash
black cloudedge/
flake8 cloudedge/
mypy cloudedge/
```

### Building and Publishing

```bash
# Build the package
python -m build

# Upload to PyPI (requires API token)
twine upload dist/*
```

## Troubleshooting

### Common Issues

1. **Authentication Fails**
   - Verify your credentials are correct
   - Check that your account has access to the devices
   - Ensure country code and phone code match your account

2. **Device Not Found**
   - Verify the device name (case-insensitive, supports partial matching)
   - Make sure the device is added to your account
   - Check that the device is online

3. **Configuration Errors**
   - Ensure you have OpenAPI credentials (obtained after login)
   - Check that the parameter name/code is valid
   - Verify the parameter value is in the correct format

### Debug Mode

Enable debug mode to see detailed API requests and responses:

```python
client = CloudEdgeClient(..., debug=True)
```

This will show:
- Authentication process details
- API request URLs and parameters
- Response data
- Error details

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request. For major changes, please open an issue first to discuss what you would like to change.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Disclaimer

This library is not officially associated with CloudEdge or SmartEye. It is a reverse-engineered implementation based on observing the official mobile app's API calls. Use at your own risk and ensure compliance with CloudEdge's terms of service.

## Beta Notice

This library is currently in **beta**. While it provides an interface for interacting with CloudEdge cameras, there are some known and unknown issues that will be addressed in future versions:

- **Status Reliability**: The API always shows the camera as online, which may not reflect the actual status.
- **Refresh Reliability**: The API refreshes only after some time when the CloudEdge app is not opened on the phone. This does not impact device control.
- **Regional Support**: Currently, only European accounts are supported. Work is in progress to dynamically gather `BASE_URL` and `OPENAPI_BASE_URL` for other regions.

We appreciate your understanding and welcome feedback to improve the library.
