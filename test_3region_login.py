#!/usr/bin/env python3
"""Test login with 3 region accounts after endpoint refactoring."""
import sys, os

PROJECT_ROOT = "/Users/fdaloisio/mygit/pycloudedge"
sys.path.insert(0, PROJECT_ROOT)

from cloudedge import CloudEdgeClient

ACCOUNTS = [
    (".env", "EU/Italy"),
    (".env_usa", "US"),
    (".env_australia", "Australia"),
]

def load_env(path):
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
    return env

for env_file, label in ACCOUNTS:
    env_path = os.path.join(PROJECT_ROOT, env_file)
    env = load_env(env_path)
    # Use a separate cache file per account to avoid cross-contamination
    cache_file = f"/tmp/.cloudedge_cache_{env['CLOUDEDGE_COUNTRY_CODE'].lower()}"
    # Remove old cache to force fresh discovery
    if os.path.exists(cache_file):
        os.remove(cache_file)
    print(f"\n{'='*60}")
    print(f"  Testing {label} ({env['CLOUDEDGE_COUNTRY_CODE']})")
    print(f"{'='*60}")
    try:
        client = CloudEdgeClient(
            username=env["CLOUDEDGE_USERNAME"],
            password=env["CLOUDEDGE_PASSWORD"],
            country_code=env["CLOUDEDGE_COUNTRY_CODE"],
            phone_code=env["CLOUDEDGE_PHONE_CODE"],
            debug=True,
            session_cache_file=cache_file,
        )
        success = client.authenticate()
        print(f"  ✅ Login OK")
        print(f"     BASE_URL:         {client.BASE_URL}")
        print(f"     OPENAPI_BASE_URL: {client.OPENAPI_BASE_URL}")
        
        # Get list of cameras
        devices = client.get_devices()
        print(f"     Found {len(devices)} devices:")
        for dev in devices:
            status = "Online" if dev.get("online") else "Offline"
            print(f"       - {dev.get('name', 'Unknown')} (SN: {dev.get('serial_number', 'Unknown')}) [{status}]")
    except Exception as e:
        print(f"  ❌ Login FAILED: {e}")
