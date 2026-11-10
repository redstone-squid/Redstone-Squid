"""Suggestion providers over computed record categories and credited creators."""

from collections.abc import Sequence
from typing import Protocol

from squid.accounts.models import AliasClaim
from squid.bot.profile_render import present_claimant
from squid.suggestions.application import Candidate
from squid.suggestions.domain import MAX_SUGGESTIONS, SuggestionRequest


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
        rows = await self._reader.title_definitions(request.query, limit=request.limit or MAX_SUGGESTIONS)
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
        profiles = await self._reader.creator_profiles(request.query, limit=request.limit or MAX_SUGGESTIONS)
        return tuple(
            Candidate.create(creator_id, label=name, kind="creator", terms=(name,)) for creator_id, name in profiles
        )


class RecordTitleProvider:
    """Suggest record titles by their record title, submitting the public UUID."""

    def __init__(self, reader: RecordTitleReader) -> None:
        self._reader = reader

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        record_titles = await self._reader.record_titles(request.query, limit=request.limit or MAX_SUGGESTIONS)
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


def _claimant_description(claim: AliasClaim) -> str:
    """Present a claimant canonically within Discord's autocomplete limit."""
    return present_claimant(claim, mention=False)[:100]


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
                # Discord truncates a description at 100 characters, and a mention renders as raw
                # `<@id>` here rather than as a chip, so this asks for claimants and then prefers a
                # readable name over the snowflake.
                description=_claimant_description(claim),
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
        creators = await self._reader.creators(request.query, limit=request.limit or MAX_SUGGESTIONS)
        return tuple(
            # Show existing ownership before the user submits a claim request.
            Candidate.create(name, description="claimed" if claimed else None, kind="creator")
            for name, claimed in creators
        )
