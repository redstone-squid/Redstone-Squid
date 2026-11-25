"""`creator:` finds builds by the public ID of the account their credits belong to."""

from dataclasses import replace
from functools import partial
from uuid import UUID

from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.accounts.domain import CURRENT_CONSENT_VERSION, AccountConsent, ClaimMethod
from squid.core.accounts.models import AccountIdentity, CreatorAlias
from squid.core.builds.application.queries import BuildListFilters
from squid.core.persistence.base import Base
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.search.application import SearchService
from squid.core.search.domain import SearchRequest, SearchScope
from squid.core.search.infrastructure.projection import SearchProjectionWorker
from tests.support.build_edits import build_edit_session
from tests.support.build_edits import transact_build_edit as transact

JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")
CONSENT = AccountConsent(CURRENT_CONSENT_VERSION, Instant.from_utc(2026, 8, 4))


async def _credited_build(
    session_factory: async_sessionmaker[AsyncSession], submitter_id: int, **credit: object
) -> int:
    async with build_edit_session(session_factory) as session:
        builds = Base.metadata.tables["builds"]
        build_id = (
            await session.execute(
                insert(builds)
                .values(submission_status=1, submitter_account_id=submitter_id, category="Other")
                .returning(builds.c.id)
            )
        ).scalar_one()
        await session.execute(insert(Base.metadata.tables["other_builds"]).values(build_id=build_id))
        await session.execute(
            insert(Base.metadata.tables["build_credits"]).values(
                build_id=build_id, position=0, role="creator", attribution="explicit", **credit
            )
        )
        return build_id


async def _project(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory.begin() as session:
        while (await SearchProjectionWorker(session).process_batch(limit=100)) != (0, 0):
            pass


async def _owners(uow_factory: partial[SqlAlchemyUnitOfWork], build_id: int) -> dict[int, UUID]:
    return await transact(uow_factory, lambda uow: uow.accounts.credit_owners(build_id))


async def _found(service: SearchService, query: str, scope: SearchScope = SearchScope.BUILDS) -> set[str]:
    return {hit.source_id for hit in (await service.search(SearchRequest(query, scope=scope))).hits}


async def test_creator_ids_follow_claims_and_identity_visibility(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    uow_factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory, verification_code_pepper="pepper")
    account = await transact(
        uow_factory,
        lambda uow: uow.accounts.create(
            consent=CONSENT,
            identities=(
                AccountIdentity.java(JAVA_UUID, username="Notch"),
                replace(AccountIdentity.discord(7), is_public=False),
            ),
        ),
        commit=True,
    )
    assert account.id is not None
    assert account.public_creator_id is not None
    public_id = str(account.public_creator_id)

    async with build_edit_session(migrated_session_factory) as session:
        alias = CreatorAlias(name="Old Name")
        session.add(alias)
        await session.flush()
        alias_id = alias.id
    by_alias = await _credited_build(migrated_session_factory, account.id, name="Old Name", alias_id=alias_id)
    by_java = await _credited_build(
        migrated_session_factory, account.id, name="Notch", kind="minecraft_java", subject=str(JAVA_UUID)
    )
    by_private_discord = await _credited_build(
        migrated_session_factory, account.id, name="notch_dc", kind="discord", subject="7"
    )
    # Anyone can type someone's ID as a credited name; that must not attribute the build.
    impostor = await _credited_build(migrated_session_factory, account.id, name=public_id)
    await transact(
        uow_factory,
        lambda uow: uow.accounts.claim_unclaimed_alias(
            account_id=account.id, name="Old Name", method=ClaimMethod.STAFF_APPROVED
        ),
        commit=True,
    )
    await _project(migrated_session_factory)
    service = SearchService(uow_factory=uow_factory)
    assert await _found(service, f"creator:{public_id}") == {str(by_alias), str(by_java)}
    assert await _found(service, f"creator:{public_id.upper()}") == {str(by_alias), str(by_java)}
    assert str(impostor) not in await _found(service, f'creator:"{public_id}"')
    owners = partial(_owners, uow_factory)
    assert await owners(by_alias) == await owners(by_java) == {0: account.public_creator_id}
    assert await owners(by_private_discord) == await owners(impostor) == {}

    public_filters = BuildListFilters(creator_id=account.public_creator_id)
    listed = await service.search(SearchRequest("", scope=SearchScope.BUILDS, catalogue_filters=public_filters))
    assert {hit.source_id for hit in listed.hits} == {str(by_alias), str(by_java)}
    async with uow_factory() as uow:
        summaries = await uow.builds.get_public_summaries([by_java, by_private_discord])
        assert [summary.creators[0].creator_id for summary in summaries] == [account.public_creator_id, None]
        assert [summary.credits[0].creator_id for summary in summaries] == [account.public_creator_id, None]

    async with migrated_session_factory.begin() as session:
        await session.execute(text("UPDATE account_identities SET is_public = true WHERE subject = '7'"))
    await _project(migrated_session_factory)

    assert await _found(service, f"creator:{public_id}") == {str(by_alias), str(by_java), str(by_private_discord)}
    assert await owners(by_private_discord) == {0: account.public_creator_id}


async def test_a_merged_accounts_old_id_still_finds_its_builds(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    uow_factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory, verification_code_pepper="pepper")
    surviving = await transact(
        uow_factory,
        lambda uow: uow.accounts.create(consent=CONSENT, identities=(AccountIdentity.discord(1),)),
        commit=True,
    )
    absorbed = await transact(
        uow_factory,
        lambda uow: uow.accounts.create(consent=CONSENT, identities=(AccountIdentity.discord(2),)),
        commit=True,
    )
    assert surviving.id is not None
    assert absorbed.id is not None
    old_id = absorbed.public_creator_id
    build = await _credited_build(migrated_session_factory, absorbed.id, name="absorbed", kind="discord", subject="2")

    await transact(uow_factory, lambda uow: uow.accounts.merge(surviving.id, absorbed.id), commit=True)
    await _project(migrated_session_factory)
    service = SearchService(uow_factory=uow_factory)

    assert await _found(service, f"creator:{old_id}") == {str(build)}
    assert await _found(service, f"creator:{surviving.public_creator_id}") == {str(build)}
