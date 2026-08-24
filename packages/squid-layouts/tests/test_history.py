"""Commit-ledger history and version-conditional undo/redo."""

import gc
import uuid
import weakref
from datetime import UTC, datetime

import anyio
import pytest

from squid_layouts import Component, state
from squid_layouts.primitives import Text
from squid_layouts.runtime import (
    CompensationIntent,
    CompensationRecordCodec,
    CompensationRetryPolicy,
    CompensationSpec,
    CompensationStatus,
    ComponentRuntime,
    History,
    HistoryEntryState,
    HistoryError,
    HistoryResultStatus,
    LocalTopicBus,
    MemoryCompensationOutbox,
    Shared,
    UndoStrategy,
    history,
    history_actions,
    transaction,
)
from squid_layouts.semantic import Action
from squid_reactive import ActionLedger, OperationEventSnapshot, add_action_outcome_sink, join_action, on_action_commit
from squid_reactive.operations import OperationContext


class Workspace(Shared[str]):
    selected: int | None = state(None)
    filters: tuple[str, ...] = state(())


class Panel(Component):
    history: History = history(limit=3)
    page: int = state(1)
    open: bool = state(default=False)

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def render(self):
        return Text(str(self.page))


class RequiredPanel(Component):
    history: History = history()
    value: int = state()

    def __init__(self) -> None:
        self.value = 3

    def render(self):
        return Text("required")


def panel() -> tuple[Panel, Workspace]:
    workspace = Workspace(LocalTopicBus(), "here")
    subject = Panel(workspace)
    ComponentRuntime(subject)
    return subject, workspace


def controls(stack: History) -> tuple[Action, Action]:
    undo, redo = history_actions(stack).actions
    assert isinstance(undo, Action)
    assert isinstance(redo, Action)
    return undo, redo


def test_record_requires_an_action_and_rejects_old_inverse_shape() -> None:
    subject, _ = panel()
    with pytest.raises(RuntimeError, match="inside an action"):
        subject.history.record("nothing")
    with transaction(), pytest.raises(TypeError):
        subject.history.record("old", undo=lambda: None)  # type: ignore[call-arg]


def test_one_entry_captures_the_whole_committed_action() -> None:
    subject, workspace = panel()
    with transaction():
        subject.history.record("select")
        subject.open = True
        workspace.selected = 7

    entry = subject.history.entries[0]
    assert len(entry.undo_plan.cells) == 2
    assert entry.state is HistoryEntryState.READY


def test_one_history_may_only_record_once_per_action() -> None:
    subject, _ = panel()
    with transaction():
        subject.history.record("first")
        with pytest.raises(HistoryError, match="already used"):
            subject.history.record("second")


async def test_undo_preserves_unrelated_later_work() -> None:
    subject, workspace = panel()
    with transaction():
        subject.history.record("select")
        workspace.selected = 7
    with transaction():
        subject.page = 4

    result = await subject.history.undo()

    assert result.status is HistoryResultStatus.APPLIED
    assert workspace.selected is None
    assert subject.page == 4
    assert subject.history.can_redo


async def test_mounted_handler_envelope_starts_a_fresh_undo_action() -> None:
    subject, _ = panel()
    with transaction():
        subject.history.record("page")
        subject.page = 4

    with transaction():
        result = await subject.history.undo()

    assert result.applied
    assert subject.page == 1


async def test_fresh_undo_is_rejected_after_outer_handler_staged_work() -> None:
    subject, _ = panel()
    with transaction():
        subject.history.record("page")
        subject.page = 4

    with pytest.raises(RuntimeError, match="staged changes"), transaction():
        subject.open = True
        await subject.history.undo()

    assert subject.page == 4
    assert subject.open is False


async def test_later_same_target_write_conflicts_without_clobbering() -> None:
    subject, workspace = panel()
    with transaction():
        subject.history.record("select")
        workspace.selected = 7
    with transaction():
        workspace.selected = 9

    result = await subject.history.undo()

    assert result.status is HistoryResultStatus.CONFLICT
    assert workspace.selected == 9
    assert subject.history.entries[0].state is HistoryEntryState.CONFLICTED
    assert subject.history.drop_conflicted() is not None
    assert not subject.history.can_undo


async def test_named_local_overwrite_policy_replaces_later_ephemeral_work() -> None:
    subject, _ = panel()
    with transaction():
        subject.history.record("page", strategy=UndoStrategy.LOCAL_OVERWRITE)
        subject.page = 4
    with transaction():
        subject.page = 8

    result = await subject.history.undo()

    assert result.applied
    assert subject.page == 1


async def test_local_overwrite_policy_refuses_shared_state() -> None:
    subject, workspace = panel()
    with transaction():
        subject.history.record("select", strategy=UndoStrategy.LOCAL_OVERWRITE)
        workspace.selected = 7
    with transaction():
        workspace.selected = 9

    result = await subject.history.undo()

    assert result.status is HistoryResultStatus.CONFLICT
    assert workspace.selected == 9


async def test_mixed_inverse_is_all_or_nothing() -> None:
    subject, workspace = panel()
    with transaction():
        subject.history.record("open selection")
        subject.open = True
        workspace.selected = 7
    with transaction():
        workspace.selected = 9

    result = await subject.history.undo()

    assert result.status is HistoryResultStatus.CONFLICT
    assert subject.open is True
    assert workspace.selected == 9


async def test_redo_is_based_on_the_actual_undo_commit() -> None:
    subject, _ = panel()
    with transaction():
        subject.history.record("page")
        subject.page = 4

    undo = await subject.history.undo()
    assert undo.applied
    assert subject.page == 1
    redo = await subject.history.redo()
    assert redo.applied
    assert subject.page == 4
    again = await subject.history.undo()
    assert again.applied
    assert subject.page == 1


async def test_intervening_write_makes_redo_conflict() -> None:
    subject, _ = panel()
    with transaction():
        subject.history.record("page")
        subject.page = 4
    assert (await subject.history.undo()).applied
    with transaction():
        subject.page = 8

    result = await subject.history.redo()

    assert result.status is HistoryResultStatus.CONFLICT
    assert subject.page == 8


async def test_absent_slot_keeps_lineage_across_undo_and_recreation() -> None:
    subject = RequiredPanel()
    ComponentRuntime(subject)
    with transaction():
        subject.history.record("delete")
        del subject.value

    with pytest.raises(AttributeError, match="never assigned"):
        _ = subject.value
    with transaction():
        subject.value = 9

    result = await subject.history.undo()

    assert result.status is HistoryResultStatus.CONFLICT
    assert subject.value == 9


async def test_deleted_slot_undo_and_redo_use_fresh_versions() -> None:
    subject = RequiredPanel()
    ComponentRuntime(subject)
    with transaction():
        subject.history.record("delete")
        del subject.value

    assert (await subject.history.undo()).applied
    assert subject.value == 3
    assert (await subject.history.redo()).applied
    with pytest.raises(AttributeError, match="never assigned"):
        _ = subject.value


async def test_selective_targeting_uses_action_identity() -> None:
    subject, workspace = panel()
    with transaction():
        subject.history.record("page")
        subject.page = 2
    first_id = subject.history.entries[-1].original_action_id
    with transaction():
        subject.history.record("select")
        workspace.selected = 7

    result = await subject.history.undo(first_id)

    assert result.applied
    assert subject.page == 1
    assert workspace.selected == 7


def test_limit_snapshot_and_controls_follow_retained_entries() -> None:
    subject, _ = panel()
    for page in range(2, 7):
        with transaction():
            subject.history.record(f"page {page}")
            subject.page = page

    assert [entry.label for entry in subject.history.entries] == ["page 4", "page 5", "page 6"]
    snapshot = subject.history.snapshot("history")
    assert snapshot.undo[-1].state == "ready"
    undo, redo = controls(subject.history)
    assert (undo.available, redo.available) == (True, False)


def test_retained_history_does_not_pin_its_component_owner_graph() -> None:
    subject, workspace = panel()
    with transaction():
        subject.history.record("page")
        subject.page = 2
        workspace.selected = 7
    owner_ref = weakref.ref(subject)
    history_ref = weakref.ref(subject.history)

    del subject
    gc.collect()

    assert owner_ref() is None
    assert history_ref() is None


async def test_failed_compensation_is_truthful_and_retry_gets_new_execution() -> None:
    subject, _ = panel()
    calls: list[str] = []

    async def compensate(key: str) -> None:
        calls.append(key)
        if len(calls) == 1:
            raise RuntimeError("service unavailable")

    with transaction():
        subject.history.record(
            "page",
            compensate=CompensationSpec(compensate, lambda commit: f"undo:{commit.context.action_id}"),
        )
        subject.page = 4

    failed = await subject.history.undo()
    assert failed.entry is not None
    first_execution = failed.entry.compensation_execution
    assert failed.status is HistoryResultStatus.FAILED
    assert first_execution is not None and first_execution.status is CompensationStatus.FAILED
    retried = await subject.history.undo()
    assert retried.entry is not None and retried.entry.compensation_execution is not None
    assert retried.applied
    assert retried.entry.compensation_execution.execution_id != first_execution.execution_id
    assert calls == [calls[0], calls[0]]


async def test_external_success_then_local_conflict_needs_reconciliation_without_duplicate_retry() -> None:
    subject, _ = panel()
    calls: list[str] = []

    async def compensate(key: str) -> None:
        calls.append(key)

    with transaction():
        subject.history.record(
            "page",
            compensate=CompensationSpec(compensate, lambda commit: f"undo:{commit.context.action_id}"),
        )
        subject.page = 4
    with transaction():
        subject.page = 8

    result = await subject.history.undo()
    retry = await subject.history.undo()

    assert result.status is HistoryResultStatus.NEEDS_RECONCILIATION
    assert result.entry is not None
    assert result.entry.state is HistoryEntryState.NEEDS_RECONCILIATION
    assert retry.status is HistoryResultStatus.NEEDS_RECONCILIATION
    assert len(calls) == 1
    assert subject.page == 8


async def test_compensation_intent_and_local_inverse_are_causal_actions() -> None:
    subject, _ = panel()
    ledger = ActionLedger()
    add_action_outcome_sink(ledger)

    async def compensate(key: str) -> None:
        pass

    try:
        with transaction():
            subject.history.record("page", compensate=CompensationSpec(compensate, lambda commit: "page"))
            subject.page = 4
        original = ledger.outcomes[-1]

        result = await subject.history.undo()
    finally:
        ledger.close()

    assert result.applied
    descendants = [outcome for outcome in ledger.outcomes if outcome.root_action_id == original.root_action_id]
    assert [outcome.kind for outcome in descendants] == ["action", "compensation", "compensation"]
    assert descendants[1].cause is not None and descendants[1].cause.kind == "operation"
    operation_events = [event for event in ledger.events if isinstance(event, OperationEventSnapshot)]
    assert [event.status for event in operation_events] == ["reverting", "external_succeeded", "reverted"]


async def test_compensation_cancellation_is_retained_and_propagated() -> None:
    subject, _ = panel()
    started = anyio.Event()

    async def compensate(key: str) -> None:
        started.set()
        await anyio.sleep_forever()

    with transaction():
        subject.history.record("page", compensate=CompensationSpec(compensate, lambda commit: "cancel"))
        subject.page = 4

    async def run() -> None:
        await subject.history.undo()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(run)
        await started.wait()
        tasks.cancel_scope.cancel()

    execution = subject.history.entries[0].compensation_execution
    assert execution is not None and execution.status is CompensationStatus.CANCELLED
    assert subject.page == 4


async def test_compensation_retry_policy_stops_new_external_attempts() -> None:
    subject, _ = panel()
    calls = 0

    async def compensate(key: str) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("unavailable")

    with transaction():
        subject.history.record(
            "page",
            compensate=CompensationSpec(
                compensate,
                lambda commit: "bounded",
                CompensationRetryPolicy(max_attempts=1),
            ),
        )
        subject.page = 4

    assert (await subject.history.undo()).status is HistoryResultStatus.FAILED
    assert (await subject.history.undo()).status is HistoryResultStatus.FAILED
    assert calls == 1


async def test_compensation_record_round_trips_and_recovers_after_restart() -> None:
    outbox = MemoryCompensationOutbox()
    root = uuid.uuid7()
    context = OperationContext(uuid.uuid7(), None, root, "delete channel")
    original = uuid.uuid7()
    intent = CompensationIntent(context, original, "undo:channel", datetime.now(UTC))
    first = await outbox.claim(intent, CompensationRetryPolicy(max_attempts=3))
    assert first.dispatch and first.attempts == 1
    await outbox.update(intent, CompensationStatus.FAILED)
    codec = CompensationRecordCodec()

    restored = MemoryCompensationOutbox(records=(codec.decode(codec.encode(outbox.records[0])),))
    retry_context = OperationContext(uuid.uuid7(), context.causal_ref(), root, context.name)
    retry_intent = CompensationIntent(retry_context, original, intent.idempotency_key, datetime.now(UTC))
    retry = await restored.claim(retry_intent, CompensationRetryPolicy(max_attempts=3))

    assert retry.dispatch and retry.attempts == 2
    await restored.update(retry_intent, CompensationStatus.EXTERNAL_SUCCEEDED)
    after_success = await restored.claim(retry_intent, CompensationRetryPolicy(max_attempts=3))
    assert not after_success.dispatch


async def test_reference_compensation_outbox_retention_is_bounded() -> None:
    outbox = MemoryCompensationOutbox(limit=2)
    for index in range(3):
        context = OperationContext(uuid.uuid7(), None, None, f"attempt {index}")
        intent = CompensationIntent(context, uuid.uuid7(), f"undo:{index}", datetime.now(UTC))
        await outbox.claim(intent, CompensationRetryPolicy())

    assert [record.intent.idempotency_key for record in outbox.records] == ["undo:1", "undo:2"]


def test_memory_outbox_persists_first_intent_at_the_commit_point() -> None:
    outbox = MemoryCompensationOutbox()
    root = uuid.uuid7()
    context = OperationContext(uuid.uuid7(), None, root, "delete channel")
    intent = CompensationIntent(context, uuid.uuid7(), "undo:channel", datetime.now(UTC))
    visible_at_commit: list[tuple] = []

    with transaction():
        joined = join_action(outbox, lambda: outbox.participant(intent))
        assert joined is not None
        on_action_commit(lambda commit, aftermath: visible_at_commit.append(outbox.records))

    assert visible_at_commit[0][0].intent == intent
    assert visible_at_commit[0][0].attempts == 0


def test_compensation_codec_rejects_unknown_corrupt_and_oversized_records() -> None:
    codec = CompensationRecordCodec()

    with pytest.raises(ValueError, match="unsupported"):
        codec.decode(b'{"schema":2}')
    with pytest.raises(ValueError, match="corrupt"):
        codec.decode(b"not-json")
    with pytest.raises(ValueError, match="maximum encoded size"):
        codec.decode(b" " * 65_537)


async def test_outbox_claim_failure_is_a_truthful_compensation_failure() -> None:
    class BrokenOutbox:
        async def claim(self, intent, retry):
            raise RuntimeError("outbox unavailable")

        async def update(self, intent, status, error=None) -> None:
            pass

    subject, _ = panel()

    async def compensate(key: str) -> None:
        raise AssertionError("claim failed, so external work must not start")

    stack = History(subject, compensation_outbox=BrokenOutbox())
    with transaction():
        stack.record("page", compensate=CompensationSpec(compensate, lambda commit: "outbox"))
        subject.page = 4

    result = await stack.undo()

    assert result.status is HistoryResultStatus.FAILED
    assert result.entry is not None and result.entry.compensation_execution is not None
    assert result.entry.compensation_execution.status is CompensationStatus.FAILED
    assert isinstance(result.error, RuntimeError)
