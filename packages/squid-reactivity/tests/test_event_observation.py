"""Observer lifetime, execution isolation, and redaction across event variants."""

import gc
import weakref
from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import override

import anyio
import pytest

from squid_reactivity import EventLedger, Reactive, TransactionCommit, observe_events, transaction
from squid_reactivity.diagnostics import (
    OperationEventSnapshot,
    RedactionPolicy,
    TransactionResultSnapshot,
    TransactionValidationSnapshot,
    emit_causal_event,
)
from squid_reactivity.operations import ProgressReporter, operation
from squid_reactivity.resources import resource
from squid_reactivity.transactions import ActorRef, ExceptionReport, TransactionContext


def test_snapshot_only_reads_metadata_when_included() -> None:
    class Metadata(Mapping[str, str]):
        iterations = 0

        @override
        def __iter__(self) -> Iterator[str]:
            self.iterations += 1
            return iter(("z", "a"))

        @override
        def __len__(self) -> int:
            return 2

        @override
        def __getitem__(self, key: str) -> str:
            return f"value-{key}"

    metadata = Metadata()
    context = replace(TransactionContext.create(actor=ActorRef("user", "1")), metadata=metadata)
    with transaction(transaction_context=context) as tx:
        pass
    assert isinstance(tx.result, TransactionCommit)
    default = TransactionResultSnapshot.from_result(tx.result)
    assert default.metadata == ()
    assert metadata.iterations == 0
    assert RedactionPolicy().redact(default) is default

    included = TransactionResultSnapshot.from_result(tx.result, RedactionPolicy(include_metadata=True))
    assert included.metadata == (("a", "value-a"), ("z", "value-z"))
    assert metadata.iterations == 1
    hidden = RedactionPolicy(include_actor=False).redact(included)
    assert hidden.actor is None
    assert hidden.metadata == ()
    assert included.actor == context.actor
    assert included.metadata == (("a", "value-a"), ("z", "value-z"))


@pytest.mark.parametrize("exception", [None, ExceptionReport("Error", "detail")])
def test_redaction_reuses_events_without_exposing_exception_messages(exception: ExceptionReport | None) -> None:
    event = OperationEventSnapshot("1", None, None, "operation", "finished", datetime.now(UTC), exception)
    assert RedactionPolicy(include_exception_messages=True).redact(event) is event
    policy = RedactionPolicy()
    redacted = policy.redact(event)
    if exception is None:
        assert redacted is event
    else:
        assert redacted.exception == ExceptionReport("Error", "[redacted]")
        assert event.exception == ExceptionReport("Error", "detail")
    assert policy.redact(redacted) is redacted


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
    assert [
        event.exception.message
        for event in ledger.events
        if not isinstance(event, TransactionValidationSnapshot) and event.exception
    ] == ["detail", "[redacted]"]


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

    def failed_callback(result: TransactionCommit) -> None:
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
    reports = [
        event.exception.message
        for event in privileged.events
        if not isinstance(event, TransactionValidationSnapshot) and event.exception
    ]
    assert reports == ["operation detail", "resource detail", "callback detail"]
    assert [
        event.exception.message
        for event in default.events
        if not isinstance(event, TransactionValidationSnapshot) and event.exception
    ] == ["[redacted]"] * 3
