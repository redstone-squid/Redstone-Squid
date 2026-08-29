"""The records diagnostics and maintenance workspace."""

from collections.abc import Sequence
from dataclasses import dataclass

from squid.bot.submission.records import RecordsScreen
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import RECORD_ENTRY_INSPECT, RECORD_ENTRY_REBUILD
from squid.records.application import (
    RebuildSummary,
    RecordComputationService,
    RecordGap,
    RecordLookupRequest,
    RecordService,
    TitleDiagnosticGap,
)
from squid.records.domain import BuildKind, RecordClass
from squid_ui.testing import RecordingResponder, labels, press, submit, submit_event


class RecordRecorder(RecordService):
    def __init__(self) -> None:
        self.gap_reads = 0
        self.materialize_calls: list[tuple[int, BuildKind | None, int | None]] = []

    async def gaps(self, *, kind: BuildKind | None = None) -> Sequence[RecordGap]:
        self.gap_reads += 1
        return (RecordGap(1, "Smallest door", None, RecordClass.SMALLEST, (7,), ("volume",)),)

    async def title_gaps(self, *, kind: BuildKind | None = None) -> Sequence[TitleDiagnosticGap]:
        return (TitleDiagnosticGap(2, "Unknown door", ({"code": "unknown"},)),)

    async def materialize_definition(
        self,
        definition_id: int,
        *,
        kind: BuildKind | None = None,
        version_id: int | None = None,
    ) -> RebuildSummary:
        self.materialize_calls.append((definition_id, kind, version_id))
        return RebuildSummary((), 1, 1, 0)

    async def lookup_or_materialize(self, request: RecordLookupRequest) -> RebuildSummary:
        return RebuildSummary((), 1, 1, 0)


class ComputationRecorder(RecordComputationService):
    def __init__(self) -> None:
        self.rebuild_calls: list[tuple[int | None, tuple[BuildKind, ...]]] = []

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
            can_inspect=inspect,
            can_rebuild=rebuild,
            authorize=Authorizer(inspect=inspect, rebuild=rebuild),
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
    assert {"Evidence gaps", "Title issues", "Lookup and rebuild"} <= set(labels(screen._tabs.render()))


async def test_lookup_materializes_and_refreshes_diagnostics() -> None:
    harness = make_screen()
    screen = harness.screen
    await screen.on_load()
    responder = RecordingResponder()
    await press(screen, "tabs.records-tabs.maintenance")

    await submit(
        screen,
        "tabs.lookup",
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
    await press(screen, "tabs.records-tabs.maintenance")

    await submit(screen, "tabs.rebuild", {"kind": None, "version_id": None})

    assert harness.computation.rebuild_calls == [(None, (BuildKind.DOOR, BuildKind.EXTENDER))]


async def test_revoked_rebuild_permission_prevents_the_write() -> None:
    harness = make_screen(rebuild=False)
    screen = harness.screen
    await screen.on_load()
    responder = RecordingResponder()

    await screen._rebuild(submit_event({"kind": None, "version_id": None}, responder=responder))

    assert harness.computation.rebuild_calls == []
    assert len(responder.notices) == 1
