"""Terminal-only tests: no sockets, no firewall, no external network."""

import pytest

from nat_sim import (
    Direction,
    ManualClock,
    NatConfig,
    NatEngine,
    NoMappingError,
    Packet,
    PortExhaustedError,
    Protocol,
)

EXT_IP = "203.0.113.1"
SERVER_IP = "198.51.100.7"
HOST_A = "10.0.0.1"
HOST_B = "10.0.0.2"


def make_engine(**overrides):
    clock = ManualClock(start=1000.0)
    config = NatConfig(
        external_ip=EXT_IP,
        port_range_start=overrides.pop("port_range_start", 40000),
        port_range_end=overrides.pop("port_range_end", 40009),
        tcp_idle_timeout=overrides.pop("tcp_idle_timeout", 300.0),
        udp_idle_timeout=overrides.pop("udp_idle_timeout", 60.0),
    )
    return NatEngine(config=config, clock=clock), clock


def outbound(src_ip, src_port, protocol=Protocol.TCP, dst_ip=SERVER_IP, dst_port=80):
    return Packet(
        direction=Direction.OUTBOUND,
        protocol=protocol,
        src_ip=src_ip,
        src_port=src_port,
        dst_ip=dst_ip,
        dst_port=dst_port,
    )


def inbound_reply(src_ip, src_port, dst_port, protocol=Protocol.TCP):
    return Packet(
        direction=Direction.INBOUND,
        protocol=protocol,
        src_ip=src_ip,
        src_port=src_port,
        dst_ip=EXT_IP,
        dst_port=dst_port,
    )


class TestOutboundMapping:
    def test_source_is_rewritten_to_external_ip_and_allocated_port(self):
        nat, _ = make_engine()
        out = nat.process(outbound(HOST_A, 12345))
        assert out.src_ip == EXT_IP
        assert out.src_port == 40000  # lowest free port in range
        assert out.dst_ip == SERVER_IP
        assert out.dst_port == 80

    def test_same_flow_reuses_mapping(self):
        nat, clock = make_engine()
        first = nat.process(outbound(HOST_A, 12345))
        clock.advance(10)
        second = nat.process(outbound(HOST_A, 12345))
        third = nat.process(outbound(HOST_A, 12345))
        assert first.src_port == second.src_port == third.src_port == 40000
        assert nat.flow_count() == 1

    def test_distinct_internal_hosts_with_same_source_port_get_unique_ports(self):
        nat, _ = make_engine()
        out_a = nat.process(outbound(HOST_A, 12345))
        out_b = nat.process(outbound(HOST_B, 12345))
        assert out_a.src_port != out_b.src_port
        assert {out_a.src_port, out_b.src_port} == {40000, 40001}
        assert nat.flow_count() == 2

    def test_same_host_different_source_port_gets_distinct_mapping(self):
        nat, _ = make_engine()
        out_1 = nat.process(outbound(HOST_A, 1111))
        out_2 = nat.process(outbound(HOST_A, 2222))
        assert out_1.src_port != out_2.src_port

    def test_tcp_and_udp_have_independent_mappings(self):
        nat, _ = make_engine()
        tcp_out = nat.process(outbound(HOST_A, 12345, Protocol.TCP))
        udp_out = nat.process(outbound(HOST_A, 12345, Protocol.UDP))
        assert nat.flow_count() == 2
        # Same 5-tuple modulo protocol must not share a conntrack entry:
        # each protocol allocates from its own pool, starting at the bottom.
        assert tcp_out.src_port == 40000
        assert udp_out.src_port == 40000
        # And each reverse path resolves independently.
        tcp_back = nat.process(inbound_reply(SERVER_IP, 80, 40000, Protocol.TCP))
        udp_back = nat.process(inbound_reply(SERVER_IP, 80, 40000, Protocol.UDP))
        assert (tcp_back.dst_ip, tcp_back.dst_port) == (HOST_A, 12345)
        assert (udp_back.dst_ip, udp_back.dst_port) == (HOST_A, 12345)

    def test_allocated_ports_stay_within_configured_range(self):
        nat, _ = make_engine(port_range_start=50000, port_range_end=50002)
        ports = {
            nat.process(outbound(HOST_A, p)).src_port for p in (1, 2, 3)
        }
        assert ports == {50000, 50001, 50002}


class TestInboundTranslation:
    def test_reply_is_translated_back_to_internal_destination(self):
        nat, _ = make_engine()
        out = nat.process(outbound(HOST_A, 12345))
        back = nat.process(inbound_reply(SERVER_IP, 80, out.src_port))
        assert back.dst_ip == HOST_A
        assert back.dst_port == 12345
        assert back.src_ip == SERVER_IP
        assert back.src_port == 80

    def test_full_round_trip_preserves_payload(self):
        nat, _ = make_engine()
        req = outbound(HOST_A, 9999)
        req = Packet(**{**req.__dict__, "payload": b"ping"})
        out = nat.process(req)
        resp = inbound_reply(SERVER_IP, 80, out.src_port)
        resp = Packet(**{**resp.__dict__, "payload": b"pong"})
        back = nat.process(resp)
        assert back.payload == b"pong"
        assert (back.dst_ip, back.dst_port) == (HOST_A, 9999)

    def test_unmapped_inbound_packet_is_not_forwarded(self):
        nat, _ = make_engine()
        with pytest.raises(NoMappingError):
            nat.process(inbound_reply(SERVER_IP, 80, 45555))

    def test_reply_to_wrong_external_port_is_rejected(self):
        nat, _ = make_engine()
        nat.process(outbound(HOST_A, 12345))  # takes port 40000
        with pytest.raises(NoMappingError):
            nat.process(inbound_reply(SERVER_IP, 80, 40001))

    def test_reply_from_unexpected_remote_is_rejected(self):
        nat, _ = make_engine()
        out = nat.process(outbound(HOST_A, 12345))
        with pytest.raises(NoMappingError):
            # Right external port, wrong remote peer -> not this flow.
            nat.process(inbound_reply("192.0.2.99", 80, out.src_port))

    def test_two_hosts_same_source_port_replies_route_correctly(self):
        nat, _ = make_engine()
        out_a = nat.process(outbound(HOST_A, 12345))
        out_b = nat.process(outbound(HOST_B, 12345))
        back_b = nat.process(inbound_reply(SERVER_IP, 80, out_b.src_port))
        back_a = nat.process(inbound_reply(SERVER_IP, 80, out_a.src_port))
        assert (back_b.dst_ip, back_b.dst_port) == (HOST_B, 12345)
        assert (back_a.dst_ip, back_a.dst_port) == (HOST_A, 12345)


class TestPortExhaustion:
    def test_exhaustion_fails_explicitly(self):
        nat, _ = make_engine(port_range_start=40000, port_range_end=40001)
        nat.process(outbound(HOST_A, 1))
        nat.process(outbound(HOST_A, 2))
        with pytest.raises(PortExhaustedError):
            nat.process(outbound(HOST_A, 3))
        # The failed attempt must not create state.
        assert nat.flow_count() == 2

    def test_exhaustion_is_per_protocol(self):
        nat, _ = make_engine(port_range_start=40000, port_range_end=40000)
        nat.process(outbound(HOST_A, 1, Protocol.TCP))
        # UDP still has its own free port at the same number.
        udp_out = nat.process(outbound(HOST_A, 1, Protocol.UDP))
        assert udp_out.src_port == 40000
        with pytest.raises(PortExhaustedError):
            nat.process(outbound(HOST_A, 2, Protocol.TCP))


class TestIdleTimeout:
    def test_udp_flow_expires_and_releases_port(self):
        nat, clock = make_engine()
        out = nat.process(outbound(HOST_A, 12345, Protocol.UDP))
        assert out.src_port == 40000
        clock.advance(61)  # udp_idle_timeout = 60
        assert nat.expire() == 1
        assert nat.flow_count() == 0
        # Reply after expiry must not be forwarded.
        with pytest.raises(NoMappingError):
            nat.process(inbound_reply(SERVER_IP, 80, 40000, Protocol.UDP))

    def test_tcp_flow_survives_udp_timeout(self):
        nat, clock = make_engine()
        nat.process(outbound(HOST_A, 12345, Protocol.TCP))
        clock.advance(61)
        assert nat.expire() == 0
        assert nat.flow_count() == 1

    def test_activity_refreshes_idle_timer(self):
        nat, clock = make_engine()
        nat.process(outbound(HOST_A, 12345, Protocol.UDP))
        clock.advance(50)
        nat.process(outbound(HOST_A, 12345, Protocol.UDP))  # refresh
        clock.advance(50)  # 100s total, but only 50s since last packet
        assert nat.expire() == 0
        clock.advance(11)  # now 61s idle
        assert nat.expire() == 1

    def test_freed_port_is_reused_by_new_flow(self):
        nat, clock = make_engine()
        first = nat.process(outbound(HOST_A, 12345, Protocol.UDP))
        clock.advance(61)
        nat.expire()
        second = nat.process(outbound(HOST_B, 5555, Protocol.UDP))
        assert second.src_port == first.src_port == 40000
        # Reverse mapping now points at the new owner.
        back = nat.process(inbound_reply(SERVER_IP, 80, 40000, Protocol.UDP))
        assert (back.dst_ip, back.dst_port) == (HOST_B, 5555)

    def test_processing_a_packet_triggers_lazy_expiry(self):
        nat, clock = make_engine()
        nat.process(outbound(HOST_A, 12345, Protocol.UDP))
        clock.advance(3600)
        # No explicit expire() call: process() expires lazily, so the
        # port is free again instead of leaking.
        out = nat.process(outbound(HOST_B, 5555, Protocol.UDP))
        assert out.src_port == 40000
        assert nat.flow_count() == 1
