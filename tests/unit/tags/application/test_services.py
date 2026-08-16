"""Tag governance service tests."""

import re
from dataclasses import replace
from decimal import Decimal

import pytest

from squid.tags.application import TagService
from squid.tags.domain import (
    TagAuthority,
    TagDefinition,
    TagModerationStatus,
    TagSemanticKind,
    TagValue,
    TagValueType,
)


class FakeRepository:
    def __init__(self) -> None:
        self.definition: TagDefinition | None = None

    async def create_showcase(
        self,
        *,
        stable_key: str,
        display_name: str,
        normalized_name: str,
        value_type: TagValueType,
        query_name: str | None,
        created_by_account_id: int,
    ) -> TagDefinition:
        # The key carries no proposer at all: it is published verbatim as `BuildTag.key`,
        # and the old `user_{discord_id}_{hex}` form leaked a snowflake into the API.
        assert re.fullmatch(r"user_[0-9a-f]{32}", stable_key), stable_key
        self.definition = TagDefinition(
            id=1,
            stable_key=stable_key,
            display_name=display_name,
            authority=TagAuthority.USER,
            semantic_kind=TagSemanticKind.SHOWCASE,
            value_type=value_type,
            moderation_status=TagModerationStatus.PENDING,
            query_name=query_name,
        )
        return self.definition

    async def pending(self) -> tuple[TagDefinition, ...]:
        return () if self.definition is None else (self.definition,)

    async def get(self, tag_id: int) -> TagDefinition | None:
        return self.definition if self.definition is not None and self.definition.id == tag_id else None

    async def approved(self) -> tuple[TagDefinition, ...]:
        if self.definition is None or self.definition.moderation_status is not TagModerationStatus.APPROVED:
            return ()
        return (self.definition,)

    async def set_status(self, tag_id: int, status: TagModerationStatus) -> TagDefinition | None:
        if self.definition is None or tag_id != self.definition.id:
            return None
        self.definition = replace(self.definition, moderation_status=status)
        return self.definition

    async def assign_showcase(
        self,
        *,
        build_id: int,
        tag_id: int,
        value: TagValue,
        actor_account_id: int,
    ) -> bool:
        assert (build_id, tag_id, value) == (10, 1, Decimal("0.4"))
        return actor_account_id == 42


@pytest.mark.asyncio
async def test_user_can_propose_only_showcase_definition_for_review() -> None:
    repository = FakeRepository()
    service = TagService(repository)

    tag = await service.propose_showcase(
        "  Closing   Showcase ",
        value_type=TagValueType.NUMERIC,
        query_name="CLOSING_SHOWCASE",
        created_by_account_id=42,
    )

    assert tag.display_name == "Closing Showcase"
    assert tag.query_name == "closing_showcase"
    assert tag.semantic_kind is TagSemanticKind.SHOWCASE
    assert tag.moderation_status is TagModerationStatus.PENDING


@pytest.mark.asyncio
async def test_staff_moderation_retains_definition_identity() -> None:
    repository = FakeRepository()
    service = TagService(repository)
    proposed = await service.propose_showcase(
        "Compact",
        value_type=TagValueType.NONE,
        query_name=None,
        created_by_account_id=42,
    )

    approved = await service.approve(proposed.id)
    archived = await service.archive(proposed.id)

    assert approved.moderation_status is TagModerationStatus.APPROVED
    assert archived.moderation_status is TagModerationStatus.ARCHIVED


@pytest.mark.asyncio
async def test_public_queries_only_return_approved_definitions() -> None:
    repository = FakeRepository()
    service = TagService(repository)
    proposed = await service.propose_showcase(
        "Compact",
        value_type=TagValueType.NONE,
        query_name=None,
        created_by_account_id=42,
    )

    assert await service.public_definitions() == ()
    assert await service.public_definition(proposed.id) is None

    approved = await service.approve(proposed.id)
    assert await service.public_definitions() == (approved,)
    assert await service.public_definition(proposed.id) == approved


@pytest.mark.asyncio
async def test_invalid_public_query_name_is_rejected_before_persistence() -> None:
    with pytest.raises(ValueError, match="query names"):
        await TagService(FakeRepository()).propose_showcase(
            "Compact",
            value_type=TagValueType.NONE,
            query_name="not-valid!",
            created_by_account_id=42,
        )


@pytest.mark.asyncio
async def test_submitter_can_attach_approved_typed_showcase_tag() -> None:
    repository = FakeRepository()
    service = TagService(repository)
    proposed = await service.propose_showcase(
        "Closing showcase",
        value_type=TagValueType.NUMERIC,
        query_name="closing_showcase",
        created_by_account_id=42,
    )
    await service.approve(proposed.id)

    assigned = await service.assign_showcase(10, proposed.id, "0.4", actor_account_id=42)

    assert assigned.id == proposed.id
