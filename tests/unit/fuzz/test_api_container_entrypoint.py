"""API-container child environment isolation tests."""

import pytest

from tests.fuzz.api.container_entrypoint import api_environment, fake_proxy
from tests.fuzz.api.environment import CONTROL_NONCE_ENV, FAKE_HOST_ENV, FAKE_PORT_ENV


def test_api_child_receives_only_squid_and_python_runtime_settings() -> None:
    source = {
        "PATH": "/app/.venv/bin",
        "HOME": "/root",
        "AWS_SECRET_ACCESS_KEY": "host-canary",
        "SQUID_DATABASE_URL": "postgresql://synthetic",
        "SQUID_API_SECRET": "synthetic-api",
        CONTROL_NONCE_ENV: "control-secret",
        FAKE_HOST_ENV: "172.18.0.5",
        FAKE_PORT_ENV: "8101",
    }

    assert api_environment(source) == {
        "PATH": "/app/.venv/bin",
        "SQUID_DATABASE_URL": "postgresql://synthetic",
        "SQUID_API_SECRET": "synthetic-api",
    }


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ({FAKE_HOST_ENV: "172.18.0.5", FAKE_PORT_ENV: "not-a-port"}, "must be an integer"),
        ({FAKE_HOST_ENV: "172.18.0.5", FAKE_PORT_ENV: "0"}, "between 1 and 65535"),
    ],
)
def test_fake_proxy_refuses_invalid_ports(source: dict[str, str], message: str) -> None:
    with pytest.raises(SystemExit, match=message):
        fake_proxy(source)
