"""The consolidated tag catalogue and moderation workspace."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import squid_ui as sl
from squid.bot.admin import Admin
from squid.bot.tags_view import RestrictionOperations, TagOperations, TagsScreen
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    RESTRICTION_ALIAS_CREATE,
    TAG_PROPOSAL_APPROVE,
    TAG_PROPOSAL_LIST,
)
from squid.tags.domain import (
    TagAuthority,
    TagDefinition,
    TagModerationStatus,
    TagSemanticKind,
    TagValueType,
)
from squid_ui.testing import labels


def tag(tag_id: int, name: str, *, pending: bool = False) -> TagDefinition:
    return TagDefinition(
        id=tag_id,
        stable_key=f"tag-{tag_id}",
        display_name=name,
        authority=TagAuthority.USER,
        semantic_kind=TagSemanticKind.SHOWCASE,
        value_type=TagValueType.NONE,
        moderation_status=TagModerationStatus.PENDING if pending else TagModerationStatus.APPROVED,
    )


def make_screen(
    *,
    build_id: int | None = 42,
    account_id: int | None = 7,
    capabilities: frozenset[PermissionNode] = frozenset(),
    allowed: bool = True,
) -> tuple[TagsScreen, Any, Any]:
    published = tag(1, "Compact")
    proposal = tag(2, "Reliable", pending=True)
    tags = SimpleNamespace(
        public_definitions=AsyncMock(return_value=(published,)),
        pending=AsyncMock(return_value=(proposal,)),
        propose_showcase=AsyncMock(return_value=proposal),
        assign_showcase=AsyncMock(return_value=published),
        approve=AsyncMock(return_value=proposal),
        reject=AsyncMock(return_value=proposal),
        archive=AsyncMock(return_value=published),
    )
    restrictions = SimpleNamespace(add_alias=AsyncMock())

    async def authorize(_node: PermissionNode) -> bool:
        return allowed

    return (
        TagsScreen(
            cast(TagOperations, tags),
            cast(RestrictionOperations, restrictions),
            build_id=build_id,
            actor_account_id=account_id,
            capabilities=capabilities,
            authorize=authorize,
        ),
        tags,
        restrictions,
    )


async def test_tags_use_browsers_and_capability_aware_tabs() -> None:
    screen, _, _ = make_screen(
        capabilities=frozenset({TAG_PROPOSAL_LIST, TAG_PROPOSAL_APPROVE, RESTRICTION_ALIAS_CREATE})
    )

    await screen.on_load()

    assert screen._catalogue is not None
    assert screen._pending is not None
    assert screen._tabs is not None
    assert {"Catalogue", "Propose and apply", "Moderation", "Restriction aliases"} <= set(
        labels(screen._tabs.render())
    )


async def test_seeded_build_application_uses_the_opening_build() -> None:
    screen, tags, _ = make_screen(build_id=42)
    await screen.on_load()
    event = SimpleNamespace(values={"tag_id": 1, "value": None}, notice=AsyncMock())

    await screen._apply(cast(sl.SubmitEvent, event))

    tags.assign_showcase.assert_awaited_once_with(42, 1, None, actor_account_id=7)
    event.notice.assert_awaited_once()


async def test_proposal_refreshes_the_service_backed_browsers() -> None:
    screen, tags, _ = make_screen()
    await screen.on_load()
    event = SimpleNamespace(
        values={"name": "Reliable", "value_type": TagValueType.NONE, "query_name": None},
        notice=AsyncMock(),
    )

    await screen._propose(cast(sl.SubmitEvent, event))

    tags.propose_showcase.assert_awaited_once_with(
        "Reliable",
        value_type=TagValueType.NONE,
        query_name=None,
        created_by_account_id=7,
    )
    assert tags.public_definitions.await_count == 2
    event.notice.assert_awaited_once()


async def test_revoked_alias_permission_prevents_mutation() -> None:
    screen, _, restrictions = make_screen(
        capabilities=frozenset({RESTRICTION_ALIAS_CREATE}),
        allowed=False,
    )
    event = SimpleNamespace(
        values={"restriction": "locational", "alias": "location"},
        notice=AsyncMock(),
    )

    await screen._add_alias(cast(sl.SubmitEvent, event))

    restrictions.add_alias.assert_not_awaited()
    event.notice.assert_awaited_once()


async def test_moderation_requires_a_decision_and_rechecks_permission() -> None:
    screen, tags, _ = make_screen(
        capabilities=frozenset({TAG_PROPOSAL_LIST, TAG_PROPOSAL_APPROVE}),
    )
    await screen.on_load()
    proposal = tag(2, "Reliable", pending=True)
    press = SimpleNamespace()

    await screen._request_moderation(cast(sl.PressEvent, press), proposal, "approve")

    assert screen._pending_action == (proposal, "approve")
    assert screen._decision is not None
    source = SimpleNamespace(notice=AsyncMock())
    transition = SimpleNamespace(source=source)
    await screen._decide(cast(Any, transition), "confirm")

    tags.approve.assert_awaited_once_with(2)
    source.notice.assert_awaited_once()
    assert screen._pending_action is None


def test_tags_are_one_app_only_workspace() -> None:
    cog = cast(Any, Admin)
    assert all(command.name != "tag" for command in cog.__cog_commands__)
    assert "tags" in {command.name for command in cog.__cog_app_commands__}
