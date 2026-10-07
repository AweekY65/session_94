"""Packet model: a TCP/UDP flow is identified by its five-tuple."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Protocol(str, Enum):
    TCP = "tcp"
    UDP = "udp"


@dataclass(frozen=True, slots=True)
class Packet:
    """An immutable IP packet carrying a TCP/UDP five-tuple."""

    protocol: Protocol
    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int

    def five_tuple(self) -> tuple:
        return (
            self.protocol,
            self.src_ip,
            self.src_port,
            self.dst_ip,
            self.dst_port,
        )
