"""Propose, moderate, and assign user showcase tags."""

import re
from collections.abc import Sequence
from typing import Protocol

from squid.core.actors import ActingContext, application_operation
from squid.core.builds.application.ports import BuildEditingUnitOfWork
from squid.core.errors import ValidationError
from squid.core.i18n import tr
from squid.core.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.core.tags.models import TagDefinition
from squid.core.unit_of_work import UnitOfWorkFactory

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

    async def proposals(self, status: TagModerationStatus) -> Sequence[TagDefinition]: ...

    async def get(self, tag_id: int | str) -> TagDefinition | None: ...

    async def approved(
        self, *, kind: TagSemanticKind | None = None, authority: TagAuthority | None = None, q: str = ""
    ) -> Sequence[TagDefinition]: ...

    async def set_status(self, tag_id: int, status: TagModerationStatus) -> TagDefinition | None: ...


class TagUnitOfWork(BuildEditingUnitOfWork, Protocol):
    @property
    def tags(self) -> TagDefinitionRepository: ...


class TagService:
    """Read published definitions and pending proposals for tag workspaces."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory[TagUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    @application_operation
    async def reserve_identity(self, *, context: ActingContext) -> int:
        async with self._uow_factory() as uow:
            return await uow.tags.reserve_identity()

    @application_operation
    async def pending(self, *, context: ActingContext) -> Sequence[TagDefinition]:
        """List user definitions awaiting staff review."""
        return await self.list_proposals(TagModerationStatus.PENDING, context=context)

    @application_operation
    async def list_proposals(self, status: TagModerationStatus, *, context: ActingContext) -> Sequence[TagDefinition]:
        async with self._uow_factory() as uow:
            return await uow.tags.proposals(status)

    @application_operation
    async def public_definitions(
        self,
        *,
        kind: TagSemanticKind | None = None,
        authority: TagAuthority | None = None,
        q: str = "",
        context: ActingContext,
    ) -> Sequence[TagDefinition]:
        """List tag definitions available to public search and build clients."""
        async with self._uow_factory() as uow:
            return await uow.tags.approved(kind=kind, authority=authority, q=q)

    @application_operation
    async def public_definition(self, tag_id: int | str, *, context: ActingContext) -> TagDefinition | None:
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
