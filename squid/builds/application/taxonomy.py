"""Edit-time resolution of build taxonomy names and showcase tags into tag assignments."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from squid.builds.domain import BuildCategory, ShowcaseTag, UnresolvedTaxonomy
from squid.builds.errors import InvalidBuildError
from squid.builds.models import Build
from squid.core.errors import ValidationError
from squid.tags.domain import TagAuthority, TagSemanticKind, TagValue, coerce_assignment_value
from squid.tags.models import TagAssignment, TagDefinition


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

    async def showcase_definitions(self, keys: Sequence[str]) -> Sequence[TagDefinition]:
        """Return the approved showcase tags with these stable keys; unknown keys are left out."""
        ...


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


async def replace_showcase_tags(build: Build, tags: Sequence[ShowcaseTag], resolver: BuildTaxonomyResolver) -> None:
    """Make `tags` the build's showcase tags, in that order.

    A key the build does not have yet must name an approved showcase tag. Each value must suit its
    tag: none for a tag that takes none, and a value of its type otherwise. A tag the build already
    has keeps its assignment, including its origin and display unit, with the new value if it
    changed. Restriction and pattern tags are left alone.

    Raises:
        InvalidBuildError: If a key names no such tag or a value does not suit its tag. The
            public context lists the offending keys under `showcase_tags`.
    """
    requested = list({tag.key: tag for tag in tags}.values())
    current = {
        assignment.definition.stable_key: assignment
        for assignment in build.tags
        if assignment.definition.semantic_kind is TagSemanticKind.SHOWCASE
    }
    added = [tag.key for tag in requested if tag.key not in current]
    definitions = {
        **{key: assignment.definition for key, assignment in current.items()},
        **{definition.stable_key: definition for definition in await resolver.showcase_definitions(added)},
    }
    values: dict[str, TagValue] = {}
    invalid: list[str] = []
    for tag in requested:
        try:
            values[tag.key] = coerce_assignment_value(definitions[tag.key], tag.value)
        except KeyError, ValidationError:
            invalid.append(tag.key)
    if invalid:
        invalid.sort()
        msg = f"Invalid showcase tags: {', '.join(invalid)}"
        raise InvalidBuildError(msg, context={"showcase_tags": invalid}, public_context={"showcase_tags": invalid})
    assignments: list[TagAssignment] = []
    for tag in requested:
        assignment = current.get(tag.key)
        if assignment is None:
            assignment = TagAssignment(definition=definitions[tag.key], value=values[tag.key], origin="submitted")
        elif assignment.value != values[tag.key]:
            assignment.value = values[tag.key]
        assignments.append(assignment)
    build.tags = [
        *(
            assignment
            for assignment in build.tags
            if assignment.definition.semantic_kind is not TagSemanticKind.SHOWCASE
        ),
        *assignments,
    ]
