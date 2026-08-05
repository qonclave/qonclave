"""
serial.py -- serial / UART transport.

For non-IP links: a LoRa radio behind a UART, or an MCU bridged to a Linux host. Exists to keep
the transport abstraction honest -- if only IP transports were implemented, IP assumptions would
leak upward unnoticed.
"""

from __future__ import annotations
