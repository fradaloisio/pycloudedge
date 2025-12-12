#!/usr/bin/env python3
"""
Basic Usage Example for CloudEdge API Library
==============================================

This example demonstrates the basic functionality of the CloudEdge library
including authentication, multi-home management, and device discovery.

This example shows:
- Authentication with CloudEdge API
- Multi-home device discovery
- Device listing from different homes
- Basic device information retrieval
- Error handling
"""

import os
import sys
from dotenv import load_dotenv

# Add parent directory to path for local development
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cloudedge import CloudEdgeClient
from cloudedge.exceptions import CloudEdgeError, AuthenticationError


def main():
    """Demonstrate basic CloudEdge API usage."""
    print("CloudEdge API Library - Basic Usage Example")
    print("=" * 50)
    
    # Load environment variables
    load_dotenv()
    
    # Get credentials from environment
    username = os.getenv("CLOUDEDGE_USERNAME")
    password = os.getenv("CLOUDEDGE_PASSWORD")
    country_code = os.getenv("CLOUDEDGE_COUNTRY_CODE")
    phone_code = os.getenv("CLOUDEDGE_PHONE_CODE")
    
    if not all([username, password, country_code, phone_code]):
        print("❌ Missing credentials in .env file!")
        print("Required variables: CLOUDEDGE_USERNAME, CLOUDEDGE_PASSWORD, CLOUDEDGE_COUNTRY_CODE, CLOUDEDGE_PHONE_CODE")
        return
    
    try:
        # Create client with debug enabled
        client = CloudEdgeClient(
            username=username,
            password=password,
            country_code=country_code,
            phone_code=phone_code,
            debug=False
            , log_signature_debug=True
            , use_epoch_timestamp=True
        )
        
        # Authenticate
        print("🔐 Authenticating...")
        success = client.authenticate()
        if not success:
            print("❌ Authentication failed!")
            return
        print("✅ Authentication successful!")
        print()
        
        # Get homes
        print("🏠 Getting homes...")
        try:
            homes = client.get_homes()
            print(f"Found {len(homes)} home(s):")
            for i, home in enumerate(homes, 1):
                # If the home name is empty / 'Unnamed', display the home ID to help debugging
                display_name = home['name'] if home['name'] and home['name'] != 'Unnamed' else f"(ID: {home['home_id']})"
                print(f"  {i}. {display_name} (ID: {home['home_id']})")
                print(f"     Owner: {home['owner']}")
                print(f"     Rooms: {home['rooms']}, Devices: {home['device_count']}")
            print()
        except Exception as e:
            print(f"❌ Failed to get homes: {e}")
            print()
        
        # Try different device retrieval methods
        # If one method finds devices, don't run subsequent methods
        print("📱 Getting devices...")
        found_devices = False
        
        # Method 1: Try default home API
        print("1. Trying default home API...")
        try:
            devices = client.get_devices()
            if devices:
                print(f"   ✅ Found {len(devices)} devices via default home API")
                found_devices = True
                for device in devices:
                    status = "🟢 Online" if device['online'] else "🔴 Offline"
                    print(f"   - {device['name']} ({device['type']}) - {status}")
            else:
                print("   ❌ No devices found via default home API")
        except Exception as e:
            print(f"   ❌ Default home API failed: {e}")
        print()

        # Method 2: Get all devices from all homes
        if not found_devices:
            print("2. Trying all homes approach...")
            try:
                all_devices = client.get_all_devices()
                if all_devices:
                    print(f"   ✅ Found {len(all_devices)} devices from all homes")
                    found_devices = True
                    for device in all_devices:
                        status = "🟢 Online" if device['online'] else "🔴 Offline"
                        home_info = f" (Home: {device.get('home_id', 'N/A')})" if device.get('home_id') else ""
                        print(f"   - {device['name']} ({device['type']}) - {status}{home_info}")
                else:
                    print("   ❌ No devices found from all homes")
            except Exception as e:
                print(f"   ❌ All homes approach failed: {e}")
                print()
        # Method 3: Get devices by specific home (if homes were found)
        if 'homes' in locals() and homes and not found_devices:
            print("3. Trying specific home...")
            first_home = homes[0]
            display_name_for_home = first_home['name'] if first_home['name'] and first_home['name'] != 'Unnamed' else f"(ID: {first_home['home_id']})"
            try:
                home_devices = client.get_devices_by_home(first_home['home_id'])
                if home_devices:
                    print(f"   ✅ Found {len(home_devices)} devices in home '{display_name_for_home}'")
                    for device in home_devices:
                        status = "🟢 Online" if device['online'] else "🔴 Offline"
                        print(f"   - {device['name']} ({device['type']}) - {status}")
                else:
                    print(f"   ❌ No devices found in home '{display_name_for_home}'")
            except Exception as e:
                print(f"   ❌ Home-specific approach failed: {e}")
            print()
        
        # Device information example (if we found any devices)
        all_devices = []
        try:
            all_devices = client.get_all_devices()
        except:
            try:
                all_devices = client.get_devices()
            except:
                pass
        
        if all_devices:
            print("📋 Device Information Example:")
            device = all_devices[0]  # Use first device
            
            print(f"Getting detailed info for: {device['name']}")
            try:
                device_info = client.get_device_info(device['name'], include_config=True)
                if device_info:
                    print(f"✅ Device: {device_info['name']}")
                    print(f"   Serial: {device_info['serial_number']}")
                    print(f"   Type: {device_info['type']}")
                    print(f"   Status: {'🟢 Online' if device_info['online'] else '🔴 Offline'}")
                    
                    if device_info.get('configuration'):
                        print("   Configuration:")
                        config_count = 0
                        for param, info in device_info['configuration'].items():
                            if config_count < 5:  # Show first 5 parameters
                                print(f"     {param}: {info['formatted']}")
                                config_count += 1
                        if len(device_info['configuration']) > 5:
                            print(f"     ... and {len(device_info['configuration']) - 5} more parameters")
                else:
                    print("❌ Failed to get device info")
            except Exception as e:
                print(f"❌ Failed to get device info: {e}")
        else:
            print("❌ No devices available for detailed info example")
        
        print()
        print("✅ Basic usage example completed!")
        
    except AuthenticationError as e:
        print(f"❌ Authentication error: {e}")
    except CloudEdgeError as e:
        print(f"❌ CloudEdge error: {e}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()