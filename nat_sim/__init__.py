"""Fully local, in-memory NAT + connection tracking simulator.

No real firewall, network namespace, or external network is ever touched:
packets are plain data objects and all state lives in memory.
"""

from nat_sim.clock import Clock, ManualClock, SystemClock
from nat_sim.conntrack import (
    NatConfig,
    NatEngine,
    NoMappingError,
    PortExhaustedError,
)
from nat_sim.packet import Direction, FiveTuple, Packet, Protocol

__all__ = [
    "Clock",
    "ManualClock",
    "SystemClock",
    "NatConfig",
    "NatEngine",
    "NoMappingError",
    "PortExhaustedError",
    "Direction",
    "FiveTuple",
    "Packet",
    "Protocol",
]
