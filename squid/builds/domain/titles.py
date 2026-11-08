"""Catalogue and display title formatting for individual builds."""

import re
from collections.abc import Iterable

from squid.builds.errors import InvalidBuildError
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
                    orientation=_required(build.orientation, "Door orientation type"),
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
                    orientation=_required(build.orientation, "Extender orientation"),
                    length=_required_positive(build.extension_length, "Extender length"),
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


def format_build_display_title(build: Build, *, markdown: bool, current_version: str | None = None) -> str:
    """Add moderation status, timing qualifiers, and compatibility to a category title.

    With markdown enabled, unrecognized terms are italicized. Supplying a
    current_version absent from the build's version list adds [BROKEN].
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
    if current_version is not None and current_version not in build.versions:
        terms.append("[BROKEN]")
    return " ".join(terms)


def _door_size(build: DoorBuild) -> str:
    if build.door_depth and build.door_depth > 1:
        return f"{build.door_width}x{build.door_height}x{build.door_depth}"
    return f"{build.door_width}x{build.door_height}"


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


def _required(value: str | None, label: str) -> str:
    if value is None or not value.strip():
        msg = f"{label} information is missing."
        raise InvalidBuildError(msg)
    return value


def _required_positive(value: int | None, label: str) -> int:
    if value is None or value <= 0:
        msg = f"{label} information is missing."
        raise InvalidBuildError(msg)
    return value
