"""Supervise loopback fakes and the ordinary API process inside its container."""

import os
import signal
import subprocess
import sys
import time
import urllib.request
from collections.abc import Mapping

from tests.fuzz.api.fake_upstreams import CONTROL_NONCE_ENV, FAKE_PORT_ENV

CHILD_EXIT_GRACE_SECONDS = 10
FAKE_READY_SECONDS = 10
_PYTHON_ENV_KEYS = frozenset({"PATH", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "PYTHONUTF8", "TMPDIR"})


def fake_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Pass the fake service only its control settings and Python runtime paths."""
    return {
        key: value
        for key, value in source.items()
        if key in _PYTHON_ENV_KEYS or key in {CONTROL_NONCE_ENV, FAKE_PORT_ENV}
    }


def api_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Pass the API only explicit SQUID configuration and Python runtime paths."""
    return {key: value for key, value in source.items() if key in _PYTHON_ENV_KEYS or key.startswith("SQUID_")}


def main() -> int:
    """Launch both children, forward termination, and reap them deterministically."""
    fake = subprocess.Popen(
        [sys.executable, "-m", "tests.fuzz.api.fake_upstreams"],
        env=fake_environment(os.environ),
        stdin=subprocess.DEVNULL,
    )
    children = [fake]
    try:
        _wait_for_fake(fake, os.environ)
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


def _wait_for_fake(fake: subprocess.Popen[bytes], environment: Mapping[str, str]) -> None:
    port = int(environment.get(FAKE_PORT_ENV, "8101"))
    deadline = time.monotonic() + FAKE_READY_SECONDS
    url = f"http://127.0.0.1:{port}/__fuzz/ready"
    while time.monotonic() < deadline:
        returncode = fake.poll()
        if returncode is not None:
            msg = f"Fake upstream process exited during startup with status {returncode}."
            raise RuntimeError(msg)
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
