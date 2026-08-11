"""Docker stack assembly constants that can be checked without Docker."""

from tests.fuzz.api.docker_stack import _FAKE_HEALTHCHECK, FAKE_CONTAINER_PORT


def test_fake_healthcheck_targets_the_fake_control_ready_endpoint() -> None:
    assert _FAKE_HEALTHCHECK[0] == "CMD-SHELL"
    assert f"127.0.0.1:{FAKE_CONTAINER_PORT}/__fuzz/ready" in _FAKE_HEALTHCHECK[1]
    assert "/readyz" not in _FAKE_HEALTHCHECK[1]
