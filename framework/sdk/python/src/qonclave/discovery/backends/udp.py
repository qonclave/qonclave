"""
udp.py -- UDP broadcast discovery.

Origin: hub/framework/discovery.py, which broadcasts a JSON heartbeat on port 8888 and answers
probes. Kept as a backend because it works on networks where multicast is filtered.
"""

from __future__ import annotations

import socket
import time

DISCOVERY_PORT = 8888


def lan_ip() -> str | None:
    """Best-effort LAN address of this node -- the IP a peer on the subnet would reach it at.
    The UDP connect never sends a packet; it only asks the OS which interface would route out."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


class UDPAnnounceBackend:
    """One broadcast-send socket and one bound receive socket, both on `port`.

    Best-effort by construction: a socket that fails to create or bind leaves the corresponding
    `can_*` property False rather than raising, and every method silently no-ops instead of
    propagating a transport error to the caller's announce loop.
    """

    def __init__(self, port: int = DISCOVERY_PORT, recv_timeout: float = 3.0):
        self.port = port
        self._recv_timeout = recv_timeout

        self._bcast_sock: socket.socket | None = None
        try:
            self._bcast_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._bcast_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            self._bcast_sock = None

        self._listen_sock: socket.socket | None = None
        try:
            self._listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._listen_sock.bind(("", port))
            self._listen_sock.settimeout(recv_timeout)
        except OSError:
            self._listen_sock = None

    @property
    def can_broadcast(self) -> bool:
        return self._bcast_sock is not None

    @property
    def can_listen(self) -> bool:
        return self._listen_sock is not None

    def broadcast(self, payload: bytes) -> None:
        if self._bcast_sock is None:
            return
        try:
            self._bcast_sock.sendto(payload, ("255.255.255.255", self.port))
        except OSError:
            pass

    def poll(self) -> tuple[bytes, tuple[str, int]] | None:
        """Wait up to `recv_timeout` for one datagram, or -- if there is no listen socket to
        wait on -- sleep that same duration doing nothing, so a caller's loop paces itself
        identically either way. Returns (data, addr), or None on timeout/absence/error."""
        if self._listen_sock is None:
            time.sleep(self._recv_timeout)
            return None
        try:
            return self._listen_sock.recvfrom(1024)
        except socket.timeout:
            return None
        except OSError:
            time.sleep(1.0)
            return None

    def reply(self, payload: bytes, addr: tuple[str, int]) -> None:
        if self._listen_sock is None:
            return
        try:
            self._listen_sock.sendto(payload, addr)
        except OSError:
            pass
