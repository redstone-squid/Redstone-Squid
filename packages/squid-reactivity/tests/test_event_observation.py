"""Observer lifetime, execution isolation, and redaction across event variants."""

import gc
import weakref
from datetime import UTC, datetime

import anyio
import pytest

from squid_reactivity import (
    ActionCommit,
    ActionResultSnapshot,
    CausalRef,
    ContinuationFailureSnapshot,
    EventCodec,
    EventLedger,
    ExceptionReport,
    OperationEventSnapshot,
    Reactive,
    RedactionPolicy,
    ResourceEventSnapshot,
    observe_events,
    transaction,
)
from squid_reactivity.actions import CausalEventSnapshot, emit_causal_event
from squid_reactivity.operations import ProgressReporter, operation
from squid_reactivity.resources import resource


async def test_scopes_isolate_siblings_and_expire_in_inherited_tasks() -> None:
    ledger = EventLedger()
    started = anyio.Event()
    released = anyio.Event()

    async def child() -> None:
        with transaction("inherited"):
            pass
        started.set()
        await released.wait()
        with transaction("expired"):
            pass

    async with anyio.create_task_group() as group:
        with observe_events(ledger):
            group.start_soon(child)
            await started.wait()
        with transaction("outside"):
            pass
        released.set()
    assert [result.name for result in ledger.results] == ["inherited"]

    first, second = EventLedger(), EventLedger()
    both_started = anyio.Event()

    async def sibling(sink: EventLedger, name: str) -> None:
        with observe_events(sink):
            if name == "first":
                await both_started.wait()
            else:
                both_started.set()
            with transaction(name):
                pass

    async with anyio.create_task_group() as group:
        group.start_soon(sibling, first, "first")
        group.start_soon(sibling, second, "second")
    assert [result.name for result in first.results] == ["first"]
    assert [result.name for result in second.results] == ["second"]


def test_nested_registration_delivers_once_and_restores_outer_policy() -> None:
    ledger = EventLedger()
    event = OperationEventSnapshot(
        "1", None, None, "operation", "failed", datetime.now(UTC), ExceptionReport("Error", "detail")
    )
    with observe_events(ledger):
        with observe_events(ledger, redaction=RedactionPolicy(include_exception_messages=True)):
            emit_causal_event(event)
        emit_causal_event(event)
    assert [event.exception.message for event in ledger.events if event.exception] == ["detail", "[redacted]"]


async def test_expired_registration_releases_sink_even_with_inherited_context() -> None:
    ledger = EventLedger()
    reference = weakref.ref(ledger)
    release = anyio.Event()
    async with anyio.create_task_group() as group:
        with observe_events(ledger):
            group.start_soon(release.wait)
        del ledger
        gc.collect()
        assert reference() is None
        release.set()


async def test_operation_resource_and_callback_errors_use_each_observers_policy() -> None:
    class Owner(Reactive):
        @operation(initial=None)
        async def work(self, progress: ProgressReporter[None]) -> None:
            raise ValueError("operation detail")

        @resource
        async def data(self) -> None:
            raise ValueError("resource detail")

    def failed_callback(result: ActionCommit) -> None:
        raise ValueError("callback detail")

    owner = Owner()
    default, privileged = EventLedger(), EventLedger()
    with (
        observe_events(default),
        observe_events(privileged, redaction=RedactionPolicy(include_exception_messages=True)),
    ):
        with pytest.raises(ValueError, match="operation detail"):
            await owner.work.start()
        with pytest.raises(ValueError, match="resource detail"):
            await owner.data
        with transaction() as tx:
            tx.after_commit(failed_callback)
    reports = [event.exception.message for event in privileged.events if event.exception]
    assert reports == ["operation detail", "resource detail", "callback detail"]
    assert [event.exception.message for event in default.events if event.exception] == ["[redacted]"] * 3


def test_event_codec_round_trips_every_variant() -> None:
    now = datetime.now(UTC)
    cause = CausalRef("action", "root")
    error = ExceptionReport("Error", "detail")
    with transaction() as tx:
        pass
    assert tx.result is not None
    events: list[CausalEventSnapshot] = [
        ActionResultSnapshot.from_result(tx.result),
        OperationEventSnapshot("operation", "root", cause, "fetch", "failed", now, error),
        ResourceEventSnapshot("generation", "root", cause, "data", "failed", now, error),
        ContinuationFailureSnapshot("failure", "root", cause, "callback", "failed", now, error),
    ]
    codec = EventCodec()
    assert [codec.decode(codec.encode(event)) for event in events] == events


@pytest.mark.parametrize(
    "data", [b"null", b"[]", b"invalid", b'{"schema":2}', b'{"schema":1,"event":"unknown","data":{}}']
)
def test_event_codec_rejects_unknown_or_malformed_records(data: bytes) -> None:
    with pytest.raises(ValueError, match="schema"):
        EventCodec().decode(data)
