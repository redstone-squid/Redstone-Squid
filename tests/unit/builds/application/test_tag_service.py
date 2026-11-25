"""Inference uses the same alias and ambiguity rules as build edits."""

from unittest.mock import AsyncMock

import pytest

from squid.core.actors import ActingContext, Actor
from squid.core.builds.application.tag_service import BuildTagService
from squid.core.builds.application.tags import TagResolution
from squid.core.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.core.tags.models import TagAlias, TagAssignment, TagDefinition


def definition(name: str, kind: TagSemanticKind, restriction_type: str | None, *aliases: str) -> TagDefinition:
    return TagDefinition(
        stable_key=name.lower().replace(" ", "_"),
        display_name=name,
        authority=TagAuthority.OFFICIAL,
        semantic_kind=kind,
        restriction_type=restriction_type,
        value_type=TagValueType.NONE,
        moderation_status=TagModerationStatus.APPROVED,
        aliases=[TagAlias(alias=alias, normalized_alias=alias.casefold()) for alias in aliases],
    )


async def test_restriction_aliases_are_canonical_and_deduplicated() -> None:
    observerless = definition("No Observers", TagSemanticKind.RESTRICTION, "component", "Obsless", "Observerless")
    seamless = definition("Full Seamless", TagSemanticKind.RESTRICTION, "wiring-placement")
    repository = AsyncMock()
    repository.resolve_tags.return_value = TagResolution(
        (TagAssignment(observerless), TagAssignment(seamless)), frozenset({"sandless"}), frozenset()
    )
    scope = AsyncMock(build_tags=repository)
    scope.__aenter__.return_value = scope
    service = BuildTagService(uow_factory=lambda: scope)

    recognized, unknown = await service.validate_restrictions(
        ["OBSLESS", " No   Observers ", "observerless", "Sandless", "Full Seamless"],
        "component",
        context=ActingContext.direct(Actor()),
    )

    assert recognized == ["No Observers"]
    assert unknown == ["Sandless", "Full Seamless"]


async def test_ambiguous_alias_stays_unknown_even_when_a_canonical_name_also_matches() -> None:
    slime = definition("No Slime Blocks", TagSemanticKind.RESTRICTION, "component", "Pistonless")
    repository = AsyncMock()
    # A canonical name selected this definition, but the shared alias is still ambiguous.
    repository.resolve_tags.return_value = TagResolution(
        (TagAssignment(slime),), frozenset({"pistonless"}), frozenset()
    )
    scope = AsyncMock(build_tags=repository)
    scope.__aenter__.return_value = scope
    service = BuildTagService(uow_factory=lambda: scope)

    assert await service.validate_restrictions(
        ["No Slime Blocks", "Pistonless"], "component", context=ActingContext.direct(Actor())
    ) == (
        ["No Slime Blocks"],
        ["Pistonless"],
    )


async def test_pattern_aliases_use_the_door_catalogue() -> None:
    pattern = definition("Full Lamp", TagSemanticKind.PATTERN, None, "Lamp Door")
    repository = AsyncMock()
    repository.resolve_tags.return_value = TagResolution((TagAssignment(pattern),), frozenset(), frozenset({"glass"}))
    scope = AsyncMock(build_tags=repository)
    scope.__aenter__.return_value = scope
    service = BuildTagService(uow_factory=lambda: scope)

    assert await service.validate_door_types(["LAMP DOOR", "Glass"], context=ActingContext.direct(Actor())) == (
        ["Full Lamp"],
        ["Glass"],
    )
    repository.resolve_tags.assert_awaited_once_with(
        build_kind="Door", restrictions=(), patterns=["LAMP DOOR", "Glass"]
    )


@pytest.mark.parametrize("phrase", ["Non-Loc/Dir", "nonloc/dir", "Not locational / directional"])
async def test_combined_negative_placement_shorthand_keeps_both_constraints(phrase: str) -> None:
    nonloc = definition("Not Locational", TagSemanticKind.RESTRICTION, "miscellaneous")
    nondir = definition("Not Directional", TagSemanticKind.RESTRICTION, "miscellaneous")
    repository = AsyncMock()
    repository.resolve_tags.return_value = TagResolution(
        (TagAssignment(nonloc), TagAssignment(nondir)), frozenset(), frozenset()
    )
    scope = AsyncMock(build_tags=repository)
    scope.__aenter__.return_value = scope
    service = BuildTagService(uow_factory=lambda: scope)

    assert await service.validate_restrictions([phrase], "miscellaneous", context=ActingContext.direct(Actor())) == (
        ["Not Locational", "Not Directional"],
        [],
    )
    repository.resolve_tags.assert_awaited_once_with(
        build_kind=None, restrictions=["Not Locational", "Not Directional"], patterns=()
    )


async def test_placement_shorthand_keeps_qualifications_instead_of_claiming_reliability() -> None:
    phrase = "Non-Loc/Dir with fixes"
    repository = AsyncMock()
    repository.resolve_tags.return_value = TagResolution((), frozenset({phrase.casefold()}), frozenset())
    scope = AsyncMock(build_tags=repository)
    scope.__aenter__.return_value = scope
    service = BuildTagService(uow_factory=lambda: scope)

    assert await service.validate_restrictions([phrase], "miscellaneous", context=ActingContext.direct(Actor())) == (
        [],
        [phrase],
    )
    repository.resolve_tags.assert_awaited_once_with(build_kind=None, restrictions=[phrase], patterns=())
