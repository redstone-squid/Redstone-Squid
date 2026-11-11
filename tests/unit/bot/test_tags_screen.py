"""The consolidated tag catalogue and moderation workspace."""

from collections.abc import Sequence
from typing import Any, cast, override
from unittest.mock import AsyncMock

import pytest

import squid_ui as sl
from squid.bot.admin import Admin
from squid.bot.tags_view import TagsScreen
from squid.builds.application.restrictions import RestrictionService
from squid.core.actors import Actor
from squid.edits.application import EditRunner
from squid.edits.domain import (
    EditAttempt,
    EditCommand,
    EditOutcome,
    EditReceipt,
    EditResult,
    Revision,
    RevisionConflictError,
)
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    RESTRICTION_ALIAS_CREATE,
    TAG_PROPOSAL_APPROVE,
    TAG_PROPOSAL_LIST,
)
from squid.tags.application import TagService
from squid.tags.application.edits import ALIAS_EDIT, AliasValues, ShowcaseValues, tag_target
from squid.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.tags.models import TagDefinition
from squid_ui.runtime import transaction
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
        self.revision = 1

    @override
    async def build_revision(self, build_id: int) -> int:
        return self.revision

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
    async def approve(self, tag_id: int) -> TagDefinition:
        self.approvals.append(tag_id)
        return self.proposal

    @override
    async def reject(self, tag_id: int) -> TagDefinition:
        return self.proposal

    @override
    async def archive(self, tag_id: int) -> TagDefinition:
        return self.published


class TagEditRecorder(EditRunner):
    def __init__(self, tags: FakeTagService, restrictions: FakeRestrictionService) -> None:
        self.tags = tags
        self.restrictions = restrictions

    async def execute(self, command: EditCommand, actor: Actor, *, attempt: EditAttempt | None = None) -> EditResult:
        if command.kind == ALIAS_EDIT:
            values = AliasValues.model_validate_json(command.values.encoded)
            item = ("locational", values.alias)
            before = AliasValues(alias=values.alias, present=item in self.restrictions.aliases).edit_values()
            if values.present:
                self.restrictions.aliases.append(item)
            else:
                self.restrictions.aliases.remove(item)
            self.restrictions.revision += 1
            revision = self.restrictions.revision
        else:
            values = ShowcaseValues.model_validate_json(command.values.encoded)
            self.tags.assignments.append(
                (int(command.expected[0].target.key), values.tag_id, values.value, actor.require_account_id())
            )
            self.tags.revision += 1
            revision = self.tags.revision
            before = ShowcaseValues(tag_id=values.tag_id, assigned=False).edit_values()
        revisions = (Revision(command.expected[0].target, revision),)
        receipt = EditReceipt(command, before, command.values, revisions)
        if attempt is not None:
            attempt.receipt = receipt
        return EditResult(
            EditOutcome(command.operation_id, actor.key, command.fingerprint, revisions, changed=True, final=False),
            receipt,
        )

    async def reconcile(self, command: EditCommand, actor: Actor, attempt: EditAttempt) -> EditResult | None:
        raise NotImplementedError


class FakeRestrictionService(RestrictionService):
    def __init__(self) -> None:
        self.aliases: list[tuple[str, str]] = []
        self.revision = 1

    @override
    async def alias_revision(self, restriction: str) -> Revision:
        return Revision(tag_target(1), self.revision)


class NoticeEvent:
    def __init__(self, **values: object) -> None:
        self.values = values
        self.errors: tuple[sl.forms.FormIssue, ...] = ()
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
            TagEditRecorder(tags, restrictions),
            AsyncMock(return_value=Actor(account_id=account_id)),
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

    with transaction():
        await screen._apply(cast(sl.SubmitEvent, event))

    assert tags.assignments == [(42, 1, None, 7)]
    assert len(event.notices) == 1


async def test_open_assignment_form_keeps_its_displayed_build_revision() -> None:
    screen, tags, _ = make_screen()
    await screen.on_load()
    opening_revision = tags.revision
    with transaction():
        await screen._apply(cast(sl.SubmitEvent, NoticeEvent(tag_id=1, value=None)))
    with transaction(), pytest.raises(RevisionConflictError):
        await screen._apply(cast(sl.SubmitEvent, NoticeEvent(tag_id=1, value=None)), base_revision=opening_revision)
    assert len(tags.assignments) == 1


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


async def test_alias_history_rechecks_authority_and_reverses_membership() -> None:
    screen, _, restrictions = make_screen(capabilities=frozenset({RESTRICTION_ALIAS_CREATE}))
    await screen.on_load()
    with transaction():
        await screen._add_alias(cast(sl.SubmitEvent, NoticeEvent(restriction="locational", alias="location")))
    assert restrictions.aliases == [("locational", "location")]
    assert (await screen.history.undo()).applied
    assert restrictions.aliases == []
    assert (await screen.history.redo()).applied
    assert restrictions.aliases == [("locational", "location")]
