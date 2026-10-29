"""Integration coverage for profile storage, visibility, and identity unlinking."""

from collections.abc import AsyncGenerator
from uuid import UUID

import anyio
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.application import AccountService
from squid.accounts.domain import (
    CURRENT_CONSENT_VERSION,
    AccountConsent,
    ClaimMethod,
    IdentityProvider,
    ProfileLink,
    ProfileUpdate,
)
from squid.accounts.domain import (
    AccountIdentity as AccountIdentityValue,
)
from squid.accounts.errors import AccountIdentityNotFoundError, LastIdentityError
from squid.accounts.infrastructure.models import AccountIdentity as AccountIdentityModel
from squid.accounts.infrastructure.models import AccountProfile as AccountProfileModel
from squid.accounts.infrastructure.models import CreatorAlias as CreatorAliasModel
from squid.accounts.infrastructure.repository import AccountRepository
from squid.persistence.base import Base

CONSENT = AccountConsent(CURRENT_CONSENT_VERSION, Instant.from_utc(2026, 8, 4))
MINECRAFT_UUID = UUID("11111111-1111-1111-1111-111111111111")

_TABLES = [
    Base.metadata.tables["accounts"],
    Base.metadata.tables["account_identities"],
    Base.metadata.tables["account_profiles"],
    Base.metadata.tables["public_creator_redirects"],
    Base.metadata.tables["creator_aliases"],
    Base.metadata.tables["creator_alias_claims"],
    Base.metadata.tables["verification_codes"],
]


@pytest.fixture
async def account_tables(async_engine: AsyncEngine) -> AsyncGenerator[AsyncEngine]:
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield async_engine
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=list(reversed(_TABLES)))


@pytest.fixture
def repository(
    account_tables: AsyncEngine,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> AccountRepository:
    del account_tables
    return AccountRepository(async_session_factory, "pepper")


async def _account(repository: AccountRepository, discord_id: int = 1):
    return await repository.create(consent=CONSENT, identities=(AccountIdentityValue.discord(discord_id),))


async def _wait_for_blocked_sessions(
    sessions: async_sessionmaker[AsyncSession], holder_pid: int, *, count: int
) -> None:
    # Include indirect blockers: one writer can hold the account lock while waiting on the
    # test's identity lock, leaving the other writer queued behind the first.
    blocked = text(
        """
        WITH RECURSIVE blocked(pid) AS (
            SELECT CAST(:holder_pid AS integer)
            UNION
            SELECT waiting.pid FROM pg_stat_activity AS waiting
            JOIN blocked ON blocked.pid = ANY(pg_blocking_pids(waiting.pid))
        )
        SELECT count(*) - 1 FROM blocked
        """
    )
    with anyio.fail_after(5):
        while True:
            async with sessions() as observer:
                if await observer.scalar(blocked, {"holder_pid": holder_pid}) == count:
                    return


class TestProfileRow:
    async def test_creating_an_account_creates_its_profile(self, repository: AccountRepository) -> None:
        account = await _account(repository)
        assert account.id is not None
        profile = await repository.get_profile(account.id)
        assert profile is not None
        assert profile.display_name is None
        assert not profile.hidden
        assert profile.links == ()

    async def test_get_or_create_identity_creates_a_profile(self, repository: AccountRepository) -> None:
        account = await repository.get_or_create_identity(IdentityProvider.DISCORD, "42")
        assert account.id is not None
        assert await repository.get_profile(account.id) is not None

    async def test_losing_the_identity_race_leaves_no_orphan_profile(
        self,
        repository: AccountRepository,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """The candidate account is deleted on the lose path; its profile must go with it."""
        first = await repository.get_or_create_identity(IdentityProvider.DISCORD, "7")
        second = await repository.get_or_create_identity(IdentityProvider.DISCORD, "7")
        assert first.id == second.id

        async with async_session_factory() as session:
            profile_ids = (await session.scalars(select(AccountProfileModel.account_id))).all()
        assert list(profile_ids) == [first.id]

    async def test_profile_is_none_for_an_unknown_account(self, repository: AccountRepository) -> None:
        assert await repository.get_profile(9999) is None


class TestProfileWrites:
    async def test_partial_update_leaves_other_fields_alone(self, repository: AccountRepository) -> None:
        account = await _account(repository)
        assert account.id is not None
        await repository.upsert_profile(account.id, ProfileUpdate(display_name="Notch", bio="builder").validated())
        updated = await repository.upsert_profile(account.id, ProfileUpdate(pronouns="they/them").validated())

        assert updated.display_name == "Notch"
        assert updated.bio == "builder"
        assert updated.pronouns == "they/them"

    async def test_explicit_null_clears_a_field(self, repository: AccountRepository) -> None:
        account = await _account(repository)
        assert account.id is not None
        await repository.upsert_profile(account.id, ProfileUpdate(bio="gone").validated())
        cleared = await repository.upsert_profile(account.id, ProfileUpdate(bio=None).validated())
        assert cleared.bio is None

    async def test_links_round_trip_through_jsonb(self, repository: AccountRepository) -> None:
        account = await _account(repository)
        assert account.id is not None
        links = (ProfileLink("Site", "https://example.com"), ProfileLink("Videos", "https://youtube.com/@x"))
        stored = await repository.upsert_profile(account.id, ProfileUpdate(links=links).validated())
        assert stored.links == links
        assert (await repository.get_profile(account.id)).links == links  # type: ignore[union-attr]

    async def test_hidden_flag_persists(self, repository: AccountRepository) -> None:
        account = await _account(repository)
        assert account.id is not None
        await repository.upsert_profile(account.id, ProfileUpdate(hidden=True).validated())
        assert (await repository.get_profile(account.id)).hidden  # type: ignore[union-attr]

    async def test_clear_profile_resets_content_but_leaves_it_visible(self, repository: AccountRepository) -> None:
        account = await _account(repository)
        assert account.id is not None
        await repository.upsert_profile(
            account.id,
            ProfileUpdate(
                display_name="Spam",
                bio="spam",
                pronouns="spam",
                links=(ProfileLink("Spam", "https://spam.example"),),
                hidden=True,
                avatar_identity_id=account.identities[0].id,
            ).validated(),
        )
        cleared = await repository.clear_profile(account.id)
        assert cleared.display_name is None
        assert cleared.bio is None
        assert cleared.pronouns is None
        assert cleared.links == ()
        assert not cleared.hidden
        assert cleared.avatar_identity_id is None
        assert await repository.get_profile(account.id) == cleared

    async def test_database_refuses_an_overlong_display_name(
        self,
        repository: AccountRepository,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """The CHECK is a guard against writes that bypass the domain, so test it directly."""
        account = await _account(repository)
        async with async_session_factory() as session:
            profile = await session.get(AccountProfileModel, account.id)
            assert profile is not None
            profile.display_name = "x" * 65
            with pytest.raises(IntegrityError):
                await session.commit()

    async def test_database_refuses_more_than_ten_links(
        self,
        repository: AccountRepository,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        account = await _account(repository)
        async with async_session_factory() as session:
            profile = await session.get(AccountProfileModel, account.id)
            assert profile is not None
            profile.links = [{"label": f"n{i}", "url": f"https://example.com/{i}"} for i in range(11)]
            with pytest.raises(IntegrityError):
                await session.commit()


class TestAvatars:
    async def test_avatar_must_reference_an_identity_of_the_same_account(self, repository: AccountRepository) -> None:
        owner = await _account(repository, discord_id=1)
        stranger = await repository.create(consent=CONSENT, identities=(AccountIdentityValue.discord(2),))
        assert owner.id is not None
        foreign_identity = stranger.identities[0]
        assert foreign_identity.id is not None

        with pytest.raises(AccountIdentityNotFoundError):
            await repository.upsert_profile(owner.id, ProfileUpdate(avatar_identity_id=foreign_identity.id).validated())

    async def test_unlinking_the_avatar_identity_clears_the_avatar(self, repository: AccountRepository) -> None:
        account = await repository.create(
            consent=CONSENT,
            identities=(
                AccountIdentityValue.discord(1),
                AccountIdentityValue.java(MINECRAFT_UUID, username="Notch"),
            ),
        )
        assert account.id is not None
        java = next(i for i in account.identities if i.provider is IdentityProvider.JAVA)
        assert java.id is not None
        await repository.upsert_profile(account.id, ProfileUpdate(avatar_identity_id=java.id).validated())

        await repository.unlink_identity(account.id, java.id)

        profile = await repository.get_profile(account.id)
        assert profile is not None
        assert profile.avatar_identity_id is None


class TestIdentityVisibilityAndUnlink:
    async def test_identities_are_public_by_default(self, repository: AccountRepository) -> None:
        account = await _account(repository)
        assert all(identity.is_public for identity in account.identities)

    async def test_visibility_toggles_and_reads_back(self, repository: AccountRepository) -> None:
        account = await _account(repository)
        assert account.id is not None
        identity_id = account.identities[0].id
        assert identity_id is not None

        hidden = await repository.set_identity_visibility(account.id, identity_id, is_public=False)
        assert not hidden.is_public

        reloaded = await repository.get_by_id(account.id)
        assert reloaded is not None
        assert not reloaded.identities[0].is_public

    async def test_visibility_refuses_another_accounts_identity(self, repository: AccountRepository) -> None:
        owner = await _account(repository, discord_id=1)
        stranger = await repository.create(consent=CONSENT, identities=(AccountIdentityValue.discord(2),))
        assert owner.id is not None
        stranger_identity_id = stranger.identities[0].id
        assert stranger_identity_id is not None

        with pytest.raises(AccountIdentityNotFoundError):
            await repository.set_identity_visibility(owner.id, stranger_identity_id, is_public=False)

    async def test_unlink_removes_only_the_named_identity(self, repository: AccountRepository) -> None:
        account = await repository.create(
            consent=CONSENT,
            identities=(
                AccountIdentityValue.discord(1),
                AccountIdentityValue.java(MINECRAFT_UUID, username="Notch"),
            ),
        )
        assert account.id is not None
        java = next(i for i in account.identities if i.provider is IdentityProvider.JAVA)
        assert java.id is not None

        removed = await repository.unlink_identity(account.id, java.id)
        assert removed is not None
        assert removed.provider is IdentityProvider.JAVA

        reloaded = await repository.get_by_id(account.id)
        assert reloaded is not None
        assert [identity.provider for identity in reloaded.identities] == [IdentityProvider.DISCORD]

    async def test_unlink_refuses_another_accounts_identity(self, repository: AccountRepository) -> None:
        owner = await _account(repository, discord_id=1)
        stranger = await repository.create(consent=CONSENT, identities=(AccountIdentityValue.discord(2),))
        assert owner.id is not None
        stranger_identity_id = stranger.identities[0].id
        assert stranger_identity_id is not None

        assert await repository.unlink_identity(owner.id, stranger_identity_id) is None
        reloaded = await repository.get_by_id(stranger.id)  # type: ignore[arg-type]
        assert reloaded is not None
        assert len(reloaded.identities) == 1

    async def test_concurrent_unlinks_keep_a_login_and_creator_credit(
        self,
        repository: AccountRepository,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        account = await repository.create(
            consent=CONSENT,
            identities=(AccountIdentityValue.discord(1), AccountIdentityValue.java(MINECRAFT_UUID)),
        )
        assert account.id is not None
        account_id = account.id
        async with async_session_factory.begin() as session:
            session.add(
                CreatorAliasModel(
                    name="Player",
                    account_id=account_id,
                    claimed_at=CONSENT.granted_at,
                    claim_method=ClaimMethod.VERIFIED_IGN,
                )
            )

        async def unused_username_lookup(_uuid: UUID) -> str | None:
            return None

        service = AccountService(repository, unused_username_lookup, lambda: 123456)
        outcomes: list[AccountIdentityValue | LastIdentityError] = []

        async def unlink(identity_id: int) -> None:
            try:
                outcomes.append(await service.unlink_identity(account_id, identity_id))
            except LastIdentityError as error:
                outcomes.append(error)

        async with async_session_factory() as blocker:
            await blocker.execute(
                select(AccountIdentityModel).where(AccountIdentityModel.account_id == account_id).with_for_update()
            )
            holder_pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
            with anyio.fail_after(5):
                async with anyio.create_task_group() as tasks:
                    for identity in account.identities:
                        assert identity.id is not None
                        tasks.start_soon(unlink, identity.id)
                    assert holder_pid is not None
                    await _wait_for_blocked_sessions(async_session_factory, holder_pid, count=2)
                    await blocker.commit()

        assert sum(isinstance(outcome, AccountIdentityValue) for outcome in outcomes) == 1
        assert sum(isinstance(outcome, LastIdentityError) for outcome in outcomes) == 1
        reloaded = await repository.get_by_id(account_id)
        assert reloaded is not None
        (remaining,) = reloaded.identities
        assert remaining.id is not None
        alias = await repository.get_alias_by_name("Player")
        assert alias is not None
        assert alias.account_id == account_id

        await repository.upsert_profile(account_id, ProfileUpdate(avatar_identity_id=remaining.id).validated())
        with pytest.raises(LastIdentityError):
            await service.unlink_identity(account_id, remaining.id)
        profile = await repository.get_profile(account_id)
        assert profile is not None
        assert profile.avatar_identity_id == remaining.id
        assert (await service.list_identities(account_id)) == (remaining,)
        with pytest.raises(AccountIdentityNotFoundError):
            await service.unlink_identity(account_id, 9999)

    async def test_avatar_edit_serializes_with_identity_unlink(
        self,
        repository: AccountRepository,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        account = await repository.create(
            consent=CONSENT,
            identities=(AccountIdentityValue.discord(1), AccountIdentityValue.java(MINECRAFT_UUID)),
        )
        assert account.id is not None
        account_id = account.id
        java = next(identity for identity in account.identities if identity.provider is IdentityProvider.JAVA)
        assert java.id is not None
        identity_id = java.id
        await repository.upsert_profile(account_id, ProfileUpdate(display_name="Builder").validated())
        removed: list[AccountIdentityValue | None] = []
        rejected: list[AccountIdentityNotFoundError] = []

        async def unlink() -> None:
            removed.append(await repository.unlink_identity(account_id, identity_id))

        async def edit_avatar() -> None:
            try:
                await repository.upsert_profile(account_id, ProfileUpdate(avatar_identity_id=identity_id).validated())
            except AccountIdentityNotFoundError as error:
                rejected.append(error)

        async with async_session_factory() as blocker:
            await blocker.get(AccountIdentityModel, identity_id, with_for_update=True)
            holder_pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
            assert holder_pid is not None
            with anyio.fail_after(5):
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(unlink)
                    await _wait_for_blocked_sessions(async_session_factory, holder_pid, count=1)
                    tasks.start_soon(edit_avatar)
                    await _wait_for_blocked_sessions(async_session_factory, holder_pid, count=2)
                    await blocker.commit()

        assert removed == [java]
        assert len(rejected) == 1
        assert rejected[0].identity_id == identity_id
        profile = await repository.get_profile(account_id)
        assert profile is not None
        assert profile.avatar_identity_id is None
        assert profile.display_name == "Builder"

    async def test_avatar_key_is_stored_for_discord(self, repository: AccountRepository) -> None:
        account = await _account(repository)
        assert account.id is not None
        identity_id = account.identities[0].id
        assert identity_id is not None

        await repository.set_identity_avatar_key(account.id, identity_id, "deadbeef")

        reloaded = await repository.get_by_id(account.id)
        assert reloaded is not None
        assert reloaded.identities[0].avatar_key == "deadbeef"
