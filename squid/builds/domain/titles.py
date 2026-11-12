"""Catalogue and display title formatting for individual builds."""

import re
from collections.abc import Iterable

from squid.builds.models import Build, DoorBuild, EntranceBuild, ExtenderBuild, OtherBuild, UtilityBuild
from squid.catalogue.domain import (
    DoorCategory,
    ExtenderCategory,
    FormattedTitle,
    RulesTitleFormatter,
    TitleSection,
    TitleToken,
)

from .models import Status
from .taxonomy import UnresolvedTaxonomy

_SHOWCASE_QUALIFIER = re.compile(r"(?:\d+\.\d+\s*s|\d+\s*[Bb]locks)")


def format_build_category(build: Build) -> FormattedTitle:
    """Format the canonical category title without individual-build decoration."""
    formatter = RulesTitleFormatter()
    unresolved = UnresolvedTaxonomy.read(build.extra_info)
    match build:
        case DoorBuild():
            return formatter.format_door(
                DoorCategory(
                    wiring_restrictions=(
                        *build.wiring_placement_restrictions,
                        *unresolved.restriction_names("wiring_placement_restrictions"),
                    ),
                    animated_restrictions=(
                        *build.animated_restrictions,
                        *unresolved.restriction_names("animated_restrictions"),
                    ),
                    size=_door_size(build),
                    types=(*build.patterns, *unresolved.patterns),
                    orientation=build.orientation or "Door (orientation unknown)",
                    component_restrictions=(
                        *build.component_restrictions,
                        *unresolved.restriction_names("component_restrictions"),
                    ),
                    miscellaneous_restrictions=(
                        *build.miscellaneous_restrictions,
                        *(
                            value
                            for value in unresolved.restriction_names("miscellaneous_restrictions")
                            if not _SHOWCASE_QUALIFIER.match(value)
                        ),
                    ),
                )
            )
        case ExtenderBuild():
            return formatter.format_extender(
                ExtenderCategory(
                    wiring_restrictions=(
                        *build.wiring_placement_restrictions,
                        *unresolved.restriction_names("wiring_placement_restrictions"),
                    ),
                    orientation=build.orientation or "Unknown direction",
                    length=build.extension_length,
                    types=(
                        *(build.patterns or ([build.extender_type] if build.extender_type else [])),
                        *unresolved.patterns,
                    ),
                    component_restrictions=(
                        *build.component_restrictions,
                        *unresolved.restriction_names("component_restrictions"),
                    ),
                    miscellaneous_restrictions=(
                        *build.miscellaneous_restrictions,
                        *(
                            value
                            for value in unresolved.restriction_names("miscellaneous_restrictions")
                            if not _SHOWCASE_QUALIFIER.match(value)
                        ),
                    ),
                )
            )
        case UtilityBuild() | EntranceBuild() | OtherBuild():
            title = build.category.value
            return FormattedTitle(
                title=title,
                title_tokens=(TitleToken(title, TitleSection.FIXED_NOUN),),
            )
        case _:
            msg = f"Title grammar does not support {build.category or 'uncategorized builds'}."
            raise NotImplementedError(msg)


def format_build_display_title(build: Build, *, markdown: bool) -> str:
    """Add moderation status and timing qualifiers to a category title.

    With markdown enabled, unrecognized terms are italicized.
    """
    category = format_build_category(build)
    terms: list[str] = []
    if build.submission_status == Status.PENDING:
        terms.append("Pending:")
    elif build.submission_status == Status.DENIED:
        terms.append("Denied:")
    if build.ai_generated:
        terms.append("\N{ROBOT FACE}")
    terms.extend(_showcase_qualifiers(build))
    terms.extend(build.component_restrictions)
    terms.extend(_unknown_components(build, markdown=markdown))
    terms.extend(_render_tokens(category.title_tokens, markdown=markdown))
    return " ".join(terms)


def _door_size(build: DoorBuild) -> str:
    width = str(build.door_width) if build.door_width is not None else "?"
    height = str(build.door_height) if build.door_height is not None else "?"
    if build.door_depth and build.door_depth > 1:
        return f"{width}x{height}x{build.door_depth}"
    return f"{width}x{height}"


def _showcase_qualifiers(build: Build) -> Iterable[str]:
    unresolved = UnresolvedTaxonomy.read(build.extra_info)
    return (
        value
        for value in unresolved.restriction_names("miscellaneous_restrictions")
        if _SHOWCASE_QUALIFIER.match(value)
    )


def _unknown_components(build: Build, *, markdown: bool) -> Iterable[str]:
    unresolved = UnresolvedTaxonomy.read(build.extra_info)
    return (_italic(value) if markdown else value for value in unresolved.restriction_names("component_restrictions"))


def _render_tokens(tokens: tuple[TitleToken, ...], *, markdown: bool) -> Iterable[str]:
    return (_italic(token.value) if markdown and not token.recognized else token.value for token in tokens)


def _italic(value: str) -> str:
    return f"*{value}*"
