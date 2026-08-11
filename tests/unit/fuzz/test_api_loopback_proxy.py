"""Bounded loopback proxy behavior without Docker."""

import socket
import threading

import pytest

from tests.fuzz.api.loopback_proxy import LoopbackTcpProxy


def test_loopback_proxy_forwards_and_stops_deterministically() -> None:
    try:
        upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except PermissionError:
        pytest.skip("This sandbox forbids loopback sockets")
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(1)

    def respond() -> None:
        connection, _address = upstream.accept()
        with connection:
            connection.sendall(connection.recv(64).upper())

    upstream_thread = threading.Thread(target=respond)
    upstream_thread.start()
    proxy = LoopbackTcpProxy("127.0.0.1", upstream.getsockname()[1])
    proxy.start()
    try:
        proxy.verify()
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=2) as client:
            client.sendall(b"fuzz lifecycle")
            assert client.recv(64) == b"FUZZ LIFECYCLE"
    finally:
        proxy.close()
        upstream.close()
        upstream_thread.join(timeout=2)

    with pytest.raises(RuntimeError, match="not live"):
        proxy.verify()


@pytest.mark.parametrize("target", ["docker.internal", "203.0.113.1", ""])
def test_loopback_proxy_refuses_unresolved_or_public_targets(target: str) -> None:
    with pytest.raises(ValueError, match="concrete target"):
        LoopbackTcpProxy(target, 8000)


@pytest.mark.parametrize(
    "limits",
    [
        {"idle_seconds": 0},
        {"lifetime_seconds": 121},
        {"max_bytes": 0},
    ],
)
def test_loopback_proxy_requires_bounded_connection_limits(limits: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="bounded"):
        LoopbackTcpProxy("127.0.0.1", 8000, **limits)
