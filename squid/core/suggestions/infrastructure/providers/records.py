"""Suggestion providers over computed record categories and credited creators."""

from collections.abc import Sequence
from typing import Protocol

from squid.core.accounts.application.presentation import claimant_label
from squid.core.accounts.models import AliasClaim
from squid.core.permissions.domain.catalogue import ACCOUNT_CLAIM_LIST
from squid.core.suggestions.application import Candidate
from squid.core.suggestions.application.queries import SuggestionRepository
from squid.core.suggestions.application.registry import SuggestionSource
from squid.core.suggestions.domain import MAX_QUERY_SUGGESTIONS, SuggestionRequest, ValueType, Visibility


class RecordDefinitionReader(Protocol):
    """Read record definitions by the title an admin recognizes them by."""

    async def title_definitions(self, query: str, *, limit: int) -> Sequence[tuple[int, str, str]]: ...


class CreatorReader(Protocol):
    """Read credited creator names."""

    async def creators(self, query: str, *, limit: int) -> Sequence[tuple[str, bool]]: ...


class RecordDefinitionProvider:
    """Suggest the categories `/records lookup` materializes, submitting the definition id."""

    def __init__(self, reader: RecordDefinitionReader) -> None:
        self._reader = reader

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        rows = await self._reader.title_definitions(request.query, limit=request.limit or MAX_QUERY_SUGGESTIONS)
        return tuple(
            Candidate.create(
                str(definition_id),
                label=title,
                description=build_kind,
                kind="record_category",
                terms=(title, str(definition_id)),
            )
            for definition_id, title, build_kind in rows
        )


class CreatorProfileReader(Protocol):
    """Read public creator identifiers."""

    async def creator_profiles(self, query: str, *, limit: int) -> Sequence[tuple[str, str]]: ...


class RecordTitleReader(Protocol):
    """Read record title identifiers."""

    async def record_titles(self, query: str, *, limit: int) -> Sequence[tuple[str, str, str | None]]: ...


class CreatorProfileProvider:
    """Suggest creators by name while submitting the public UUID a subscription stores."""

    def __init__(self, reader: CreatorProfileReader) -> None:
        self._reader = reader

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        profiles = await self._reader.creator_profiles(request.query, limit=request.limit or MAX_QUERY_SUGGESTIONS)
        return tuple(
            Candidate.create(creator_id, label=name, kind="creator", terms=(name,)) for creator_id, name in profiles
        )


class RecordTitleProvider:
    """Suggest record titles by their record title, submitting the public UUID."""

    def __init__(self, reader: RecordTitleReader) -> None:
        self._reader = reader

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        record_titles = await self._reader.record_titles(request.query, limit=request.limit or MAX_QUERY_SUGGESTIONS)
        return tuple(
            Candidate.create(
                public_id,
                label=title,
                description=subtitle,
                kind="record_title",
                terms=(title, subtitle) if subtitle else (title,),
            )
            for public_id, title, subtitle in record_titles
        )


class PendingAliasClaims(Protocol):
    """Read creator credit claims awaiting staff review."""

    async def pending_alias_claims(self, *, with_claimants: bool = False) -> Sequence[AliasClaim]: ...


class AliasClaimProvider:
    """Suggest the creator credit claims a reviewer can act on."""

    def __init__(self, accounts: PendingAliasClaims) -> None:
        self._accounts = accounts

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        del request
        # Uncached for the same reason as pending tags: a reviewer works down this list, and a
        # claim they just resolved must stop being offered.
        return tuple(
            Candidate.create(
                str(claim.id),
                label=f"#{claim.id} · {claim.alias_name}",
                description=claimant_label(claim),
                kind="claim",
                terms=(claim.alias_name, str(claim.id)),
            )
            for claim in await self._accounts.pending_alias_claims(with_claimants=True)
        )


class CreatorProvider:
    """Suggest creator names already credited on a build."""

    def __init__(self, reader: CreatorReader) -> None:
        self._reader = reader

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        creators = await self._reader.creators(request.query, limit=request.limit or MAX_QUERY_SUGGESTIONS)
        return tuple(
            # Show existing ownership before the user submits a claim request.
            Candidate.create(name, description="claimed" if claimed else None, kind="creator")
            for name, claimed in creators
        )


def record_sources(
    repository: SuggestionRepository, accounts: PendingAliasClaims | None
) -> tuple[SuggestionSource, ...]:
    sources = (
        SuggestionSource(
            id="title_definitions",
            provider=RecordDefinitionProvider(repository),
            kind_label="record_category",
        ),
        SuggestionSource(
            id="creators",
            provider=CreatorProvider(repository),
            kind_label="creator",
            multi_value=",",
        ),
        SuggestionSource(
            id="creator_profiles",
            provider=CreatorProfileProvider(repository),
            kind_label="creator",
        ),
        SuggestionSource(
            id="record_titles",
            provider=RecordTitleProvider(repository),
            kind_label="record_title",
        ),
    )
    if accounts is None:
        return sources
    return (
        *sources,
        SuggestionSource(
            id="alias_claims_pending",
            provider=AliasClaimProvider(accounts),
            visibility=Visibility.REQUIRES_NODE,
            required_node=ACCOUNT_CLAIM_LIST.name,
            value_type=ValueType.INTEGER,
            kind_label="claim",
        ),
    )
