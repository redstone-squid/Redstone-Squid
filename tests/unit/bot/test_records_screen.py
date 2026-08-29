"""The records diagnostics and maintenance workspace."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import squid_ui as sl
from squid.bot.submission.records import (
    RecordCog,
    RecordComputationOperations,
    RecordOperations,
    RecordsScreen,
)
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import RECORD_ENTRY_INSPECT
from squid.records.application import RebuildSummary
from squid.records.domain import BuildKind
from squid_ui.testing import labels


def make_screen(*, inspect: bool = True, rebuild: bool = True) -> RecordsScreen:
    gap = SimpleNamespace(definition_id=1, title="Smallest door", build_ids=(7,), fields=("volume",))
    title = SimpleNamespace(definition_id=2, title="Unknown door", diagnostics=({"code": "unknown"},))
    records = SimpleNamespace(
        gaps=AsyncMock(return_value=(gap,)),
        title_gaps=AsyncMock(return_value=(title,)),
        materialize_definition=AsyncMock(return_value=RebuildSummary((), 1, 1, 0)),
        lookup_or_materialize=AsyncMock(return_value=RebuildSummary((), 1, 1, 0)),
    )
    computation = SimpleNamespace(rebuild=AsyncMock(return_value=RebuildSummary((), 4, 3, 1)))

    async def authorize(node: PermissionNode) -> bool:
        return inspect if node is RECORD_ENTRY_INSPECT else rebuild

    return RecordsScreen(
        cast(RecordOperations, records),
        cast(RecordComputationOperations, computation),
        can_inspect=inspect,
        can_rebuild=rebuild,
        authorize=authorize,
    )


async def test_records_use_tabs_and_browsers_for_diagnostics() -> None:
    screen = make_screen()
    await screen.on_load()

    assert screen._gaps is not None
    assert screen._titles is not None
    assert screen._tabs is not None
    assert {"Evidence gaps", "Title issues", "Lookup and rebuild"} <= set(labels(screen._tabs.render()))


async def test_lookup_materializes_and_refreshes_diagnostics() -> None:
    screen = make_screen()
    await screen.on_load()
    event = SimpleNamespace(
        values={"kind": BuildKind.DOOR, "base_key": "42", "restrictions": None, "version_id": 3},
        notice=AsyncMock(),
    )

    await screen._lookup(cast(sl.SubmitEvent, event))

    cast(Any, screen._records).materialize_definition.assert_awaited_once_with(
        42,
        kind=BuildKind.DOOR,
        version_id=3,
    )
    assert cast(Any, screen._records).gaps.await_count == 2
    event.notice.assert_awaited_once()


async def test_rebuild_uses_the_existing_default_kinds() -> None:
    screen = make_screen()
    await screen.on_load()
    event = SimpleNamespace(values={"kind": None, "version_id": None}, notice=AsyncMock())

    await screen._rebuild(cast(sl.SubmitEvent, event))

    cast(Any, screen._computation).rebuild.assert_awaited_once_with(
        current_version_id=None,
        kinds=(BuildKind.DOOR, BuildKind.EXTENDER),
    )


async def test_revoked_rebuild_permission_prevents_the_write() -> None:
    screen = make_screen(rebuild=False)
    await screen.on_load()
    event = SimpleNamespace(values={"kind": None, "version_id": None}, notice=AsyncMock())

    await screen._rebuild(cast(sl.SubmitEvent, event))

    cast(Any, screen._computation).rebuild.assert_not_awaited()
    event.notice.assert_awaited_once()


def test_records_are_one_app_only_workspace() -> None:
    cog = cast(Any, RecordCog)
    assert cog.__cog_commands__ == []
    assert [command.qualified_name for command in cog.__cog_app_commands__] == ["records"]
