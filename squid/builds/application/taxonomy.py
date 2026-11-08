"""Edit-time resolution of build taxonomy names into tag assignments."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from squid.builds.domain import BuildCategory, UnresolvedTaxonomy
from squid.builds.models import Build
from squid.tags.domain import TagAuthority, TagSemanticKind
from squid.tags.models import TagAssignment


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

    Replace official restriction and pattern tags while retaining other tags. Names that resolve
    to no single official tag become the build's unresolved names (see UnresolvedTaxonomy). The
    build's earlier unresolved names are resolved again, so one that now matches an official tag
    becomes an assignment. Doors and extenders with no requested or unresolved pattern use Regular.
    """
    # Collect every name to resolve, deduplicated the way unresolved names are, so that the ones the
    # resolver reports as unknown can be kept with their spelling and restriction field.
    requested = UnresolvedTaxonomy.read(build.extra_info).add(
        restrictions=[
            (field_name, value) for field_name, values in build.restrictions.items() for value in values or ()
        ],
        patterns=build.patterns,
    )
    patterns = list(requested.patterns) or (
        ["Regular"] if build.category in {BuildCategory.DOOR, BuildCategory.EXTENDER} else []
    )
    resolution = await resolver.resolve_official(
        build_kind=build.category.value,
        restrictions=requested.restriction_names(),
        patterns=patterns,
    )
    requested.retain(restrictions=resolution.unknown_restrictions, patterns=resolution.unknown_patterns).write(
        build.extra_info
    )

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
