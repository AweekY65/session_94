"""Fully local NAT and connection-tracking simulator.

Everything lives in memory: no real firewall, network namespaces, or
external network access is touched.
"""

from .clock import Clock, ManualClock
from .packet import Packet, Protocol
from .nat import NATSimulator, PortExhaustedError

__all__ = [
    "Clock",
    "ManualClock",
    "Packet",
    "Protocol",
    "NATSimulator",
    "PortExhaustedError",
]
