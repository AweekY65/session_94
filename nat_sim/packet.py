"""Packet model: a plain immutable data object, never a real socket."""

from __future__ import annotations

import enum
from dataclasses import dataclass


class Protocol(enum.Enum):
    TCP = "tcp"
    UDP = "udp"


class Direction(enum.Enum):
    OUTBOUND = "outbound"  # internal host -> external server
    INBOUND = "inbound"    # external server -> internal host


@dataclass(frozen=True)
class FiveTuple:
    """Uniquely identifies one direction of a flow."""

    protocol: Protocol
    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int

    def reversed(self) -> "FiveTuple":
        return FiveTuple(
            protocol=self.protocol,
            src_ip=self.dst_ip,
            src_port=self.dst_port,
            dst_ip=self.src_ip,
            dst_port=self.src_port,
        )


@dataclass(frozen=True)
class Packet:
    """A simulated packet. `direction` is relative to the NAT gateway."""

    direction: Direction
    protocol: Protocol
    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int
    payload: bytes = b""

    def five_tuple(self) -> FiveTuple:
        return FiveTuple(
            protocol=self.protocol,
            src_ip=self.src_ip,
            src_port=self.src_port,
            dst_ip=self.dst_ip,
            dst_port=self.dst_port,
        )

    def with_addresses(
        self,
        *,
        src_ip: str | None = None,
        src_port: int | None = None,
        dst_ip: str | None = None,
        dst_port: int | None = None,
    ) -> "Packet":
        return Packet(
            direction=self.direction,
            protocol=self.protocol,
            src_ip=src_ip if src_ip is not None else self.src_ip,
            src_port=src_port if src_port is not None else self.src_port,
            dst_ip=dst_ip if dst_ip is not None else self.dst_ip,
            dst_port=dst_port if dst_port is not None else self.dst_port,
            payload=self.payload,
        )
