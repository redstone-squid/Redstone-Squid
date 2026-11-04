"""Edit-time resolution of build taxonomy names into tag assignments."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from squid.builds.domain import BuildCategory, UnknownRestrictions
from squid.builds.models import Build
from squid.tags.domain import TagAuthority, TagSemanticKind
from squid.tags.models import TagAssignment


def normalize_tag_name(value: str) -> str:
    """Collapse case and internal whitespace the way taxonomy resolution matches names."""
    return " ".join(value.casefold().split())


@dataclass(frozen=True, slots=True)
class TaxonomyResolution:
    """The outcome of resolving requested taxonomy names against official tags.

    Unknown names are reported normalized (see :func:`normalize_tag_name`); a
    name is unknown when it matches no approved official definition, or when it
    matches more than one and the request is therefore ambiguous.
    """

    assignments: tuple[TagAssignment, ...]
    unknown_restrictions: frozenset[str]
    unknown_patterns: frozenset[str]


class BuildTaxonomyResolver(Protocol):
    """Resolve restriction and pattern names against the official taxonomy."""

    async def resolve_official(
        self,
        *,
        build_kind: str | None,
        restrictions: Sequence[str],
        patterns: Sequence[str],
    ) -> TaxonomyResolution: ...


async def apply_build_taxonomy(build: Build, resolver: BuildTaxonomyResolver) -> None:
    """Resolve editable restriction and pattern names into tag assignments.

    The repository persists build.tags; BuildLoader derives the editable name
    lists from those tags when reading a build. This function resolves edits in
    the other direction, then rewrites the lists with the resolved display names
    so the prepared build agrees with what a later reload will show.

    Replace official restriction and pattern tags while retaining other tags.
    Keep unknown or ambiguous input in extra_info for reviewers. Doors and
    extenders with no requested pattern use Regular.
    """
    restrictions = [value for values in build.restrictions.values() for value in values or ()]
    patterns = list(build.patterns) or (
        ["Regular"] if build.category in {BuildCategory.DOOR, BuildCategory.EXTENDER} else []
    )
    resolution = await resolver.resolve_official(
        build_kind=build.category.value,
        restrictions=restrictions,
        patterns=patterns,
    )
    _record_unknowns(build, patterns, resolution)

    retained = [
        assignment
        for assignment in build.tags
        if assignment.definition.authority is not TagAuthority.OFFICIAL
        or assignment.definition.semantic_kind not in {TagSemanticKind.RESTRICTION, TagSemanticKind.PATTERN}
    ]
    build.tags = [*retained, *resolution.assignments]

    resolved = [assignment.definition for assignment in resolution.assignments]
    build.wiring_placement_restrictions = [
        definition.display_name
        for definition in resolved
        if definition.semantic_kind is TagSemanticKind.RESTRICTION and definition.restriction_type == "wiring-placement"
    ]
    build.animated_restrictions = [
        definition.display_name
        for definition in resolved
        if definition.semantic_kind is TagSemanticKind.RESTRICTION and definition.restriction_type == "animated"
    ]
    build.component_restrictions = [
        definition.display_name
        for definition in resolved
        if definition.semantic_kind is TagSemanticKind.RESTRICTION and definition.restriction_type == "component"
    ]
    build.miscellaneous_restrictions = [
        definition.display_name
        for definition in resolved
        if definition.semantic_kind is TagSemanticKind.RESTRICTION and definition.restriction_type == "miscellaneous"
    ]
    build.patterns = [
        definition.display_name for definition in resolved if definition.semantic_kind is TagSemanticKind.PATTERN
    ]


def _record_unknowns(build: Build, patterns: Sequence[str], resolution: TaxonomyResolution) -> None:
    """Merge unresolvable names into ``extra_info`` so nothing is silently lost."""
    current_restrictions: UnknownRestrictions = {}
    current_restrictions.update(build.extra_info.get("unknown_restrictions", {}))
    for field_name, values in build.restrictions.items():
        new_values = [value for value in values or () if normalize_tag_name(value) in resolution.unknown_restrictions]
        existing_values = cast(list[str], current_restrictions.get(field_name, []))
        if new_values or existing_values:
            current_restrictions[field_name] = list(dict.fromkeys([*existing_values, *new_values]))
    if current_restrictions:
        build.extra_info["unknown_restrictions"] = current_restrictions

    new_patterns = [value for value in patterns if normalize_tag_name(value) in resolution.unknown_patterns]
    existing_patterns = build.extra_info.get("unknown_patterns", [])
    if new_patterns or existing_patterns:
        build.extra_info["unknown_patterns"] = list(dict.fromkeys([*existing_patterns, *new_patterns]))
