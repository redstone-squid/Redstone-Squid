"""Resolve build tag names and values when saving edits."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from squid.core.builds.domain import BuildCategory, ShowcaseTag, UnresolvedTags
from squid.core.builds.errors import InvalidBuildError
from squid.core.builds.models import Build
from squid.core.errors import ValidationError
from squid.core.tags.domain import TagSemanticKind, TagValue, coerce_assignment_value
from squid.core.tags.models import TagAssignment, TagDefinition


@dataclass(frozen=True, slots=True)
class TagResolution:
    """The outcome of resolving requested tag names against approved tags.

    Unknown names are reported normalized (see :func:`normalize_tag_name`); a
    name is unknown when it matches no approved definition, or when it
    matches more than one and the request is therefore ambiguous.
    """

    assignments: tuple[TagAssignment, ...]
    unknown_restrictions: frozenset[str]
    unknown_patterns: frozenset[str]


class BuildTagResolver(Protocol):
    """Resolve restriction and pattern names against approved tags."""

    async def resolve_tags(
        self,
        *,
        build_kind: str | None,
        restrictions: Sequence[str],
        patterns: Sequence[str],
    ) -> TagResolution: ...

    async def showcase_definitions(self, keys: Sequence[str]) -> Sequence[TagDefinition]:
        """Return the approved showcase tags with these stable keys; unknown keys are left out."""
        ...


async def apply_build_tags(build: Build, resolver: BuildTagResolver) -> None:
    """Resolve editable restriction and pattern names into tag assignments.

    The repository persists build.tags; BuildLoader derives the editable name
    lists from those tags when reading a build. This function resolves edits in
    the other direction, then rewrites the lists with the resolved display names
    so the prepared build agrees with what a later reload will show.

    Replace restriction and pattern tags while retaining other tags. Names that resolve
    to no single approved tag become the build's unresolved names (see UnresolvedTags). The
    build's earlier unresolved names are resolved again, so one that now matches an approved tag
    becomes an assignment. Doors and extenders with no requested or unresolved pattern use Regular.
    """
    # Collect every name to resolve, deduplicated the way unresolved names are, so that the ones the
    # resolver reports as unknown can be kept with their spelling and restriction field.
    requested = UnresolvedTags.read(build.extra_info).add(
        restrictions=[
            (field_name, value) for field_name, values in build.restrictions.items() for value in values or ()
        ],
        patterns=build.patterns,
    )
    patterns = list(requested.patterns) or (
        ["Regular"] if build.category in {BuildCategory.DOOR, BuildCategory.EXTENDER} else []
    )
    resolution = await resolver.resolve_tags(
        build_kind=build.category.value,
        restrictions=requested.restriction_names(),
        patterns=patterns,
    )
    requested.retain(restrictions=resolution.unknown_restrictions, patterns=resolution.unknown_patterns).write(
        build.extra_info
    )

    retained = [
        assignment for assignment in build.tags if assignment.definition.semantic_kind is TagSemanticKind.SHOWCASE
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


async def replace_showcase_tags(build: Build, tags: Sequence[ShowcaseTag], resolver: BuildTagResolver) -> None:
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
