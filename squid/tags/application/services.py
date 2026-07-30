"""Application services for user-defined and official tag governance."""

import re
from collections.abc import Sequence
from typing import Protocol
from uuid import uuid4

from squid.tags.domain import TagDefinition, TagModerationStatus, TagValueType

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
        created_by_discord_id: int,
    ) -> TagDefinition: ...

    async def pending(self) -> Sequence[TagDefinition]: ...

    async def set_status(self, tag_id: int, status: TagModerationStatus) -> TagDefinition | None: ...


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
        created_by_discord_id: int,
    ) -> TagDefinition:
        normalized_name = " ".join(display_name.casefold().split())
        if not 1 <= len(normalized_name) <= 80:
            msg = "tag names must contain between 1 and 80 characters"
            raise ValueError(msg)
        normalized_query = query_name.casefold().strip() if query_name is not None else None
        if normalized_query == "":
            normalized_query = None
        if normalized_query is not None and _QUERY_NAME.fullmatch(normalized_query) is None:
            msg = "query names must start with a letter and contain only lowercase letters, digits, or underscores"
            raise ValueError(msg)
        return await self._repository.create_showcase(
            stable_key=f"user_{created_by_discord_id}_{uuid4().hex}",
            display_name=" ".join(display_name.split()),
            normalized_name=normalized_name,
            value_type=value_type,
            query_name=normalized_query,
            created_by_discord_id=created_by_discord_id,
        )

    async def pending(self) -> Sequence[TagDefinition]:
        """List user definitions awaiting staff review."""
        return await self._repository.pending()

    async def approve(self, tag_id: int) -> TagDefinition:
        """Publish a pending tag."""
        return await self._set_status(tag_id, TagModerationStatus.APPROVED)

    async def reject(self, tag_id: int) -> TagDefinition:
        """Reject a proposed tag without deleting its audit trail."""
        return await self._set_status(tag_id, TagModerationStatus.REJECTED)

    async def archive(self, tag_id: int) -> TagDefinition:
        """Hide a published tag while retaining assignments and history."""
        return await self._set_status(tag_id, TagModerationStatus.ARCHIVED)

    async def _set_status(self, tag_id: int, status: TagModerationStatus) -> TagDefinition:
        definition = await self._repository.set_status(tag_id, status)
        if definition is None:
            msg = f"tag {tag_id} does not exist"
            raise ValueError(msg)
        return definition
