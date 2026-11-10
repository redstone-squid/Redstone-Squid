"""Unit definitions survive loading, public rendering, and edit copies."""

from decimal import Decimal
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.models import Account
from squid.api.v1.schemas.builds import BuildSummary
from squid.builds.application.queries import PublicBuildSummary
from squid.builds.domain import Status
from squid.builds.models import OtherBuild
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.tags.models import TagAssignment, TagDefinition, UnitDefinition
from tests.support.build_edits import build_edit_session
from tests.support.build_edits import transact_build_edit as transact


async def test_public_values_and_incompatible_assignment_units(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with build_edit_session(migrated_session_factory) as session:
        account = Account()
        session.add(account)
        session.add_all(
            [
                UnitDefinition(key="test_tick", dimension="time", symbol="gt", scale_to_base=Decimal("0.05")),
                UnitDefinition(key="test_second", dimension="time", symbol="s", scale_to_base=Decimal(1)),
                UnitDefinition(key="test_block", dimension="length", symbol="b", scale_to_base=Decimal(1)),
            ]
        )
        await session.flush()
        definition = TagDefinition(
            stable_key="test_delay",
            display_name="Delay",
            authority=TagAuthority.OFFICIAL,
            semantic_kind=TagSemanticKind.RESTRICTION,
            restriction_type="miscellaneous",
            value_type=TagValueType.NUMERIC,
            moderation_status=TagModerationStatus.APPROVED,
            canonical_unit="test_tick",
            default_display_unit="test_tick",
        )
        session.add(definition)
        await session.flush()
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    build = OtherBuild(
        submitter_account_id=account.id,
        submission_status=Status.CONFIRMED,
        tags=[TagAssignment(definition, Decimal(20), "test_second")],
    )
    await transact(
        factory,
        lambda uow: uow.builds.save(build),
        commit=True,
    )
    assert build.id is not None
    loaded = await transact(factory, lambda uow: uow.builds.get_by_id(build.id))
    assert loaded is not None
    summary = await transact(factory, lambda uow: uow.builds.get_public_summaries([build.id]))
    for tag in (
        summary[0].tags[0],
        PublicBuildSummary.from_build(loaded).tags[0],
        BuildSummary.from_domain(loaded).tags[0],
        BuildSummary.from_domain(loaded.copy_for_edit()).tags[0],
    ):
        assert tag.value == Decimal(1)
        assert tag.unit == "test_second"
    edited = loaded.copy_for_edit()
    edited.tags[0].display_unit = "test_block"
    await transact(
        factory,
        lambda uow: uow.builds.save(edited),
        commit=True,
    )
    loaded = await transact(factory, lambda uow: uow.builds.get_by_id(build.id))
    assert loaded is not None
    assert loaded.tags[0].display_unit is None
    assert loaded.tags[0].value == Decimal(20)
    assert loaded.tags[0].presentation_unit is not None
    assert loaded.tags[0].presentation_unit.key == "test_tick"

    # History must survive JSON decoding without losing arbitrary decimal precision.
    precise = Decimal("12345678901234567890.12345678901234567890")
    edited = loaded.copy_for_edit()
    edited.tags[0].numeric_value = precise
    await transact(
        factory,
        lambda uow: uow.builds.save(edited),
        commit=True,
    )
    async with factory() as uow:
        page = await uow.build_history.list_history(build.id)
        entry = await uow.build_history.get_history_entry(build.id, page.items[0].id)
        assert entry is not None
        assert entry.after is not None
        tags = entry.after.payload["tags"]
        assert isinstance(tags, list)
        assert isinstance(tags[0], dict)
        assert tags[0]["numeric_value"] == str(precise)
