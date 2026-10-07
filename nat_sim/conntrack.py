"""In-memory NAT engine with connection tracking.

All mappings, port state and timestamps live in this process only.
Time comes from an injectable clock so idle timeouts are deterministic
in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nat_sim.clock import Clock, SystemClock
from nat_sim.packet import Direction, FiveTuple, Packet, Protocol


class PortExhaustedError(Exception):
    """Raised when no external source port is available in the range."""


class NoMappingError(Exception):
    """Raised when an inbound packet matches no conntrack entry."""


@dataclass(frozen=True)
class NatConfig:
    external_ip: str
    port_range_start: int = 40000
    port_range_end: int = 40100  # inclusive
    tcp_idle_timeout: float = 300.0
    udp_idle_timeout: float = 60.0

    def __post_init__(self) -> None:
        if not (0 < self.port_range_start <= self.port_range_end <= 65535):
            raise ValueError("invalid port range")
        if self.tcp_idle_timeout <= 0 or self.udp_idle_timeout <= 0:
            raise ValueError("idle timeouts must be positive")

    def idle_timeout(self, protocol: Protocol) -> float:
        if protocol is Protocol.TCP:
            return self.tcp_idle_timeout
        return self.udp_idle_timeout


@dataclass
class ConntrackEntry:
    """One tracked flow with its forward and reverse mappings."""

    original: FiveTuple      # internal -> remote, as seen pre-NAT
    external_port: int       # allocated external source port
    created_at: float
    last_seen: float

    @property
    def protocol(self) -> Protocol:
        return self.original.protocol

    def reply_tuple(self, external_ip: str) -> FiveTuple:
        """The 5-tuple an inbound reply packet for this flow carries."""
        return FiveTuple(
            protocol=self.original.protocol,
            src_ip=self.original.dst_ip,
            src_port=self.original.dst_port,
            dst_ip=external_ip,
            dst_port=self.external_port,
        )


@dataclass
class NatEngine:
    """Stateful NAT + conntrack. Purely in-memory."""

    config: NatConfig
    clock: Clock = field(default_factory=SystemClock)

    def __post_init__(self) -> None:
        self._forward: dict[FiveTuple, ConntrackEntry] = {}
        self._reverse: dict[FiveTuple, ConntrackEntry] = {}
        # (protocol, external_port) -> entry, for allocation and collision checks
        self._ports: dict[tuple[Protocol, int], ConntrackEntry] = {}

    # ------------------------------------------------------------------ API

    def process(self, packet: Packet) -> Packet:
        """Translate one packet. Returns the rewritten packet.

        Raises PortExhaustedError if a new flow cannot get a port and
        NoMappingError if an inbound packet matches no live flow.
        """
        self.expire()
        if packet.direction is Direction.OUTBOUND:
            return self._process_outbound(packet)
        return self._process_inbound(packet)

    def expire(self) -> int:
        """Drop entries idle longer than their protocol timeout."""
        now = self.clock.now()
        stale = [
            key
            for key, entry in self._forward.items()
            if now - entry.last_seen >= self.config.idle_timeout(entry.protocol)
        ]
        for key in stale:
            self._remove(self._forward[key])
        return len(stale)

    def flow_count(self) -> int:
        return len(self._forward)

    def external_port_for(self, original: FiveTuple) -> int | None:
        entry = self._forward.get(original)
        return entry.external_port if entry else None

    # ------------------------------------------------------------- outbound

    def _process_outbound(self, packet: Packet) -> Packet:
        key = packet.five_tuple()
        entry = self._forward.get(key)
        if entry is None:
            entry = self._create_entry(key)
        entry.last_seen = self.clock.now()
        return packet.with_addresses(
            src_ip=self.config.external_ip,
            src_port=entry.external_port,
        )

    def _create_entry(self, original: FiveTuple) -> ConntrackEntry:
        port = self._allocate_port(original.protocol)
        now = self.clock.now()
        entry = ConntrackEntry(
            original=original,
            external_port=port,
            created_at=now,
            last_seen=now,
        )
        self._forward[original] = entry
        self._reverse[entry.reply_tuple(self.config.external_ip)] = entry
        self._ports[(original.protocol, port)] = entry
        return entry

    def _allocate_port(self, protocol: Protocol) -> int:
        cfg = self.config
        for port in range(cfg.port_range_start, cfg.port_range_end + 1):
            if (protocol, port) not in self._ports:
                return port
        raise PortExhaustedError(
            f"no free {protocol.value} port in "
            f"[{cfg.port_range_start}, {cfg.port_range_end}]"
        )

    # -------------------------------------------------------------- inbound

    def _process_inbound(self, packet: Packet) -> Packet:
        key = packet.five_tuple()
        entry = self._reverse.get(key)
        if entry is None:
            raise NoMappingError(f"no conntrack entry for {key}")
        entry.last_seen = self.clock.now()
        return packet.with_addresses(
            dst_ip=entry.original.src_ip,
            dst_port=entry.original.src_port,
        )

    # -------------------------------------------------------------- cleanup

    def _remove(self, entry: ConntrackEntry) -> None:
        self._forward.pop(entry.original, None)
        self._reverse.pop(entry.reply_tuple(self.config.external_ip), None)
        self._ports.pop((entry.protocol, entry.external_port), None)
