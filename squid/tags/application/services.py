"""Propose, moderate, and assign user showcase tags."""

import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Protocol
from uuid import uuid4

from squid.builds.application.ports import BuildEditingUnitOfWork
from squid.core.errors import ValidationError
from squid.core.i18n import tr
from squid.tags.application.history import AssignmentSnapshot
from squid.tags.domain import TagModerationStatus, TagValue, TagValueType
from squid.tags.errors import TagNotFoundError
from squid.tags.models import TagDefinition
from squid.unit_of_work import UnitOfWorkFactory

_QUERY_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class TagDefinitionRepository(Protocol):
    """Persistence required by the tag moderation workflow."""

    async def create_showcase(
        self,
        *,
        stable_key: str,
        display_name: str,
        normalized_name: str,
        value_type: TagValueType,
        query_name: str | None,
        created_by_account_id: int,
    ) -> TagDefinition: ...

    async def pending(self) -> Sequence[TagDefinition]: ...

    async def get(self, tag_id: int) -> TagDefinition | None: ...

    async def approved(self) -> Sequence[TagDefinition]: ...

    async def set_status(self, tag_id: int, status: TagModerationStatus) -> TagDefinition | None: ...

    async def build_revision(self, build_id: int) -> int: ...

    async def assignment(self, build_id: int, tag_id: int) -> AssignmentSnapshot | None: ...

    async def replace_assignment(
        self,
        build_id: int,
        tag_id: int,
        expected: AssignmentSnapshot | None,
        replacement: AssignmentSnapshot | None,
        *,
        actor_account_id: int,
    ) -> bool: ...

    async def assign_showcase(
        self,
        *,
        build_id: int,
        tag_id: int,
        value: TagValue,
        actor_account_id: int,
    ) -> bool: ...


class TagUnitOfWork(BuildEditingUnitOfWork, Protocol):
    @property
    def tags(self) -> TagDefinitionRepository: ...


class TagService:
    """Create user showcase tags and moderate their publication state.

    A proposal creates a pending definition. Staff approval makes it available
    publicly, after which a build's submitter can assign it to that build with
    a value of the declared type. Definitions and assignments are separate: the
    same approved definition can be used by many builds.

    Callers must authorize moderation requests. Status changes do not require a
    particular previous status, so an archived or rejected tag can be approved
    again. ShowcaseAssignmentEdit owns revisioned build assignments.
    """

    def __init__(self, *, uow_factory: UnitOfWorkFactory[TagUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def propose_showcase(
        self,
        display_name: str,
        *,
        value_type: TagValueType,
        query_name: str | None,
        created_by_account_id: int,
    ) -> TagDefinition:
        """Create a pending tag, normalizing whitespace and its optional search name."""
        async with self._uow_factory() as uow:
            normalized_name = " ".join(display_name.casefold().split())
            if not 1 <= len(normalized_name) <= 80:
                msg = tr(t"tags-tag-name-length = Tag names must contain between 1 and 80 characters.")
                raise ValidationError(msg)
            normalized_query = query_name.casefold().strip() if query_name is not None else None
            if normalized_query == "":
                normalized_query = None
            if normalized_query is not None and _QUERY_NAME.fullmatch(normalized_query) is None:
                msg = tr(
                    t"tags-names-start-letter = query names must "
                    t"start with a letter and contain only lowercase letters, digits, or underscores"
                )
                raise ValidationError(msg)
            result = await uow.tags.create_showcase(
                # The key is public, so it must not contain the proposer's account ID.
                stable_key=f"user_{uuid4().hex}",
                display_name=" ".join(display_name.split()),
                normalized_name=normalized_name,
                value_type=value_type,
                query_name=normalized_query,
                created_by_account_id=created_by_account_id,
            )
            await uow.commit()
            return result

    async def pending(self) -> Sequence[TagDefinition]:
        """List user definitions awaiting staff review."""
        async with self._uow_factory() as uow:
            return await uow.tags.pending()

    async def public_definitions(self) -> Sequence[TagDefinition]:
        """List tag definitions available to public search and build clients."""
        async with self._uow_factory() as uow:
            return await uow.tags.approved()

    async def build_revision(self, build_id: int) -> int:
        async with self._uow_factory() as uow:
            return await uow.tags.build_revision(build_id)

    async def public_definition(self, tag_id: int) -> TagDefinition | None:
        """Return an approved tag, or None if it is missing or has another status."""
        async with self._uow_factory() as uow:
            definition = await uow.tags.get(tag_id)
            if definition is None or definition.moderation_status is not TagModerationStatus.APPROVED:
                return None
            return definition

    async def approve(self, tag_id: int) -> TagDefinition:
        """Approve an existing tag for public use."""
        return await self._set_status(tag_id, TagModerationStatus.APPROVED)

    async def reject(self, tag_id: int) -> TagDefinition:
        """Reject a proposed tag without deleting its audit trail."""
        return await self._set_status(tag_id, TagModerationStatus.REJECTED)

    async def archive(self, tag_id: int) -> TagDefinition:
        """Hide a published tag while retaining assignments and history."""
        return await self._set_status(tag_id, TagModerationStatus.ARCHIVED)

    async def _set_status(self, tag_id: int, status: TagModerationStatus) -> TagDefinition:
        async with self._uow_factory() as uow:
            definition = await uow.tags.set_status(tag_id, status)
            if definition is None:
                raise TagNotFoundError(tag_id)
            await uow.commit()
            return definition


def coerce_assignment_value(definition: TagDefinition, raw_value: str | None) -> TagValue:
    """Parse a tag value without converting units.

    Numeric values must be finite decimals in the tag's canonical unit. Boolean
    values accept true/false, yes/no, or 1/0 without regard to letter case.
    """
    display_name = definition.display_name
    if definition.value_type is TagValueType.NONE:
        if raw_value not in {None, ""}:
            raise ValidationError(tr(t"tags-does-not-accept-value = {display_name} does not accept a value."))
        return None
    if raw_value is None or not raw_value.strip():
        value_type = definition.value_type.value
        raise ValidationError(tr(t"tags-requires-value = {display_name} requires a {value_type} value."))
    value = raw_value.strip()
    if definition.value_type is TagValueType.TEXT:
        return value
    if definition.value_type is TagValueType.BOOLEAN:
        normalized = value.casefold()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
        raise ValidationError(tr(t"tags-expects-true-false = {display_name} expects true or false."))
    try:
        numeric = Decimal(value)
    except InvalidOperation as error:
        raise ValidationError(
            tr(t"tags-expects-number-canonical = {display_name} expects a number in its canonical unit.")
        ) from error
    if not numeric.is_finite():
        raise ValidationError(tr(t"tags-expects-finite-number = {display_name} expects a finite number."))
    return numeric


def require_showcase(definition: TagDefinition | None) -> TagDefinition:
    if (
        definition is None
        or definition.authority.value != "user"
        or definition.semantic_kind.value != "showcase"
        or definition.moderation_status is not TagModerationStatus.APPROVED
    ):
        msg = tr(t"tags-approved-user-showcase = An approved user showcase tag is required.")
        raise ValidationError(msg)
    return definition
