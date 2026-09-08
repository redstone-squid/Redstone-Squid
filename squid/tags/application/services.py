"""Application services for user-defined and official tag governance."""

import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Protocol
from uuid import uuid4

from squid.core.errors import ValidationError
from squid.core.i18n import tr
from squid.tags.domain import TagDefinition, TagModerationStatus, TagValue, TagValueType
from squid.tags.errors import TagNotFoundError

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
    ) -> TagDefinition:
        """Insert a user showcase tag in the pending state, returning it with its assigned id.

        The caller normalizes the names; this only stores them. A duplicate `stable_key` or `query_name` violates a
        unique constraint.
        """
        ...

    async def pending(self) -> Sequence[TagDefinition]:
        """Definitions awaiting review, oldest first."""
        ...

    async def get(self, tag_id: int) -> TagDefinition | None:
        """Any definition by id whatever its moderation status, or `None`."""
        ...

    async def approved(self) -> Sequence[TagDefinition]:
        """Published definitions, in display order."""
        ...

    async def set_status(self, tag_id: int, status: TagModerationStatus) -> TagDefinition | None:
        """Move a definition to `status` and reproject it for search, or return `None` if no such tag exists."""
        ...

    async def assign_showcase(
        self,
        *,
        build_id: int,
        tag_id: int,
        value: TagValue,
        actor_account_id: int,
    ) -> bool:
        """Attach the tag to a build owned by the actor, replacing any existing value.

        Returns `False` when the build does not exist, is owned by somebody else, or the tag is gone. Raises
        `ValueError` if `value` does not match the definition's value type.
        """
        ...


class TagService:
    """Create user showcase tags and moderate their publication state."""

    def __init__(self, repository: TagDefinitionRepository) -> None:
        self._repository = repository

    async def propose_showcase(
        self,
        display_name: str,
        *,
        value_type: TagValueType,
        query_name: str | None,
        created_by_account_id: int,
    ) -> TagDefinition:
        """Create a pending user showcase tag.

        Raises `ValidationError` if the name is not 1-80 characters, or if `query_name` is not a lowercase
        identifier of at most 64 characters.
        """
        normalized_name = " ".join(display_name.casefold().split())
        if not 1 <= len(normalized_name) <= 80:
            msg = tr(t"Tag names must contain between 1 and 80 characters.")
            raise ValidationError(msg)
        normalized_query = query_name.casefold().strip() if query_name is not None else None
        if normalized_query == "":
            normalized_query = None
        if normalized_query is not None and _QUERY_NAME.fullmatch(normalized_query) is None:
            msg = tr(t"query names must start with a letter and contain only lowercase letters, digits, or underscores")
            raise ValidationError(msg)
        return await self._repository.create_showcase(
            # No submitter identity in the key: it is never parsed -- the only literal
            # comparison anywhere is against an official key -- and embedding one would
            # publish who proposed a tag through `BuildTag.key`.
            stable_key=f"user_{uuid4().hex}",
            display_name=" ".join(display_name.split()),
            normalized_name=normalized_name,
            value_type=value_type,
            query_name=normalized_query,
            created_by_account_id=created_by_account_id,
        )

    async def pending(self) -> Sequence[TagDefinition]:
        """List user definitions awaiting staff review."""
        return await self._repository.pending()

    async def public_definitions(self) -> Sequence[TagDefinition]:
        """List tag definitions available to public search and build clients."""
        return await self._repository.approved()

    async def public_definition(self, tag_id: int) -> TagDefinition | None:
        """A definition by id, or `None` if it does not exist or is not approved."""
        definition = await self._repository.get(tag_id)
        if definition is None or definition.moderation_status is not TagModerationStatus.APPROVED:
            return None
        return definition

    async def approve(self, tag_id: int) -> TagDefinition:
        """Publish a pending tag; raises `TagNotFoundError` if no tag has that id."""
        return await self._set_status(tag_id, TagModerationStatus.APPROVED)

    async def reject(self, tag_id: int) -> TagDefinition:
        """Reject a proposed tag, keeping the row; raises `TagNotFoundError` if no tag has that id."""
        return await self._set_status(tag_id, TagModerationStatus.REJECTED)

    async def archive(self, tag_id: int) -> TagDefinition:
        """Hide a published tag while keeping its assignments; raises `TagNotFoundError` if no tag has that id."""
        return await self._set_status(tag_id, TagModerationStatus.ARCHIVED)

    async def assign_showcase(
        self,
        build_id: int,
        tag_id: int,
        raw_value: str | None,
        *,
        actor_account_id: int,
    ) -> TagDefinition:
        """Attach an approved showcase tag to a build the caller submitted, returning the tag's definition.

        Raises `ValidationError` if the tag is not an approved user showcase tag, if `raw_value` does not parse as
        the tag's value type, or if the caller does not own the build.
        """
        definition = await self._repository.get(tag_id)
        if (
            definition is None
            or definition.authority.value != "user"
            or definition.semantic_kind.value != "showcase"
            or definition.moderation_status is not TagModerationStatus.APPROVED
        ):
            msg = tr(t"An approved user showcase tag is required.")
            raise ValidationError(msg)
        value = _coerce_assignment_value(definition, raw_value)
        assigned = await self._repository.assign_showcase(
            build_id=build_id,
            tag_id=tag_id,
            value=value,
            actor_account_id=actor_account_id,
        )
        if not assigned:
            msg = tr(t"The build does not exist or was not submitted by you.")
            raise ValidationError(msg)
        return definition

    async def _set_status(self, tag_id: int, status: TagModerationStatus) -> TagDefinition:
        definition = await self._repository.set_status(tag_id, status)
        if definition is None:
            raise TagNotFoundError(tag_id)
        return definition


def _coerce_assignment_value(definition: TagDefinition, raw_value: str | None) -> TagValue:
    display_name = definition.display_name
    if definition.value_type is TagValueType.NONE:
        if raw_value not in {None, ""}:
            raise ValidationError(tr(t"{display_name} does not accept a value."))
        return None
    if raw_value is None or not raw_value.strip():
        value_type = definition.value_type.value
        raise ValidationError(tr(t"{display_name} requires a {value_type} value."))
    value = raw_value.strip()
    if definition.value_type is TagValueType.TEXT:
        return value
    if definition.value_type is TagValueType.BOOLEAN:
        normalized = value.casefold()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
        raise ValidationError(tr(t"{display_name} expects true or false."))
    try:
        numeric = Decimal(value)
    except InvalidOperation as error:
        raise ValidationError(tr(t"{display_name} expects a number in its canonical unit.")) from error
    if not numeric.is_finite():
        raise ValidationError(tr(t"{display_name} expects a finite number."))
    return numeric
