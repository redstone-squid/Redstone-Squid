"""Commit-ledger history and version-conditional undo/redo."""

import gc
import uuid
import weakref
from datetime import UTC, datetime

import anyio
import pytest

from squid_reactivity import (
    ActionLedger,
    ChangeReport,
    OperationEventSnapshot,
    TransactionContribution,
    add_action_result_sink,
    enlist,
    on_action_commit,
)
from squid_reactivity.operations import OperationContext
from squid_ui import Component, state
from squid_ui.primitives import Text
from squid_ui.runtime import (
    CompensationClaim,
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
    SharedState,
    UndoMode,
    history,
    history_actions,
    inspect_cells,
    transaction,
)
from squid_ui.semantic import ActionControl


class Workspace(SharedState[str]):
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


class UnassignedPanel(Component):
    """A slot that holds no value of its own until an action puts one there.

    How an absent slot actually arises: `__init__` leaves the field alone, so the cell exists
    and its declared default is what anyone reads, but nothing has been assigned and the first
    write's inverse restores absence rather than a value. `_checked_init` makes this the only
    shape that reaches it -- a field declared `state()` with no default must be assigned during
    construction, so no component can carry one that is absent.
    """

    history: History = history()
    value: int = state(0)

    def render(self):
        return Text("unassigned")


def panel() -> tuple[Panel, Workspace]:
    workspace = Workspace(LocalTopicBus(), "here")
    subject = Panel(workspace)
    ComponentRuntime(subject)
    return subject, workspace


def controls(stack: History) -> tuple[ActionControl, ActionControl]:
    undo, redo = history_actions(stack).controls
    assert isinstance(undo, ActionControl)
    assert isinstance(redo, ActionControl)
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
    # A rejected admission started no inverse, so the entry is still undoable.
    assert subject.history.entries[0].state is HistoryEntryState.READY
    assert subject.history.can_undo

    result = await subject.history.undo()

    assert result.applied
    assert subject.page == 1


async def test_fresh_redo_is_rejected_after_outer_handler_staged_work() -> None:
    subject, _ = panel()
    with transaction():
        subject.history.record("page")
        subject.page = 4
    assert (await subject.history.undo()).applied

    with pytest.raises(RuntimeError, match="staged changes"), transaction():
        subject.open = True
        await subject.history.redo()

    assert subject.page == 1
    assert subject.open is False
    assert subject.history.redoable[0].state is HistoryEntryState.UNDONE
    assert subject.history.can_redo

    result = await subject.history.redo()

    assert result.applied
    assert subject.page == 4


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
    assert subject.history.delete_conflicted() is not None
    assert not subject.history.can_undo


async def test_named_local_overwrite_policy_replaces_later_ephemeral_work() -> None:
    subject, _ = panel()
    with transaction():
        subject.history.record("page", mode=UndoMode.LOCAL_OVERWRITE)
        subject.page = 4
    with transaction():
        subject.page = 8

    result = await subject.history.undo()

    assert result.applied
    assert subject.page == 1


async def test_local_overwrite_policy_refuses_shared_state() -> None:
    subject, workspace = panel()
    with transaction():
        subject.history.record("select", mode=UndoMode.LOCAL_OVERWRITE)
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


async def test_absent_slot_keeps_lineage_across_undo_and_reassignment() -> None:
    subject = UnassignedPanel()
    ComponentRuntime(subject)
    # Not read before the recorded action: reading materializes the default onto the cell,
    # which is exactly the absence this is about.
    with transaction():
        subject.history.record("assign")
        subject.value = 7
    with transaction():
        subject.value = 9

    result = await subject.history.undo()

    assert result.status is HistoryResultStatus.CONFLICT
    assert subject.value == 9


async def test_absent_slot_undo_and_redo_use_fresh_versions() -> None:
    subject = UnassignedPanel()
    ComponentRuntime(subject)
    with transaction():
        subject.history.record("assign")
        subject.value = 7

    assert (await subject.history.undo()).applied
    assert not inspect_cells(subject)["value"].assigned, "back to holding no value of its own"
    assert subject.value == 0, "so the declared default is what reads"
    assert (await subject.history.redo()).applied
    assert subject.value == 7


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


async def test_participant_planning_failure_returns_failed_without_partial_inverse() -> None:
    class BadToken:
        def plan_inverse(self):
            raise RuntimeError("backend unavailable")

    class Participant:
        def prepare(self, view) -> None:
            return None

        def describe_change(self, prepared: None) -> TransactionContribution:
            return TransactionContribution("bad", BadToken(), ChangeReport(participants=1))

        def apply(self, prepared: None) -> None:
            pass

        def abort(self, prepared: None, cause: BaseException) -> None:
            pass

        def finalize(self, prepared: None) -> None:
            pass

    subject, _ = panel()
    with transaction():
        subject.history.record("page")
        subject.page = 4
        enlist(object(), Participant)

    result = await subject.history.undo()

    assert result.status is HistoryResultStatus.FAILED
    assert subject.page == 4
    assert isinstance(result.error, RuntimeError)


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
    add_action_result_sink(ledger)

    async def compensate(key: str) -> None:
        pass

    try:
        with transaction():
            subject.history.record("page", compensate=CompensationSpec(compensate, lambda commit: "page"))
            subject.page = 4
        original = ledger.results[-1]

        result = await subject.history.undo()
    finally:
        ledger.close()

    assert result.applied
    descendants = [result for result in ledger.results if result.root_action_id == original.root_action_id]
    assert [result.kind for result in descendants] == ["normal", "compensation", "compensation"]
    assert descendants[1].cause is not None and descendants[1].cause.kind == "operation"
    operation_events = [event for event in ledger.events if isinstance(event, OperationEventSnapshot)]
    assert [event.status for event in operation_events] == ["reverting", "external_succeeded", "reverted"]


async def test_fresh_compensation_is_rejected_after_outer_handler_staged_work() -> None:
    subject, _ = panel()
    calls: list[str] = []

    async def compensate(key: str) -> None:
        calls.append(key)

    with transaction():
        subject.history.record("page", compensate=CompensationSpec(compensate, lambda commit: "page"))
        subject.page = 4

    with pytest.raises(RuntimeError, match="staged changes"), transaction():
        subject.open = True
        await subject.history.undo()

    assert calls == []
    assert subject.history.entries[0].state is HistoryEntryState.READY
    assert subject.history.can_undo

    result = await subject.history.undo()

    assert result.applied
    assert calls == ["page"]


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
    assert outbox.dropped_records == 0, "an unsettled record carries no answer to lose"


async def test_reference_compensation_outbox_reports_a_forgotten_settled_answer() -> None:
    """The bound can only be held by forgetting, and forgetting a "yes" repeats an effect."""
    outbox = MemoryCompensationOutbox(limit=2)

    def intent_for(index: int) -> CompensationIntent:
        context = OperationContext(uuid.uuid7(), None, None, f"attempt {index}")
        return CompensationIntent(context, uuid.uuid7(), f"undo:{index}", datetime.now(UTC))

    settled = intent_for(0)
    await outbox.claim(settled, CompensationRetryPolicy())
    await outbox.update(settled, CompensationStatus.EXTERNAL_SUCCEEDED)
    assert not (await outbox.claim(settled, CompensationRetryPolicy())).dispatch

    for index in (1, 2):
        await outbox.claim(intent_for(index), CompensationRetryPolicy())

    assert outbox.dropped_records == 1
    # The record is gone, so the outbox can no longer refuse it -- which is exactly why the
    # loss is counted rather than absorbed.
    assert (await outbox.claim(settled, CompensationRetryPolicy())).dispatch


def test_memory_outbox_persists_first_intent_at_the_commit_point() -> None:
    outbox = MemoryCompensationOutbox()
    root = uuid.uuid7()
    context = OperationContext(uuid.uuid7(), None, root, "delete channel")
    intent = CompensationIntent(context, uuid.uuid7(), "undo:channel", datetime.now(UTC))
    visible_at_commit: list[tuple] = []

    with transaction():
        joined = enlist(outbox, lambda: outbox.participant(intent))
        assert joined is not None
        on_action_commit(lambda commit, continuation: visible_at_commit.append(outbox.records))

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


async def test_outbox_failure_after_external_success_needs_reconciliation_before_local_inverse() -> None:
    class BrokenUpdateOutbox:
        async def claim(self, intent, retry):
            return CompensationClaim(dispatch=True, attempts=1, status=CompensationStatus.REVERTING)

        async def update(self, intent, status, error=None) -> None:
            raise RuntimeError("cannot persist external success")

    subject, _ = panel()
    calls: list[str] = []

    async def compensate(key: str) -> None:
        calls.append(key)

    stack = History(subject, compensation_outbox=BrokenUpdateOutbox())
    with transaction():
        stack.record("page", compensate=CompensationSpec(compensate, lambda commit: "outbox"))
        subject.page = 4

    result = await stack.undo()
    repeated = await stack.undo()

    assert result.status is HistoryResultStatus.NEEDS_RECONCILIATION
    assert repeated is result
    assert subject.page == 4
    assert calls == ["outbox"]
