"""The consolidated tag catalogue and moderation workspace."""

from collections.abc import Sequence
from typing import Any, cast, override

import squid_ui as sl
from squid.bot.admin import Admin
from squid.bot.tags_view import TagsScreen
from squid.builds.application.restrictions import RestrictionService
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    RESTRICTION_ALIAS_CREATE,
    TAG_PROPOSAL_APPROVE,
    TAG_PROPOSAL_LIST,
)
from squid.tags.application import TagService
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


class FakeTagService(TagService):
    def __init__(self) -> None:
        self.published = tag(1, "Compact")
        self.proposal = tag(2, "Reliable", pending=True)
        self.public_reads = 0
        self.proposals: list[tuple[str, TagValueType, str | None, int]] = []
        self.assignments: list[tuple[int, int, str | None, int]] = []
        self.approvals: list[int] = []

    @override
    async def public_definitions(self) -> Sequence[TagDefinition]:
        self.public_reads += 1
        return (self.published,)

    @override
    async def pending(self) -> Sequence[TagDefinition]:
        return (self.proposal,)

    @override
    async def propose_showcase(
        self,
        display_name: str,
        *,
        value_type: TagValueType,
        query_name: str | None,
        created_by_account_id: int,
    ) -> TagDefinition:
        self.proposals.append((display_name, value_type, query_name, created_by_account_id))
        return self.proposal

    @override
    async def assign_showcase(
        self, build_id: int, tag_id: int, raw_value: str | None, *, actor_account_id: int
    ) -> TagDefinition:
        self.assignments.append((build_id, tag_id, raw_value, actor_account_id))
        return self.published

    @override
    async def approve(self, tag_id: int) -> TagDefinition:
        self.approvals.append(tag_id)
        return self.proposal

    @override
    async def reject(self, tag_id: int) -> TagDefinition:
        return self.proposal

    @override
    async def archive(self, tag_id: int) -> TagDefinition:
        return self.published


class FakeRestrictionService(RestrictionService):
    def __init__(self) -> None:
        self.aliases: list[tuple[str, str]] = []

    @override
    async def add_alias(self, restriction: str, alias: str) -> None:
        self.aliases.append((restriction, alias))


class NoticeEvent:
    def __init__(self, **values: object) -> None:
        self.values = values
        self.notices: list[object] = []

    async def notice(self, text: object, **_kwargs: object) -> None:
        self.notices.append(text)


class TransitionEvent:
    def __init__(self, source: NoticeEvent) -> None:
        self.source = source


def make_screen(
    *,
    build_id: int | None = 42,
    account_id: int | None = 7,
    capabilities: frozenset[PermissionNode] = frozenset(),
    allowed: bool = True,
) -> tuple[TagsScreen, FakeTagService, FakeRestrictionService]:
    tags = FakeTagService()
    restrictions = FakeRestrictionService()

    async def authorize(_node: PermissionNode) -> bool:
        return allowed

    return (
        TagsScreen(
            tags,
            restrictions,
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
    assert {"Catalogue", "Propose and apply", "Moderation", "Restriction aliases"} <= set(labels(screen._tabs.render()))


async def test_seeded_build_application_uses_the_opening_build() -> None:
    screen, tags, _ = make_screen(build_id=42)
    await screen.on_load()
    event = NoticeEvent(tag_id=1, value=None)

    await screen._apply(cast(sl.SubmitEvent, event))

    assert tags.assignments == [(42, 1, None, 7)]
    assert len(event.notices) == 1


async def test_proposal_refreshes_the_service_backed_browsers() -> None:
    screen, tags, _ = make_screen()
    await screen.on_load()
    event = NoticeEvent(name="Reliable", value_type=TagValueType.NONE, query_name=None)

    await screen._propose(cast(sl.SubmitEvent, event))

    assert tags.proposals == [("Reliable", TagValueType.NONE, None, 7)]
    assert tags.public_reads == 2
    assert len(event.notices) == 1


async def test_revoked_alias_permission_prevents_mutation() -> None:
    screen, _, restrictions = make_screen(
        capabilities=frozenset({RESTRICTION_ALIAS_CREATE}),
        allowed=False,
    )
    event = NoticeEvent(restriction="locational", alias="location")

    await screen._add_alias(cast(sl.SubmitEvent, event))

    assert restrictions.aliases == []
    assert len(event.notices) == 1


async def test_moderation_requires_a_decision_and_rechecks_permission() -> None:
    screen, tags, _ = make_screen(
        capabilities=frozenset({TAG_PROPOSAL_LIST, TAG_PROPOSAL_APPROVE}),
    )
    await screen.on_load()
    proposal = tag(2, "Reliable", pending=True)
    press = object()

    await screen._request_moderation(cast(sl.PressEvent, press), proposal, "approve")

    assert screen._pending_action == (proposal, "approve")
    assert screen._decision is not None
    source = NoticeEvent()
    transition = TransitionEvent(source)
    await screen._decide(cast(Any, transition), "confirm")

    assert tags.approvals == [2]
    assert len(source.notices) == 1
    assert screen._pending_action is None


def test_tags_are_one_app_only_workspace() -> None:
    cog = cast(Any, Admin)
    assert all(command.name != "tag" for command in cog.__cog_commands__)
    assert "tags" in {command.name for command in cog.__cog_app_commands__}
