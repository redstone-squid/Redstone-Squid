"""API-container child environment isolation tests."""

from tests.fuzz.api.container_entrypoint import api_environment, fake_environment
from tests.fuzz.api.fake_upstreams import CONTROL_NONCE_ENV, FAKE_PORT_ENV


def test_fake_child_receives_no_api_database_or_host_credentials() -> None:
    source = {
        "PATH": "/app/.venv/bin",
        "HOME": "/root",
        "AWS_SECRET_ACCESS_KEY": "host-canary",
        "SQUID_DATABASE_URL": "postgresql://app-secret",
        "SQUID_API_SECRET": "api-secret",
        CONTROL_NONCE_ENV: "control-secret",
        FAKE_PORT_ENV: "8101",
    }

    assert fake_environment(source) == {
        "PATH": "/app/.venv/bin",
        CONTROL_NONCE_ENV: "control-secret",
        FAKE_PORT_ENV: "8101",
    }


def test_api_child_receives_only_squid_and_python_runtime_settings() -> None:
    source = {
        "PATH": "/app/.venv/bin",
        "HOME": "/root",
        "AWS_SECRET_ACCESS_KEY": "host-canary",
        "SQUID_DATABASE_URL": "postgresql://synthetic",
        "SQUID_API_SECRET": "synthetic-api",
        CONTROL_NONCE_ENV: "control-secret",
    }

    assert api_environment(source) == {
        "PATH": "/app/.venv/bin",
        "SQUID_DATABASE_URL": "postgresql://synthetic",
        "SQUID_API_SECRET": "synthetic-api",
    }
