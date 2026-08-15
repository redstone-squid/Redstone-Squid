"""Form options served from the suggestion registry.

Drafts pin a schema revision, and option revisions are content-addressed, so this must produce
byte-identical revisions to the catalogue it replaces. A revision that moved for no reason would
look to every client like the option set had changed.
"""

from collections.abc import Sequence
from typing import Any, cast

import pytest

from squid.submissions.infrastructure.options import ApprovedSubmissionOptionCatalog
from squid.submissions.infrastructure.suggestion_options import SuggestionFormOptionCatalog
from squid.suggestions.application import SuggestionService
from squid.suggestions.infrastructure.catalogue import build_registry
from squid.suggestions.infrastructure.repository import TaxonomyEntry
from squid.tags.domain import (
    TagAuthority,
    TagDefinition,
    TagModerationStatus,
    TagSemanticKind,
    TagValueType,
)
from squid.versions.domain import MinecraftVersion

TAGS = (
    ("seamless", "Seamless", TagSemanticKind.PATTERN),
    ("full_lamp", "Full Lamp", TagSemanticKind.PATTERN),
    ("locational", "Locational", TagSemanticKind.RESTRICTION),
    ("directional", "Directional", TagSemanticKind.RESTRICTION),
    ("showcase_fast", "Fast", TagSemanticKind.SHOWCASE),
)

VERSIONS = (
    MinecraftVersion("Java", 1, 21, 5),
    MinecraftVersion("Java", 1, 21, 4),
    MinecraftVersion("Bedrock", 1, 21, 50),
)


def _definition(index: int, key: str, name: str, kind: TagSemanticKind) -> TagDefinition:
    return TagDefinition(
        id=index,
        stable_key=key,
        display_name=name,
        authority=TagAuthority.OFFICIAL if kind is not TagSemanticKind.SHOWCASE else TagAuthority.USER,
        semantic_kind=kind,
        value_type=TagValueType.NONE,
        moderation_status=TagModerationStatus.APPROVED,
    )


class FakeTags:
    async def public_definitions(self) -> tuple[TagDefinition, ...]:
        return tuple(_definition(index, *tag) for index, tag in enumerate(TAGS, start=1))

    async def pending(self) -> tuple[TagDefinition, ...]:
        return ()


class FakeVersions:
    async def list_all(self) -> tuple[MinecraftVersion, ...]:
        return VERSIONS


class FakeSuggestionRepository:
    """Serves the same rows the tag service does, in an arbitrary order.

    Deliberately unsorted: the ordering that decides the revision has to come from the provider,
    not from whatever order the database happened to return.
    """

    async def taxonomy(
        self,
        semantic_kind: str,
        *,
        build_kind: str | None = None,
        authority: str | None = "official",
    ) -> Sequence[TaxonomyEntry]:
        del build_kind, authority
        return [
            TaxonomyEntry(id=index, stable_key=key, display_name=name, semantic_kind=semantic_kind)
            for index, (key, name, kind) in enumerate(reversed(TAGS), start=1)
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
async def test_taxonomy_options_match_the_catalogue_they_replace(source: str) -> None:
    existing, replacement = catalogues()
    before = await existing.options(source, "door", locale="en")
    after = await replacement.options(source, "door", locale="en")
    assert after.options == before.options
    assert after.revision == before.revision


async def test_version_options_match_the_catalogue_they_replace() -> None:
    existing, replacement = catalogues()
    before = await existing.options("approved_source_versions", "door", locale="en")
    after = await replacement.options("approved_source_versions", "door", locale="en")
    assert after.options == before.options
    assert after.revision == before.revision


async def test_an_unregistered_source_is_still_rejected_as_a_value_error() -> None:
    _, replacement = catalogues()
    with pytest.raises(ValueError, match="unknown submission option source"):
        await replacement.options("untrusted", "door", locale="en")


async def test_a_queried_source_is_not_readable_as_form_options() -> None:
    """A form needs the whole set; `builds` has no whole set to give."""
    _, replacement = catalogues()
    with pytest.raises(ValueError, match="unknown submission option source"):
        await replacement.options("builds", "door", locale="en")
