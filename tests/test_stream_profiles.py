import json

from cloudedge.client import _extract_app_streaming_metadata
from cloudedge.p2p.p2p_streamer import P2PStreamer
from cloudedge.stream_profiles import (
    get_available_live_stream_ids,
    get_live_stream_profiles,
    select_default_live_stream_id,
    select_live_stream_id,
    supports_adaptive_live_stream,
)


def _raw_device(caps, version=80):
    return {
        "deviceUUID": "device-uuid",
        "capability": json.dumps(
            {
                "ver": version,
                "caps": json.dumps(caps, separators=(",", ":")),
            }
        ),
    }


def test_extracts_nested_android_stream_capabilities():
    raw = _raw_device(
        {
            "vst": 2,
            "bps2": json.dumps({"0": "2304x1296@15", "2": "640x360@15"}),
            "msc": json.dumps([{"v_id": 2, "bps2": {"2": "640x360@15"}}]),
            "adb": 1,
            "sfi": 2,
            "mcps": 65536,
        },
        version=81,
    )

    metadata = _extract_app_streaming_metadata(raw)

    assert metadata["device_uuid"] == "device-uuid"
    assert metadata["capability_version"] == 81
    assert metadata["bps2"] == {"0": "2304x1296@15", "2": "640x360@15"}
    assert metadata["msc"][0]["v_id"] == 2
    assert metadata["adb"] == 1
    assert metadata["sfi"] == 2
    assert metadata["mcps"] == 65536


def test_adaptive_camera_prefers_stream_105_and_exposes_fixed_profiles():
    device = _extract_app_streaming_metadata(
        _raw_device(
            {
                "bps2": {"0": "2304x1296@15", "2": "640x360@15"},
                "adb": 1,
            },
            version=81,
        )
    )

    assert supports_adaptive_live_stream(device) is True
    assert get_available_live_stream_ids(device) == [105, 100, 102]
    assert select_default_live_stream_id(device) == 105
    assert select_live_stream_id(device, prefer_low=True) == 102

    profiles = get_live_stream_profiles(device)
    assert profiles[1] == {
        "video_id": 100,
        "profile_key": 0,
        "adaptive": False,
        "description": "2304x1296@15",
        "width": 2304,
        "height": 1296,
        "fps": 15,
    }


def test_fixed_profile_selection_maps_bps2_key_to_100_family():
    device = _extract_app_streaming_metadata(
        _raw_device({"bps2": {"2": "1280x720@12"}})
    )

    assert select_default_live_stream_id(device) == 102
    assert get_available_live_stream_ids(device) == [102]


def test_multi_sensor_profiles_use_lowest_video_channel_like_android():
    device = _extract_app_streaming_metadata(
        _raw_device(
            {
                "msc": [
                    {"v_id": 4, "bps2": {"0": "1920x1080@15"}},
                    {"v_id": 1, "bps2": {"3": "320x180@10"}},
                ]
            }
        )
    )

    assert select_default_live_stream_id(device) == 103
    assert get_available_live_stream_ids(device) == [103]


def test_legacy_bitrate_mask_uses_android_default_priority():
    device = {"bps": (1 << 0) | (1 << 2)}

    assert select_default_live_stream_id(device) == 2
    assert get_available_live_stream_ids(device) == [0, 2]


def test_device_type_16_forces_stream_zero():
    device = {
        "type_id": 16,
        "capability_version": 81,
        "adb": 1,
        "bps2": {"2": "640x360@15"},
    }

    assert select_default_live_stream_id(device) == 0
    assert get_available_live_stream_ids(device) == [0]


class _RefreshCapabilityApi:
    def refresh_streaming_metadata(self, device):
        device.update(
            {
                "capability_version": 81,
                "adb": 1,
                "bps2": {"0": "1920x1080@15"},
            }
        )
        return device

    def get_device_online_status(self, serial_number):
        return "online"


def test_streamer_reselects_automatic_video_id_after_metadata_refresh(monkeypatch):
    streamer = P2PStreamer(
        api=_RefreshCapabilityApi(),
        device={
            "serial_number": "ppsl123",
            "device_uuid": "device-uuid",
            "host_key": "host-key",
            "name": "Camera",
        },
    )
    selected = []

    monkeypatch.setattr(
        "cloudedge.p2p.p2p_streamer._resolve_signaling_candidates",
        lambda api, device=None: [("signaling.example", 28974)],
    )

    class _Sig:
        def __init__(self, host, port):
            pass

        def connect(self):
            pass

        def send_logout(self, device_uuid):
            pass

        def close(self):
            pass

    monkeypatch.setattr("cloudedge.p2p.p2p_streamer.MsgSvrClient", _Sig)

    def capture_video_id(self, sig):
        selected.append(self._video_id)
        self.request_stop()
        return (1, 1)

    monkeypatch.setattr(P2PStreamer, "_do_stream", capture_video_id)

    assert streamer._video_id == 0
    assert streamer.run_session() == (1, 1)
    assert selected == [105]
