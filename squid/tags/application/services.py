"""Propose, moderate, and assign user showcase tags."""

import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Protocol

from squid.builds.application.ports import BuildEditingUnitOfWork
from squid.core.errors import ValidationError
from squid.core.i18n import tr
from squid.tags.application.history import AssignmentSnapshot
from squid.tags.domain import TagModerationStatus, TagValue, TagValueType
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

    async def reserve_identity(self) -> int: ...

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
    """Read published definitions and pending proposals for tag workspaces."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory[TagUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def reserve_identity(self) -> int:
        async with self._uow_factory() as uow:
            return await uow.tags.reserve_identity()

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
            if (
                definition is None
                or definition.withdrawn
                or definition.moderation_status is not TagModerationStatus.APPROVED
            ):
                return None
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
        or definition.withdrawn
        or definition.authority.value != "user"
        or definition.semantic_kind.value != "showcase"
        or definition.moderation_status is not TagModerationStatus.APPROVED
    ):
        msg = tr(t"tags-approved-user-showcase = An approved user showcase tag is required.")
        raise ValidationError(msg)
    return definition


def normalize_proposal(display_name: str, query_name: str | None) -> tuple[str, str | None]:
    name = " ".join(display_name.split())
    if not 1 <= len(name.casefold()) <= 80:
        raise ValidationError(tr(t"tags-tag-name-length = Tag names must contain between 1 and 80 characters."))
    query = (query_name.casefold().strip() or None) if query_name is not None else None
    if query is not None and _QUERY_NAME.fullmatch(query) is None:
        raise ValidationError(
            tr(
                t"tags-names-start-letter = query names must "
                t"start with a letter and contain only lowercase letters, digits, or underscores"
            )
        )
    return name, query
