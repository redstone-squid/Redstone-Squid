"""Inference uses the same alias and ambiguity rules as build edits."""

from unittest.mock import AsyncMock

from squid.builds.application.taxonomy import TaxonomyResolution
from squid.builds.application.taxonomy_service import BuildTaxonomyService
from squid.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.tags.models import TagAlias, TagAssignment, TagDefinition


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
    repository.resolve_official.return_value = TaxonomyResolution(
        (TagAssignment(observerless), TagAssignment(seamless)), frozenset({"sandless"}), frozenset()
    )
    scope = AsyncMock(build_taxonomy=repository)
    scope.__aenter__.return_value = scope
    service = BuildTaxonomyService(uow_factory=lambda: scope)

    recognized, unknown = await service.validate_restrictions(
        ["OBSLESS", " No   Observers ", "observerless", "Sandless", "Full Seamless"], "component"
    )

    assert recognized == ["No Observers"]
    assert unknown == ["Sandless", "Full Seamless"]


async def test_ambiguous_alias_stays_unknown_even_when_a_canonical_name_also_matches() -> None:
    slime = definition("No Slime Blocks", TagSemanticKind.RESTRICTION, "component", "Pistonless")
    repository = AsyncMock()
    # A canonical name selected this definition, but the shared alias is still ambiguous.
    repository.resolve_official.return_value = TaxonomyResolution(
        (TagAssignment(slime),), frozenset({"pistonless"}), frozenset()
    )
    scope = AsyncMock(build_taxonomy=repository)
    scope.__aenter__.return_value = scope
    service = BuildTaxonomyService(uow_factory=lambda: scope)

    assert await service.validate_restrictions(["No Slime Blocks", "Pistonless"], "component") == (
        ["No Slime Blocks"],
        ["Pistonless"],
    )


async def test_pattern_aliases_use_the_door_catalogue() -> None:
    pattern = definition("Full Lamp", TagSemanticKind.PATTERN, None, "Lamp Door")
    repository = AsyncMock()
    repository.resolve_official.return_value = TaxonomyResolution(
        (TagAssignment(pattern),), frozenset(), frozenset({"glass"})
    )
    scope = AsyncMock(build_taxonomy=repository)
    scope.__aenter__.return_value = scope
    service = BuildTaxonomyService(uow_factory=lambda: scope)

    assert await service.validate_door_types(["LAMP DOOR", "Glass"]) == (["Full Lamp"], ["Glass"])
    repository.resolve_official.assert_awaited_once_with(
        build_kind="Door", restrictions=(), patterns=["LAMP DOOR", "Glass"]
    )
