#!/usr/bin/env python3
"""
Command Line Interface for CloudEdge API Library
================================================

Provides a simple CLI for common CloudEdge operations.
"""

import asyncio
import argparse
import os
import sys
import json
import time
from typing import Optional
from dotenv import load_dotenv

from .client import CloudEdgeClient
from .exceptions import CloudEdgeError, AuthenticationError, DeviceNotFoundError


class CloudEdgeCLI:
    """Command line interface for CloudEdge API."""
    
    def __init__(self):
        self.client: Optional[CloudEdgeClient] = None
        
    def authenticate(self, username: str, password: str, country_code: str, phone_code: str, debug: bool = False):
        """Authenticate with CloudEdge API."""
        self.client = CloudEdgeClient(username, password, country_code, phone_code, debug=debug)
        return self.client.authenticate()
        
    def list_homes(self):
        """List all homes."""
        if not self.client:
            raise AuthenticationError("Not authenticated")
            
        homes = self.client.get_homes()
        
        if not homes:
            print("No homes found.")
            return
            
        print(f"Found {len(homes)} home(s):")
        print("-" * 80)
        for i, home in enumerate(homes, 1):
            print(f"{i:2d}. {home['name']:<30} (ID: {home['home_id']})")
            print(f"    Owner: {home['owner']}")
            print(f"    Rooms: {home['rooms']}")
            print(f"    Devices: {home['device_count']}")
            print()
            
    def list_devices(self, home_id: Optional[str] = None, all_homes: bool = False):
        """List devices."""
        if not self.client:
            raise AuthenticationError("Not authenticated")
            
        if all_homes:
            devices = self.client.get_all_devices()
            print("Getting devices from all homes...")
        elif home_id:
            devices = self.client.get_devices(home_id=home_id)
            print(f"Getting devices from home {home_id}...")
        else:
            devices = self.client.get_devices()
            print("Getting devices from default home...")
        
        if not devices:
            print("❌ No devices found!")
            return
            
        print(f"Found {len(devices)} device(s):")
        print("-" * 80)
        for i, device in enumerate(devices, 1):
            status = "🟢 Online" if device['online'] else "🔴 Offline"
            home_info = f" (Home: {device.get('home_id', 'Default')})" if device.get('home_id') else ""
            print(f"{i:2d}. {device['name']:<30} {status}{home_info}")
            print(f"    Serial: {device['serial_number']}")
            print(f"    Type: {device['type']}")
            print()
            
    def device_info(self, device_name: str):
        """Get detailed device information."""
        if not self.client:
            raise AuthenticationError("Not authenticated")
            
        device_info = self.client.get_device_info(device_name, include_config=True)
        
        if not device_info:
            raise DeviceNotFoundError(f"Device '{device_name}' not found")
            
        print(f"Device Information: {device_info['name']}")
        print("=" * 50)
        print(f"Serial Number: {device_info['serial_number']}")
        print(f"Type: {device_info['type']}")
        print(f"Status: {'🟢 Online' if device_info['online'] else '🔴 Offline'}")
        
        if device_info.get('configuration'):
            print(f"\nConfiguration:")
            print("-" * 30)
            for param, info in device_info['configuration'].items():
                print(f"{param:<25}: {info['formatted']}")
                
    def set_parameter(self, device_name: str, parameter: str, value: str):
        """Set a device parameter."""
        if not self.client:
            raise AuthenticationError("Not authenticated")
            
        # Try to convert value to appropriate type
        try:
            if value.isdigit():
                value = int(value)
            elif value.replace('.', '').isdigit():
                value = float(value)
        except:
            pass  # Keep as string
            
        success = self.client.set_device_parameter(device_name, parameter, value)
        
        if success:
            print(f"✅ Successfully set {parameter} = {value} on device '{device_name}'")
        else:
            print(f"❌ Failed to set {parameter} on device '{device_name}'")
            
    def monitor(self, device_name: Optional[str] = None, interval: int = 30):
        """Monitor device(s) status."""
        if not self.client:
            raise AuthenticationError("Not authenticated")
            
        if device_name:
            devices = [self.client.find_device_by_name(device_name)]
            if not devices[0]:
                raise DeviceNotFoundError(f"Device '{device_name}' not found")
        else:
            devices = self.client.get_all_devices()
            
        print(f"Monitoring {len(devices)} device(s) every {interval} seconds...")
        print("Press Ctrl+C to stop")
        print()
        
        try:
            while True:
                for device in devices:
                    status = self.client.get_device_status(device['device_id'])
                    if status:
                        status_text = "🟢 Online" if status['online'] else "🔴 Offline"
                        home_info = f" (Home: {device.get('home_id', 'Default')})" if device.get('home_id') else ""
                        print(f"{device['name']:<30}: {status_text}{home_info}")
                    else:
                        print(f"{device['name']:<30}: ❓ Unknown")
                        
                print()
                time.sleep(interval)
                
        except KeyboardInterrupt:
            print("Monitoring stopped.")


def get_credentials():
    """Get credentials from environment or prompt user."""
    username = os.getenv("CLOUDEDGE_USERNAME")
    password = os.getenv("CLOUDEDGE_PASSWORD")
    country_code = os.getenv("CLOUDEDGE_COUNTRY_CODE")
    phone_code = os.getenv("CLOUDEDGE_PHONE_CODE")
    
    if not all([username, password, country_code, phone_code]):
        print("Missing credentials. Please set environment variables or use command line options.")
        return None, None, None, None
        
    return username, password, country_code, phone_code


def main():
    """Main CLI function."""
    # Load environment variables from .env file
    load_dotenv()
    
    parser = argparse.ArgumentParser(description="CloudEdge API Command Line Interface")
    parser.add_argument("--username", help="CloudEdge username")
    parser.add_argument("--password", help="CloudEdge password") 
    parser.add_argument("--country-code", help="Country code (e.g., US)")
    parser.add_argument("--phone-code", help="Phone code (e.g., +1)")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # List homes command  
    subparsers.add_parser("homes", help="List all homes")
    
    # List devices command
    list_parser = subparsers.add_parser("list", help="List devices")
    list_parser.add_argument("--home-id", help="Get devices from specific home ID")
    list_parser.add_argument("--all-homes", action="store_true", help="Get devices from all homes")
    
    # Device info command
    info_parser = subparsers.add_parser("info", help="Get device information")
    info_parser.add_argument("device_name", help="Device name")
    
    # Set parameter command
    set_parser = subparsers.add_parser("set", help="Set device parameter")
    set_parser.add_argument("device_name", help="Device name")
    set_parser.add_argument("parameter", help="Parameter name")
    set_parser.add_argument("value", help="Parameter value")
    
    # Monitor command
    monitor_parser = subparsers.add_parser("monitor", help="Monitor device status")
    monitor_parser.add_argument("--device", help="Device name (monitor all if not specified)")
    monitor_parser.add_argument("--interval", type=int, default=30, help="Check interval in seconds")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
        
    # Get credentials
    username = args.username or os.getenv("CLOUDEDGE_USERNAME")
    password = args.password or os.getenv("CLOUDEDGE_PASSWORD")
    country_code = args.country_code or os.getenv("CLOUDEDGE_COUNTRY_CODE")
    phone_code = args.phone_code or os.getenv("CLOUDEDGE_PHONE_CODE")
    
    if not all([username, password, country_code, phone_code]):
        print("❌ Missing credentials. Set environment variables or use command line options.")
        print("Required: CLOUDEDGE_USERNAME, CLOUDEDGE_PASSWORD, CLOUDEDGE_COUNTRY_CODE, CLOUDEDGE_PHONE_CODE")
        sys.exit(1)
    
    # Create CLI instance
    cli = CloudEdgeCLI()
    
    try:
        # Authenticate
        print("🔐 Authenticating...")
        success = cli.authenticate(username, password, country_code, phone_code, debug=args.debug)
        if not success:
            print("❌ Authentication failed!")
            sys.exit(1)
        print("✅ Authenticated successfully!")
        print()
        
        # Execute command
        if args.command == "homes":
            cli.list_homes()
        elif args.command == "list":
            cli.list_devices(home_id=getattr(args, 'home_id', None), 
                           all_homes=getattr(args, 'all_homes', False))
        elif args.command == "info":
            cli.device_info(args.device_name)
        elif args.command == "set":
            cli.set_parameter(args.device_name, args.parameter, args.value)
        elif args.command == "monitor":
            cli.monitor(args.device, args.interval)
            
    except CloudEdgeError as e:
        print(f"❌ CloudEdge Error: {e}")
        sys.exit(1)
    except Exception as e:
        if args.debug:
            import traceback
            traceback.print_exc()
        else:
            print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()