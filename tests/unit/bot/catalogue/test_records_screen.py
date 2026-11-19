"""The records diagnostics and maintenance workspace."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import override

import pytest

import squid_ui as sl
from squid.bot.catalogue.records import RecordsScreen
from squid.bot.utils.permissions import Grants
from squid.core.permissions.domain import PermissionNode
from squid.core.permissions.domain.catalogue import RECORD_ENTRY_INSPECT, RECORD_ENTRY_REBUILD
from squid.core.records.application import (
    RebuildSummary,
    RecordComputationService,
    RecordGap,
    RecordLookupRequest,
    RecordService,
    TitleDiagnosticGap,
)
from squid.core.records.domain import BaseTitle, BuildKind
from squid.core.suggestions.domain import Suggestion
from squid_ui.testing import RecordingResponder, labels, press, press_event, submit, submit_event


class RecordRecorder(RecordService):
    def __init__(self) -> None:
        self.gap_reads = 0
        self.materialize_calls: list[tuple[int, BuildKind | None, int | None]] = []

    @override
    async def gaps(self, *, kind: BuildKind | None = None) -> Sequence[RecordGap]:
        self.gap_reads += 1
        return (RecordGap(1, "Smallest door", None, BaseTitle.SMALLEST, (7,), ("volume",), BuildKind.DOOR),)

    @override
    async def title_gaps(self, *, kind: BuildKind | None = None) -> Sequence[TitleDiagnosticGap]:
        return (TitleDiagnosticGap(2, "Unknown door", ({"code": "unknown"},), BuildKind.DOOR),)

    @override
    async def materialize_definition(
        self,
        definition_id: int,
        *,
        kind: BuildKind | None = None,
        version_id: int | None = None,
    ) -> RebuildSummary:
        self.materialize_calls.append((definition_id, kind, version_id))
        return RebuildSummary((), 1, 1, 0)

    @override
    async def lookup_or_materialize(self, request: RecordLookupRequest) -> RebuildSummary:
        return RebuildSummary((), 1, 1, 0)


class ComputationRecorder(RecordComputationService):
    def __init__(self) -> None:
        self.rebuild_calls: list[tuple[int | None, tuple[BuildKind, ...]]] = []
        self.pin_calls: list[int | None] = []

    @override
    async def pin_current_version(self, version_id: int | None) -> None:
        self.pin_calls.append(version_id)

    @override
    async def rebuild(
        self,
        *,
        current_version_id: int | None = None,
        kinds: Sequence[BuildKind] = (BuildKind.DOOR, BuildKind.EXTENDER),
    ) -> RebuildSummary:
        self.rebuild_calls.append((current_version_id, tuple(kinds)))
        return RebuildSummary((), 4, 3, 1)


class Authorizer:
    def __init__(self, *, inspect: bool, rebuild: bool) -> None:
        self.inspect = inspect
        self.rebuild = rebuild

    async def __call__(self, node: PermissionNode) -> bool:
        if node is RECORD_ENTRY_INSPECT:
            return self.inspect
        assert node is RECORD_ENTRY_REBUILD
        return self.rebuild


@dataclass(frozen=True)
class ScreenHarness:
    screen: RecordsScreen
    records: RecordRecorder
    computation: ComputationRecorder


def make_screen(*, inspect: bool = True, rebuild: bool = True) -> ScreenHarness:
    records = RecordRecorder()
    computation = ComputationRecorder()

    return ScreenHarness(
        RecordsScreen(
            records,
            computation,
            grants=Grants(
                frozenset(
                    node for node, held in ((RECORD_ENTRY_INSPECT, inspect), (RECORD_ENTRY_REBUILD, rebuild)) if held
                ),
                Authorizer(inspect=inspect, rebuild=rebuild),
            ),
        ),
        records,
        computation,
    )


async def test_records_use_tabs_and_browsers_for_diagnostics() -> None:
    screen = make_screen().screen
    await screen.on_load()

    assert screen._gaps is not None
    assert screen._titles is not None
    assert screen._tabs is not None
    assert {"Missing evidence", "Unrecognised titles", "Recompute"} <= set(labels(screen._tabs.render()))


async def test_lookup_materializes_and_refreshes_diagnostics() -> None:
    """A numeric lookup selects a TitleDefinition definition, then refreshes its evidence diagnostics."""
    harness = make_screen()
    screen = harness.screen
    await screen.on_load()
    responder = RecordingResponder()
    assert screen._tabs is not None
    await press(screen._tabs, "records-tabs.maintenance")

    await submit(
        screen._tabs,
        "lookup",
        {"kind": BuildKind.DOOR, "base_key": "42", "restrictions": None, "version_id": 3},
        responder=responder,
    )

    assert harness.records.materialize_calls == [(42, BuildKind.DOOR, 3)]
    assert harness.records.gap_reads == 2
    assert len(responder.notices) == 1


async def test_rebuild_uses_the_existing_default_kinds() -> None:
    harness = make_screen()
    screen = harness.screen
    await screen.on_load()
    assert screen._tabs is not None
    await press(screen._tabs, "records-tabs.maintenance")

    await submit(screen._tabs, "rebuild", {"kind": None, "version_id": None})

    assert harness.computation.rebuild_calls == [(None, (BuildKind.DOOR, BuildKind.EXTENDER))]


async def test_revoked_rebuild_permission_prevents_the_write() -> None:
    harness = make_screen(rebuild=False)
    screen = harness.screen
    await screen.on_load()
    responder = RecordingResponder()

    await screen._rebuild(submit_event({"kind": None, "version_id": None}, responder=responder))

    assert harness.computation.rebuild_calls == []
    assert len(responder.notices) == 1


async def test_failed_diagnostic_refresh_rolls_back_the_visible_browsers() -> None:
    screen = make_screen().screen
    await screen.on_load()
    original = screen._gaps, screen._titles, screen._tabs

    async def refresh_then_fail() -> None:
        await screen.on_load()
        raise RuntimeError("later failure")

    with pytest.raises(RuntimeError, match="later failure"), sl.runtime.transaction():
        await refresh_then_fail()

    assert (screen._gaps, screen._titles, screen._tabs) == original


async def test_rebuild_explicitly_sets_the_shared_current_version() -> None:
    harness = make_screen()
    await harness.screen.on_load()
    assert harness.screen._tabs is not None
    await press(harness.screen._tabs, "records-tabs.maintenance")

    await submit(harness.screen._tabs, "rebuild", {"kind": None, "version_id": 3})

    assert harness.computation.pin_calls == [3]
    assert harness.computation.rebuild_calls == [(3, (BuildKind.DOOR, BuildKind.EXTENDER))]


@pytest.mark.parametrize("kind", list(BuildKind))
async def test_diagnostic_recompute_carries_kind_version_and_rechecks_permission(kind: BuildKind) -> None:
    harness = make_screen()
    screen = harness.screen
    await screen.on_load()

    with sl.runtime.transaction():
        screen._version = Suggestion(value="9", label="Java 1.21", kind="version")
        await screen._recompute_diagnostic(42, kind, press_event())
    assert harness.records.materialize_calls == [(42, kind, 9)]
    screen.grants = Grants(screen.grants.held, Authorizer(inspect=False, rebuild=True))
    await screen._recompute_diagnostic(42, kind, press_event())
    assert harness.records.materialize_calls == [(42, kind, 9)]
