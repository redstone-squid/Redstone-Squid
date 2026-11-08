"""Check which actors the Discord build browser lets use a build's featured schematic."""

from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import Any, cast

import pytest
from whenever import Instant

from squid.bot.submission.build_browse import _BuildDetail  # pyright: ignore[reportPrivateUsage]
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import BUILD_SCHEMATIC_DOWNLOAD_PRIVATE, BUILD_SCHEMATIC_VIEW_PRIVATE
from squid.schematics.application import SchematicPublication, StoredSchematic
from squid.schematics.domain import SchematicLicense, SchematicVisibility
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


def _detail(stored: StoredSchematic | None, *, actor: int | None, held: set[str]) -> _BuildDetail:
    async def featured_for_build(build_id: int) -> StoredSchematic | None:
        return stored

    async def authorize(node: PermissionNode) -> bool:
        return node.name in held

    schematics = SimpleNamespace(featured_for_build=featured_for_build, available=True)
    return _BuildDetail(
        cast(Any, SimpleNamespace(id=7)),
        queries=cast(Any, None),
        builds=cast(Any, None),
        schematics=cast(Any, schematics),
        render_build=cast(Any, None),
        capabilities=cast(Any, None),
        actor_account_id=actor,
        authorize=authorize,
        refresh_posts=cast(Any, None),
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
