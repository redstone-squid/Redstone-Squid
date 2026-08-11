"""Supervise a loopback fake-upstream forwarder and the ordinary API process."""

import os
import signal
import subprocess
import sys
import time
import urllib.request
from collections.abc import Mapping

from tests.fuzz.api.environment import FAKE_HOST_ENV, FAKE_PORT_ENV
from tests.fuzz.api.loopback_proxy import LoopbackTcpProxy

CHILD_EXIT_GRACE_SECONDS = 10
FAKE_READY_SECONDS = 10
_PYTHON_ENV_KEYS = frozenset({"PATH", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "PYTHONUTF8", "TMPDIR"})


def api_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Pass the API only explicit SQUID configuration and Python runtime paths."""
    return {key: value for key, value in source.items() if key in _PYTHON_ENV_KEYS or key.startswith("SQUID_")}


def fake_proxy(source: Mapping[str, str]) -> LoopbackTcpProxy:
    """Create the API-container loopback proxy to the isolated fake-upstream container."""
    target_host = source.get(FAKE_HOST_ENV, "")
    try:
        port = int(source.get(FAKE_PORT_ENV, "8101"))
    except ValueError:
        raise SystemExit(f"{FAKE_PORT_ENV} must be an integer") from None
    if not 1 <= port <= 65535:
        raise SystemExit(f"{FAKE_PORT_ENV} must be between 1 and 65535")
    return LoopbackTcpProxy(target_host, port, listen_port=port, max_connections=32)


def main() -> int:
    """Launch the forwarder and API child, forward termination, and reap deterministically."""
    proxy = fake_proxy(os.environ)
    proxy.start()
    children: list[subprocess.Popen[bytes]] = []
    try:
        _wait_for_fake(proxy.port)
        api = subprocess.Popen(
            [sys.executable, "-m", "squid.api.app"],
            env=api_environment(os.environ),
            stdin=subprocess.DEVNULL,
        )
        children.append(api)
        _install_signal_forwarding(children)
        return _wait_for_first_exit(children)
    finally:
        _stop_children(children)
        proxy.close()


def _wait_for_fake(port: int) -> None:
    deadline = time.monotonic() + FAKE_READY_SECONDS
    url = f"http://127.0.0.1:{port}/__fuzz/ready"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.25) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.05)
    msg = "Fake upstream process did not become ready."
    raise TimeoutError(msg)


def _install_signal_forwarding(children: list[subprocess.Popen[bytes]]) -> None:
    def forward(received: int, _frame: object) -> None:
        for child in children:
            if child.poll() is None:
                child.send_signal(received)

    signal.signal(signal.SIGINT, forward)
    signal.signal(signal.SIGTERM, forward)


def _wait_for_first_exit(children: list[subprocess.Popen[bytes]]) -> int:
    while True:
        for child in children:
            returncode = child.poll()
            if returncode is not None:
                return returncode
        time.sleep(0.1)


def _stop_children(children: list[subprocess.Popen[bytes]]) -> None:
    for child in children:
        if child.poll() is None:
            child.terminate()
    deadline = time.monotonic() + CHILD_EXIT_GRACE_SECONDS
    for child in children:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            child.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
