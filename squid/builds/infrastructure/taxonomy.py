"""Validation adapter for the unified build taxonomy."""

from collections.abc import Callable, Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from squid.builds.application.taxonomy import TaxonomyResolution
from squid.builds.domain import normalize_tag_name
from squid.builds.domain.models import RestrictionTypeLiteral
from squid.builds.errors import AliasAlreadyAddedError, AliasInUseError, RestrictionNotFoundError
from squid.builds.infrastructure.restrictions import RestrictionRepository, get_restriction_id
from squid.tags.domain import TagSemanticKind
from squid.tags.models import TagAlias, TagApplicability, TagAssignment, TagDefinition


class PostgresBuildTaxonomy:
    """Validate inferred restriction and pattern names."""

    def __init__(self, session: Callable[[], AsyncSession]):
        self._session = session

    async def get_restriction_id(self, name_or_alias: str) -> int | None:
        session = self._session()
        return await get_restriction_id(session, name_or_alias)

    async def fetch_all_restrictions(self) -> list[TagDefinition]:
        session = self._session()
        result = await session.execute(
            select(TagDefinition)
            .options(selectinload(TagDefinition.aliases))
            .where(
                TagDefinition.authority == "official",
                TagDefinition.semantic_kind == "restriction",
                TagDefinition.moderation_status == "approved",
            )
        )
        return list(result.scalars().all())

    async def add_restriction_alias_by_id(self, restriction_id: int, alias: str) -> None:
        session = self._session()
        definition = await session.get(TagDefinition, restriction_id)
        if definition is None:
            raise RestrictionNotFoundError(str(restriction_id))
        await RestrictionRepository(self._session).add_alias(definition.display_name, alias)

    async def add_restriction_alias(self, name_or_alias: str, alias: str) -> None:
        rid = await self.get_restriction_id(name_or_alias)
        alias_rid = await self.get_restriction_id(alias)
        if rid is None:
            raise RestrictionNotFoundError(name_or_alias)
        if alias_rid == rid:
            raise AliasAlreadyAddedError(alias, rid)
        if alias_rid is not None:
            raise AliasInUseError(alias, alias_rid)
        await self.add_restriction_alias_by_id(rid, alias)

    async def get_valid_restrictions(self, type: RestrictionTypeLiteral) -> Sequence[str]:
        session = self._session()
        return (
            await session.scalars(
                select(TagDefinition.display_name).where(
                    TagDefinition.authority == "official",
                    TagDefinition.semantic_kind == "restriction",
                    TagDefinition.restriction_type == type,
                    TagDefinition.moderation_status == "approved",
                )
            )
        ).all()

    async def get_valid_door_types(self) -> Sequence[str]:
        session = self._session()
        return (
            await session.scalars(
                select(TagDefinition.display_name).where(
                    TagDefinition.authority == "official",
                    TagDefinition.semantic_kind == "pattern",
                    TagDefinition.moderation_status == "approved",
                    TagDefinition.applicabilities.any(build_kind="Door"),
                )
            )
        ).all()

    async def resolve_official(
        self,
        *,
        build_kind: str | None,
        restrictions: Sequence[str],
        patterns: Sequence[str],
    ) -> TaxonomyResolution:
        session = self._session()
        rows, unknown_restrictions, unknown_patterns = await _resolve_official_tag_rows(
            session,
            build_kind=build_kind,
            restrictions=restrictions,
            patterns=patterns,
        )
        return TaxonomyResolution(
            assignments=tuple(
                TagAssignment(
                    definition=row,
                    provenance="submitted",
                )
                for row in rows
            ),
            unknown_restrictions=frozenset(unknown_restrictions),
            unknown_patterns=frozenset(unknown_patterns),
        )


async def _resolve_official_tag_rows(
    session: AsyncSession,
    *,
    build_kind: str | None,
    restrictions: Sequence[str],
    patterns: Sequence[str],
) -> tuple[list[TagDefinition], set[str], set[str]]:
    """Match approved official names and aliases, optionally limited to a build kind.

    Only unambiguous names select tags. Return selected rows in display order
    and normalized names that could not be resolved, separated by semantic kind.
    """
    restriction_names = {normalize_tag_name(name) for name in restrictions}
    pattern_names = {normalize_tag_name(name) for name in patterns}
    if not restriction_names and not pattern_names:
        return [], set(), set()

    matched_name = or_(
        TagDefinition.normalized_name.in_(restriction_names | pattern_names),
        TagAlias.normalized_alias.in_(restriction_names | pattern_names),
    )
    matched_kind = or_(
        (TagDefinition.semantic_kind == TagSemanticKind.RESTRICTION)
        & matched_name
        & or_(
            TagDefinition.normalized_name.in_(restriction_names),
            TagAlias.normalized_alias.in_(restriction_names),
        ),
        (TagDefinition.semantic_kind == TagSemanticKind.PATTERN)
        & matched_name
        & or_(
            TagDefinition.normalized_name.in_(pattern_names),
            TagAlias.normalized_alias.in_(pattern_names),
        ),
    )
    statement = (
        select(TagDefinition)
        .options(selectinload(TagDefinition.aliases))
        .outerjoin(TagAlias, TagAlias.tag_id == TagDefinition.id)
        .where(
            TagDefinition.authority == "official",
            TagDefinition.moderation_status == "approved",
            matched_kind,
        )
        .order_by(TagDefinition.default_display_order, TagDefinition.id)
    )
    if build_kind is not None:
        statement = statement.join(
            TagApplicability,
            TagApplicability.tag_id == TagDefinition.id,
        ).where(TagApplicability.build_kind == build_kind)
    rows = list((await session.scalars(statement)).unique().all())
    matched_restrictions = _unambiguously_matched_names(rows, restriction_names, TagSemanticKind.RESTRICTION)
    matched_patterns = _unambiguously_matched_names(rows, pattern_names, TagSemanticKind.PATTERN)
    selected = [
        row
        for row in rows
        if (
            (row.semantic_kind == TagSemanticKind.RESTRICTION and bool(_definition_names(row) & matched_restrictions))
            or (row.semantic_kind == TagSemanticKind.PATTERN and bool(_definition_names(row) & matched_patterns))
        )
    ]
    return selected, restriction_names - matched_restrictions, pattern_names - matched_patterns


def _unambiguously_matched_names(
    definitions: Sequence[TagDefinition],
    requested: set[str],
    semantic_kind: TagSemanticKind,
) -> set[str]:
    return {
        name
        for name in requested
        if sum(
            name in _definition_names(definition)
            for definition in definitions
            if definition.semantic_kind == semantic_kind
        )
        == 1
    }


def _definition_names(definition: TagDefinition) -> set[str]:
    return {definition.normalized_name, *(alias.normalized_alias for alias in definition.aliases)}
