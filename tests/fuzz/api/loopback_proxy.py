"""Small bounded host-loopback TCP forwarder into an internal Docker network."""

import select
import socket
import threading
from contextlib import suppress
from ipaddress import IPv4Address, IPv4Network
from time import monotonic

_PRIVATE_NETWORKS = tuple(IPv4Network(network) for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


class LoopbackTcpProxy:
    """Forward a bounded number of loopback connections to one fixed target."""

    def __init__(
        self,
        target_host: str,
        target_port: int,
        *,
        max_connections: int = 64,
        idle_seconds: float = 5,
        lifetime_seconds: float = 30,
        max_bytes: int = 16 * 1024 * 1024,
        listen_port: int = 0,
    ) -> None:
        try:
            target = IPv4Address(target_host)
        except ValueError:
            target = None
        private_target = target is not None and (
            target.is_loopback or any(target in network for network in _PRIVATE_NETWORKS)
        )
        if not private_target or not 1 <= target_port <= 65_535:
            msg = "A loopback proxy requires a concrete target and valid port."
            raise ValueError(msg)
        if not 1 <= max_connections <= 256:
            msg = "A loopback proxy connection limit must be between 1 and 256."
            raise ValueError(msg)
        if not 0 <= listen_port <= 65_535:
            msg = "A loopback proxy listener port must be between 0 and 65535."
            raise ValueError(msg)
        if not 0 < idle_seconds <= 30 or not 0 < lifetime_seconds <= 120 or not 1 <= max_bytes <= 64 * 1024 * 1024:
            msg = "A loopback proxy requires bounded idle time, lifetime, and transferred bytes."
            raise ValueError(msg)
        self.target = (target_host, target_port)
        self.idle_seconds = idle_seconds
        self.lifetime_seconds = lifetime_seconds
        self.max_bytes = max_bytes
        self._semaphore = threading.BoundedSemaphore(max_connections)
        self._stop = threading.Event()
        self._connections: set[socket.socket] = set()
        self._connections_lock = threading.Lock()
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", listen_port))
        self._listener.listen(min(max_connections, 128))
        self._listener.settimeout(0.25)
        self._thread = threading.Thread(target=self._accept, name="ApiFuzzLoopbackProxy", daemon=True)

    @property
    def port(self) -> int:
        """Return the allocated IPv4-loopback listener port."""
        return int(self._listener.getsockname()[1])

    def start(self) -> None:
        """Begin accepting connections exactly once."""
        self._thread.start()

    def verify(self) -> None:
        """Require the proxy thread and listener to remain live."""
        if self._stop.is_set() or not self._thread.is_alive() or self._listener.fileno() < 0:
            msg = "Disposable API loopback proxy is not live."
            raise RuntimeError(msg)

    def close(self) -> None:
        """Stop accepting, close live sockets, and join the bounded worker thread."""
        self._stop.set()
        with suppress(OSError):
            self._listener.close()
        with self._connections_lock:
            connections = tuple(self._connections)
        for connection in connections:
            with suppress(OSError):
                connection.shutdown(socket.SHUT_RDWR)
            connection.close()
        if self._thread.ident is not None:
            self._thread.join(timeout=2)
        if self._thread.is_alive():
            msg = "Disposable API loopback proxy did not stop."
            raise RuntimeError(msg)

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                client, _address = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            if not self._semaphore.acquire(blocking=False):
                client.close()
                continue
            thread = threading.Thread(target=self._forward, args=(client,), name="ApiFuzzProxyConnection", daemon=True)
            thread.start()

    def _forward(self, client: socket.socket) -> None:
        upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._track(client, upstream)
        started_at = monotonic()
        last_activity_at = started_at
        transferred_bytes = 0
        try:
            upstream.settimeout(2)
            upstream.connect(self.target)
            client.settimeout(2)
            upstream.settimeout(2)
            peers = (client, upstream)
            while not self._stop.is_set():
                readable, _writable, exceptional = select.select(peers, (), peers, 0.5)
                if exceptional:
                    return
                current_time = monotonic()
                if (
                    current_time - started_at >= self.lifetime_seconds
                    or current_time - last_activity_at >= self.idle_seconds
                ):
                    return
                for source in readable:
                    destination = upstream if source is client else client
                    try:
                        chunk = source.recv(64 * 1024)
                        if not chunk:
                            return
                        transferred_bytes += len(chunk)
                        if transferred_bytes > self.max_bytes:
                            return
                        destination.sendall(chunk)
                        last_activity_at = monotonic()
                    except OSError:
                        return
        except OSError:
            return
        finally:
            self._untrack(client, upstream)
            client.close()
            upstream.close()
            self._semaphore.release()

    def _track(self, *connections: socket.socket) -> None:
        with self._connections_lock:
            self._connections.update(connections)

    def _untrack(self, *connections: socket.socket) -> None:
        with self._connections_lock:
            self._connections.difference_update(connections)
