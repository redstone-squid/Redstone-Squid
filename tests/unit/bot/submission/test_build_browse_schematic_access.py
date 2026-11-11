"""Check which actors the Discord build browser lets use a build's featured schematic."""

from dataclasses import dataclass, field, replace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from whenever import Instant

import squid_ui as sl
from squid.bot.submission.schematic_tools import SchematicTools
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import BUILD_SCHEMATIC_DOWNLOAD_PRIVATE, BUILD_SCHEMATIC_VIEW_PRIVATE
from squid.schematics.application import SchematicPublication, SchematicService, StoredSchematic
from squid.schematics.domain import SchematicLicense, SchematicVisibility
from squid_ui.testing import press_event
from tests.unit.schematics.fakes import make_analysis

UPLOADER_ID = 11
OTHER_ID = 12


def _stored(publication: SchematicPublication) -> StoredSchematic:
    return StoredSchematic(
        id=3,
        build_id=7,
        file_sha256="a" * 64,
        is_primary=True,
        original_filename=None,
        analysis=make_analysis(),
        publication=publication,
        uploaded_by_account_id=UPLOADER_ID,
    )


PRIVATE = _stored(SchematicPublication(visibility=SchematicVisibility.REVIEWER_ONLY))
PUBLIC = _stored(
    SchematicPublication(
        visibility=SchematicVisibility.PUBLIC_DOWNLOAD,
        license=SchematicLicense.CC0_1_0,
        rights_attested_at=Instant.from_utc(2026, 1, 1),
        rights_attested_by_account_id=UPLOADER_ID,
        published_at=Instant.from_utc(2026, 1, 1),
    )
)
WITHDRAWN = replace(PUBLIC, publication=replace(PUBLIC.publication, withdrawn_at=Instant.from_utc(2026, 2, 1)))


@dataclass
class _Event:
    notices: list[object] = field(default_factory=list)

    async def notice(self, text: object) -> None:
        self.notices.append(text)


class FeaturedSchematics(SchematicService):
    def __init__(self, stored: StoredSchematic | None) -> None:
        self.stored = stored

    @property
    def available(self) -> bool:
        return True

    async def featured_for_build(self, build_id: int) -> StoredSchematic | None:
        assert build_id == 7
        return self.stored


def _detail(stored: StoredSchematic | None, *, actor: int | None, held: set[str]) -> SchematicTools:
    async def authorize(node: PermissionNode) -> bool:
        return node.name in held

    return SchematicTools(
        7,
        schematics=FeaturedSchematics(stored),
        measure_timing=False,
        actor_account_id=actor,
        authorize=authorize,
    )


@pytest.mark.parametrize("node", [BUILD_SCHEMATIC_DOWNLOAD_PRIVATE, BUILD_SCHEMATIC_VIEW_PRIVATE])
async def test_public_schematics_are_open_to_everyone(node: PermissionNode) -> None:
    event = _Event()

    assert await _detail(PUBLIC, actor=None, held=set())._primary(cast(Any, event), node) is PUBLIC  # pyright: ignore[reportPrivateUsage]
    assert event.notices == []


@pytest.mark.parametrize("stored", [PRIVATE, WITHDRAWN])
async def test_non_public_schematics_are_refused_without_a_grant(stored: StoredSchematic) -> None:
    event = _Event()

    result = await _detail(stored, actor=OTHER_ID, held=set())._primary(  # pyright: ignore[reportPrivateUsage]
        cast(Any, event), BUILD_SCHEMATIC_DOWNLOAD_PRIVATE
    )

    assert result is None
    assert len(event.notices) == 1


async def test_the_uploader_may_use_their_own_non_public_schematic() -> None:
    result = await _detail(PRIVATE, actor=UPLOADER_ID, held=set())._primary(  # pyright: ignore[reportPrivateUsage]
        cast(Any, _Event()), BUILD_SCHEMATIC_DOWNLOAD_PRIVATE
    )

    assert result is PRIVATE


async def test_each_private_access_node_covers_only_its_own_actions() -> None:
    detail = _detail(PRIVATE, actor=OTHER_ID, held={BUILD_SCHEMATIC_VIEW_PRIVATE.name})

    viewed = await detail._primary(cast(Any, _Event()), BUILD_SCHEMATIC_VIEW_PRIVATE)  # pyright: ignore[reportPrivateUsage]
    downloaded = await detail._primary(cast(Any, _Event()), BUILD_SCHEMATIC_DOWNLOAD_PRIVATE)  # pyright: ignore[reportPrivateUsage]

    assert viewed is PRIVATE
    assert downloaded is None


async def test_an_anonymous_actor_never_matches_a_legacy_row_without_an_uploader() -> None:
    legacy = replace(PRIVATE, uploaded_by_account_id=None)

    result = await _detail(legacy, actor=None, held=set())._primary(  # pyright: ignore[reportPrivateUsage]
        cast(Any, _Event()), BUILD_SCHEMATIC_VIEW_PRIVATE
    )

    assert result is None


async def test_schematic_actions_publish_their_results_inside_a_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    detail = _detail(PUBLIC, actor=None, held=set())
    monkeypatch.setattr(detail._schematics, "explain_render_skip", AsyncMock(return_value=None))
    monkeypatch.setattr(detail._schematics, "convert", AsyncMock(return_value=(b"schematic", ())))

    with sl.runtime.transaction():
        await detail._schematic_info(press_event())
        await detail._schematic_download(press_event())

    assert detail._schematic_result is not None
    assert detail._asset is not None
    assert detail._asset.name == "build-7.litematic"
    assert detail._asset.source == sl.document.InlineAsset(b"schematic")


async def test_a_failed_schematic_action_discards_its_results(monkeypatch: pytest.MonkeyPatch) -> None:
    detail = _detail(PUBLIC, actor=None, held=set())
    monkeypatch.setattr(detail._schematics, "explain_render_skip", AsyncMock(return_value=None))
    monkeypatch.setattr(detail._schematics, "convert", AsyncMock(return_value=(b"schematic", ())))

    async def download_then_fail() -> None:
        await detail._schematic_info(press_event())
        await detail._schematic_download(press_event())
        raise RuntimeError("later failure")

    with pytest.raises(RuntimeError, match="later failure"), sl.runtime.transaction():
        await download_then_fail()

    assert detail._schematic_result is None
    assert detail._asset is None
