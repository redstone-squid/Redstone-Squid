"""Suggestion providers over computed record categories and credited creators."""

from collections.abc import Sequence
from typing import Protocol

from squid.accounts.domain import AliasClaim, IdentityProvider
from squid.suggestions.application import Candidate, candidate
from squid.suggestions.domain import MAX_SUGGESTIONS, SuggestionRequest


class RecordDefinitionReader(Protocol):
    """Read record definitions by the title an admin recognizes them by."""

    async def record_definitions(self, query: str, *, limit: int) -> Sequence[tuple[int, str, str]]:
        """Return at most `limit` `(id, title, build_kind)` rows matching `query`."""
        ...


class CreatorReader(Protocol):
    """Read credited creator names."""

    async def creators(self, query: str, *, limit: int) -> Sequence[tuple[str, bool]]:
        """Return at most `limit` `(name, claimed)` pairs whose name starts with `query`."""
        ...


class RecordDefinitionProvider:
    """Suggest the categories `/records lookup` materializes, submitting the definition id."""

    def __init__(self, reader: RecordDefinitionReader) -> None:
        self._reader = reader

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        rows = await self._reader.record_definitions(request.query, limit=request.limit or MAX_SUGGESTIONS)
        return tuple(
            candidate(
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

    async def creator_profiles(self, query: str, *, limit: int) -> Sequence[tuple[str, str]]:
        """Return at most `limit` `(public_creator_id, name)` pairs for accounts holding an alias."""
        ...


class CompetitionReader(Protocol):
    """Read record competition identifiers."""

    async def competitions(self, query: str, *, limit: int) -> Sequence[tuple[str, str, str | None]]:
        """Return at most `limit` `(public_id, title, subtitle)` rows matching `query`."""
        ...


class CreatorProfileProvider:
    """Suggest creators by name while submitting the public UUID a subscription stores.

    `/notifications follow-creator` takes a bare UUID, which is discoverable nowhere else in
    Discord.
    """

    def __init__(self, reader: CreatorProfileReader) -> None:
        self._reader = reader

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        profiles = await self._reader.creator_profiles(request.query, limit=request.limit or MAX_SUGGESTIONS)
        return tuple(candidate(creator_id, label=name, kind="creator", terms=(name,)) for creator_id, name in profiles)


class CompetitionProvider:
    """Suggest record competitions by their record title, submitting the public UUID."""

    def __init__(self, reader: CompetitionReader) -> None:
        self._reader = reader

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        competitions = await self._reader.competitions(request.query, limit=request.limit or MAX_SUGGESTIONS)
        return tuple(
            candidate(
                public_id,
                label=title,
                description=subtitle,
                kind="competition",
                terms=(title, subtitle) if subtitle else (title,),
            )
            for public_id, title, subtitle in competitions
        )


class PendingAliasClaims(Protocol):
    """Read creator credit claims awaiting staff review."""

    async def pending_alias_claims(self, *, with_claimants: bool = False) -> Sequence[AliasClaim]:
        """Return the claims still awaiting a decision; `with_claimants` loads each claimant's account."""
        ...


def _claimant_description(claim: AliasClaim) -> str:
    """Describe a claimant in the little room an autocomplete row has.

    Not `present_claimant`: this surface cannot render a mention, so it prefers a readable name and
    falls back to the internal id only when there is nothing else.
    """
    claimant = claim.claimant
    if claimant is not None:
        java = claimant.identity(IdentityProvider.JAVA)
        if java is not None and java.display_name is not None:
            return java.display_name[:100]
        if claimant.public_creator_id is not None:
            return f"creator {claimant.public_creator_id}"[:100]
    return f"account {claim.account_id}"


class AliasClaimProvider:
    """Suggest the creator credit claims a reviewer can act on."""

    def __init__(self, accounts: PendingAliasClaims) -> None:
        self._accounts = accounts

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        del request
        # Uncached for the same reason as pending tags: a reviewer works down this list, and a
        # claim they just resolved must stop being offered.
        return tuple(
            candidate(
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
            # Whether a name is claimed decides whether claiming it will succeed, so it is worth
            # showing before the command is submitted rather than in the rejection afterwards.
            candidate(name, description="claimed" if claimed else None, kind="creator")
            for name, claimed in creators
        )
