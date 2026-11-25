"""The records diagnostics and maintenance workspace."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import override

import pytest

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.catalogue.records import RecordsScreen
from squid.bot.utils.permissions import Grants
from squid.core.actors import ActingContext, UserActor
from squid.core.permissions.domain import PermissionNode
from squid.core.permissions.domain.catalogue import RECORD_ENTRY_INSPECT, RECORD_ENTRY_REBUILD
from squid.core.records.application import (
    RebuildSummary,
    RecordAdministrationService,
    RecordGap,
    RecordLookupRequest,
    RecordNameDiagnostic,
    RecordService,
)
from squid.core.records.domain import BuildKind, RecordCriterion, RecordFact
from squid_ui.ext.discord.testing import assert_within_limits, commit_render, delivered_to, message_harness
from squid_ui.testing import RecordingResponder, labels, press, press_event, submit, submit_event
from tests.support.discord import make_layout_bot

pytestmark = pytest.mark.usefixtures("discord_screen_context")


class RecordRecorder(RecordService):
    def __init__(self) -> None:
        self.gap_reads = 0
        self.recompute_calls: list[int] = []

    @override
    async def gaps(self, *, kind: BuildKind | None = None, context: ActingContext) -> Sequence[RecordGap]:
        self.gap_reads += 1
        return (
            RecordGap(
                1, "Smallest door", None, RecordCriterion.SMALLEST, (7,), (RecordFact.FIXED_VOLUME,), BuildKind.DOOR
            ),
        )

    @override
    async def title_gaps(
        self, *, kind: BuildKind | None = None, context: ActingContext
    ) -> Sequence[RecordNameDiagnostic]:
        return (RecordNameDiagnostic(2, "Unknown door", ({"code": "unknown"},), BuildKind.DOOR),)

    @override
    async def request_definition_recompute(self, definition_id: int, *, context: ActingContext) -> None:
        self.recompute_calls.append(definition_id)

    @override
    async def lookup_or_materialize(self, request: RecordLookupRequest, *, context: ActingContext) -> int:
        return 5


class ComputationRecorder(RecordAdministrationService):
    def __init__(self) -> None:
        self.rebuild_calls: list[tuple[int | None, tuple[BuildKind, ...]]] = []
        self.contexts: list[ActingContext] = []

    @override
    async def rebuild(
        self,
        *,
        context: ActingContext,
        current_version_id: int | None = None,
        kinds: Sequence[BuildKind] = (BuildKind.DOOR, BuildKind.EXTENDER),
    ) -> RebuildSummary:
        self.contexts.append(context)
        self.rebuild_calls.append((current_version_id, tuple(kinds)))
        return RebuildSummary((), records=4, changed=3, retired=1, with_gaps=1)


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


async def test_numeric_lookup_queues_the_definitions_record() -> None:
    """A numeric lookup selects a RecordDefinition and queues the record it names."""
    harness = make_screen()
    screen = harness.screen
    await screen.on_load()
    responder = RecordingResponder()
    assert screen._tabs is not None
    await press(screen._tabs, "records-tabs.maintenance")

    await submit(
        screen._tabs,
        "lookup",
        {"kind": BuildKind.DOOR, "base_key": "42", "restrictions": None},
        responder=responder,
    )

    assert harness.records.recompute_calls == [42]
    assert len(responder.notices) == 1


async def test_rebuild_uses_the_existing_default_kinds() -> None:
    harness = make_screen()
    screen = harness.screen
    await screen.on_load()
    assert screen._tabs is not None
    await press(screen._tabs, "records-tabs.maintenance")

    context = ActingContext.direct(UserActor(discord_user_id=10))
    with context.scope():
        await submit(screen._tabs, "rebuild", {"kind": None, "version_id": None})
    assert harness.computation.contexts == [context]

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

    with ActingContext.direct(UserActor(discord_user_id=10)).scope():
        await submit(harness.screen._tabs, "rebuild", {"kind": None, "version_id": 3})

    assert harness.computation.rebuild_calls == [(3, (BuildKind.DOOR, BuildKind.EXTENDER))]


async def test_diagnostic_recompute_rechecks_permission() -> None:
    harness = make_screen()
    screen = harness.screen
    await screen.on_load()

    await screen._recompute_diagnostic(42, press_event())
    assert harness.records.recompute_calls == [42]
    screen.grants = Grants(screen.grants.held, Authorizer(inspect=False, rebuild=True))
    await screen._recompute_diagnostic(42, press_event())
    assert harness.records.recompute_calls == [42]


async def test_large_diagnostics_render_within_discord_limits() -> None:
    harness = make_screen()

    async def gaps(*, kind: BuildKind | None = None, context: ActingContext) -> Sequence[RecordGap]:
        return tuple(
            RecordGap(
                i,
                f"Record category {i} with a descriptive title",
                None,
                RecordCriterion.SMALLEST,
                (i,),
                (RecordFact.FIXED_VOLUME,),
                BuildKind.DOOR,
            )
            for i in range(1, 101)
        )

    harness.records.gaps = gaps
    await harness.screen.on_load()
    root = make_layout_bot().ui.mount(harness.screen, access=sd.Owner(7), timeout=300)
    await root.send(delivered_to(message_harness()))
    view = commit_render(root)
    assert_within_limits(view)
    assert "Record category 1" in str(view.to_components())
