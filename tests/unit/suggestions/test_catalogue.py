"""Registered suggestion source tests.

The registry is a contract between three surfaces, so what it publishes is checked here rather
than discovered when a Discord dropdown silently comes back empty.
"""

from typing import Any, cast

import pytest

from squid.submissions.infrastructure.options import _SOURCES as FORM_OPTION_SOURCES
from squid.suggestions.application import SuggestionRegistry
from squid.suggestions.domain import SourceKind, Visibility
from squid.suggestions.infrastructure.catalogue import build_registry


def registry(*, discord: bool = False) -> SuggestionRegistry:
    """Build a registry with stub dependencies; nothing here touches the database.

    Sources are constructed eagerly but only query when asked, so the whole catalogue can be
    inspected without a database or any real service behind it.
    """
    stub = cast(Any, object())
    extras: dict[str, Any] = (
        {"starboards": stub, "permission_roles": stub, "notifications": stub, "accounts": stub} if discord else {}
    )
    return build_registry(repository=stub, search=stub, versions=stub, tags=stub, **extras)


def test_the_api_registry_omits_gateway_only_sources() -> None:
    """The API process has no starboards, so those sources are absent rather than broken."""
    api = registry()
    assert "starboard_names" not in api
    assert "permission_roles" not in api
    assert "approved_restrictions" in api


def test_the_discord_registry_adds_the_guild_scoped_sources() -> None:
    discord = registry(discord=True)
    assert "starboard_names" in discord
    assert "permission_roles" in discord
    assert "alias_claims_pending" in discord


def test_every_form_option_source_is_also_a_suggestion_source() -> None:
    """The two catalogues share a namespace, so a form field's options are always completable."""
    published = registry(discord=True)
    for source in (*FORM_OPTION_SOURCES, "approved_source_versions"):
        assert source in published, source


def test_guild_scoped_sources_declare_the_context_they_need() -> None:
    """Without this, a missing context resolver would answer from the wrong guild."""
    discord = registry(discord=True)
    for source_id in ("starboard_names", "permission_roles"):
        assert discord.resolve(source_id).context_keys == frozenset({"guild_id"})


def test_sources_holding_unreviewed_data_are_gated() -> None:
    discord = registry(discord=True)
    for source_id in ("builds_pending", "tags_pending", "alias_claims_pending"):
        source = discord.resolve(source_id)
        assert source.visibility is Visibility.REQUIRES_NODE
        assert source.required_node


def test_public_build_suggestions_cannot_leak_unconfirmed_submissions() -> None:
    """`builds` and `builds_pending` must not be interchangeable."""
    published = registry(discord=True)
    assert published.resolve("builds").visibility is Visibility.PUBLIC
    assert published.resolve("builds_pending").visibility is Visibility.REQUIRES_NODE


def test_enumerable_sources_are_the_ones_that_can_be_listed() -> None:
    enumerable = {source.id for source in registry(discord=True).enumerable()}
    assert "approved_restrictions" in enumerable
    # Builds are unbounded, so they must not be advertised as fully listable or cached.
    assert "builds" not in enumerable


@pytest.mark.parametrize(
    "source_id",
    ["builds", "builds_pending", "restriction_ids", "showcase_tag_ids", "version_ids", "tags_pending"],
)
def test_id_valued_sources_are_declared_integer(source_id: str) -> None:
    """A slash parameter typed as an integer rejects a string choice value."""
    from squid.suggestions.domain import ValueType

    assert registry(discord=True).resolve(source_id).value_type is ValueType.INTEGER


def test_source_ids_are_unique_and_well_formed() -> None:
    published = registry(discord=True)
    assert len(published) == len({source.id for source in published})


def test_enumerable_sources_are_not_viewer_scoped() -> None:
    """A cached, revisioned set cannot depend on who asked for it."""
    for source in registry(discord=True).enumerable():
        assert source.visibility is not Visibility.VIEWER_SCOPED
        assert source.kind is SourceKind.ENUMERABLE
