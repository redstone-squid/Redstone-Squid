"""Build persistence and edit-time taxonomy resolution against migrated PostgreSQL.

Resolve taxonomy names before saving: known names become tag assignments, while unknown or
ambiguous names are retained in extra_info. Compare saved and reloaded category fields,
including the relationships and enum types that ordinary scalar equality can miss.
"""

# ruff: noqa: RUF001, RUF002  The case-folding regression below is about confusable
# characters; they are its inputs, not typos.

from dataclasses import fields as dataclass_fields
from functools import partial
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.models import Account, AccountIdentity, CreatorAlias
from squid.builds.application.taxonomy import apply_build_taxonomy, replace_showcase_tags
from squid.builds.application.taxonomy_service import BuildTaxonomyService
from squid.builds.domain import BuildCategory, Status
from squid.builds.domain.credits import name_credits
from squid.builds.infrastructure.repository import BuildRepository
from squid.builds.models import BUILD_CLASS_BY_CATEGORY, Build, BuildLink, DoorBuild, ExtenderBuild, OtherBuild
from squid.core.errors import InvalidStateError
from squid.messages.domain import MessageRef, MessageSnapshot
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.settings.models import ServerSetting
from squid.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.tags.models import TagAlias, TagApplicability, TagDefinition
from squid.unit_of_work import UnitOfWorkFactory
from squid.versions.models import Version
from tests.support.build_edits import build_edit_session
from tests.support.build_edits import transact_build_edit as transact

# Canonical display names of the official taxonomy seeded below, one per
# restriction type plus two patterns. Tests that expect a clean round trip
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
    async with build_edit_session(session_factory) as session:
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
                _official_tag(stable_key="compact", display_name="Compact", semantic_kind=TagSemanticKind.SHOWCASE),
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
        "credits": name_credits(["Alice", "Bob"], legacy=True),
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


# Compare relationship-derived lists without requiring the database to preserve input order.
_SET_COMPARED = {
    "wiring_placement_restrictions",
    "animated_restrictions",
    "component_restrictions",
    "miscellaneous_restrictions",
    "patterns",
    "creators_ign",
    "links",
}
# Require a submission timestamp without comparing its initial value.
_DB_GENERATED = {"submission_time"}
# Compared structurally below rather than by dataclass equality.
_TAG_FIELDS = {"tags"}


def _assert_round_trip(saved: Build, loaded: Build) -> None:
    """Field-for-field comparison over the dataclass, with labelled exceptions."""
    assert type(loaded) is type(saved), "category subclass did not round-trip"
    for field in dataclass_fields(saved):
        if not field.compare:
            continue
        name = field.name
        saved_value = getattr(saved, name)
        loaded_value = getattr(loaded, name)
        if name in _DB_GENERATED:
            assert loaded_value is not None, f"{name} should be database-generated"
        elif isinstance(saved_value, Instant):
            assert loaded_value == saved_value.round("microsecond", mode="floor"), f"{name} did not round-trip"
        elif name == "links":
            assert {(link.url, link.media_type) for link in loaded_value} == {
                (link.url, link.media_type) for link in saved_value
            }
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
    repository: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    build: Build,
) -> None:
    """Persist the way the application service does: resolve taxonomy, then save."""
    await apply_build_taxonomy(build, BuildTaxonomyService(uow_factory=partial(SqlAlchemyUnitOfWork, session_factory)))
    await transact(
        repository,
        lambda uow: uow.builds.save(build),
        commit=True,
    )


@pytest.mark.parametrize("category", list(BuildCategory), ids=lambda category: category.value)
async def test_round_trip_is_identity_for_known_taxonomy(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    category: BuildCategory,
) -> None:
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    build = _make_build(category, account_id)
    await _resolve_and_save(migrated_session_factory, repository, build)
    assert build.id is not None

    loaded = await transact(repository, lambda uow: uow.builds.get_by_id(build.id))
    assert loaded is not None
    _assert_round_trip(build, loaded)


async def test_attachment_enrichment_evidence_round_trips_as_nested_json(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    build = _make_build(BuildCategory.DOOR, account_id)
    build.extra_info["schematic_duplicates"] = [
        {
            "build_id": 17,
            "title": "Compact candidate",
            "tier": "identical",
            "footprint_distance": 0.0,
            "source_attachments": [
                {"attachment_id": "101", "filename": "first.litematic"},
                {"attachment_id": "102", "filename": "copy.litematic"},
            ],
        }
    ]
    build.extra_info["attachment_failures"] = [
        {
            "attachment_id": "103",
            "filename": "unchecked.schem",
            "stage": "duplicate-check",
            "detail": "Duplicate lookup was unavailable.",
        },
        {
            "attachment_id": "104",
            "filename": "unstored.schem",
            "stage": "record",
            "detail": "Analysis persistence was unavailable.",
        },
    ]

    await _resolve_and_save(migrated_session_factory, repository, build)
    assert build.id is not None

    loaded = await transact(repository, lambda uow: uow.builds.get_by_id(build.id))

    assert loaded is not None
    assert loaded.extra_info == build.extra_info


async def test_loaded_status_is_a_status_member(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Check identity as well as equality: Status is an IntEnum.

    A stored integer compares equal to its enum member but lacks .name and fails
    identity checks used by callers.
    """
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    build = _make_build(BuildCategory.DOOR, account_id)
    await _resolve_and_save(migrated_session_factory, repository, build)
    assert build.id is not None

    loaded = await transact(repository, lambda uow: uow.builds.get_by_id(build.id))
    assert loaded is not None
    assert loaded.submission_status is Status.PENDING

    await transact(
        repository,
        lambda uow: uow.builds.confirm(loaded),
        commit=True,
    )
    reloaded = await transact(repository, lambda uow: uow.builds.get_by_id(build.id))
    assert reloaded is not None
    assert reloaded.submission_status is Status.CONFIRMED


async def test_persisting_a_build_creates_no_account(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Saving a build for an existing account must not create accounts or identities."""
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    async with migrated_session_factory() as session:
        before = await session.scalar(select(func.count()).select_from(Account))

    await _resolve_and_save(migrated_session_factory, repository, _make_build(BuildCategory.DOOR, account_id))

    async with migrated_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Account)) == before
        assert await session.scalar(select(func.count()).select_from(AccountIdentity)) == 0


async def test_persisting_a_build_for_an_unknown_account_is_refused(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_catalogue(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    with pytest.raises(InvalidStateError):
        await _resolve_and_save(migrated_session_factory, repository, _make_build(BuildCategory.DOOR, 999_999))


@pytest.mark.parametrize("category", list(BuildCategory), ids=lambda category: category.value)
async def test_update_round_trip_preserves_category_fields(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    category: BuildCategory,
) -> None:
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

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
        build.extension_time = 30
    await _resolve_and_save(migrated_session_factory, repository, build)

    loaded = await transact(repository, lambda uow: uow.builds.get_by_id(build.id))
    assert loaded is not None
    _assert_round_trip(build, loaded)


async def test_alias_resolves_to_canonical_display_name(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A restriction submitted under an alias canonicalizes at edit time."""
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    build = _make_build(BuildCategory.UTILITY, account_id)
    build.component_restrictions = [OBSERVERLESS_ALIAS]
    await _resolve_and_save(migrated_session_factory, repository, build)
    assert build.id is not None

    # apply_build_taxonomy rewrote the field to the canonical display name.
    assert build.component_restrictions == ["Observerless"]
    loaded = await transact(repository, lambda uow: uow.builds.get_by_id(build.id))
    assert loaded is not None
    assert loaded.component_restrictions == ["Observerless"]


async def test_source_messages_round_trip(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    build = _make_build(BuildCategory.OTHER, account_id)
    frozen = OtherBuild(
        submission_status=build.submission_status,
        submitter_account_id=account_id,
        versions=["Java 1.21.0"],
        source_messages=(
            MessageSnapshot(
                id=6000,
                guild_id=4000,
                channel_id=5000,
                author_id=7000,
                content="the original submission text",
                created_at=Instant.from_utc(2026, 11, 9),
            ),
            MessageSnapshot(id=6001, guild_id=4000, channel_id=5000, author_id=7000, content="a follow-up"),
        ),
    )
    await transact(
        repository,
        lambda uow: uow.builds.save(frozen),
        commit=True,
    )
    assert frozen.id is not None

    loaded = await transact(repository, lambda uow: uow.builds.get_by_id(frozen.id))
    assert loaded is not None
    assert loaded.source_messages == (
        MessageRef(6000, channel_id=5000, guild_id=4000),
        MessageRef(6001, channel_id=5000, guild_id=4000),
    )
    # The submission link points at the request, not at a trailing image.
    assert loaded.original_link == "https://discord.com/channels/4000/5000/6000"
    stored = await transact(repository, lambda uow: uow.messages.get_by_id(6000))
    assert stored is not None
    assert stored.author_id == 7000
    assert stored.content == "the original submission text"
    assert stored.created_at == Instant.from_utc(2026, 11, 9)


async def test_saving_a_build_keeps_newer_source_message_content(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A build edit must not roll back a Discord edit recorded after the build was loaded."""
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    source = MessageSnapshot(id=6100, guild_id=4000, channel_id=5000, author_id=7000, content="original")
    build = OtherBuild(
        submission_status=Status.PENDING,
        submitter_account_id=account_id,
        versions=["Java 1.21.0"],
        source_messages=(source,),
    )
    await transact(
        repository,
        lambda uow: uow.builds.save(build),
        commit=True,
    )
    assert build.id is not None

    loaded = await transact(repository, lambda uow: uow.builds.get_by_id(build.id))
    assert loaded is not None
    await transact(repository, lambda uow: uow.messages.record_edit(6100, "edited", Instant.now()), commit=True)
    edited = loaded.copy_for_edit()
    edited.description = "changed"
    await transact(
        repository,
        lambda uow: uow.builds.save(edited),
        commit=True,
    )

    stored = await transact(repository, lambda uow: uow.messages.get_by_id(6100))
    assert stored is not None
    assert stored.content == "edited"
    reloaded = await transact(repository, lambda uow: uow.builds.get_by_id(build.id))
    assert reloaded is not None
    assert reloaded.description == "changed"
    assert reloaded.source_messages == (MessageRef(6100, channel_id=5000, guild_id=4000),)


async def test_one_message_can_source_several_builds(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A build-log message can supply provenance for every build in a submission bundle."""
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    shared = MessageSnapshot(id=8000, guild_id=4000, channel_id=5000, author_id=7000, content="two doors")

    build_ids: list[int] = []
    for _ in range(2):
        build = OtherBuild(
            submission_status=Status.PENDING,
            submitter_account_id=account_id,
            versions=["Java 1.21.0"],
            source_messages=(shared,),
        )
        await transact(
            repository,
            lambda uow, build=build: uow.builds.save(build),
            commit=True,
        )
        assert build.id is not None
        build_ids.append(build.id)

    assert sorted(await transact(repository, lambda uow: uow.builds.list_ids_for_source_message(8000))) == sorted(
        build_ids
    )
    for build_id in build_ids:
        loaded = await transact(repository, lambda uow, build_id=build_id: uow.builds.get_by_id(build_id))
        assert loaded is not None
        assert [message.id for message in loaded.source_messages] == [8000]


# --- Edit-time taxonomy handling ---------------------------------------------


async def test_unknown_restriction_is_recorded_at_edit_time_and_round_trips(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    build = _make_build(BuildCategory.UTILITY, account_id)
    build.component_restrictions = ["Imaginary Component"]
    await apply_build_taxonomy(
        build, BuildTaxonomyService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    )

    # Retain the unrecognized name in extra_info and remove it from the resolved
    # restriction list before saving, so reloading preserves both representations.
    assert build.extra_info.get("unknown_restrictions") == {"component_restrictions": ["Imaginary Component"]}
    assert build.component_restrictions == []

    await transact(
        repository,
        lambda uow: uow.builds.save(build),
        commit=True,
    )
    assert build.id is not None
    loaded = await transact(repository, lambda uow: uow.builds.get_by_id(build.id))
    assert loaded is not None
    _assert_round_trip(build, loaded)


async def test_ambiguous_restriction_is_recorded_as_unknown(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    build = _make_build(BuildCategory.UTILITY, account_id)
    # Matches two official definitions via their shared alias, so it resolves
    # to neither and is recorded like an unknown.
    build.component_restrictions = [AMBIGUOUS_ALIAS]
    await _resolve_and_save(migrated_session_factory, repository, build)
    assert build.id is not None

    loaded = await transact(repository, lambda uow: uow.builds.get_by_id(build.id))
    assert loaded is not None
    assert loaded.component_restrictions == []
    assert loaded.extra_info.get("unknown_restrictions") == {"component_restrictions": [AMBIGUOUS_ALIAS]}


async def test_save_persists_tags_verbatim_without_mutating_input(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Saving alone does not resolve restriction names or supply a default pattern."""
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    build = _make_build(BuildCategory.DOOR, account_id)
    build.patterns = []
    await transact(
        repository,
        lambda uow: uow.builds.save(build),
        commit=True,
    )

    # Saving does not interpret the restriction names or add a default pattern;
    # those changes belong to apply_build_taxonomy.
    assert build.patterns == []
    assert build.tags == []

    assert build.id is not None
    loaded = await transact(repository, lambda uow: uow.builds.get_by_id(build.id))
    assert loaded is not None
    assert loaded.tags == []
    assert loaded.patterns == []


async def test_apply_build_taxonomy_defaults_door_pattern(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    build = _make_build(BuildCategory.DOOR, account_id)
    build.patterns = []
    await apply_build_taxonomy(
        build, BuildTaxonomyService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    )

    # The default pattern is materialized visibly at edit time.
    assert build.patterns == ["Regular"]
    assert {assignment.definition.stable_key for assignment in build.tags} >= {"pattern-regular"}

    await transact(
        repository,
        lambda uow: uow.builds.save(build),
        commit=True,
    )
    assert build.id is not None
    loaded = await transact(repository, lambda uow: uow.builds.get_by_id(build.id))
    assert loaded is not None
    assert loaded.patterns == ["Regular"]


async def test_replacing_links_of_one_media_type_moves_a_shared_url(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Links are keyed by (build_id, url), so assigning another media type moves the URL."""
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    build = _make_build(BuildCategory.OTHER, account_id)
    build.replace_links("image", ["https://example.com/dual.png"])
    build.replace_links("video", ["https://example.com/dual.png"])
    build.replace_links("image", [])
    await _resolve_and_save(migrated_session_factory, repository, build)

    assert build.id is not None
    loaded = await transact(repository, lambda uow: uow.builds.get_by_id(build.id))
    assert loaded is not None
    assert loaded.image_urls == ()
    assert loaded.video_urls == ("https://example.com/dual.png",)


async def test_an_unknown_door_size_stays_unknown_through_save_and_reload(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Historical posts can omit a door's size; saving must not substitute one."""
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    build = DoorBuild(
        submission_status=Status.PENDING,
        submitter_account_id=account_id,
        versions=["Java 1.21.0"],
        # The column is NOT NULL, so save() normalizes None to False; setting it
        # keeps this test about door dimensions.
        ai_generated=False,
    )
    assert build.door_dimensions == (None, None, None)
    await _resolve_and_save(migrated_session_factory, repository, build)
    assert build.id is not None

    loaded = await transact(repository, lambda uow: uow.builds.get_by_id(build.id))
    assert isinstance(loaded, DoorBuild)
    assert loaded.door_dimensions == (None, None, None)
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
        "credits": name_credits(
            draw(st.lists(st.sampled_from(["Alice", "Bob", "Charlie"]), max_size=3, unique=True)), legacy=True
        ),
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
    """Compare each generated build after taxonomy resolution and save.

    Examples share the seeded catalogue and database, but each inserts and reads
    its own build row.
    """
    session_factory, account_id = seeded_factory
    repository = partial(SqlAlchemyUnitOfWork, session_factory)
    category, field_values = values

    build = BUILD_CLASS_BY_CATEGORY[category](
        submission_status=Status.PENDING,
        submitter_account_id=account_id,
        versions=["Java 1.21.0"],
        **field_values,  # type: ignore[arg-type]
    )
    await _resolve_and_save(session_factory, repository, build)
    assert build.id is not None

    loaded = await transact(repository, lambda uow: uow.builds.get_by_id(build.id))
    assert loaded is not None
    _assert_round_trip(build, loaded)


async def test_repeat_credit_of_a_case_folding_name_resolves_to_one_alias(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Repeat lookup must use the same Unicode fold as alias insertion.

    Python casefold maps both sigma spellings to σσ. Lowercasing can retain final
    sigma, causing a lookup miss followed by a uniqueness conflict.
    """
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    for spelling in ("ΣΣ", "ΣΣ", "Σς"):
        build = _make_build(BuildCategory.UTILITY, account_id)
        build.creators_ign = [spelling]
        await _resolve_and_save(migrated_session_factory, repository, build)

    async with migrated_session_factory() as session:
        aliases = list((await session.scalars(select(CreatorAlias.normalized_name))).all())
    assert aliases == ["σσ"], "the three credits must resolve to exactly one creator"


async def test_detached_build_preview_changes_only_on_explicit_save(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from tests.support.database import assert_loaded_detached

    account_id = await _seed_catalogue(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    submitted = _make_build(BuildCategory.DOOR, account_id)
    await _resolve_and_save(migrated_session_factory, repository, submitted)
    loaded = await transact(repository, lambda uow: uow.builds.get_by_id(submitted.id))
    assert loaded is not None
    assert_loaded_detached(loaded)
    assert_loaded_detached(loaded, attributes={"links", "tags"})
    for assignment in loaded.tags:
        assert_loaded_detached(assignment)
        assert_loaded_detached(assignment.definition)

    preview = loaded.copy_for_edit()
    preview.width = (loaded.width or 0) + 1
    preview.extra_info["unknown_patterns"] = ["preview only"]
    preview.add_link("image", "https://example.com/preview.png")
    preview.tags[0].evidence = "preview evidence"
    assert loaded.extra_info.get("unknown_patterns") != ["preview only"]
    assert "https://example.com/preview.png" not in loaded.image_urls
    assert loaded.tags[0].evidence != "preview evidence"

    unchanged = await transact(repository, lambda uow: uow.builds.get_by_id(loaded.id))
    assert unchanged is not None
    assert unchanged.width == loaded.width
    assert unchanged.revision == loaded.revision

    await transact(
        repository,
        lambda uow: uow.builds.save(preview),
        commit=True,
    )
    saved = await transact(repository, lambda uow: uow.builds.get_by_id(loaded.id))
    assert saved is not None
    assert saved.width == preview.width
    assert saved.extra_info["unknown_patterns"] == ["preview only"]
    assert "https://example.com/preview.png" in saved.image_urls
    assert saved.revision == loaded.revision + 1


async def test_source_read_preserves_unclassified_links(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from uuid import uuid4

    owner = await _seed_catalogue(migrated_session_factory)
    draft_id = uuid4()
    build = OtherBuild(submitter_account_id=owner, source_submission_draft_id=draft_id)
    build.links = [BuildLink(url="https://example.com/unclassified", media_type=None)]
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    await transact(
        repository,
        lambda uow: uow.builds.save(build),
        commit=True,
    )

    async with build_edit_session(migrated_session_factory) as session:
        loaded = await BuildRepository(lambda: session).get_by_source_submission_draft_id(draft_id)
        assert loaded is not None
        assert not session.dirty

    async with migrated_session_factory() as session:
        links = (await session.scalars(select(BuildLink).where(BuildLink.build_id == build.id))).all()
        assert [(link.url, link.media_type) for link in links] == [("https://example.com/unclassified", None)]


async def test_tracked_status_change_advances_the_revision_once(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner = await _seed_catalogue(migrated_session_factory)
    build = OtherBuild(submitter_account_id=owner)
    async with (
        SqlAlchemyUnitOfWork(migrated_session_factory) as uow,
        uow.builds.editing(actor="test_fixture", source="fixture"),
    ):
        await uow.builds.save(build)
        await uow.commit()
    assert build.id is not None
    previous_revision = build.revision
    async with (
        SqlAlchemyUnitOfWork(migrated_session_factory) as uow,
        uow.builds.editing(actor="test_fixture", source="fixture"),
    ):
        tracked = await uow.builds.get_for_update(build.id)
        assert tracked is not None
        await uow.builds.confirm(tracked)
        assert tracked.submission_status is Status.CONFIRMED
        await uow.commit()
    async with SqlAlchemyUnitOfWork(migrated_session_factory) as uow:
        loaded = await uow.builds.get_by_id(build.id)
        assert loaded is not None
        assert loaded.revision == previous_revision + 1
        assert loaded.submission_status is Status.CONFIRMED


async def test_showcase_tags_are_replaced_by_key_beside_resolved_restrictions(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _seed_catalogue(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    build = _make_build(BuildCategory.UTILITY, account_id)
    await _resolve_and_save(migrated_session_factory, repository, build)
    assert build.id is not None
    loaded = await transact(repository, lambda uow: uow.builds.get_by_id(build.id))
    assert loaded is not None
    restrictions = loaded.restrictions

    taxonomy = BuildTaxonomyService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    await replace_showcase_tags(loaded, ["compact"], taxonomy)
    await _resolve_and_save(migrated_session_factory, repository, loaded)

    reloaded = await transact(repository, lambda uow: uow.builds.get_by_id(build.id))
    assert reloaded is not None
    showcase = [
        tag.definition.stable_key for tag in reloaded.tags if tag.definition.semantic_kind is TagSemanticKind.SHOWCASE
    ]
    assert showcase == ["compact"]
    assert reloaded.restrictions == restrictions
