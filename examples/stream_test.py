#!/usr/bin/env python3
"""
Example demonstrating how to use the P2P streaming capabilities
of pycloudedge to capture video frames from a camera.
"""
import argparse
import os
import shutil
import subprocess
import sys
import threading
import time

# Add parent directory to path to import local cloudedge
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from cloudedge.client import CloudEdgeClient

def _convert_hevc_to_annex_b(frame_data: bytes) -> bytes:
    """Convert a single HEVC access unit to Annex B if needed."""
    if not frame_data:
        return frame_data

    if frame_data.startswith(b"\x00\x00\x00\x01") or frame_data.startswith(b"\x00\x00\x01"):
        return frame_data

    converted = bytearray()
    offset = 0
    remaining = len(frame_data)

    # Many cameras emit NAL units with a 4-byte big-endian length prefix.
    while remaining >= 4:
        nal_len = int.from_bytes(frame_data[offset:offset + 4], "big")
        next_offset = offset + 4 + nal_len
        if nal_len <= 0 or next_offset > len(frame_data):
            break
        converted.extend(b"\x00\x00\x00\x01")
        converted.extend(frame_data[offset + 4:next_offset])
        offset = next_offset
        remaining = len(frame_data) - offset

    if converted and offset == len(frame_data):
        return bytes(converted)

    # Fallback: treat the whole payload as one NAL unit.
    return b"\x00\x00\x00\x01" + frame_data

def parse_args():
    parser = argparse.ArgumentParser(
        description="Test CloudEdge P2P streaming and optionally display live video via ffplay.",
    )
    parser.add_argument(
        "env_file",
        nargs="?",
        default=".env",
        help="Path to the .env file containing CloudEdge credentials.",
    )
    parser.add_argument(
        "--ffplay",
        action="store_true",
        help="Pipe raw HEVC video to ffplay for live playback.",
    )
    return parser.parse_args()

def main():
    args = parse_args()

    # Load credentials
    from dotenv import load_dotenv
    env_arg = args.env_file
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    env_candidates = [
        env_arg,
        os.path.join(repo_root, env_arg),
        os.path.join(os.path.dirname(__file__), env_arg),
    ]
    env_file = next((path for path in env_candidates if os.path.exists(path)), None)
    if not env_file:
        print(f"Environment file not found: {env_arg}")
        sys.exit(1)

    load_dotenv(env_file, override=True)
    
    username = os.environ.get("CLOUDEDGE_USERNAME")
    password = os.environ.get("CLOUDEDGE_PASSWORD")
    country_code = os.environ.get("CLOUDEDGE_COUNTRY_CODE", "IT")
    phone_code = os.environ.get("CLOUDEDGE_PHONE_CODE", "39")
    
    if not username or not password:
        print("Please set CLOUDEDGE_USERNAME and CLOUDEDGE_PASSWORD")
        sys.exit(1)

    print("Authenticating...")
    
    # Use a separate cache file based on the env filename to avoid cross-contamination
    cache_file = f".cloudedge_session_cache_{os.path.basename(env_file).replace('.', '')}"
    
    client = CloudEdgeClient(
        username=username,
        password=password,
        country_code=country_code,
        phone_code=phone_code,
        session_cache_file=cache_file,
    )
    
    try:
        client.authenticate()
        print("✅ Authenticated successfully")
    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        sys.exit(1)
        
    print("Discovering devices across all homes...")
    devices = []
    try:
        homes_resp = client.get_homes()
        print(f"  Found {len(homes_resp)} homes.")
        for home in homes_resp:
            hid = home.get("home_id") or home.get("id")
            if hid:
                h_devices = client.get_devices_by_home(str(hid))
                if h_devices:
                    devices.extend(h_devices)
                    
        # Also grab default home devices just in case they aren't tied to a specific home ID
        def_devices = client.get_devices()
        if def_devices:
            # Add only devices we haven't seen yet based on SN
            seen_sns = {d.get("serial_number", d.get("snNum")) for d in devices}
            for d in def_devices:
                sn = d.get("serial_number", d.get("snNum"))
                if sn not in seen_sns:
                    devices.append(d)
                    
    except Exception as e:
        print(f"Error fetching homes/devices: {e}")
        # fallback
        devices = client.get_devices()

    if not devices:
        print("No devices found.")
        sys.exit(0)
        
    print(f"Found {len(devices)} devices:")
    cameras = devices
    
    if not cameras:
        print("No cameras found.")
        sys.exit(0)
        
    for i, cam in enumerate(cameras):
        status = "Online" if cam.get("online") else "Offline"
        print(f"  [{i}] {cam.get('name', 'Camera')} (SN: {cam.get('serial_number')}) [{status}]")
        
    # Pick the camera named "Garage", or the first one if it's the only one found.
    # In this case we specifically want "Garage"
    target_cam = None
    for cam in cameras:
        if cam.get("name", "").lower() == "garage":
            target_cam = cam
            break
            
    if not target_cam:
        if len(cameras) > 0:
            target_cam = cameras[0]
            print(f"\nCamera 'Garage' not found. Falling back to '{target_cam.get('name')}'...")
        else:
            print("\nCamera 'Garage' not found and no other cameras available.")
            sys.exit(1)
            
    sn = target_cam.get("serial_number")
    
    print(f"\nTargeting camera: {target_cam.get('name')} ({sn})")
    
    # State for our callbacks
    video_frames_received = 0
    total_video_bytes = 0
    ffplay_proc = None

    if args.ffplay:
        ffplay_path = shutil.which("ffplay")
        if not ffplay_path:
            print("ffplay not found in PATH. Install ffmpeg or run without --ffplay.")
            sys.exit(1)
        ffplay_proc = subprocess.Popen(
            [
                ffplay_path,
                "-loglevel", "warning",
                "-fflags", "nobuffer",
                "-flags", "low_delay",
                "-framedrop",
                "-f", "hevc",
                "-i", "pipe:0",
            ],
            stdin=subprocess.PIPE,
        )
        print("Live preview enabled via ffplay.")
    
    def on_video_frame(frame_data: bytes):
        nonlocal video_frames_received, total_video_bytes
        video_frames_received += 1
        total_video_bytes += len(frame_data)
        if ffplay_proc and ffplay_proc.stdin:
            try:
                ffplay_proc.stdin.write(_convert_hevc_to_annex_b(frame_data))
                ffplay_proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
        if video_frames_received % 30 == 0:
            print(f"  [STREAM] Received {video_frames_received} frames ({total_video_bytes / 1024:.1f} KB)")
            
    def on_login():
        print("  [STREAM] P2P Stream connected and VVP login succeeded!")
        
    def on_disconnect():
        print("  [STREAM] P2P Stream disconnected.")

    streamer = client.create_streamer(
        device=target_cam,
        on_video=on_video_frame,
        on_login=on_login,
        on_disconnect=on_disconnect,
    )
    
    if not streamer:
        print("Failed to initialize streamer.")
        sys.exit(1)
        
    print("\nStarting P2P stream session... (will automatically wake battery cameras)")
    print("Press Ctrl+C to stop.\n")
    
    # Run the streamer in a background thread so we can wait and cleanly stop it
    stream_thread = threading.Thread(target=streamer.run_session, daemon=True)
    stream_thread.start()
    
    try:
        # Run for 15 seconds to collect some frames
        for _ in range(15):
            time.sleep(1)
            if not stream_thread.is_alive():
                print("Stream thread exited early.")
                break
    except KeyboardInterrupt:
        print("\nStopping stream...")
        
    finally:
        streamer.request_stop()
        stream_thread.join(timeout=3.0)
        if ffplay_proc:
            if ffplay_proc.stdin:
                try:
                    ffplay_proc.stdin.close()
                except OSError:
                    pass
            try:
                ffplay_proc.terminate()
                ffplay_proc.wait(timeout=2.0)
            except Exception:
                pass
        print(f"\nStream session ended. Total frames: {video_frames_received}")

if __name__ == "__main__":
    main()
