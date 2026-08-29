"""Application services for user-defined and official tag governance."""

import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Protocol
from uuid import uuid4

from squid.core.errors import ValidationError
from squid.core.i18n import _
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
    ) -> TagDefinition: ...

    async def pending(self) -> Sequence[TagDefinition]: ...

    async def get(self, tag_id: int) -> TagDefinition | None: ...

    async def approved(self) -> Sequence[TagDefinition]: ...

    async def set_status(self, tag_id: int, status: TagModerationStatus) -> TagDefinition | None: ...

    async def assign_showcase(
        self,
        *,
        build_id: int,
        tag_id: int,
        value: TagValue,
        actor_account_id: int,
    ) -> bool: ...


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
        normalized_name = " ".join(display_name.casefold().split())
        if not 1 <= len(normalized_name) <= 80:
            msg = _("Tag names must contain between 1 and 80 characters.")
            raise ValidationError(msg)
        normalized_query = query_name.casefold().strip() if query_name is not None else None
        if normalized_query == "":
            normalized_query = None
        if normalized_query is not None and _QUERY_NAME.fullmatch(normalized_query) is None:
            msg = _("query names must start with a letter and contain only lowercase letters, digits, or underscores")
            raise ValidationError(msg)
        return await self._repository.create_showcase(
            # No submitter identity in the key. It is never parsed -- the only literal
            # comparison anywhere is against an official key -- so publishing a proposer
            # in `BuildTag.key` bought nothing and leaked who proposed a tag.
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
        """Return an approved public tag definition by identifier."""
        definition = await self._repository.get(tag_id)
        if definition is None or definition.moderation_status is not TagModerationStatus.APPROVED:
            return None
        return definition

    async def approve(self, tag_id: int) -> TagDefinition:
        """Publish a pending tag."""
        return await self._set_status(tag_id, TagModerationStatus.APPROVED)

    async def reject(self, tag_id: int) -> TagDefinition:
        """Reject a proposed tag without deleting its audit trail."""
        return await self._set_status(tag_id, TagModerationStatus.REJECTED)

    async def archive(self, tag_id: int) -> TagDefinition:
        """Hide a published tag while retaining assignments and history."""
        return await self._set_status(tag_id, TagModerationStatus.ARCHIVED)

    async def assign_showcase(
        self,
        build_id: int,
        tag_id: int,
        raw_value: str | None,
        *,
        actor_account_id: int,
    ) -> TagDefinition:
        """Attach an approved showcase tag to a build submitted by the caller."""
        definition = await self._repository.get(tag_id)
        if (
            definition is None
            or definition.authority.value != "user"
            or definition.semantic_kind.value != "showcase"
            or definition.moderation_status is not TagModerationStatus.APPROVED
        ):
            msg = _("An approved user showcase tag is required.")
            raise ValidationError(msg)
        value = _coerce_assignment_value(definition, raw_value)
        assigned = await self._repository.assign_showcase(
            build_id=build_id,
            tag_id=tag_id,
            value=value,
            actor_account_id=actor_account_id,
        )
        if not assigned:
            msg = _("The build does not exist or was not submitted by you.")
            raise ValidationError(msg)
        return definition

    async def _set_status(self, tag_id: int, status: TagModerationStatus) -> TagDefinition:
        definition = await self._repository.set_status(tag_id, status)
        if definition is None:
            raise TagNotFoundError(tag_id)
        return definition


def _coerce_assignment_value(definition: TagDefinition, raw_value: str | None) -> TagValue:
    if definition.value_type is TagValueType.NONE:
        if raw_value not in {None, ""}:
            msg = _("{display_name} does not accept a value.")
            raise ValidationError(msg, message_params={"display_name": definition.display_name})
        return None
    if raw_value is None or not raw_value.strip():
        msg = _("{display_name} requires a {value_type} value.")
        raise ValidationError(
            msg,
            message_params={"display_name": definition.display_name, "value_type": definition.value_type.value},
        )
    value = raw_value.strip()
    if definition.value_type is TagValueType.TEXT:
        return value
    if definition.value_type is TagValueType.BOOLEAN:
        normalized = value.casefold()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
        msg = _("{display_name} expects true or false.")
        raise ValidationError(msg, message_params={"display_name": definition.display_name})
    try:
        numeric = Decimal(value)
    except InvalidOperation as error:
        msg = _("{display_name} expects a number in its canonical unit.")
        raise ValidationError(msg, message_params={"display_name": definition.display_name}) from error
    if not numeric.is_finite():
        msg = _("{display_name} expects a finite number.")
        raise ValidationError(msg, message_params={"display_name": definition.display_name})
    return numeric
