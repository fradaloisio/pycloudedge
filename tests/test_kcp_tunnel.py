import struct

from cloudedge.p2p.kcp_tunnel import (
    IVA_FRAME_SIZE,
    KCP_CMD_ACK,
    KCP_CMD_PUSH,
    KCP_HEADER_SIZE,
    KCP_WND,
    KcpTunnel,
    build_iva_handshake,
    build_iva_data_frame,
    build_kcp_segment,
    parse_iva_frame,
    parse_kcp_segment,
    parse_kcp_segments,
)


def test_iva_handshake_matches_android_xts_capture():
    frame = build_iva_handshake(0x070E8A6E, 0x06E9AFF6)

    assert frame == bytes.fromhex(
        "ff0100006e8a0e07f6afe9060003127000000000"
    )


def test_parse_kcp_segments_reads_compound_udp_datagram():
    first = build_kcp_segment(KCP_CMD_PUSH, sn=10, data=b"first")
    second = build_kcp_segment(KCP_CMD_ACK, sn=7)

    segments = parse_kcp_segments(first + second)

    assert [segment["cmd"] for segment in segments] == [KCP_CMD_PUSH, KCP_CMD_ACK]
    assert segments[0]["data"] == b"first"
    assert segments[1]["sn"] == 7
    assert parse_kcp_segment(first + second) == segments[0]


def test_parse_kcp_segments_rejects_truncated_payload():
    packet = bytearray(build_kcp_segment(KCP_CMD_PUSH, sn=1, data=b"short"))
    struct.pack_into("<I", packet, 0x14, 100)

    assert parse_kcp_segments(bytes(packet)) is None
    assert parse_kcp_segment(bytes(packet)) is None


def test_parse_kcp_segments_rejects_truncated_compound_header():
    valid = build_kcp_segment(KCP_CMD_ACK, sn=1)

    assert len(valid) == KCP_HEADER_SIZE
    assert parse_kcp_segments(valid + b"\x0c\x00") is None


def test_parse_iva_frame_rejects_truncated_declared_payload():
    frame = bytearray(build_iva_data_frame(b"ok", 1, 2))
    struct.pack_into("<I", frame, 0x10, 100)

    assert len(frame) == IVA_FRAME_SIZE + 2
    assert parse_iva_frame(bytes(frame)) is None


def test_process_input_delivers_all_pushes_in_compound_datagram():
    tunnel = KcpTunnel(lambda data: None)
    packet = b"".join(
        (
            build_kcp_segment(KCP_CMD_PUSH, sn=4, frg=0, data=b"first"),
            build_kcp_segment(KCP_CMD_PUSH, sn=5, frg=0, data=b"second"),
        )
    )

    assert tunnel.process_input(packet) == ("data", b"first")
    assert tunnel.poll_data() == b"second"
    assert tunnel.poll_data() is None
    assert tunnel.pending_acks == [(4, 0), (5, 0)]


def test_process_input_handles_all_acks_in_compound_datagram():
    tunnel = KcpTunnel(lambda data: None)
    tunnel.sent_segments = {3: (b"one", 0), 4: (b"two", 0)}
    packet = b"".join(
        (
            build_kcp_segment(KCP_CMD_ACK, sn=3),
            build_kcp_segment(KCP_CMD_ACK, sn=4),
        )
    )

    assert tunnel.process_input(packet) == ("ack", 3)
    assert tunnel.acked_sns == {3, 4}
    assert tunnel.sent_segments == {}


def test_process_input_rejects_entire_malformed_compound_datagram():
    tunnel = KcpTunnel(lambda data: None)
    valid = build_kcp_segment(KCP_CMD_PUSH, sn=0, data=b"must-not-arrive")
    truncated = bytearray(build_kcp_segment(KCP_CMD_PUSH, sn=1, data=b"bad"))
    struct.pack_into("<I", truncated, 0x14, 50)

    assert tunnel.process_input(valid + bytes(truncated)) is None
    assert tunnel.next_recv_sn == -1
    assert tunnel.recv_buf == {}
    assert tunnel.pending_acks == []


def test_gap_skip_discards_damaged_fragmented_message():
    tunnel = KcpTunnel(lambda data: None)

    assert tunnel.process_input(
        build_kcp_segment(KCP_CMD_PUSH, sn=0, frg=1, data=b"before-gap")
    )[0] == "fragment"
    assert tunnel.process_input(
        build_kcp_segment(KCP_CMD_PUSH, sn=2, frg=0, data=b"truncated-tail")
    )[0] == "fragment"

    assert tunnel.skip_gap() is True
    assert tunnel.poll_data() is None
    assert tunnel.recv_frag_buf == []

    assert tunnel.process_input(
        build_kcp_segment(KCP_CMD_PUSH, sn=3, frg=0, data=b"clean-message")
    ) == ("data", b"clean-message")


def test_advertised_window_matches_android_client_capture():
    segment = parse_kcp_segment(build_kcp_segment(KCP_CMD_ACK, sn=1))

    assert KCP_WND == 1024
    assert segment["wnd"] == 1024
