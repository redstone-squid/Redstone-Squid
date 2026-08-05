"""Optional process-local observability setup tests."""

import logging
from collections.abc import Iterator

import pytest
from pytest_mock import MockerFixture

from squid import observability
from squid.config import ObservabilityConfig
from squid.observability import ObservabilityHandle, configure_observability


@pytest.fixture(autouse=True)
def _reset_observability_state() -> Iterator[None]:
    previous_pid = observability._configured_pid  # pyright: ignore[reportPrivateUsage]
    previous_handle = observability._configured_handle  # pyright: ignore[reportPrivateUsage]
    observability._configured_pid = None  # pyright: ignore[reportPrivateUsage]
    observability._configured_handle = None  # pyright: ignore[reportPrivateUsage]
    yield
    observability._configured_pid = previous_pid  # pyright: ignore[reportPrivateUsage]
    observability._configured_handle = previous_handle  # pyright: ignore[reportPrivateUsage]


def enabled_config() -> ObservabilityConfig:
    return ObservabilityConfig.model_validate({"enabled": True, "endpoint": "http://collector:4318"})


def test_disabled_configuration_does_not_probe_optional_dependency(mocker: MockerFixture) -> None:
    configure_sdk = mocker.patch.object(observability, "_configure_otel")

    handle = configure_observability(ObservabilityConfig(), service_name="bot")
    handle.shutdown()

    configure_sdk.assert_not_called()


def test_enabled_configuration_is_process_idempotent(mocker: MockerFixture) -> None:
    shutdown = mocker.Mock()
    configured = ObservabilityHandle(shutdown)
    configure_sdk = mocker.patch.object(observability, "_configure_otel", return_value=configured)

    first = configure_observability(enabled_config(), service_name="api")
    second = configure_observability(enabled_config(), service_name="api")
    first.shutdown()
    second.shutdown()

    assert first is second
    configure_sdk.assert_called_once_with(enabled_config(), service_name="api")
    shutdown.assert_called_once_with()


def test_missing_optional_extra_degrades_to_warning(caplog: pytest.LogCaptureFixture, mocker: MockerFixture) -> None:
    missing_extra = ModuleNotFoundError("No module named 'opentelemetry'", name="opentelemetry")
    mocker.patch.object(observability, "_configure_otel", side_effect=missing_extra)

    handle = configure_observability(enabled_config(), service_name="bot")

    handle.shutdown()

    assert "optional 'observability' extra is not installed" in caplog.text


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("http://collector:4318", "http://collector:4318/v1/traces"),
        ("https://example.test/otel/", "https://example.test/otel/v1/traces"),
        ("https://example.test/v1/traces", "https://example.test/v1/traces"),
    ],
)
def test_trace_endpoint_appends_signal_path(base: str, expected: str) -> None:
    assert observability._trace_endpoint(base) == expected  # pyright: ignore[reportPrivateUsage]


def test_metrics_endpoint_uses_its_signal_path() -> None:
    assert (  # pyright: ignore[reportPrivateUsage]
        observability._signal_endpoint("http://collector:4318", "metrics") == "http://collector:4318/v1/metrics"
    )


def test_inherited_configured_state_is_rejected_after_fork(mocker: MockerFixture) -> None:
    observability._configured_pid = 100  # pyright: ignore[reportPrivateUsage]
    observability._configured_handle = ObservabilityHandle()  # pyright: ignore[reportPrivateUsage]
    mocker.patch.object(observability.os, "getpid", return_value=200)

    with pytest.raises(RuntimeError, match="before this process forked"):
        configure_observability(enabled_config(), service_name="api")


def test_correlation_id_uses_active_trace_id(mocker: MockerFixture) -> None:
    mocker.patch.object(observability, "_current_trace_id", return_value="a" * 32)

    assert observability.correlation_id() == "a" * 32


def test_correlation_id_has_a_local_fallback(mocker: MockerFixture) -> None:
    mocker.patch.object(observability, "_current_trace_id", return_value=None)

    error_id = observability.correlation_id()

    assert len(error_id) == 12
    assert int(error_id, 16) >= 0


def test_trace_context_filter_adds_active_ids(mocker: MockerFixture) -> None:
    mocker.patch.object(observability, "_current_trace_context", return_value=("a" * 32, "b" * 16))
    record = logging.LogRecord("squid.test", logging.INFO, __file__, 1, "message", (), None)

    assert observability.TraceContextFilter().filter(record) is True

    assert vars(record)["trace_id"] == "a" * 32
    assert vars(record)["span_id"] == "b" * 16


def test_trace_context_filter_preserves_propagated_child_ids(mocker: MockerFixture) -> None:
    mocker.patch.object(observability, "_current_trace_context", return_value=("a" * 32, "b" * 16))
    record = logging.LogRecord("squid.worker", logging.INFO, __file__, 1, "message", (), None)
    vars(record)["trace_id"] = "c" * 32
    vars(record)["span_id"] = "d" * 16

    observability.TraceContextFilter().filter(record)

    assert vars(record)["trace_id"] == "c" * 32
    assert vars(record)["span_id"] == "d" * 16
