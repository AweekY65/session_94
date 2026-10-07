"""End-to-end tests for the local NAT/conntrack simulator.

Everything runs in memory; no real network or firewall is touched.
"""

import pytest

from nat_sim import ManualClock, NATSimulator, Packet, PortExhaustedError, Protocol

WAN_HOST = ("203.0.113.7", 80)  # simulated remote server


def make_nat(port_range=(40000, 40010), idle_timeout=60.0):
    clock = ManualClock()
    nat = NATSimulator(
        public_ip="198.51.100.1",
        port_range=port_range,
        idle_timeout=idle_timeout,
        clock=clock,
    )
    return nat, clock


def outbound(nat, proto, int_ip, int_port, remote=WAN_HOST):
    return nat.translate_outbound(
        Packet(proto, int_ip, int_port, remote[0], remote[1])
    )


def inbound(nat, proto, ext_port, remote=WAN_HOST):
    return nat.translate_inbound(
        Packet(proto, remote[0], remote[1], "198.51.100.1", ext_port)
    )


def test_outbound_rewrites_source_to_public_ip_and_allocated_port():
    nat, _ = make_nat()
    out = outbound(nat, Protocol.TCP, "10.0.0.2", 12345)
    assert out.src_ip == "198.51.100.1"
    assert out.src_port == 40000
    assert (out.dst_ip, out.dst_port) == WAN_HOST


def test_same_flow_reuses_mapping_across_packets():
    nat, _ = make_nat()
    first = outbound(nat, Protocol.TCP, "10.0.0.2", 12345)
    second = outbound(nat, Protocol.TCP, "10.0.0.2", 12345)
    assert first.src_port == second.src_port
    assert nat.active_flows() == 1


def test_multiple_internal_hosts_get_distinct_mappings():
    nat, _ = make_nat()
    a = outbound(nat, Protocol.TCP, "10.0.0.2", 12345)
    b = outbound(nat, Protocol.TCP, "10.0.0.3", 12345)
    assert a.src_port != b.src_port
    assert nat.active_flows() == 2


def test_same_source_port_on_different_hosts_is_disambiguated():
    nat, _ = make_nat()
    outbound(nat, Protocol.TCP, "10.0.0.2", 12345)
    out_b = outbound(nat, Protocol.TCP, "10.0.0.3", 12345)
    # Reverse traffic for host B must come back to B, not A.
    reply = inbound(nat, Protocol.TCP, out_b.src_port)
    assert reply is not None
    assert (reply.dst_ip, reply.dst_port) == ("10.0.0.3", 12345)


def test_bidirectional_round_trip_restores_internal_destination():
    nat, _ = make_nat()
    out = outbound(nat, Protocol.TCP, "10.0.0.2", 12345)
    reply = inbound(nat, Protocol.TCP, out.src_port)
    assert reply is not None
    assert reply.dst_ip == "10.0.0.2"
    assert reply.dst_port == 12345
    assert (reply.src_ip, reply.src_port) == WAN_HOST


def test_inbound_without_mapping_is_dropped():
    nat, _ = make_nat()
    assert inbound(nat, Protocol.TCP, 40000) is None
    # Wrong destination IP (not the NAT's public address) is also dropped.
    pkt = Packet(Protocol.TCP, WAN_HOST[0], WAN_HOST[1], "192.0.2.9", 40000)
    assert nat.translate_inbound(pkt) is None


def test_tcp_and_udp_have_independent_mappings():
    nat, _ = make_nat()
    tcp_out = outbound(nat, Protocol.TCP, "10.0.0.2", 12345)
    udp_out = outbound(nat, Protocol.UDP, "10.0.0.2", 12345)
    assert tcp_out.src_port != udp_out.src_port
    # UDP reverse traffic resolves to the UDP flow.
    reply = inbound(nat, Protocol.UDP, udp_out.src_port)
    assert reply is not None
    assert (reply.dst_ip, reply.dst_port) == ("10.0.0.2", 12345)
    # TCP traffic aimed at the UDP port has no mapping and is dropped.
    assert inbound(nat, Protocol.TCP, udp_out.src_port) is None


def test_port_exhaustion_fails_explicitly():
    nat, _ = make_nat(port_range=(40000, 40001))
    outbound(nat, Protocol.TCP, "10.0.0.2", 1)
    outbound(nat, Protocol.TCP, "10.0.0.2", 2)
    with pytest.raises(PortExhaustedError):
        outbound(nat, Protocol.TCP, "10.0.0.2", 3)


def test_idle_flow_expires_and_port_is_reused():
    nat, clock = make_nat(port_range=(40000, 40000), idle_timeout=60.0)
    out = outbound(nat, Protocol.TCP, "10.0.0.2", 12345)
    assert out.src_port == 40000
    clock.advance(61.0)
    assert nat.active_flows() == 0
    # Expired mapping no longer resolves inbound traffic.
    assert inbound(nat, Protocol.TCP, 40000) is None
    # The freed port is handed out again for a new flow.
    out2 = outbound(nat, Protocol.TCP, "10.0.0.3", 12345)
    assert out2.src_port == 40000


def test_activity_refreshes_idle_timeout():
    nat, clock = make_nat(idle_timeout=60.0)
    out = outbound(nat, Protocol.TCP, "10.0.0.2", 12345)
    clock.advance(50.0)
    outbound(nat, Protocol.TCP, "10.0.0.2", 12345)  # keepalive
    clock.advance(50.0)  # 100s since start, 50s since last packet
    assert nat.active_flows() == 1
    reply = inbound(nat, Protocol.TCP, out.src_port)
    assert reply is not None
    assert reply.dst_ip == "10.0.0.2"


def test_inbound_activity_also_refreshes_timeout():
    nat, clock = make_nat(idle_timeout=60.0)
    out = outbound(nat, Protocol.UDP, "10.0.0.2", 5353)
    clock.advance(50.0)
    assert inbound(nat, Protocol.UDP, out.src_port) is not None
    clock.advance(50.0)
    assert nat.active_flows() == 1


def test_distinct_remote_endpoints_get_distinct_flows():
    nat, _ = make_nat()
    a = outbound(nat, Protocol.TCP, "10.0.0.2", 12345, remote=("203.0.113.7", 80))
    b = outbound(nat, Protocol.TCP, "10.0.0.2", 12345, remote=("203.0.113.8", 80))
    assert a.src_port != b.src_port
    # Replies are routed by remote endpoint, not just by external port.
    reply_a = inbound(nat, Protocol.TCP, a.src_port, remote=("203.0.113.7", 80))
    assert reply_a is not None
    # Remote B talking to flow A's external port does not match.
    assert inbound(nat, Protocol.TCP, a.src_port, remote=("203.0.113.8", 80)) is None
