#!/usr/bin/env python3
"""
Network Ping Status Example
===========================

This example demonstrates the enhanced network ping functionality
for accurate device online status when on the same local network.

Requirements:
- cloudedge-api library installed
- .env file with your credentials
- Devices on the same local network as the client
"""

import os
import sys
from dotenv import load_dotenv

# Add parent directory to path for local development
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cloudedge import CloudEdgeClient

# Load environment variables
load_dotenv()


def main():
    """Demonstrate enhanced network ping functionality."""
    print("CloudEdge API - Network Ping Status Example")
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
    
    # Get credentials from environment
    username = os.getenv("CLOUDEDGE_USERNAME")
    password = os.getenv("CLOUDEDGE_PASSWORD")
    country_code = os.getenv("CLOUDEDGE_COUNTRY_CODE")
    phone_code = os.getenv("CLOUDEDGE_PHONE_CODE")
    
    # Create client with enhanced ping enabled
    print("🔧 Creating client with enhanced ping functionality...")
    # Create client with enhanced ping functionality
    client = CloudEdgeClient(
        username=username,
        password=password,
        country_code=country_code,
        phone_code=phone_code,
        debug=True,  # Enable debug mode to see ping details
        enable_network_ping=True,
        ping_timeout=2.0
    )
    
    try:
        # Authenticate
        success = client.authenticate()
        
        if not success:
            print("❌ Authentication failed!")
            return
        
        print("✅ Authentication successful!")
        
        # Show network configuration
        network_info = client.get_network_info()
        print(f"\n🌐 Network Configuration:")
        print(f"   Local network detected: {network_info['local_network']}")
        print(f"   Ping enabled: {network_info['ping_enabled']}")
        print(f"   Ping timeout: {network_info['ping_timeout']}s")
        
        # Get devices with ping-based status
        print(f"\n📡 Device Status with IP Addresses")
        print("=" * 70)
        
        devices = client.get_all_devices()
        
        if devices:
            print(f"{'Device Name':<20} {'IP Address':<15} {'Status':<12} {'Serial Number'}")
            print("-" * 70)
            
            for device in devices:
                device_name = device['name']
                status = "🟢 Online" if device['online'] else "🔴 Offline"
                serial = device['serial_number']
                
                # Get device IP from configuration
                device_ip = "N/A"
                try:
                    config = client.get_device_config(serial)
                    if config and 'iot' in config:
                        iot_data = config['iot']
                        if isinstance(iot_data, dict):
                            # Parameter 126 is IP_ADDRESS
                            device_ip = iot_data.get('126', 'N/A')
                except Exception:
                    pass
                
                print(f"{device_name:<20} {device_ip:<15} {status:<12} {serial}")
            
            print(f"\n📊 Summary:")
            online_count = sum(1 for d in devices if d['online'])
            offline_count = len(devices) - online_count
            print(f"   Total devices: {len(devices)}")
            print(f"   Online: {online_count}")
            print(f"   Offline: {offline_count}")
        else:
            print("❌ No devices found")
        
        print(f"\n💡 Tips:")
        print(f"   • Ping-based status provides real-time accuracy when on same network")
        print(f"   • Device IPs are retrieved from device configuration")
        print(f"   • Status is determined by ping response when devices are local")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()