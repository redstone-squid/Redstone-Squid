"""Round-trip coverage pinning the build persistence semantics.

This is the safety net for the build aggregate redesign: the door/extender field
lists currently exist in four hand-maintained copies across the mapper and the
repository, and nothing else ties them together. Every test here runs against a
migrated PostgreSQL database, exercising the real write path
(`apply_build_taxonomy` + `BuildRepository.save`) and the real read path
(`BuildMapper.to_domain`).

Taxonomy names are resolved into tag assignments at edit time by
``apply_build_taxonomy``, which canonicalizes the typed restriction fields and
records unresolvable or ambiguous names in ``extra_info``. After it runs,
save→load is the identity (up to database-generated timestamps), and ``save()``
itself persists ``build.tags`` verbatim without interpreting the string fields.

One asymmetry remains characterized for a later phase: missing door dimensions
are silently defaulted to 1x2 on write.
"""

from dataclasses import fields as dataclass_fields
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.infrastructure.models import Account
from squid.builds.application.taxonomy import apply_build_taxonomy
from squid.builds.domain import (
    BUILD_CLASS_BY_CATEGORY,
    Build,
    BuildCategory,
    BuildLink,
    DoorBuild,
    ExtenderBuild,
    OriginalMessage,
    OtherBuild,
    Status,
)
from squid.builds.infrastructure.repository import BuildRepository
from squid.builds.infrastructure.taxonomy import OfficialTagResolver
from squid.settings.infrastructure.models import ServerSetting
from squid.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.tags.infrastructure.models import TagAlias, TagApplicability, TagDefinition
from squid.versions.infrastructure.models import Version

# Canonical display names of the official taxonomy seeded below, one per
# restriction bucket plus two patterns. Tests that expect a clean round trip
# must draw restriction/pattern names from these.
KNOWN_RESTRICTIONS = {
    "wiring-placement": "Seamless",
    "animated": "Instant",
    "component": "Observerless",
    "miscellaneous": "Locational",
}
KNOWN_PATTERNS = ("Regular", "Full Lamp")
OBSERVERLESS_ALIAS = "No Observers"
AMBIGUOUS_ALIAS = "Pistonless"

_ALL_BUILD_KINDS = tuple(category.value for category in BuildCategory)


def _official_tag(
    *,
    stable_key: str,
    display_name: str,
    semantic_kind: TagSemanticKind,
    restriction_type: str | None = None,
    aliases: tuple[str, ...] = (),
) -> TagDefinition:
    return TagDefinition(
        stable_key=stable_key,
        display_name=display_name,
        normalized_name=" ".join(display_name.casefold().split()),
        authority=TagAuthority.OFFICIAL,
        semantic_kind=semantic_kind,
        restriction_type=restriction_type,
        value_type=TagValueType.NONE,
        moderation_status=TagModerationStatus.APPROVED,
        aliases=[TagAlias(alias=alias, normalized_alias=" ".join(alias.casefold().split())) for alias in aliases],
        applicabilities=[TagApplicability(build_kind=kind) for kind in _ALL_BUILD_KINDS],
    )


async def _seed_catalogue(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Seed one account, one version, one server, and the official taxonomy."""
    async with session_factory.begin() as session:
        account = Account()
        session.add(account)
        session.add(ServerSetting(server_id=4000))
        session.add(
            Version(
                edition="Java",
                major_version=1,
                minor_version=21,
                patch_number=0,
                data_version=3953,
            )
        )
        session.add_all(
            [
                _official_tag(
                    stable_key="seamless",
                    display_name="Seamless",
                    semantic_kind=TagSemanticKind.RESTRICTION,
                    restriction_type="wiring-placement",
                ),
                _official_tag(
                    stable_key="instant",
                    display_name="Instant",
                    semantic_kind=TagSemanticKind.RESTRICTION,
                    restriction_type="animated",
                ),
                _official_tag(
                    stable_key="observerless",
                    display_name="Observerless",
                    semantic_kind=TagSemanticKind.RESTRICTION,
                    restriction_type="component",
                    aliases=(OBSERVERLESS_ALIAS,),
                ),
                _official_tag(
                    stable_key="locational",
                    display_name="Locational",
                    semantic_kind=TagSemanticKind.RESTRICTION,
                    restriction_type="miscellaneous",
                ),
                _official_tag(
                    stable_key="pattern-regular",
                    display_name="Regular",
                    semantic_kind=TagSemanticKind.PATTERN,
                ),
                _official_tag(
                    stable_key="pattern-full-lamp",
                    display_name="Full Lamp",
                    semantic_kind=TagSemanticKind.PATTERN,
                ),
                # Two component restrictions sharing one alias: requesting the
                # alias matches both, so resolution treats it as ambiguous.
                _official_tag(
                    stable_key="pistonless-a",
                    display_name="Pistonless (sticky)",
                    semantic_kind=TagSemanticKind.RESTRICTION,
                    restriction_type="component",
                    aliases=(AMBIGUOUS_ALIAS,),
                ),
                _official_tag(
                    stable_key="pistonless-b",
                    display_name="Pistonless (regular)",
                    semantic_kind=TagSemanticKind.RESTRICTION,
                    restriction_type="component",
                    aliases=(AMBIGUOUS_ALIAS,),
                ),
            ]
        )
        await session.flush()
        return account.id


def _make_build(category: BuildCategory, account_id: int) -> Build:
    """A fully populated build whose taxonomy resolves without loss."""
    common: dict[str, Any] = {
        "submission_status": Status.PENDING,
        "record_category": "Smallest",
        "submitter_account_id": account_id,
        "versions": ["Java 1.21.0"],
        "version_spec": "1.21+",
        "width": 5,
        "height": 7,
        "depth": 3,
        "wiring_placement_restrictions": [KNOWN_RESTRICTIONS["wiring-placement"]],
        "animated_restrictions": [KNOWN_RESTRICTIONS["animated"]],
        "component_restrictions": [KNOWN_RESTRICTIONS["component"]],
        "miscellaneous_restrictions": [KNOWN_RESTRICTIONS["miscellaneous"]],
        "extra_info": {"user": "Some additional context"},
        "creators_ign": ["Alice", "Bob"],
        "links": [
            BuildLink(url="https://example.com/a.png", media_type="image"),
            BuildLink(url="https://example.com/a.mp4", media_type="video"),
            BuildLink(url="https://example.com/world.zip", media_type="world-download"),
            BuildLink(url="https://example.com/a.schem", media_type="schematic"),
            BuildLink(url="https://example.com/render.png", media_type="render"),
        ],
        "display_name": "Round trip probe",
        "completion_time": "~2 seconds",
        "completion_evidence": "https://example.com/evidence",
        "description": "A build with every mappable field populated.",
        "ai_generated": False,
    }
    if category is BuildCategory.DOOR:
        return DoorBuild(
            **common,
            patterns=["Full Lamp"],
            door_width=2,
            door_height=3,
            door_depth=1,
            orientation="Trapdoor",
            normal_opening_time=12,
            normal_closing_time=15,
            visible_opening_time=10,
            visible_closing_time=13,
        )
    if category is BuildCategory.EXTENDER:
        return ExtenderBuild(
            **common,
            patterns=["Full Lamp"],
            orientation="Upward",
            extension_length=6,
            extender_type="Regular",
        )
    return BUILD_CLASS_BY_CATEGORY[category](**common)


# Fields whose lists are rebuilt from tag rows on read; ordering is not part of
# the contract, so they compare as sets.
_SET_COMPARED = {
    "wiring_placement_restrictions",
    "animated_restrictions",
    "component_restrictions",
    "miscellaneous_restrictions",
    "patterns",
    "creators_ign",
    "links",
}
# The ORM fills submission_time at insert (default_factory=Instant.now), so a
# None on the domain object does not survive the trip. save() likewise stamps
# edited_time with a nanosecond Instant.now() that PostgreSQL truncates to
# microseconds, so the caller's in-memory value never equals the stored one.
_DB_GENERATED = {"submission_time", "edited_time"}
# Compared structurally below rather than by dataclass equality.
_TAG_FIELDS = {"tags"}


def _assert_round_trip(saved: Build, loaded: Build) -> None:
    """Field-for-field comparison over the dataclass, with labelled exceptions."""
    assert type(loaded) is type(saved), "category subclass did not round-trip"
    for field in dataclass_fields(saved):
        name = field.name
        saved_value = getattr(saved, name)
        loaded_value = getattr(loaded, name)
        if name in _DB_GENERATED:
            assert loaded_value is not None, f"{name} should be database-generated"
        elif name in _SET_COMPARED:
            assert set(loaded_value) == set(saved_value), f"{name} did not round-trip"
        elif name in _TAG_FIELDS:
            saved_tags = {(a.definition.stable_key, a.value) for a in saved_value}
            loaded_tags = {(a.definition.stable_key, a.value) for a in loaded_value}
            assert loaded_tags == saved_tags, "tag assignments did not round-trip"
        else:
            assert loaded_value == saved_value, f"{name} did not round-trip"


async def _resolve_and_save(
    session_factory: async_sessionmaker[AsyncSession],
    repository: BuildRepository,
    build: Build,
) -> None:
    """Persist the way the application service does: resolve taxonomy, then save."""
    await apply_build_taxonomy(build, OfficialTagResolver(session_factory))
    await repository.save(build)


@pytest.mark.parametrize("category", list(BuildCategory), ids=lambda category: category.value)
async def test_round_trip_is_identity_for_known_taxonomy(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    category: BuildCategory,
) -> None:
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = BuildRepository(migrated_session_factory)

    build = _make_build(category, account_id)
    await _resolve_and_save(migrated_session_factory, repository, build)
    assert build.id is not None

    loaded = await repository.get_by_id(build.id)
    assert loaded is not None
    _assert_round_trip(build, loaded)


@pytest.mark.parametrize("category", list(BuildCategory), ids=lambda category: category.value)
async def test_update_round_trip_preserves_category_fields(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    category: BuildCategory,
) -> None:
    """The update path (`_update_existing`) copies the same field lists as insert."""
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = BuildRepository(migrated_session_factory)

    build = _make_build(category, account_id)
    await _resolve_and_save(migrated_session_factory, repository, build)
    assert build.id is not None

    build.description = "Edited description"
    build.width = 9
    if isinstance(build, DoorBuild):
        build.door_width = 4
        build.normal_opening_time = 20
    elif isinstance(build, ExtenderBuild):
        build.extension_length = 11
    await _resolve_and_save(migrated_session_factory, repository, build)

    loaded = await repository.get_by_id(build.id)
    assert loaded is not None
    _assert_round_trip(build, loaded)


async def test_alias_resolves_to_canonical_display_name(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A restriction submitted under an alias canonicalizes at edit time."""
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = BuildRepository(migrated_session_factory)

    build = _make_build(BuildCategory.UTILITY, account_id)
    build.component_restrictions = [OBSERVERLESS_ALIAS]
    await _resolve_and_save(migrated_session_factory, repository, build)
    assert build.id is not None

    # apply_build_taxonomy rewrote the field to the canonical display name.
    assert build.component_restrictions == ["Observerless"]
    loaded = await repository.get_by_id(build.id)
    assert loaded is not None
    assert loaded.component_restrictions == ["Observerless"]


async def test_original_message_round_trips(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = BuildRepository(migrated_session_factory)

    build = _make_build(BuildCategory.OTHER, account_id)
    frozen = OtherBuild(
        submission_status=build.submission_status,
        submitter_account_id=account_id,
        versions=["Java 1.21.0"],
        original_message=OriginalMessage(
            message_id=6000,
            server_id=4000,
            channel_id=5000,
            author_id=7000,
            content="the original submission text",
        ),
    )
    await repository.save(frozen)
    assert frozen.id is not None

    loaded = await repository.get_by_id(frozen.id)
    assert loaded is not None
    assert loaded.original_message is not None
    assert loaded.original_message.server_id == 4000
    assert loaded.original_message.channel_id == 5000
    assert loaded.original_message.message_id == 6000
    assert loaded.original_message.author_id == 7000
    assert loaded.original_message.content == "the original submission text"


# --- Edit-time taxonomy handling ---------------------------------------------


async def test_unknown_restriction_is_recorded_at_edit_time_and_round_trips(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = BuildRepository(migrated_session_factory)

    build = _make_build(BuildCategory.UTILITY, account_id)
    build.component_restrictions = ["Imaginary Component"]
    await apply_build_taxonomy(build, OfficialTagResolver(migrated_session_factory))

    # The unresolvable name moved into extra_info before anything was saved,
    # and the typed field was canonicalized, so save→load stays the identity.
    assert build.extra_info["unknown_restrictions"] == {"component_restrictions": ["Imaginary Component"]}
    assert build.component_restrictions == []

    await repository.save(build)
    assert build.id is not None
    loaded = await repository.get_by_id(build.id)
    assert loaded is not None
    _assert_round_trip(build, loaded)


async def test_ambiguous_restriction_is_recorded_as_unknown(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = BuildRepository(migrated_session_factory)

    build = _make_build(BuildCategory.UTILITY, account_id)
    # Matches two official definitions via their shared alias, so it resolves
    # to neither and is recorded like an unknown.
    build.component_restrictions = [AMBIGUOUS_ALIAS]
    await _resolve_and_save(migrated_session_factory, repository, build)
    assert build.id is not None

    loaded = await repository.get_by_id(build.id)
    assert loaded is not None
    assert loaded.component_restrictions == []
    assert loaded.extra_info["unknown_restrictions"] == {"component_restrictions": [AMBIGUOUS_ALIAS]}


async def test_save_persists_tags_verbatim_without_mutating_input(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The repository no longer interprets the restriction string fields."""
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = BuildRepository(migrated_session_factory)

    build = _make_build(BuildCategory.DOOR, account_id)
    build.patterns = []
    await repository.save(build)

    # Without apply_build_taxonomy, the strings are not resolved, no default
    # pattern is injected, and the input is not mutated.
    assert build.patterns == []
    assert build.tags == []

    assert build.id is not None
    loaded = await repository.get_by_id(build.id)
    assert loaded is not None
    assert loaded.tags == []
    assert loaded.patterns == []


async def test_apply_build_taxonomy_defaults_door_pattern(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = BuildRepository(migrated_session_factory)

    build = _make_build(BuildCategory.DOOR, account_id)
    build.patterns = []
    await apply_build_taxonomy(build, OfficialTagResolver(migrated_session_factory))

    # The default pattern is materialized visibly at edit time.
    assert build.patterns == ["Regular"]
    assert {assignment.definition.stable_key for assignment in build.tags} >= {"pattern-regular"}

    await repository.save(build)
    assert build.id is not None
    loaded = await repository.get_by_id(build.id)
    assert loaded is not None
    assert loaded.patterns == ["Regular"]


async def test_replacing_links_of_one_media_type_moves_a_shared_url(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The schema keys links by (build_id, url), and the model now matches it.

    Under the five parallel url lists a caller could place one URL under two
    media types, a state the database rejected only at save time. With a single
    typed collection, re-adding the URL as a video simply moves it.
    """
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = BuildRepository(migrated_session_factory)

    build = _make_build(BuildCategory.OTHER, account_id)
    build.replace_links("image", ["https://example.com/dual.png"])
    build.replace_links("video", ["https://example.com/dual.png"])
    build.replace_links("image", [])
    await _resolve_and_save(migrated_session_factory, repository, build)

    assert build.id is not None
    loaded = await repository.get_by_id(build.id)
    assert loaded is not None
    assert loaded.image_urls == ()
    assert loaded.video_urls == ("https://example.com/dual.png",)


async def test_door_dimension_defaults_are_declared_not_coerced_at_save(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Door dimensions default on the entity, so save has nothing to coerce.

    They used to be optional on the flat model and were silently forced to 1x2
    during the write; DoorBuild declares them as required ints with those
    defaults instead.
    """
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = BuildRepository(migrated_session_factory)

    build = DoorBuild(
        submission_status=Status.PENDING,
        submitter_account_id=account_id,
        versions=["Java 1.21.0"],
        # The column is NOT NULL, so save() normalizes None to False; setting it
        # keeps this test about door dimensions.
        ai_generated=False,
    )
    assert (build.door_width, build.door_height) == (1, 2)
    await _resolve_and_save(migrated_session_factory, repository, build)
    assert build.id is not None

    loaded = await repository.get_by_id(build.id)
    assert loaded is not None
    _assert_round_trip(build, loaded)


# --- Property-based round trip -----------------------------------------------


# The pools are disjoint per media type: build_links' primary key is
# (build_id, url), so one URL cannot carry two media types on the same build.
_IMAGE_URLS = st.lists(
    st.sampled_from(["https://example.com/one.png", "https://example.com/two.png"]),
    max_size=2,
    unique=True,
)
_VIDEO_URLS = st.lists(
    st.sampled_from(["https://example.com/one.mp4", "https://example.com/two.mp4"]),
    max_size=2,
    unique=True,
)


@st.composite
def _build_values(draw: st.DrawFn) -> tuple[BuildCategory, dict[str, object]]:
    category = draw(st.sampled_from(list(BuildCategory)))
    values: dict[str, object] = {
        "width": draw(st.none() | st.integers(min_value=1, max_value=100)),
        "height": draw(st.none() | st.integers(min_value=1, max_value=100)),
        "depth": draw(st.none() | st.integers(min_value=1, max_value=100)),
        "description": draw(st.none() | st.text(st.characters(codec="utf-8", exclude_categories=("C",)), max_size=80)),
        "creators_ign": draw(st.lists(st.sampled_from(["Alice", "Bob", "Charlie"]), max_size=3, unique=True)),
        "links": [
            *(BuildLink(url=url, media_type="image") for url in draw(_IMAGE_URLS)),
            *(BuildLink(url=url, media_type="video") for url in draw(_VIDEO_URLS)),
        ],
        "component_restrictions": draw(
            st.lists(st.sampled_from([KNOWN_RESTRICTIONS["component"]]), max_size=1, unique=True)
        ),
        "miscellaneous_restrictions": draw(
            st.lists(st.sampled_from([KNOWN_RESTRICTIONS["miscellaneous"]]), max_size=1, unique=True)
        ),
        "ai_generated": draw(st.booleans()),
    }
    if category is BuildCategory.DOOR:
        values["door_width"] = draw(st.integers(min_value=1, max_value=10))
        values["door_height"] = draw(st.integers(min_value=1, max_value=10))
        values["orientation"] = draw(st.sampled_from(["Door", "Skydoor", "Trapdoor"]))
        values["patterns"] = draw(st.lists(st.sampled_from(list(KNOWN_PATTERNS)), max_size=2, unique=True))
        values["normal_opening_time"] = draw(st.none() | st.integers(min_value=0, max_value=10_000))
    elif category is BuildCategory.EXTENDER:
        values["extension_length"] = draw(st.none() | st.integers(min_value=1, max_value=30))
        values["patterns"] = draw(st.lists(st.sampled_from(list(KNOWN_PATTERNS)), max_size=2, unique=True))
    return category, values


@pytest.fixture
async def seeded_factory(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> tuple[async_sessionmaker[AsyncSession], int]:
    """One seeded database shared by every hypothesis example of a test."""
    account_id = await _seed_catalogue(migrated_session_factory)
    return migrated_session_factory, account_id


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(values=_build_values())
async def test_round_trip_property(
    seeded_factory: tuple[async_sessionmaker[AsyncSession], int],
    values: tuple[BuildCategory, dict[str, object]],
) -> None:
    """Any build drawn from resolvable taxonomy survives save→load intact.

    Examples share one migrated database per test run; every example inserts a
    fresh row, so state never leaks between examples.
    """
    session_factory, account_id = seeded_factory
    repository = BuildRepository(session_factory)
    category, field_values = values

    build = BUILD_CLASS_BY_CATEGORY[category](
        submission_status=Status.PENDING,
        submitter_account_id=account_id,
        versions=["Java 1.21.0"],
        **field_values,  # type: ignore[arg-type]
    )
    await _resolve_and_save(session_factory, repository, build)
    assert build.id is not None

    loaded = await repository.get_by_id(build.id)
    assert loaded is not None
    _assert_round_trip(build, loaded)
