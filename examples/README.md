# CloudEdge API Examples

This directory contains example scripts demonstrating various uses of the CloudEdge API library.

## Examples Overview

### 1. Basic Example (`basic_example.py`)
**Purpose:** Introduction to the library basics
**Features:**
- Authentication
- Multi-home device discovery
- Device listing from all homes vs specific homes
- Basic device status checking
- Simple error handling

**Usage:**
```bash
python examples/basic_example.py
```

### 2. Device Control (`device_control.py`)
**Purpose:** Demonstrate device control and configuration
**Features:**
- Finding devices by name
- Setting device parameters
- Interactive device control
- Multiple parameter configuration
- Parameter validation

**Usage:**\n```bash\npython examples/device_control.py\n```\n\n### 3. Network Ping Status (`network_ping_status.py`)\n**Purpose:** Enhanced ping-based online status checking\n**Features:**\n- Automatic local network detection\n- Ping-based real-time device status\n- Comparison between API and ping status\n- Accurate status when devices are on same network\n- Configurable ping timeout\n\n**Usage:**\n```bash\npython examples/network_ping_status.py\n```

## Prerequisites

Before running any examples, ensure you have:

1. **Installed the library:**
   ```bash
   pip install cloudedge-api
   ```

2. **Set up environment variables:**
   Create a `.env` file in your project root:
   ```env
   CLOUDEDGE_USERNAME=your-email@example.com
   CLOUDEDGE_PASSWORD=your-password
   CLOUDEDGE_COUNTRY_CODE=US
   CLOUDEDGE_PHONE_CODE=+1
   ```

3. **Required Python packages:**
   ```bash
   pip install python-dotenv  # For environment variable loading
   ```

## Running Examples

### Quick Start
```bash
# Clone or download the examples
cd examples/

# Run basic example
python basic_example.py

# Run interactive device control
python device_control.py
```

### Using Your Own Credentials
1. Copy the `.env.example` to `.env`
2. Fill in your CloudEdge credentials
3. Run any example script

## Example Patterns

### Authentication Pattern
```python
from cloudedge import CloudEdgeClient
import os

client = CloudEdgeClient(
    username=os.getenv("CLOUDEDGE_USERNAME"),
    password=os.getenv("CLOUDEDGE_PASSWORD"),
    country_code=os.getenv("CLOUDEDGE_COUNTRY_CODE"),
    phone_code=os.getenv("CLOUDEDGE_PHONE_CODE"),
    debug=True
)

success = client.authenticate()
if not success:
    print("Authentication failed!")
    return
```

### Device Discovery Pattern
```python
# Get all devices from all homes (recommended for multi-home setups)
all_devices = client.get_all_devices()

# Get devices from specific home
homes = client.get_homes()
if homes:
    home_devices = client.get_devices_by_home(homes[0]['home_id'])
    print(f"Devices in {homes[0]['name']}: {len(home_devices)}")

# Get devices from default home API
default_devices = client.get_devices()

# Find specific device across all homes
device = client.find_device_by_name("Front Door Camera")
```

### Device Control Pattern
```python
# Simple parameter setting
success = client.set_device_parameter("Camera", "FRONT_LIGHT_SWITCH", 1)
if success:
    print("Front light turned on")

# Multiple parameters
success = client.set_device_config("ABC123456789", {
    "167": 1,  # Front light on
    "106": 1,  # Motion detection on
    "152": 75  # Volume 75%
})
if success:
    print("Multiple parameters set successfully")
```

## Error Handling

All examples include proper error handling:

```python
from cloudedge import (
    AuthenticationError,
    DeviceNotFoundError,
    ConfigurationError,
    NetworkError
)

try:
    success = client.authenticate()
    if not success:
        raise AuthenticationError("Authentication failed")
    # ... your code
except AuthenticationError:
    print("Check your credentials")
except DeviceNotFoundError:
    print("Device not found")
except ConfigurationError:
    print("Configuration failed")
except NetworkError:
    print("Network error")
```

### Debug Mode
Enable debug mode in any example:
```python
client = CloudEdgeClient(..., debug=True)
```

This will show:
- Authentication details
- API request/response data
- Parameter mappings
- Error details
