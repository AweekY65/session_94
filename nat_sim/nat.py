"""In-memory NAT + connection-tracking simulator.

Outbound (internal -> external) packets get their source address rewritten
to the NAT's public IP plus an allocated external source port. Inbound
(external -> internal) packets are matched against the conntrack table and
their destination is rewritten back to the original internal endpoint.
Unmatched inbound packets are dropped (``translate_inbound`` returns None).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .clock import Clock, SystemClock
from .packet import Packet, Protocol


class PortExhaustedError(RuntimeError):
    """Raised when no external port is available in the configured range."""


@dataclass(slots=True)
class _Flow:
    """One conntrack entry: forward key <-> external port mapping."""

    forward_key: tuple  # (proto, int_ip, int_port, remote_ip, remote_port)
    reverse_key: tuple  # (proto, remote_ip, remote_port, ext_port)
    ext_port: int
    last_seen: float


@dataclass(slots=True)
class NATSimulator:
    """Simulates a NAPT (port-translating NAT) gateway entirely in memory."""

    public_ip: str
    port_range: tuple[int, int] = (40000, 60999)
    idle_timeout: float = 300.0
    clock: Clock = field(default_factory=SystemClock)
    _flows_by_forward: dict[tuple, _Flow] = field(init=False, repr=False)
    _flows_by_reverse: dict[tuple, _Flow] = field(init=False, repr=False)
    _port_to_flow: dict[int, _Flow] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        lo, hi = self.port_range
        if not (0 < lo <= hi <= 65535):
            raise ValueError(f"invalid port range: {self.port_range}")
        if self.idle_timeout <= 0:
            raise ValueError("idle_timeout must be positive")
        self._flows_by_forward = {}
        self._flows_by_reverse = {}
        self._port_to_flow = {}

    # ------------------------------------------------------------------ API

    def translate_outbound(self, packet: Packet) -> Packet:
        """Rewrite an internal->external packet, allocating a port if new.

        Subsequent packets of the same five-tuple reuse the existing mapping.
        Raises PortExhaustedError if the port range is full.
        """
        now = self.clock.now()
        self._expire_idle(now)
        fwd = (
            packet.protocol,
            packet.src_ip,
            packet.src_port,
            packet.dst_ip,
            packet.dst_port,
        )
        flow = self._flows_by_forward.get(fwd)
        if flow is None:
            flow = self._create_flow(fwd, now)
        flow.last_seen = now
        return Packet(
            protocol=packet.protocol,
            src_ip=self.public_ip,
            src_port=flow.ext_port,
            dst_ip=packet.dst_ip,
            dst_port=packet.dst_port,
        )

    def translate_inbound(self, packet: Packet) -> Packet | None:
        """Rewrite an external->internal packet via the conntrack table.

        Returns None (packet dropped) when no mapping exists.
        """
        if packet.dst_ip != self.public_ip:
            return None
        now = self.clock.now()
        self._expire_idle(now)
        rev = (
            packet.protocol,
            packet.src_ip,
            packet.src_port,
            packet.dst_port,
        )
        flow = self._flows_by_reverse.get(rev)
        if flow is None:
            return None
        flow.last_seen = now
        _, int_ip, int_port, _, _ = flow.forward_key
        return Packet(
            protocol=packet.protocol,
            src_ip=packet.src_ip,
            src_port=packet.src_port,
            dst_ip=int_ip,
            dst_port=int_port,
        )

    def active_flows(self) -> int:
        """Number of live conntrack entries (after expiry sweep)."""
        self._expire_idle(self.clock.now())
        return len(self._flows_by_forward)

    # -------------------------------------------------------------- internals

    def _create_flow(self, fwd: tuple, now: float) -> _Flow:
        proto, _, _, remote_ip, remote_port = fwd
        lo, hi = self.port_range
        ext_port: int | None = None
        for candidate in range(lo, hi + 1):
            if candidate not in self._port_to_flow:
                ext_port = candidate
                break
        if ext_port is None:
            raise PortExhaustedError(
                f"no free external port in range {lo}-{hi}"
            )
        rev = (proto, remote_ip, remote_port, ext_port)
        flow = _Flow(
            forward_key=fwd,
            reverse_key=rev,
            ext_port=ext_port,
            last_seen=now,
        )
        self._flows_by_forward[fwd] = flow
        self._flows_by_reverse[rev] = flow
        self._port_to_flow[ext_port] = flow
        return flow

    def _expire_idle(self, now: float) -> None:
        stale = [
            flow
            for flow in self._flows_by_forward.values()
            if now - flow.last_seen >= self.idle_timeout
        ]
        for flow in stale:
            del self._flows_by_forward[flow.forward_key]
            del self._flows_by_reverse[flow.reverse_key]
            del self._port_to_flow[flow.ext_port]
