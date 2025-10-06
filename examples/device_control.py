#!/usr/bin/env python3
"""
Device Control Example
======================

This example demonstrates how to control CloudEdge devices:
- Finding devices by name across multiple homes
- Getting device configuration
- Setting device parameters
- Controlling various device features (lights, motion detection, volume)

Requirements:
- cloudedge-api library installed
- .env file with your credentials
"""

import os
import time
import sys
from dotenv import load_dotenv

# Add parent directory to path for local development
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cloudedge import (
    CloudEdgeClient, 
    AuthenticationError, 
    DeviceNotFoundError, 
    ConfigurationError,
    get_parameter_name
)

# Load environment variables
load_dotenv()


def demonstrate_device_control():
    """Demonstrate various device control operations."""
    
    client = CloudEdgeClient(
        username=os.getenv("CLOUDEDGE_USERNAME"),
        password=os.getenv("CLOUDEDGE_PASSWORD"),
        country_code=os.getenv("CLOUDEDGE_COUNTRY_CODE"),
        phone_code=os.getenv("CLOUDEDGE_PHONE_CODE"),
        debug=False  # Disable debug for cleaner output
    )
    
    try:
        # Authenticate
        print("🔐 Authenticating...")
        success = client.authenticate()
        if not success:
            print("❌ Authentication failed!")
            return
        print("✅ Authenticated successfully!")
        
        # Get devices - try all homes first (recommended)
        print("\n📱 Getting devices...")
        devices = []
        try:
            devices = client.get_all_devices()
            if devices:
                print(f"✅ Found {len(devices)} devices across all homes")
            else:
                print("❌ No devices found in all homes, trying default home...")
                devices = client.get_devices()
                if devices:
                    print(f"✅ Found {len(devices)} devices in default home")
        except Exception as e:
            print(f"❌ Error getting devices: {e}")
            devices = client.get_devices()  # Fallback to default
            
        if not devices:
            print("❌ No devices found!")
            return
            
        print(f"\n📱 Available devices:")
        for i, device in enumerate(devices, 1):
            status = "🟢 Online" if device['online'] else "🔴 Offline"
            home_info = f" (Home: {device.get('home_id', 'Default')})" if device.get('home_id') else ""
            print(f"  {i}. {device['name']} - {status}{home_info}")
        
        # For demo, use the first device
        target_device = devices[0]
        device_name = target_device['name']
        
        print(f"\n🎯 Using device: {device_name}")
        
        # Get comprehensive device info
        print(f"\n📊 Getting device information...")
        device_info = client.get_device_info(device_name)
        
        if device_info and device_info.get('configuration'):
            print(f"\n⚙️ Current Configuration:")
            config = device_info['configuration']
            
            # Show some key parameters
            key_params = [
                'FRONT_LIGHT_SWITCH', 'LED_ENABLE', 'MOTION_DET_ENABLE', 
                'SPEAK_VOLUME', 'WIFI_STRENGTH', 'BATTERY_PERCENT'
            ]
            
            for param in key_params:
                if param in config:
                    info = config[param]
                    print(f"  {param}: {info['formatted']}")
        
        # Demonstrate parameter control
        print(f"\n🎮 Demonstrating device control...")
        
        # Example 1: Toggle front light
        print("\n💡 Front Light Control:")
        try:
            # Turn on front light
            print("  - Turning on front light...")
            success = client.set_device_parameter(device_name, "FRONT_LIGHT_SWITCH", 1)
            if success:
                print("  ✅ Front light turned ON")
                
                # Wait a moment, then turn off
                time.sleep(2)
                print("  - Turning off front light...")
                success = client.set_device_parameter(device_name, "FRONT_LIGHT_SWITCH", 0)
                if success:
                    print("  ✅ Front light turned OFF")
            else:
                print("  ❌ Failed to control front light")
                
        except ConfigurationError as e:
            print(f"  ❌ Configuration error: {e}")
        except Exception as e:
            print(f"  ❌ Error: {e}")
        
        # Example 2: Motion detection control
        print("\n🏃 Motion Detection Control:")
        try:
            # Enable motion detection
            print("  - Enabling motion detection...")
            success = client.set_device_parameter(device_name, "MOTION_DET_ENABLE", 1)
            if success:
                print("  ✅ Motion detection ENABLED")
            else:
                print("  ❌ Failed to enable motion detection")
                
        except ConfigurationError as e:
            print(f"  ❌ Configuration error: {e}")
        except Exception as e:
            print(f"  ❌ Error: {e}")
        
        # Example 3: Volume control
        print("\n🔊 Volume Control:")
        try:
            # Set volume to 75%
            print("  - Setting volume to 75%...")
            success = client.set_device_parameter(device_name, "SPEAK_VOLUME", 75)
            if success:
                print("  ✅ Volume set to 75%")
            else:
                print("  ❌ Failed to set volume")
                
        except ConfigurationError as e:
            print(f"  ❌ Configuration error: {e}")
        except Exception as e:
            print(f"  ❌ Error: {e}")
        
        # Example 4: Multiple parameters at once
        print("\n⚙️ Setting Multiple Parameters:")
        try:
            # Set multiple parameters using low-level method
            parameters = {
                "103": 1,   # LED_ENABLE = On
                "106": 1,   # MOTION_DET_ENABLE = On
                "152": 50   # SPEAK_VOLUME = 50%
            }
            
            print("  - Setting LED on, motion detection on, volume 50%...")
            success = client.set_device_config(target_device['serial_number'], parameters)
            if success:
                print("  ✅ Multiple parameters set successfully")
                # Display what was set
                for code, value in parameters.items():
                    param_name = get_parameter_name(code)
                    print(f"    • {param_name} = {value}")
            else:
                print("  ❌ Failed to set multiple parameters")
                
        except ConfigurationError as e:
            print(f"  ❌ Configuration error: {e}")
        except Exception as e:
            print(f"  ❌ Error: {e}")
        
        print(f"\n✅ Device control demonstration completed!")
        
    except AuthenticationError:
        print("❌ Authentication failed - check your credentials")
    except DeviceNotFoundError as e:
        print(f"❌ Device not found: {e}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")


def interactive_device_control():
    """Interactive device control mode."""
    
    client = CloudEdgeClient(
        username=os.getenv("CLOUDEDGE_USERNAME"),
        password=os.getenv("CLOUDEDGE_PASSWORD"),
        country_code=os.getenv("CLOUDEDGE_COUNTRY_CODE"),
        phone_code=os.getenv("CLOUDEDGE_PHONE_CODE")
    )
    
    try:
        print("🔐 Authenticating...")
        success = client.authenticate()
        if not success:
            print("❌ Authentication failed!")
            return
        
        # Get devices from all homes
        devices = []
        try:
            devices = client.get_all_devices()
        except:
            devices = client.get_devices()
        
        if not devices:
            print("❌ No devices found!")
            return
        
        print("📱 Available devices:")
        for i, device in enumerate(devices, 1):
            home_info = f" (Home: {device.get('home_id', 'Default')})" if device.get('home_id') else ""
            print(f"  {i}. {device['name']}{home_info}")
        
        # Let user choose device
        if len(devices) > 1:
            while True:
                try:
                    choice = int(input(f"\nChoose device (1-{len(devices)}): ")) - 1
                    if 0 <= choice < len(devices):
                        target_device = devices[choice]
                        break
                    else:
                        print("Invalid choice!")
                except ValueError:
                    print("Please enter a number!")
        else:
            target_device = devices[0]
        
        device_name = target_device['name']
        print(f"\n🎯 Selected device: {device_name}")
        
        # Keep reference to target device for status checks
        original_target_device = target_device
        
        # Simple interactive loop
        while True:
            print("\n🎮 Available commands:")
            print("  1. Turn front light ON")
            print("  2. Turn front light OFF")
            print("  3. Enable motion detection")
            print("  4. Disable motion detection")
            print("  5. Set volume to 50%")
            print("  6. Set volume to 100%")
            print("  7. Get device info")
            print("  8. Toggle LED")
            print("  0. Exit")
            
            try:
                choice = input("\nEnter command number: ").strip()
                
                if choice == "0":
                    break
                elif choice == "1":
                    success = client.set_device_parameter(device_name, "FRONT_LIGHT_SWITCH", 1)
                    if success:
                        print("✅ Front light turned ON")
                    else:
                        print("❌ Failed to turn on front light")
                elif choice == "2":
                    success = client.set_device_parameter(device_name, "FRONT_LIGHT_SWITCH", 0)
                    if success:
                        print("✅ Front light turned OFF")
                    else:
                        print("❌ Failed to turn off front light")
                elif choice == "3":
                    success = client.set_device_parameter(device_name, "MOTION_DET_ENABLE", 1)
                    if success:
                        print("✅ Motion detection ENABLED")
                    else:
                        print("❌ Failed to enable motion detection")
                elif choice == "4":
                    success = client.set_device_parameter(device_name, "MOTION_DET_ENABLE", 0)
                    if success:
                        print("✅ Motion detection DISABLED")
                    else:
                        print("❌ Failed to disable motion detection")
                elif choice == "5":
                    success = client.set_device_parameter(device_name, "SPEAK_VOLUME", 50)
                    if success:
                        print("✅ Volume set to 50%")
                    else:
                        print("❌ Failed to set volume")
                elif choice == "6":
                    success = client.set_device_parameter(device_name, "SPEAK_VOLUME", 100)
                    if success:
                        print("✅ Volume set to 100%")
                    else:
                        print("❌ Failed to set volume")
                elif choice == "7":
                    info = client.get_device_info(device_name)
                    if info:
                        # Use the original device's online status since get_device_info returns incorrect status
                        actual_online = original_target_device.get('online', False)
                        print(f"\n📊 Device: {info['name']}")
                        print(f"   Serial: {info['serial_number']}")
                        print(f"   Online: {'🟢 Yes' if actual_online else '🔴 No'}")
                        if info.get('configuration'):
                            print("   Key parameters:")
                            for param in ['FRONT_LIGHT_SWITCH', 'MOTION_DET_ENABLE', 'SPEAK_VOLUME', 'LED_ENABLE']:
                                if param in info['configuration']:
                                    print(f"     {param}: {info['configuration'][param]['formatted']}")
                    else:
                        print("❌ Failed to get device info")
                elif choice == "8":
                    # Toggle LED - get current state first
                    info = client.get_device_info(device_name)
                    if info and info.get('configuration') and 'LED_ENABLE' in info['configuration']:
                        current_state = info['configuration']['LED_ENABLE']['value']
                        new_state = 1 if current_state == 0 else 0
                        success = client.set_device_parameter(device_name, "LED_ENABLE", new_state)
                        if success:
                            state_text = "ON" if new_state == 1 else "OFF"
                            print(f"✅ LED turned {state_text}")
                        else:
                            print("❌ Failed to toggle LED")
                    else:
                        print("❌ Could not get current LED state")
                else:
                    print("❌ Invalid choice!")
                    
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"❌ Error: {e}")
        
        print("\n👋 Goodbye!")
        
    except Exception as e:
        print(f"❌ Error: {e}")


def main():
    """Main function."""
    print("CloudEdge API - Device Control Example")
    print("=" * 50)
    
    # Check environment variables
    required_vars = ["CLOUDEDGE_USERNAME", "CLOUDEDGE_PASSWORD", "CLOUDEDGE_COUNTRY_CODE", "CLOUDEDGE_PHONE_CODE"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        print("❌ Missing required environment variables:")
        for var in missing_vars:
            print(f"   - {var}")
        print("\nPlease create a .env file with your CloudEdge credentials.")
        return
    
    print("Choose mode:")
    print("1. Automated demonstration")
    print("2. Interactive control")
    
    try:
        choice = input("Enter choice (1 or 2): ").strip()
        
        if choice == "1":
            demonstrate_device_control()
        elif choice == "2":
            interactive_device_control()
        else:
            print("❌ Invalid choice!")
            
    except KeyboardInterrupt:
        print("\n👋 Exiting...")


if __name__ == "__main__":
    main()