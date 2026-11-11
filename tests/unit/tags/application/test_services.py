"""Tag governance service tests."""

import re
from dataclasses import replace
from decimal import Decimal
from functools import partial

import pytest
from whenever import Instant

from squid.tags.application import TagService
from squid.tags.application.history import AssignmentSnapshot
from squid.tags.application.services import coerce_assignment_value, require_showcase
from squid.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValue, TagValueType
from squid.tags.models import TagDefinition
from tests.support.unit_of_work import CommittedState, FakeUnitOfWork


class FakeRepository:
    def __init__(self) -> None:
        self.definition: TagDefinition | None = None
        self.snapshot: AssignmentSnapshot | None = None

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
        # BuildTag.key is public. Its format must not expose the proposer's ID.
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

    async def build_revision(self, build_id: int) -> int:
        return 1

    async def assignment(self, build_id: int, tag_id: int) -> AssignmentSnapshot | None:
        return self.snapshot

    async def replace_assignment(
        self,
        build_id: int,
        tag_id: int,
        expected: AssignmentSnapshot | None,
        replacement: AssignmentSnapshot | None,
        *,
        actor_account_id: int,
    ) -> bool:
        raise NotImplementedError("Assignment history is not exercised by these governance tests.")

    async def assign_showcase(
        self,
        *,
        build_id: int,
        tag_id: int,
        value: TagValue,
        actor_account_id: int,
    ) -> bool:
        assert (build_id, tag_id, value) == (10, 1, Decimal("0.4"))
        if actor_account_id != 42:
            return False
        self.snapshot = AssignmentSnapshot(
            TagValueType.NUMERIC, value, None, None, None, "user", actor_account_id, Instant.now()
        )
        return True


class FakeTagUnitOfWork(FakeUnitOfWork[FakeRepository]):
    @property
    def tags(self) -> FakeRepository:
        return self.state


def _copy_repository(repository: FakeRepository) -> FakeRepository:
    copied = FakeRepository()
    copied.snapshot = repository.snapshot
    copied.definition = None if repository.definition is None else replace(repository.definition)
    return copied


def _service() -> TagService:
    committed = CommittedState(FakeRepository(), clone=_copy_repository)
    return TagService(uow_factory=partial(FakeTagUnitOfWork, committed))


@pytest.mark.asyncio
async def test_user_can_propose_only_showcase_definition_for_review() -> None:
    service = _service()

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
    service = _service()
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
    service = _service()
    proposed = await service.propose_showcase(
        "Compact",
        value_type=TagValueType.NONE,
        query_name=None,
        created_by_account_id=42,
    )

    assert await service.public_definitions() == ()
    assert await service.public_definition(proposed.id) is None

    approved = await service.approve(proposed.id)
    assert [tag.id for tag in await service.public_definitions()] == [approved.id]
    public = await service.public_definition(proposed.id)
    assert public is not None
    assert public.id == approved.id


@pytest.mark.asyncio
async def test_invalid_public_query_name_is_rejected_before_persistence() -> None:
    with pytest.raises(ValueError, match="query names"):
        await _service().propose_showcase(
            "Compact",
            value_type=TagValueType.NONE,
            query_name="not-valid!",
            created_by_account_id=42,
        )


@pytest.mark.asyncio
async def test_approved_showcase_values_are_coerced_to_the_declared_type() -> None:
    service = _service()
    proposed = await service.propose_showcase(
        "Closing showcase",
        value_type=TagValueType.NUMERIC,
        query_name="closing_showcase",
        created_by_account_id=42,
    )
    approved = await service.approve(proposed.id)
    assert coerce_assignment_value(require_showcase(approved), "0.4") == Decimal("0.4")
