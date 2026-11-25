"""Suggestion-backed form options must preserve existing catalogue revisions.

Revisions identify option content, so changing providers must not make unchanged choices
appear new to clients with pinned drafts.
"""

from collections.abc import Sequence
from typing import Any, cast

import pytest

from squid.core.actors import ActingContext, Actor
from squid.core.submissions.infrastructure.options import ApprovedSubmissionOptionCatalog
from squid.core.submissions.infrastructure.suggestion_options import SuggestionFormOptionCatalog
from squid.core.suggestions.application import SuggestionService
from squid.core.suggestions.infrastructure.catalogue import build_registry
from squid.core.suggestions.infrastructure.repository import TagEntry
from squid.core.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.core.tags.models import TagDefinition
from squid.core.versions.domain import MinecraftVersion

TAGS = (
    ("seamless", "Seamless", TagSemanticKind.PATTERN, TagValueType.NONE),
    ("full_lamp", "Full Lamp", TagSemanticKind.PATTERN, TagValueType.NONE),
    ("locational", "Locational", TagSemanticKind.RESTRICTION, TagValueType.NONE),
    ("directional", "Directional", TagSemanticKind.RESTRICTION, TagValueType.NONE),
    ("showcase_fast", "Fast", TagSemanticKind.SHOWCASE, TagValueType.NONE),
    ("showcase_speed", "Speed", TagSemanticKind.SHOWCASE, TagValueType.NUMERIC),
)

VERSIONS = (
    MinecraftVersion("Java", 1, 21, 5),
    MinecraftVersion("Java", 1, 21, 4),
    MinecraftVersion("Bedrock", 1, 21, 50),
)


def _definition(index: int, key: str, name: str, kind: TagSemanticKind, value_type: TagValueType) -> TagDefinition:
    return TagDefinition(
        id=index,
        stable_key=key,
        display_name=name,
        authority=TagAuthority.OFFICIAL if kind is not TagSemanticKind.SHOWCASE else TagAuthority.USER,
        semantic_kind=kind,
        value_type=value_type,
        moderation_status=TagModerationStatus.APPROVED,
    )


class FakeTags:
    async def public_definitions(self, *, context: ActingContext) -> tuple[TagDefinition, ...]:
        return tuple(_definition(index, *tag) for index, tag in enumerate(TAGS, start=1))

    async def pending(self, *, context: ActingContext) -> tuple[TagDefinition, ...]:
        return ()


class FakeVersions:
    async def list_all(self, *, context: ActingContext) -> tuple[MinecraftVersion, ...]:
        return VERSIONS


class FakeSuggestionRepository:
    """Return matching tag keys and labels in reverse fixture order.

    Providers must establish the order used for revision hashing. Numeric IDs are synthetic;
    authority and build-kind filters are not modeled here.
    """

    async def tag_entries(
        self,
        semantic_kind: str,
        *,
        build_kind: str | None = None,
        authority: str | None = "official",
    ) -> Sequence[TagEntry]:
        del build_kind, authority
        return [
            TagEntry(
                id=index,
                stable_key=key,
                display_name=name,
                semantic_kind=semantic_kind,
                value_type=None if value_type is TagValueType.NONE else value_type.value,
            )
            for index, (key, name, kind, value_type) in enumerate(reversed(TAGS), start=1)
            if kind.value == semantic_kind
        ]

    async def version_ids(self) -> Sequence[tuple[int, str]]:
        return []


def catalogues() -> tuple[ApprovedSubmissionOptionCatalog, SuggestionFormOptionCatalog]:
    tags = FakeTags()
    versions = FakeVersions()
    repository = FakeSuggestionRepository()
    registry = build_registry(
        repository=cast(Any, repository),
        search=cast(Any, None),
        versions=versions,
        tags=tags,
    )
    return ApprovedSubmissionOptionCatalog(tags, versions), SuggestionFormOptionCatalog(SuggestionService(registry))


@pytest.mark.parametrize("source", ["approved_patterns", "approved_restrictions", "approved_showcase_tags"])
async def test_tag_options_match_the_catalogue_they_replace(source: str) -> None:
    existing, replacement = catalogues()
    before = await existing.options(source, "door", locale="en", context=ActingContext.direct(Actor()))
    after = await replacement.options(source, "door", locale="en", context=ActingContext.direct(Actor()))
    assert after.options == before.options
    assert after.revision == before.revision


async def test_showcase_options_carry_the_value_each_tag_takes() -> None:
    _, catalogue = catalogues()
    options = await catalogue.options(
        "approved_showcase_tags", "door", locale="en", context=ActingContext.direct(Actor())
    )
    assert [(option.value, option.value_type) for option in options.options] == [
        ("showcase_fast", None),
        ("showcase_speed", "numeric"),
    ]


async def test_version_options_match_the_catalogue_they_replace() -> None:
    existing, replacement = catalogues()
    before = await existing.options(
        "approved_source_versions", "door", locale="en", context=ActingContext.direct(Actor())
    )
    after = await replacement.options(
        "approved_source_versions", "door", locale="en", context=ActingContext.direct(Actor())
    )
    assert after.options == before.options
    assert after.revision == before.revision


async def test_an_unregistered_source_is_still_rejected_as_a_value_error() -> None:
    _, replacement = catalogues()
    with pytest.raises(ValueError, match="unknown submission option source"):
        await replacement.options("untrusted", "door", locale="en", context=ActingContext.direct(Actor()))


async def test_a_queried_source_is_not_readable_as_form_options() -> None:
    """A form needs the whole set; `builds` has no whole set to give."""
    _, replacement = catalogues()
    with pytest.raises(ValueError, match="unknown submission option source"):
        await replacement.options("builds", "door", locale="en", context=ActingContext.direct(Actor()))
