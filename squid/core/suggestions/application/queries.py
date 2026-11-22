"""Suggestion-shaped reads, each using an independent database scope."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Protocol

from squid.core.unit_of_work import UnitOfWork, UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class TagEntry:
    """One tag value and every string that should match it."""

    id: int
    stable_key: str
    display_name: str
    semantic_kind: str
    aliases: tuple[str, ...] = ()
    value_type: str | None = None
    """The value a showcase tag takes, such as "numeric"; None when it takes none."""


@dataclass(frozen=True, slots=True)
class DocumentEntry:
    """One projected resource offered as a completion."""

    source_key: str
    title: str
    subtitle: str | None
    status: str | None


class SuggestionRepository(Protocol):
    """Read candidate data inside the caller's transaction.

    Status and authority filters come from the registered provider. Implementations
    do not authorize the requester; the service checks visibility before calling.
    """

    async def tag_entries(
        self, semantic_kind: str, *, build_kind: str | None = None, authority: str | None = "official"
    ) -> Sequence[TagEntry]: ...
    async def documents(
        self, resource_kind: str, query: str, *, statuses: Collection[str] | None = None, limit: int
    ) -> Sequence[DocumentEntry]: ...
    async def title_definitions(self, query: str, *, limit: int) -> Sequence[tuple[int, str, str]]: ...
    async def conversion_versions(self) -> Sequence[tuple[int, str]]: ...

    async def version_ids(self) -> Sequence[tuple[int, str]]: ...
    async def creator_profiles(self, query: str, *, limit: int) -> Sequence[tuple[str, str]]: ...
    async def filter_values(self, field_name: str, prefix: str, *, limit: int) -> Sequence[str]: ...
    async def record_titles(self, query: str, *, limit: int) -> Sequence[tuple[str, str, str | None]]: ...
    async def creators(self, query: str, *, limit: int) -> Sequence[tuple[str, bool]]: ...


class SuggestionQueryService:
    """Give each provider read its own unit of work so concurrent calls do not share a session."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory[SuggestionUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def tag_entries(
        self, semantic_kind: str, *, build_kind: str | None = None, authority: str | None = "official"
    ) -> Sequence[TagEntry]:
        async with self._uow_factory() as uow:
            return await uow.suggestions.tag_entries(semantic_kind, build_kind=build_kind, authority=authority)

    async def documents(
        self, resource_kind: str, query: str, *, statuses: Collection[str] | None = None, limit: int
    ) -> Sequence[DocumentEntry]:
        async with self._uow_factory() as uow:
            return await uow.suggestions.documents(resource_kind, query, statuses=statuses, limit=limit)

    async def title_definitions(self, query: str, *, limit: int) -> Sequence[tuple[int, str, str]]:
        async with self._uow_factory() as uow:
            return await uow.suggestions.title_definitions(query, limit=limit)

    async def conversion_versions(self) -> Sequence[tuple[int, str]]:
        async with self._uow_factory() as uow:
            return await uow.suggestions.conversion_versions()

    async def version_ids(self) -> Sequence[tuple[int, str]]:
        async with self._uow_factory() as uow:
            return await uow.suggestions.version_ids()

    async def creator_profiles(self, query: str, *, limit: int) -> Sequence[tuple[str, str]]:
        async with self._uow_factory() as uow:
            return await uow.suggestions.creator_profiles(query, limit=limit)

    async def filter_values(self, field_name: str, prefix: str, *, limit: int) -> Sequence[str]:
        async with self._uow_factory() as uow:
            return await uow.suggestions.filter_values(field_name, prefix, limit=limit)

    async def record_titles(self, query: str, *, limit: int) -> Sequence[tuple[str, str, str | None]]:
        async with self._uow_factory() as uow:
            return await uow.suggestions.record_titles(query, limit=limit)

    async def creators(self, query: str, *, limit: int) -> Sequence[tuple[str, bool]]:
        async with self._uow_factory() as uow:
            return await uow.suggestions.creators(query, limit=limit)


class SuggestionUnitOfWork(UnitOfWork, Protocol):
    @property
    def suggestions(self) -> SuggestionRepository: ...
