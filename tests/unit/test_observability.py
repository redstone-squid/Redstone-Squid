"""Optional process-local observability setup tests."""

import logging
from collections.abc import Iterator
from typing import Any

import pytest
from pytest_mock import MockerFixture

from squid import observability
from squid.config import ObservabilityConfig
from squid.observability import ObservabilityHandle, configure_observability


@pytest.fixture(autouse=True)
def _reset_observability_state() -> Iterator[None]:
    previous_pid = observability._configured_pid  # pyright: ignore[reportPrivateUsage]
    previous_handle = observability._configured_handle  # pyright: ignore[reportPrivateUsage]
    previous_active = observability._ACTIVE  # pyright: ignore[reportPrivateUsage]
    previous_counters = observability._counters  # pyright: ignore[reportPrivateUsage]
    previous_histograms = observability._histograms  # pyright: ignore[reportPrivateUsage]
    previous_gauges = observability._gauges  # pyright: ignore[reportPrivateUsage]
    observability._configured_pid = None  # pyright: ignore[reportPrivateUsage]
    observability._configured_handle = None  # pyright: ignore[reportPrivateUsage]
    observability._ACTIVE = None  # pyright: ignore[reportPrivateUsage]
    observability._counters = {}  # pyright: ignore[reportPrivateUsage]
    observability._histograms = {}  # pyright: ignore[reportPrivateUsage]
    observability._gauges = {}  # pyright: ignore[reportPrivateUsage]
    yield
    observability._configured_pid = previous_pid  # pyright: ignore[reportPrivateUsage]
    observability._configured_handle = previous_handle  # pyright: ignore[reportPrivateUsage]
    observability._ACTIVE = previous_active  # pyright: ignore[reportPrivateUsage]
    observability._counters = previous_counters  # pyright: ignore[reportPrivateUsage]
    observability._histograms = previous_histograms  # pyright: ignore[reportPrivateUsage]
    observability._gauges = previous_gauges  # pyright: ignore[reportPrivateUsage]


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
    ("base", "signal", "expected"),
    [
        ("http://collector:4318", "traces", "http://collector:4318/v1/traces"),
        ("https://example.test/otel/", "traces", "https://example.test/otel/v1/traces"),
        ("https://example.test/v1/traces", "traces", "https://example.test/v1/traces"),
        ("http://collector:4318", "metrics", "http://collector:4318/v1/metrics"),
    ],
)
def test_signal_endpoint_appends_signal_path(base: str, signal: str, expected: str) -> None:
    assert observability._signal_endpoint(base, signal) == expected  # pyright: ignore[reportPrivateUsage]


def test_shutdown_clears_active_telemetry_and_metric_caches(mocker: MockerFixture) -> None:
    telemetry = _fake_telemetry(mocker)
    counter = mocker.Mock()
    observability._ACTIVE = telemetry  # pyright: ignore[reportPrivateUsage]
    observability._counters["counter"] = counter  # pyright: ignore[reportPrivateUsage]
    observability._histograms["histogram"] = mocker.Mock()  # pyright: ignore[reportPrivateUsage]
    observability._gauges["gauge"] = mocker.Mock()  # pyright: ignore[reportPrivateUsage]

    handle = ObservabilityHandle(lambda: observability._clear_telemetry(telemetry))  # pyright: ignore[reportPrivateUsage]
    handle.shutdown()
    observability.add_counter("counter")

    assert observability._ACTIVE is None  # pyright: ignore[reportPrivateUsage]
    assert observability._counters == {}  # pyright: ignore[reportPrivateUsage]
    assert observability._histograms == {}  # pyright: ignore[reportPrivateUsage]
    assert observability._gauges == {}  # pyright: ignore[reportPrivateUsage]
    counter.add.assert_not_called()


def test_foreign_pid_makes_public_telemetry_helpers_inert(mocker: MockerFixture) -> None:
    telemetry = _fake_telemetry(mocker, pid=observability.os.getpid() + 1)
    observability._ACTIVE = telemetry  # pyright: ignore[reportPrivateUsage]

    with observability.trace_span("span"):
        pass
    with observability.extracted_trace_span("child", {}):
        pass
    observability.record_current_exception(RuntimeError("boom"))
    observability.add_counter("counter")
    observability.record_histogram("histogram", 1.0)
    observability.record_gauge("gauge", 1)

    telemetry.tracer.start_as_current_span.assert_not_called()
    telemetry.worker_tracer.start_as_current_span.assert_not_called()
    telemetry.current_span.assert_not_called()
    telemetry.meter.create_counter.assert_not_called()
    telemetry.meter.create_histogram.assert_not_called()
    telemetry.meter.create_gauge.assert_not_called()


def test_resource_attributes_include_environment_and_release() -> None:
    config = ObservabilityConfig.model_validate(
        {
            "environment": "staging",
            "release": "abcdef123456",
        }
    )

    assert observability._resource_attributes(config, "worker") == {  # pyright: ignore[reportPrivateUsage]
        "service.name": "redstone-squid-worker",
        "deployment.environment.name": "staging",
        "service.version": "abcdef123456",
    }


def test_inherited_configured_state_is_rejected_after_fork(mocker: MockerFixture) -> None:
    observability._configured_pid = 100  # pyright: ignore[reportPrivateUsage]
    observability._configured_handle = ObservabilityHandle()  # pyright: ignore[reportPrivateUsage]
    mocker.patch.object(observability.os, "getpid", return_value=200)

    with pytest.raises(RuntimeError, match="before this process forked"):
        configure_observability(enabled_config(), service_name="api")


def test_correlation_id_stays_unique_without_a_tracer(mocker: MockerFixture) -> None:
    """Errors stay correlatable on deployments that never installed the SDK.

    `build_error_presentation` shows this id to the user and logs it beside the redacted
    diagnostic, so the untraced path has to keep producing distinct ids rather than a
    constant. That it prefers the active trace id when there is one is proven for real,
    against a live tracer, in tests/integration/observability/test_traces.py.
    """
    mocker.patch.object(observability, "_current_trace_id", return_value=None)

    error_ids = {observability.correlation_id() for _ in range(3)}

    assert len(error_ids) == 3
    assert all(len(error_id) == 12 for error_id in error_ids)
    assert all(set(error_id) <= set("0123456789abcdef") for error_id in error_ids)


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


def test_bound_correlation_id_wins_and_resets(mocker: MockerFixture) -> None:
    mocker.patch.object(observability, "_current_trace_id", return_value=None)

    token = observability.bind_correlation_id("request-scoped-id")
    try:
        assert observability.correlation_id() == "request-scoped-id"
    finally:
        observability.unbind_correlation_id(token)

    # After unbinding, the untraced fallback resumes producing 12-hex-char ids.
    fallback = observability.correlation_id()
    assert len(fallback) == 12
    assert set(fallback) <= set("0123456789abcdef")


def test_trace_context_filter_stamps_bound_request_id(mocker: MockerFixture) -> None:
    mocker.patch.object(observability, "_current_trace_context", return_value=None)
    record = logging.LogRecord("squid.test", logging.INFO, __file__, 1, "message", (), None)

    token = observability.bind_correlation_id("bound-request-id")
    try:
        observability.TraceContextFilter().filter(record)
    finally:
        observability.unbind_correlation_id(token)

    assert vars(record)["request_id"] == "bound-request-id"


def test_trace_context_filter_preserves_preset_request_id(mocker: MockerFixture) -> None:
    mocker.patch.object(observability, "_current_trace_context", return_value=None)
    record = logging.LogRecord("squid.test", logging.INFO, __file__, 1, "message", (), None)
    vars(record)["request_id"] = "already-set"

    token = observability.bind_correlation_id("bound-request-id")
    try:
        observability.TraceContextFilter().filter(record)
    finally:
        observability.unbind_correlation_id(token)

    assert vars(record)["request_id"] == "already-set"


def test_correlation_reference_shortens_a_trace_id_and_leaves_the_fallback_alone(
    mocker: MockerFixture,
) -> None:
    """The two transports must show the same width, whether or not the SDK is installed.

    A user reading an error card cannot tell which deployment they hit, so a reference quoted
    from a traced process and one quoted from an untraced process have to look alike -- otherwise
    the moderator looking it up has to know which is which.
    """
    assert observability.correlation_reference("a" * 32) == "a" * 12

    mocker.patch.object(observability, "_current_trace_id", return_value=None)
    fallback = observability.correlation_id()

    assert observability.correlation_reference(fallback) == fallback


def test_correlation_scope_is_reentrant_and_unbinds(mocker: MockerFixture) -> None:
    """A hybrid command opens both the tree's scope and the prefix path's, and must get one ID."""
    mocker.patch.object(observability, "_current_trace_id", return_value=None)

    with observability.correlation_scope() as outer:
        with observability.correlation_scope() as inner:
            assert inner == outer
        # The nested scope must not have unbound the outer one on the way out.
        assert observability.correlation_id() == outer

    assert observability.correlation_id() != outer


def test_correlated_log_buffer_keeps_the_most_recent_records_per_correlation() -> None:
    buffer = observability.CorrelatedLogBuffer(max_records=2)
    buffer.setFormatter(logging.Formatter("%(message)s"))

    for index in range(3):
        buffer.handle(_correlated_record(f"first-{index}", correlation="one"))
    buffer.handle(_correlated_record("other", correlation="two"))

    assert buffer.drain("one") == ("first-1", "first-2")
    assert buffer.drain("two") == ("other",)


def test_correlated_log_buffer_drains_once() -> None:
    """A drained tail must not reappear on a second error sharing the correlation."""
    buffer = observability.CorrelatedLogBuffer()
    buffer.setFormatter(logging.Formatter("%(message)s"))
    buffer.handle(_correlated_record("only", correlation="one"))

    assert buffer.drain("one") == ("only",)
    assert buffer.drain("one") == ()


def test_correlated_log_buffer_evicts_whole_correlations_past_its_bound() -> None:
    """One pathological correlation must not be able to displace every other tail."""
    buffer = observability.CorrelatedLogBuffer(max_records=4, max_correlations=2)
    buffer.setFormatter(logging.Formatter("%(message)s"))

    for name in ("one", "two", "three"):
        buffer.handle(_correlated_record(name, correlation=name))

    assert buffer.drain("one") == ()
    assert buffer.drain("two") == ("two",)
    assert buffer.drain("three") == ("three",)


def test_correlated_log_buffer_ignores_records_without_a_correlation() -> None:
    """Records logged outside any command or request have no tail to belong to."""
    buffer = observability.CorrelatedLogBuffer()
    buffer.setFormatter(logging.Formatter("%(message)s"))

    buffer.handle(logging.LogRecord("squid.test", logging.INFO, __file__, 1, "loose", (), None))

    assert buffer.drain("one") == ()


def _correlated_record(message: str, *, correlation: str) -> logging.LogRecord:
    record = logging.LogRecord("squid.test", logging.INFO, __file__, 1, message, (), None)
    record.request_id = correlation
    return record


def _fake_telemetry(mocker: MockerFixture, *, pid: int | None = None) -> Any:
    return observability._Telemetry(  # pyright: ignore[reportPrivateUsage]
        pid=observability.os.getpid() if pid is None else pid,
        tracer=mocker.Mock(),
        worker_tracer=mocker.Mock(),
        meter=mocker.Mock(),
        propagator=mocker.Mock(),
        current_span=mocker.Mock(),
        error_status=mocker.Mock(),
        instrument_api_app=mocker.Mock(),
    )


def test_install_trace_context_log_filter_is_idempotent() -> None:
    handler = logging.NullHandler()
    handler.set_name("squid-test-idempotent-handler")
    logging.getLogger().addHandler(handler)
    try:
        observability.install_trace_context_log_filter()
        observability.install_trace_context_log_filter()

        trace_filters = [f for f in handler.filters if isinstance(f, observability.TraceContextFilter)]
        assert len(trace_filters) == 1
    finally:
        logging.getLogger().removeHandler(handler)
