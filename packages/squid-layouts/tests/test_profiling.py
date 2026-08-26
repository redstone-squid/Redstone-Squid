"""Bounded runtime trace tests for the in-process profiler."""

import asyncio
import json
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from squid_layouts.profiling import (
    MemoryProfiler,
    NoOpProfiler,
    OperationKind,
    OperationRecorder,
    SpanId,
    TraceId,
    TraceLink,
    TraceResult,
    TraceStatus,
    snapshot_json,
)


class Clock:
    def __init__(self) -> None:
        self.value = 100.0
        self.wall = datetime(2026, 8, 22, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.value

    def utc(self) -> datetime:
        return self.wall + timedelta(seconds=self.value - 100.0)

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _ids() -> Iterator[bytes]:
    counter = 1
    while True:
        yield counter.to_bytes(16, "big")
        counter += 1


def profiler(clock: Clock, **kwargs: object) -> MemoryProfiler:
    generated = _ids()

    def next_id(size: int) -> bytes:
        return next(generated)[-size:]

    return MemoryProfiler(
        clock=clock.monotonic,
        wall_clock=clock.utc,
        id_source=next_id,
        **cast(Any, kwargs),
    )


def only_trace(subject: MemoryProfiler):
    traces = (
        subject.snapshot().recent
        + subject.snapshot().slow
        + subject.snapshot().failed
        + subject.snapshot().deadline_misses
    )
    assert len(traces) == 1
    return traces[0]


def trace_by_name(subject: MemoryProfiler, name: str):
    traces = (
        subject.snapshot().recent
        + subject.snapshot().slow
        + subject.snapshot().failed
        + subject.snapshot().deadline_misses
    )
    matches = tuple(trace for trace in traces if trace.name == name)
    assert len(matches) == 1
    return matches[0]


def aggregate(subject: MemoryProfiler, name: str):
    for item in subject.snapshot().aggregates:
        if item.key.name == name:
            return item
    raise AssertionError(f"missing aggregate {name}")


def span_aggregate(subject: MemoryProfiler, name: str):
    for item in subject.snapshot().span_aggregates:
        if item.key.span_name == name:
            return item
    raise AssertionError(f"missing span aggregate {name}")


def test_identifier_width_and_nonzero_are_enforced() -> None:
    with pytest.raises(ValueError, match=r"16 bytes.*all zero"):
        TraceId(bytes(16))
    with pytest.raises(ValueError, match=r"8 bytes.*all zero"):
        SpanId(b"short")
    assert TraceId(b"\x00" * 15 + b"\x01")
    assert SpanId(b"\x00" * 7 + b"\x01")


def test_root_and_nested_parentage_and_offsets() -> None:
    clock = Clock()
    subject = profiler(clock)

    with subject.operation(OperationKind.DISPATCH, name="save") as operation:
        with operation.span("middleware"):
            clock.advance(0.2)
            with operation.span("handler"):
                clock.advance(0.3)
            clock.advance(0.4)
        with operation.span("post"):
            clock.advance(0.1)

    trace = trace_by_name(subject, "save")
    root, middleware, handler, post = trace.spans

    assert trace.name == "save"
    assert root.parent_span_id is None
    assert middleware.parent_span_id == root.span_id
    assert handler.parent_span_id == middleware.span_id
    assert post.parent_span_id == root.span_id
    assert middleware.started == pytest.approx(0.0)
    assert middleware.duration == pytest.approx(0.9)
    assert handler.started == pytest.approx(0.2)
    assert handler.duration == pytest.approx(0.3)
    assert trace.duration == pytest.approx(1.0)
    assert all(span.duration >= 0 for span in trace.spans)


def test_concurrent_sibling_spans_keep_operation_parent() -> None:
    clock = Clock()
    subject = profiler(clock)

    entered = asyncio.Event()
    release = asyncio.Event()

    async def child(operation: OperationRecorder, name: str) -> None:
        with operation.span(name):
            entered.set()
            await release.wait()

    async def run() -> None:
        with subject.operation(OperationKind.DELIVERY, name="notify") as operation:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(child(operation, "one"))
                tasks.create_task(child(operation, "two"))
                await entered.wait()
                clock.advance(1.0)
                release.set()

    asyncio.run(run())

    root, one, two = only_trace(subject).spans
    assert one.parent_span_id == root.span_id
    assert two.parent_span_id == root.span_id


def test_detached_span_measures_work_overlapping_lexical_spans() -> None:
    clock = Clock()
    subject = profiler(clock)

    with subject.operation(OperationKind.DISPATCH, name="click") as operation:
        acknowledgement = operation.start_span("acknowledgement")
        clock.advance(0.1)
        with operation.span("handler"):
            clock.advance(0.2)
            acknowledgement.set_attribute("source", "watchdog")
            acknowledgement.finish()
            clock.advance(0.3)
        acknowledgement.finish(TraceStatus.FAILED)

    trace = trace_by_name(subject, "click")
    root, acknowledgement_span, handler = trace.spans
    assert acknowledgement_span.parent_span_id == root.span_id
    assert handler.parent_span_id == root.span_id
    assert acknowledgement_span.duration == pytest.approx(0.3)
    assert acknowledgement_span.attributes[0].value == "watchdog"
    assert acknowledgement_span.status is TraceStatus.COMPLETED


def test_operation_may_include_elapsed_queue_time_and_record_its_span() -> None:
    clock = Clock()
    subject = profiler(clock)
    queued_at = clock.monotonic()
    clock.advance(2.0)

    with subject.operation(OperationKind.TOPIC_DELIVERY, name="topic", started=queued_at) as operation:
        operation.record_span("queue_wait", 2.0, attributes={"triggers": 3})
        clock.advance(0.5)

    trace = trace_by_name(subject, "topic")
    queue_wait = next(span for span in trace.spans if span.name == "queue_wait")
    assert trace.duration == pytest.approx(2.5)
    assert queue_wait.started == pytest.approx(0.0)
    assert queue_wait.duration == pytest.approx(2.0)
    assert queue_wait.attributes[0].value == 3


def test_operation_counters_survive_tail_sampling_and_roll_into_window() -> None:
    clock = Clock()
    subject = profiler(clock, recent=0)

    with subject.operation(OperationKind.SEND, name="panel") as operation:
        operation.increment("planner.calls")
        operation.increment("planner.cache_hits")
        operation.increment("planner.states_explored", 7)

    snapshot = subject.snapshot()
    counters = {aggregate.key.counter_name: aggregate for aggregate in snapshot.counter_aggregates}
    assert snapshot.recent == ()
    assert counters["planner.calls"].lifetime == 1
    assert counters["planner.cache_hits"].window == 1
    assert counters["planner.states_explored"].lifetime == 7


def test_task_local_parentage_carries_into_child_tasks() -> None:
    clock = Clock()
    subject = profiler(clock)
    links: list[TraceLink | None] = []

    async def child(operation: OperationRecorder) -> None:
        with operation.span("child"):
            links.append(subject.capture_link())

    async def run() -> None:
        with subject.operation(OperationKind.DISPATCH, name="parent") as operation:
            with operation.span("outer"):
                await asyncio.sleep(0)
                awaitable = asyncio.create_task(child(operation))
                await awaitable
                links.append(subject.capture_link())

            links.append(subject.capture_link())

    asyncio.run(run())

    trace = trace_by_name(subject, "parent")
    assert links[0] is not None
    assert links[1] is not None
    assert links[0].span_id is not None
    assert trace.spans[1].parent_span_id == trace.spans[0].span_id
    assert links[0].span_id != links[1].span_id


def test_caught_child_failure_still_completes_operation() -> None:
    clock = Clock()
    subject = profiler(clock)

    with subject.operation(OperationKind.DISPATCH, name="caught") as operation, operation.span("handler") as captured:
        captured.set_status(TraceStatus.FAILED)

    trace = trace_by_name(subject, "caught")
    assert trace.result.status is TraceStatus.COMPLETED
    assert trace.spans[1].status is TraceStatus.FAILED


def test_escaping_exception_overrides_an_explicit_span_outcome() -> None:
    clock = Clock()
    subject = profiler(clock)

    with (
        subject.operation(OperationKind.DISPATCH, name="caught") as operation,
        pytest.raises(ValueError),
        operation.span("handler") as span,
    ):
        span.set_status(TraceStatus.COMPLETED)
        raise ValueError("the explicit success must not hide this")

    trace = trace_by_name(subject, "caught")
    assert trace.result.status is TraceStatus.COMPLETED
    assert trace.spans[1].status is TraceStatus.FAILED


def test_escaping_exception_records_only_type() -> None:
    clock = Clock()
    subject = profiler(clock)

    with pytest.raises(LookupError), subject.operation(OperationKind.DELIVERY, name="lookup"):
        raise LookupError("secret payload")

    trace = subject.snapshot().failed[0]
    assert trace.result == TraceResult(TraceStatus.FAILED, "builtins.LookupError")
    detail = trace.result.detail
    assert detail is not None
    assert "secret payload" not in detail


def test_cancellation_is_recorded_and_propagated() -> None:
    clock = Clock()
    subject = profiler(clock)

    async def cancelled() -> None:
        with subject.operation(OperationKind.REFRESH, name="refresh"):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancelled())

    assert subject.snapshot().failed[0].result.status is TraceStatus.CANCELLED


def test_exception_group_cancellation_detected_when_nested() -> None:
    clock = Clock()
    subject = profiler(clock)

    async def grouped() -> None:
        with subject.operation(OperationKind.DELIVERY, name="grouped"):
            raise BaseExceptionGroup(
                "group",
                [RuntimeError("bad"), asyncio.CancelledError()],
            )

    with pytest.raises(BaseExceptionGroup):
        asyncio.run(grouped())

    assert subject.snapshot().failed[0].result.status is TraceStatus.CANCELLED


def test_active_snapshot_exposes_only_running_spans() -> None:
    clock = Clock()
    subject = profiler(clock)

    with subject.operation(OperationKind.DISPATCH, name="vote") as operation, operation.span("action"):
        clock.advance(0.75)
        active = subject.snapshot().active
        assert len(active) == 1
        active_trace = active[0]
        assert active_trace.name == "vote"
        assert active_trace.elapsed == pytest.approx(0.75)
        assert len(active_trace.current_spans) == 1
        active_span = active_trace.current_spans[0]
        assert active_span.parent_span_id is not None
        assert active_span.name == "action"
        assert active_span.elapsed == pytest.approx(0.75)

    assert subject.snapshot().active == ()


def test_capture_link_deduplicates_and_tracks_omitted() -> None:
    clock = Clock()
    subject = profiler(clock, max_links=1)

    with subject.operation(OperationKind.DISPATCH, name="producer") as operation, operation.span("work"):
        captured = subject.capture_link()
        assert captured is not None

    other = TraceLink(
        TraceId((2).to_bytes(16, "big")),
        SpanId((2).to_bytes(8, "big")),
    )
    with subject.operation(
        OperationKind.DELIVERY,
        name="consumer",
        links=(captured, captured, other),
    ):
        pass

    trace = trace_by_name(subject, "consumer")
    assert trace.links == (captured,)
    assert trace.omitted_links == 1


def test_max_links_zero_keeps_no_links() -> None:
    clock = Clock()
    subject = profiler(clock, max_links=0)

    with subject.operation(OperationKind.DISPATCH, name="producer") as operation, operation.span("work"):
        source = subject.capture_link()
        assert source is not None

    with subject.operation(OperationKind.REFRESH, name="consumer", links=(source,)):
        pass

    trace = trace_by_name(subject, "consumer")
    assert trace.links == ()
    assert trace.omitted_links == 1


def test_attribute_validation_limits_and_rejection_count() -> None:
    clock = Clock()
    subject = profiler(clock)

    with subject.operation(
        OperationKind.SEND,
        name="attrs",
        attributes={
            "ok": "value",
            "num": 3,
            "flt": 1.5,
            "flag": True,
            "too_long": "x" * 300,
            "bad": object(),  # type: ignore[dict-item]
            "nan": float("nan"),
            "inf": float("inf"),
            42: "bad-key",  # type: ignore[dict-item]
            "": "empty-key",
        },
    ):
        pass

    root = only_trace(subject).spans[0]
    assert len(root.attributes) == 5
    assert root.attributes[0].key == "ok"
    assert root.attributes[0].value == "value"
    assert root.attributes[4].key == "too_long"
    long_value = root.attributes[4].value
    assert isinstance(long_value, str)
    assert len(long_value) == 256
    assert subject.snapshot().health.rejected_attributes == 6


def test_tail_sampling_prefers_fail_slow_and_deadline() -> None:
    clock = Clock()
    calls: list[int] = []

    def sample_source() -> float:
        calls.append(1)
        return 0.0

    subject = profiler(
        clock,
        recent=1,
        slow=1,
        failed=1,
        deadline_misses=1,
        slow_threshold=1.0,
        sample_source=sample_source,
    )

    with subject.operation(OperationKind.DISPATCH, name="ok"):
        clock.advance(0.1)
    with subject.operation(OperationKind.DISPATCH, name="slow"):
        clock.advance(2.0)
    with pytest.raises(RuntimeError), subject.operation(OperationKind.DISPATCH, name="fail"):
        raise RuntimeError
    with subject.operation(OperationKind.REFRESH, name="miss") as operation:
        operation.mark_deadline_missed()
        clock.advance(0.1)

    snapshot = subject.snapshot()
    assert [trace.name for trace in snapshot.recent] == ["ok"]
    assert [trace.name for trace in snapshot.slow] == ["slow"]
    assert [trace.name for trace in snapshot.failed] == ["fail"]
    assert [trace.name for trace in snapshot.deadline_misses] == ["miss"]
    assert snapshot.health.sampled_out == 0
    assert calls == []


def test_all_operations_contribute_to_aggregates() -> None:
    clock = Clock()
    subject = profiler(clock, recent=0, slow=0, failed=0, deadline_misses=0)

    with subject.operation(OperationKind.DISPATCH, name="first") as operation, operation.span("handler"):
        clock.advance(0.1)
    with subject.operation(OperationKind.DISPATCH, name="second"):
        clock.advance(0.1)
    with subject.operation(OperationKind.DISPATCH, name="third"):
        clock.advance(0.1)

    assert subject.snapshot().recent == ()
    assert subject.snapshot().health.sampled_out == 0
    assert subject.snapshot().health.dropped_traces == 3
    assert aggregate(subject, "first").lifetime.observations == 1
    assert span_aggregate(subject, "handler").lifetime.observations == 1
    assert sum(item.lifetime.observations for item in subject.snapshot().aggregates) == 3


def test_zero_sized_buffers_record_zero_traces_and_counts() -> None:
    clock = Clock()
    subject = profiler(clock, recent=0, slow=0, failed=0, deadline_misses=0)

    with subject.operation(OperationKind.DISPATCH, name="one"):
        clock.advance(0.1)

    snapshot = subject.snapshot()
    assert snapshot.recent == ()
    assert snapshot.health.sampled_out == 0
    assert snapshot.health.dropped_traces == 1
    assert snapshot.aggregates[0].lifetime.observations == 1


def test_disabled_failure_retention_is_dropped_not_sampled_out() -> None:
    clock = Clock()
    subject = profiler(clock, recent=0, slow=0, failed=0, deadline_misses=0, slow_threshold=1)

    with pytest.raises(RuntimeError), subject.operation(OperationKind.DISPATCH, name="failed"):
        raise RuntimeError

    health = subject.snapshot().health
    assert health.sampled_out == 0
    assert health.dropped_traces == 1


def test_recent_eviction_count() -> None:
    clock = Clock()
    subject = profiler(clock, recent=2, slow=0, failed=0, deadline_misses=0, sample_rate=1.0)

    with subject.operation(OperationKind.DISPATCH, name="one"):
        clock.advance(0.1)
    with subject.operation(OperationKind.DISPATCH, name="two"):
        clock.advance(0.1)
    with subject.operation(OperationKind.DISPATCH, name="three"):
        clock.advance(0.1)

    snapshot = subject.snapshot()
    assert len(snapshot.recent) == 2
    assert snapshot.health.evicted == 1


def test_rolling_window_histograms_expire_without_new_traces() -> None:
    clock = Clock()
    subject = profiler(clock, window_seconds=10, window_slices=2)

    with subject.operation(OperationKind.SEND, name="send") as operation, operation.span("write"):
        clock.advance(0.5)

    assert aggregate(subject, "send").window.observations == 1
    assert span_aggregate(subject, "write").window.observations == 1
    clock.advance(11)
    assert aggregate(subject, "send").window.observations == 0
    assert span_aggregate(subject, "write").window.observations == 0


def test_aggregate_overflow() -> None:
    clock = Clock()
    subject = profiler(clock, max_aggregate_keys=1)

    with subject.operation(OperationKind.DISPATCH, name="a"):
        clock.advance(0.1)
    with subject.operation(OperationKind.DISPATCH, name="b"):
        clock.advance(0.1)
    with subject.operation(OperationKind.SEND, name="c"):
        clock.advance(0.1)

    aggregates = subject.snapshot().aggregates
    assert len(aggregates) == 2
    assert any(item.key.name == "<overflow>" for item in aggregates)

    overflow = next(item for item in aggregates if item.key.name == "<overflow>")
    assert overflow.lifetime.observations == 2
    assert overflow.key.operation is None
    assert overflow.key.status is None
    assert overflow.key.detail is None
    assert overflow.key.disposition is None
    assert overflow.key.action is None
    assert overflow.key.presentation is None


def test_zero_aggregate_key_bound_uses_single_overflow_key() -> None:
    clock = Clock()
    subject = profiler(clock, max_aggregate_keys=0)

    with subject.operation(OperationKind.DISPATCH, name="a"):
        clock.advance(0.1)
    with subject.operation(OperationKind.DISPATCH, name="b"):
        clock.advance(0.1)

    assert len(subject.snapshot().aggregates) == 1
    assert subject.snapshot().aggregates[0].key.name == "<overflow>"
    assert subject.snapshot().aggregates[0].lifetime.observations == 2


def test_span_aggregate_overflow_is_bounded_and_honest() -> None:
    clock = Clock()
    subject = profiler(clock, max_span_aggregate_keys=1)

    with subject.operation(OperationKind.DISPATCH, name="action") as operation:
        with operation.span("one"):
            clock.advance(0.1)
        with operation.span("two"):
            clock.advance(0.1)
        with operation.span("three"):
            clock.advance(0.1)

    aggregates = subject.snapshot().span_aggregates
    assert len(aggregates) == 2
    overflow = next(item for item in aggregates if item.key.span_name == "<overflow>")
    assert overflow.key.operation is None
    assert overflow.key.status is None
    assert overflow.lifetime.observations == 2


def test_counter_aggregate_overflow_is_bounded_and_honest() -> None:
    clock = Clock()
    subject = profiler(clock, max_counter_keys=1)

    with subject.operation(OperationKind.SEND, name="panel") as operation:
        operation.increment("one")
        operation.increment("two", 2)
        operation.increment("three", 3)

    aggregates = subject.snapshot().counter_aggregates
    assert len(aggregates) == 2
    overflow = next(item for item in aggregates if item.key.counter_name == "<overflow>")
    assert overflow.key.operation is None
    assert overflow.lifetime == 5
    assert overflow.window == 5


def test_percentile_rank_uses_ceiling() -> None:
    clock = Clock()
    subject = profiler(clock, window_seconds=30, histogram_bounds=(0.1, 0.2, 0.3), max_aggregate_keys=1)

    with subject.operation(OperationKind.DISPATCH, name="a"):
        clock.advance(0.05)
    with subject.operation(OperationKind.DISPATCH, name="a"):
        clock.advance(0.15)
    with subject.operation(OperationKind.DISPATCH, name="a"):
        clock.advance(0.25)

    stats = aggregate(subject, "a")
    assert stats.lifetime.percentile(0.34) == pytest.approx(0.2)
    assert stats.lifetime.percentile(0.99) == pytest.approx(0.3)


def test_snapshot_json_exports_frozen_snapshot() -> None:
    clock = Clock()
    subject = profiler(clock)

    with subject.operation(OperationKind.SEND, name="send"):
        clock.advance(0.1)

    encoded = snapshot_json(subject.snapshot())
    decoded = json.loads(encoded)
    assert decoded["schema_version"] == 1
    assert decoded["recent"][0]["trace_id"] == str(subject.snapshot().recent[0].trace_id)
    assert decoded["recent"][0]["operation"] == "send"
    assert decoded["active"] == []


def test_trace_start_is_relative_to_the_exported_wall_clock_anchor() -> None:
    clock = Clock()
    subject = profiler(clock)
    clock.advance(2.0)

    with subject.operation(OperationKind.SEND, name="send"):
        clock.advance(0.1)

    snapshot = subject.snapshot()
    assert snapshot.recent[0].started == pytest.approx(2.0)
    assert snapshot.started_at + timedelta(seconds=snapshot.recent[0].started) == datetime(
        2026, 8, 22, 0, 0, 2, tzinfo=UTC
    )


def test_operation_recorder_expires_with_its_dynamic_scope() -> None:
    clock = Clock()
    subject = profiler(clock)

    with subject.operation(OperationKind.DISPATCH, name="done") as operation:
        link = subject.capture_link()

    assert link is not None
    assert subject.capture_link() is None
    with operation.span("too-late"):
        assert subject.capture_link() is None
    operation.set_result(TraceResult(TraceStatus.FAILED))
    operation.mark_deadline_missed()

    trace = trace_by_name(subject, "done")
    assert [span.name for span in trace.spans] == ["done"]
    assert trace.result.status is TraceStatus.COMPLETED
    assert not trace.deadline_missed


def test_noop_profiler_does_not_inspect_inputs() -> None:
    class ExplodingMapping(dict[str, str]):
        def items(self):  # pragma: no cover
            raise AssertionError("no-op profiler should not inspect attributes")

    subject = NoOpProfiler()
    link = TraceLink(TraceId((1).to_bytes(16, "big")), SpanId((1).to_bytes(8, "big")))
    with (
        subject.operation(
            OperationKind.DISPATCH,
            attributes=ExplodingMapping(),
            links=(link,),
        ) as operation,
        operation.span("handler", attributes=ExplodingMapping()),
    ):
        assert subject.capture_link() is None

    assert subject.snapshot().active == ()


def test_profiler_failure_isolation_preserves_application_behavior() -> None:
    clock = Clock()

    def bad_ids(_: int) -> bytes:
        return b"\x00" * 16

    subject = MemoryProfiler(clock=clock.monotonic, wall_clock=clock.utc, id_source=bad_ids)

    with subject.operation(OperationKind.DISPATCH, name="bad"):
        pass

    snapshot = subject.snapshot()
    assert snapshot.active == ()
    assert snapshot.health.internal_failures >= 1


def test_core_import_still_works_when_discord_and_anyio_missing() -> None:
    code = """
import importlib.abc
import sys


class BlockAdapterDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        head = fullname.split(\".\", 1)[0]
        if head in {\"discord\", \"anyio\"}:
            raise ModuleNotFoundError(fullname)
        return None


sys.meta_path.insert(0, BlockAdapterDependencies())
import squid_layouts
import squid_layouts.profiling
assert \"discord\" not in sys.modules
assert \"anyio\" not in sys.modules
"""

    # `sys.executable`, not a bare "python": the interpreter on PATH is not this venv when
    # pytest is driven from Git Bash, and the import assertions below only mean anything when
    # the subprocess is the same interpreter that installed squid_layouts.
    subprocess.run([sys.executable, "-c", code], check=True)
