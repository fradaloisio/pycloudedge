"""CloudEdge live-stream capability parsing and stream selection."""

import json
import re
from typing import Any, Dict, List


_CAPABILITY_FIELDS = (
    "vst",
    "bps",
    "bps2",
    "msc",
    "pbr",
    "adb",
    "sfi",
    "mcps",
    "rns",
    "auf",
)
_PROFILE_RE = re.compile(r"^\s*(\d+)x(\d+)(?:@([0-9.]+))?")


def _decode_json(value: Any) -> Any:
    """Decode JSON strings while accepting values already decoded by callers."""
    decoded = value
    for _ in range(2):
        if not isinstance(decoded, str) or not decoded.strip():
            break
        try:
            decoded = json.loads(decoded)
        except (TypeError, ValueError):
            break
    return decoded


def _as_int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def extract_stream_capabilities(device: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the capability fields used by the Android streaming SDK.

    The API normally returns ``capability`` as JSON whose ``caps`` member is
    itself either a JSON object or an escaped JSON string.
    """
    capability = _decode_json(device.get("capability"))
    if not isinstance(capability, dict):
        capability = {}

    caps = _decode_json(capability.get("caps", device.get("caps")))
    if not isinstance(caps, dict):
        caps = {}

    metadata: Dict[str, Any] = {}
    version = capability.get("ver", device.get("ver"))
    if version not in (None, ""):
        metadata["capability_version"] = _as_int(version)
    if caps:
        metadata["capabilities"] = caps

    for field in _CAPABILITY_FIELDS:
        value = caps.get(field, device.get(field))
        if value in (None, ""):
            continue
        if field in ("bps2", "pbr", "msc"):
            value = _decode_json(value)
        metadata[field] = value

    return metadata


def _profile_source(device: Dict[str, Any]) -> Dict[str, Any]:
    bps2 = _decode_json(device.get("bps2"))
    if isinstance(bps2, dict) and bps2:
        return bps2

    msc = _decode_json(device.get("msc"))
    if not isinstance(msc, list):
        return {}

    entries = [entry for entry in msc if isinstance(entry, dict)]
    entries.sort(key=lambda entry: _as_int(entry.get("v_id"), 2**31 - 1))
    for entry in entries:
        entry_bps2 = _decode_json(entry.get("bps2"))
        if isinstance(entry_bps2, dict) and entry_bps2:
            return entry_bps2
    return {}


def _parse_profile_value(value: Any) -> Dict[str, Any]:
    profile: Dict[str, Any] = {"description": str(value)}
    match = _PROFILE_RE.match(str(value))
    if not match:
        return profile
    profile["width"] = int(match.group(1))
    profile["height"] = int(match.group(2))
    if match.group(3):
        fps = float(match.group(3))
        profile["fps"] = int(fps) if fps.is_integer() else fps
    return profile


def supports_adaptive_live_stream(device: Dict[str, Any]) -> bool:
    """Return whether Android selects the special adaptive stream ID 105."""
    return (
        _as_int(device.get("capability_version")) >= 81
        and _as_int(device.get("adb")) == 1
    )


def get_live_stream_profiles(device: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return normalized live profiles advertised by a camera."""
    profiles: List[Dict[str, Any]] = []
    if supports_adaptive_live_stream(device):
        profiles.append(
            {
                "video_id": 105,
                "profile_key": None,
                "adaptive": True,
                "description": "adaptive",
            }
        )

    source = _profile_source(device)
    numeric_keys = []
    for key in source:
        profile_key = _as_int(key)
        if 0 <= profile_key <= 4:
            numeric_keys.append((profile_key, key))

    for profile_key, source_key in sorted(numeric_keys):
        profile = {
            "video_id": 100 + profile_key,
            "profile_key": profile_key,
            "adaptive": False,
        }
        profile.update(_parse_profile_value(source[source_key]))
        profiles.append(profile)

    return profiles


def get_available_live_stream_ids(device: Dict[str, Any]) -> List[int]:
    """Return stream IDs in the order preferred by the Android client."""
    if _as_int(device.get("type_id")) == 16:
        return [0]

    profile_ids = [profile["video_id"] for profile in get_live_stream_profiles(device)]
    if profile_ids:
        return profile_ids

    bps = _as_int(device.get("bps"), 0)
    if bps > 0:
        return [stream_id for stream_id in range(10) if bps & (1 << stream_id)]
    if _as_int(device.get("vst")) == 1:
        return [0]
    return [0, 1]


def select_default_live_stream_id(device: Dict[str, Any]) -> int:
    """Select the same default live stream family used by CloudEdge Android."""
    if _as_int(device.get("type_id")) == 16:
        return 0
    if supports_adaptive_live_stream(device):
        return 105

    source = _profile_source(device)
    for profile_key in range(4):
        if str(profile_key) in source or profile_key in source:
            return 100 + profile_key

    bps = _as_int(device.get("bps"), 0)
    if bps > 0:
        # This is the priority order in CloudEdge's getDefaultStreamId().
        for stream_id in (1, 2, 3, 0, 4, 5, 8, 6, 7, 9):
            if bps & (1 << stream_id):
                return stream_id

    if "vst" in device:
        return 1
    return 0


def select_live_stream_id(device: Dict[str, Any], prefer_low: bool = False) -> int:
    """Choose a default or lowest-resolution live stream for a consumer."""
    if not prefer_low:
        return select_default_live_stream_id(device)

    profiles = [
        profile
        for profile in get_live_stream_profiles(device)
        if not profile.get("adaptive")
    ]
    profiles_with_size = [
        profile for profile in profiles if profile.get("width") and profile.get("height")
    ]
    if profiles_with_size:
        return min(
            profiles_with_size,
            key=lambda profile: profile["width"] * profile["height"],
        )["video_id"]
    if profiles:
        return profiles[-1]["video_id"]

    available = get_available_live_stream_ids(device)
    return available[-1]
