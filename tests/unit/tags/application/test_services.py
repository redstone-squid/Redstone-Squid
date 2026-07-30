"""Tag governance service tests."""

from dataclasses import replace

import pytest

from squid.tags.application import TagService
from squid.tags.domain import (
    TagAuthority,
    TagDefinition,
    TagModerationStatus,
    TagSemanticKind,
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
        created_by_discord_id: int,
    ) -> TagDefinition:
        assert stable_key.startswith(f"user_{created_by_discord_id}_")
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

    async def set_status(self, tag_id: int, status: TagModerationStatus) -> TagDefinition | None:
        if self.definition is None or tag_id != self.definition.id:
            return None
        self.definition = replace(self.definition, moderation_status=status)
        return self.definition


@pytest.mark.asyncio
async def test_user_can_propose_only_showcase_definition_for_review() -> None:
    repository = FakeRepository()
    service = TagService(repository)

    tag = await service.propose_showcase(
        "  Closing   Showcase ",
        value_type=TagValueType.NUMERIC,
        query_name="CLOSING_SHOWCASE",
        created_by_discord_id=42,
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
        created_by_discord_id=42,
    )

    approved = await service.approve(proposed.id)
    archived = await service.archive(proposed.id)

    assert approved.moderation_status is TagModerationStatus.APPROVED
    assert archived.moderation_status is TagModerationStatus.ARCHIVED


@pytest.mark.asyncio
async def test_invalid_public_query_name_is_rejected_before_persistence() -> None:
    with pytest.raises(ValueError, match="query names"):
        await TagService(FakeRepository()).propose_showcase(
            "Compact",
            value_type=TagValueType.NONE,
            query_name="not-valid!",
            created_by_discord_id=42,
        )
