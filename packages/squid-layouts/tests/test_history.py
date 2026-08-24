"""Commit-ledger history and version-conditional undo/redo."""

import pytest

from squid_layouts import Component, state
from squid_layouts.primitives import Text
from squid_layouts.runtime import (
    CompensationSpec,
    CompensationStatus,
    ComponentRuntime,
    History,
    HistoryEntryState,
    HistoryError,
    HistoryResultStatus,
    LocalTopicBus,
    Shared,
    history,
    history_actions,
    transaction,
)
from squid_layouts.semantic import Action


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
