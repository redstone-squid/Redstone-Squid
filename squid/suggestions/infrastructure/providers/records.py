"""Suggestion providers over computed record categories and credited creators."""

from collections.abc import Sequence
from typing import Protocol

from squid.accounts.domain import AliasClaim
from squid.suggestions.application import Candidate, candidate
from squid.suggestions.domain import MAX_SUGGESTIONS, SuggestionRequest


class RecordKeyReader(Protocol):
    """Read canonical record category keys."""

    async def record_base_keys(self, query: str, *, limit: int) -> Sequence[tuple[str, str]]: ...


class CreatorReader(Protocol):
    """Read credited creator names."""

    async def creators(self, query: str, *, limit: int) -> Sequence[tuple[str, bool]]: ...


class RecordBaseKeyProvider:
    """Suggest the canonical category keys `/admin records-lookup` materializes against."""

    def __init__(self, reader: RecordKeyReader) -> None:
        self._reader = reader

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        keys = await self._reader.record_base_keys(request.query, limit=request.limit or MAX_SUGGESTIONS)
        return tuple(candidate(key, description=build_kind, kind="record_category") for key, build_kind in keys)


class CreatorProfileReader(Protocol):
    """Read public creator identifiers."""

    async def creator_profiles(self, query: str, *, limit: int) -> Sequence[tuple[str, str]]: ...


class CompetitionReader(Protocol):
    """Read record competition identifiers."""

    async def competitions(self, query: str, *, limit: int) -> Sequence[tuple[str, str, str]]: ...


class CreatorProfileProvider:
    """Suggest creators by name while submitting the public UUID a subscription stores.

    `/notifications follow-creator` currently asks for a bare UUID, which is not discoverable from
    anywhere in Discord.
    """

    def __init__(self, reader: CreatorProfileReader) -> None:
        self._reader = reader

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        profiles = await self._reader.creator_profiles(request.query, limit=request.limit or MAX_SUGGESTIONS)
        return tuple(candidate(creator_id, label=name, kind="creator", terms=(name,)) for creator_id, name in profiles)


class CompetitionProvider:
    """Suggest record competitions by their readable identity, submitting the public UUID."""

    def __init__(self, reader: CompetitionReader) -> None:
        self._reader = reader

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        competitions = await self._reader.competitions(request.query, limit=request.limit or MAX_SUGGESTIONS)
        return tuple(
            candidate(
                public_id,
                label=f"{title} — {category_key}",
                kind="competition",
                terms=(title, category_key),
            )
            for public_id, title, category_key in competitions
        )


class PendingAliasClaims(Protocol):
    """Read creator credit claims awaiting staff review."""

    async def pending_alias_claims(self) -> Sequence[AliasClaim]: ...


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
                description=f"account {claim.account_id}",
                kind="claim",
                terms=(claim.alias_name, str(claim.id)),
            )
            for claim in await self._accounts.pending_alias_claims()
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
