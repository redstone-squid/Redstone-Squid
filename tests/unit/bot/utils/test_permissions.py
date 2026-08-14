"""Discord-facing permission checks over the node engine."""

from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import discord
import pytest
from discord.ext.commands import Context

from squid.accounts.application import AccountService
from squid.bot.app import RedstoneSquid
from squid.bot.utils.permissions import (
    AccountIdCache,
    PermissionNodeRequired,
    requires,
    subject_for,
)
from squid.permissions.application import PermissionService
from squid.permissions.application.ports import AssignmentRecord, GrantRecord, RoleRecord, SubjectRecords
from squid.permissions.domain import BUILTIN_ROLES_BY_KEY, Effect

BUILTIN_ROWS = tuple(
    RoleRecord(id=index + 1, slug=role.key, guild_id=None, builtin_key=role.key, rank=role.rank, protected=True)
    for index, role in enumerate(BUILTIN_ROLES_BY_KEY.values())
)
ROLE_ID = {row.builtin_key: row.id for row in BUILTIN_ROWS}


class FakeAccountService:
    def __init__(self) -> None:
        self.lookups = 0

    async def get_account(self, discord_id: int) -> SimpleNamespace:
        self.lookups += 1
        return SimpleNamespace(id=discord_id)


class FakePermissionStore:
    def __init__(self, *, grants: tuple[GrantRecord, ...] = (), assignments: tuple[AssignmentRecord, ...] = ()) -> None:
        self._records = SubjectRecords(epoch=1, roles=BUILTIN_ROWS, grants=grants, assignments=assignments)

    async def load_for_subject(self, **_kwargs: object) -> SubjectRecords:
        return self._records

    async def epoch(self) -> int:
        return 1


def _member(user_id: int, guild: SimpleNamespace, **permissions: bool):
    """A stand-in that passes `isinstance(..., discord.Member)`.

    Subject building keys off that check to decide whether there are roles and a
    guild to consider, so a plain namespace would silently produce an empty
    subject and prove nothing.
    """
    guild_permissions = discord.Permissions.none()
    guild_permissions.update(**permissions)
    member = Mock(spec=discord.Member)
    member.id = user_id
    member.guild = guild
    member.guild_permissions = guild_permissions
    member.roles = [SimpleNamespace(id=30)]
    return member


def _bot(
    *,
    owner_id: int = 1,
    member_id: int | None = None,
    store: FakePermissionStore | None = None,
) -> tuple[RedstoneSquid, SimpleNamespace]:
    guild = SimpleNamespace(id=100, owner_id=2)
    member = _member(member_id, guild) if member_id is not None else None
    guild.get_member = lambda user_id: member if member is not None and member.id == user_id else None
    services = SimpleNamespace(
        accounts=FakeAccountService(),
        permissions=PermissionService(store or FakePermissionStore()),
    )

    async def is_owner(user: object) -> bool:
        return getattr(user, "id", None) == owner_id

    bot = SimpleNamespace(
        owner_id=owner_id,
        owner_ids=None,
        services=services,
        account_ids=AccountIdCache(),
        get_guild=lambda server_id: guild if server_id == guild.id else None,
        is_owner=is_owner,
    )
    return cast(RedstoneSquid, bot), guild


def _context(bot: RedstoneSquid, guild: SimpleNamespace, author: SimpleNamespace) -> Context[RedstoneSquid]:
    return cast(Context[RedstoneSquid], SimpleNamespace(bot=bot, author=author, guild=guild))


class TestRequires:
    async def test_a_granted_node_passes(self) -> None:
        store = FakePermissionStore(
            assignments=(AssignmentRecord(role_id=ROLE_ID["trusted"], subject_account_id=8),),
        )
        bot, guild = _bot(member_id=8, store=store)
        context = _context(bot, guild, _member(8, guild))

        assert await requires("vote.log_delete.cast").predicate(context)

    async def test_a_missing_node_names_itself(self) -> None:
        bot, guild = _bot(member_id=8)
        context = _context(bot, guild, _member(8, guild))

        with pytest.raises(PermissionNodeRequired) as raised:
            await requires("build.submission.approve").predicate(context)

        assert raised.value.nodes == ("build.submission.approve",)
        assert raised.value.forbidden is False

    async def test_a_forbid_is_reported_as_such(self) -> None:
        """The refusal has to read differently: nothing the user does will help."""
        store = FakePermissionStore(
            grants=(GrantRecord(pattern="build.**", effect=int(Effect.FORBID), subject_account_id=8),),
        )
        bot, guild = _bot(member_id=8, store=store)
        context = _context(bot, guild, _member(8, guild))

        with pytest.raises(PermissionNodeRequired) as raised:
            await requires("build.submission.approve").predicate(context)

        assert raised.value.forbidden is True

    async def test_any_mode_passes_on_one_held_node(self) -> None:
        store = FakePermissionStore(
            assignments=(AssignmentRecord(role_id=ROLE_ID["trusted"], subject_account_id=8),),
        )
        bot, guild = _bot(member_id=8, store=store)
        context = _context(bot, guild, _member(8, guild))

        assert await requires("build.submission.approve", "vote.log_delete.cast", mode="any").predicate(context)

    async def test_the_declared_nodes_are_introspectable(self) -> None:
        """The taxonomy test reads this rather than a predicate's name."""
        decorated = requires("settings.server.edit", mode="all")

        assert decorated.predicate.__squid_nodes__ == ("settings.server.edit",)  # pyrefly: ignore[missing-attribute]

    def test_an_unknown_node_fails_at_import_time(self) -> None:
        from squid.permissions.domain import UnknownPermissionNodeError

        with pytest.raises(UnknownPermissionNodeError):
            requires("build.submission.aprove")


class TestSubject:
    async def test_the_subject_is_resolved_once_per_invocation(self) -> None:
        bot, guild = _bot(member_id=8)
        context = _context(bot, guild, _member(8, guild))

        first = await subject_for(context)
        second = await subject_for(context)

        assert first is second
        assert cast(FakeAccountService, bot.services.accounts).lookups == 1

    async def test_manage_server_reaches_the_bridge(self) -> None:
        bot, guild = _bot(member_id=9)
        author = _member(9, guild, manage_guild=True)
        context = _context(bot, guild, author)

        subject = await subject_for(context)

        assert subject.discord_guild_admin is True
        assert subject.guild_id == 100
        assert subject.discord_role_ids == frozenset({30})


class TestAccountIdCache:
    async def test_a_repeated_lookup_hits_the_cache(self) -> None:
        accounts = FakeAccountService()
        cache = AccountIdCache()

        assert await cache.resolve(cast(AccountService, accounts), 5) == 5
        assert await cache.resolve(cast(AccountService, accounts), 5) == 5
        assert accounts.lookups == 1

    async def test_forgetting_an_entry_forces_a_reread(self) -> None:
        accounts = FakeAccountService()
        cache = AccountIdCache()

        await cache.resolve(cast(AccountService, accounts), 5)
        cache.forget(5)
        await cache.resolve(cast(AccountService, accounts), 5)

        assert accounts.lookups == 2
