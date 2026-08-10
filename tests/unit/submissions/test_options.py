import pytest

from squid.submissions.application import CheckedInFormManifestRegistry
from squid.submissions.infrastructure.options import ApprovedTagOptionCatalog
from squid.tags.domain import (
    TagAuthority,
    TagDefinition,
    TagModerationStatus,
    TagSemanticKind,
    TagValueType,
)


class FakeTags:
    async def public_definitions(self) -> tuple[TagDefinition, ...]:
        return (
            _tag(1, "seamless", "Seamless", TagSemanticKind.PATTERN),
            _tag(2, "locational", "Locational", TagSemanticKind.RESTRICTION, restriction_type="door"),
            _tag(3, "directional", "Directional", TagSemanticKind.RESTRICTION, restriction_type="component"),
            _tag(4, "showcase_fast", "Fast", TagSemanticKind.SHOWCASE),
        )


def _tag(
    tag_id: int,
    key: str,
    name: str,
    kind: TagSemanticKind,
    *,
    restriction_type: str | None = None,
) -> TagDefinition:
    return TagDefinition(
        id=tag_id,
        stable_key=key,
        display_name=name,
        authority=TagAuthority.OFFICIAL,
        semantic_kind=kind,
        value_type=TagValueType.NONE,
        moderation_status=TagModerationStatus.APPROVED,
        restriction_type=restriction_type,
    )


@pytest.mark.asyncio
async def test_option_catalog_uses_stable_keys_and_category_filters() -> None:
    catalog = ApprovedTagOptionCatalog(FakeTags())

    door = await catalog.options("approved_restrictions", "door", locale="en")
    other = await catalog.options("approved_restrictions", "other", locale="en")

    assert [option.value for option in door.options] == ["directional", "locational"]
    assert [option.value for option in other.options] == ["directional", "locational"]
    assert door.revision > 0
    assert door.revision == (await catalog.options("approved_restrictions", "door", locale="de")).revision


@pytest.mark.asyncio
async def test_checked_in_registry_resolves_only_exact_revision() -> None:
    registry = CheckedInFormManifestRegistry()
    current = await registry.current(locale="en")

    assert await registry.get(current.schema_id, current.revision, locale="en") == current
    assert await registry.get(current.schema_id, current.revision + 1, locale="en") is None


@pytest.mark.asyncio
async def test_unknown_option_source_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown submission option source"):
        await ApprovedTagOptionCatalog(FakeTags()).options("untrusted", "door", locale="en")
