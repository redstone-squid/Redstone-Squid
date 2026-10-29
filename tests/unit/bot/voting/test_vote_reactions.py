"""Reaction adaptation with real consent, actor resolution, routing and task ownership."""

import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal, cast

import anyio
import discord
import pytest
from whenever import Instant

from squid.accounts.domain import CURRENT_CONSENT_VERSION, Account, AccountConsent, AccountIdentity, IdentityProvider
from squid.bot.reactions import ReactionClearEvent, ReactionEvent, ReactionResolver, ReactionRouter
from squid.bot.utils.permissions import AccountIdCache
from squid.bot.voting.vote import VoteCog
from squid.permissions.domain import PermissionNode, Subject
from squid.runtime import BackgroundTaskSupervisor
from squid.voting.application.ports import VoteActorResolver
from squid.voting.domain import (
    CastVoteResult,
    VoteActor,
    VoteKind,
    VoteMessage,
    VoteSelection,
    VoteSessionSnapshot,
    VoteVisibility,
)
from tests.support.discord import make_layout_bot, make_reaction_payload
from tests.support.voting import GENERIC_OPTIONS, poll_snapshot

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


@dataclass
class Member:
    guild: Guild
    id: int = 70
    bot: bool = False
    roles: tuple[discord.Role, ...] = ()


@dataclass
class Guild:
    id: int

    def get_member(self, user_id: int) -> discord.Member:
        return cast(discord.Member, Member(self, id=user_id))


@dataclass
class Reaction:
    emoji: str
    members: tuple[Member, ...] = ()
    me: bool = True

    async def users(self, *, limit: None) -> AsyncIterator[Member]:
        for member in self.members:
            yield member


@dataclass(frozen=True)
class ForbiddenResponse:
    status: int = 403
    reason: str = "Forbidden"


@dataclass
class Message:
    trace: list[str]
    reactions: list[Reaction] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[tuple[str, int]] = field(default_factory=list)
    forbidden: set[str] = field(default_factory=set)

    async def add_reaction(self, emoji: str) -> None:
        self.added.append(emoji)
        if emoji in self.forbidden:
            raise discord.Forbidden(cast(Any, ForbiddenResponse()), "Cannot add reaction")

    async def remove_reaction(self, emoji: discord.PartialEmoji | str, user: discord.abc.Snowflake) -> None:
        self.removed.append((str(emoji), user.id))
        self.trace.append("removed")


@dataclass
class Accounts:
    account: Account = field(
        default_factory=lambda: Account(
            id=7,
            identities=(AccountIdentity.discord(70),),
            consent=AccountConsent(CURRENT_CONSENT_VERSION, Instant.now()),
        )
    )

    async def get_account_by_identity(self, provider: IdentityProvider, subject: str) -> Account | None:
        return self.account if self.account.identity_for(provider, subject) else None


@dataclass
class Permissions:
    subjects: list[Subject] = field(default_factory=list)

    async def capabilities(self, subject: Subject, nodes: Iterable[PermissionNode | str]) -> frozenset[str]:
        self.subjects.append(subject)
        return frozenset()


@dataclass
class Votes:
    trace: list[str]
    snapshot: VoteSessionSnapshot = field(
        default_factory=lambda: poll_snapshot(
            visibility=VoteVisibility.VISIBLE_LIVE, messages=(VoteMessage(100, 200, 10),)
        )
    )
    casts: list[tuple[int, VoteActor, str]] = field(default_factory=list)
    failure: Exception | None = None
    resolver: VoteActorResolver | None = None

    def set_actor_resolver(self, resolver: VoteActorResolver) -> None:
        self.resolver = resolver

    async def get_session(self, message_id: int) -> VoteSessionSnapshot | None:
        return self.snapshot if message_id in self.snapshot.message_ids else None

    async def list_open(self, kind: VoteKind) -> tuple[VoteSessionSnapshot, ...]:
        return (self.snapshot,) if self.snapshot.kind is kind else ()

    async def cast_vote(self, message_id: int, actor: VoteActor, emoji: str) -> CastVoteResult:
        if self.failure is not None:
            raise self.failure
        self.casts.append((message_id, actor, emoji))
        self.trace.append("committed")
        return CastVoteResult(self.snapshot)


@dataclass
class Services:
    votes: Votes
    accounts: Accounts = field(default_factory=Accounts)
    permissions: Permissions = field(default_factory=Permissions)


class ReactionHarness:
    def __init__(self) -> None:
        self.trace: list[str] = []
        self.services = Services(Votes(self.trace))
        self.messages = {100: Message(self.trace), 101: Message(self.trace)}
        self.guilds = {guild_id: Guild(guild_id) for guild_id in (10, 11)}
        self.refreshes: list[tuple[str, str]] = []
        self.refreshed: asyncio.Queue[None] = asyncio.Queue()
        self.supervisor = BackgroundTaskSupervisor()
        self.bot: RedstoneSquid = make_layout_bot(
            user=discord.Object(999),
            services=self.services,
            account_ids=AccountIdCache(),
            background_tasks=self.supervisor,
            get_guild=self.guilds.get,
            get_or_fetch_message=self.fetch_message,
            refresh_posts=self.refresh_posts,
            is_owner=self.is_owner,
            wait_until_ready=self.wait_until_ready,
        )
        self.bot.reactions = ReactionRouter(self.bot, self.supervisor, concurrency=1)
        self.cog = VoteCog(self.bot)

    @asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        async with self.supervisor.running():
            try:
                yield
            finally:
                await self.bot.reactions.close()
                await self.cog.ui_unload()

    async def fetch_message(self, channel_id: int, message_id: int) -> discord.Message | None:
        return cast(discord.Message | None, self.messages.get(message_id))

    async def refresh_posts(self, kind: str, key: str) -> None:
        self.refreshes.append((kind, key))
        self.refreshed.put_nowait(None)

    async def is_owner(self, user: object) -> bool:
        return False

    async def wait_until_ready(self) -> None:
        pass

    def event(
        self, action: Literal["REACTION_ADD", "REACTION_REMOVE"] = "REACTION_ADD", *, message_id: int = 100
    ) -> ReactionEvent:
        guild_id, emoji = (10, "1️⃣") if message_id == 100 else (11, "🔴")
        payload = make_reaction_payload(
            message_id=message_id,
            channel_id=message_id + 100,
            guild_id=guild_id,
            user_id=70,
            emoji=emoji,
            event_type=action,
        )
        return ReactionEvent(payload, emoji, ReactionResolver(self.bot, None))

    def selected(self) -> None:
        self.services.votes.snapshot = replace(
            self.services.votes.snapshot, selections=(VoteSelection(7, 10, "one", "1️⃣", 1),)
        )

    def anonymous(self) -> None:
        self.services.votes.snapshot = poll_snapshot(messages=(VoteMessage(100, 200, 10),))


@pytest.fixture
def reactions() -> ReactionHarness:
    return ReactionHarness()


async def test_reconciliation_job_is_supervised_and_stops_on_unload(reactions: ReactionHarness) -> None:
    async with reactions.running():
        await reactions.cog.ui_load()
        with anyio.fail_after(1):
            while "vote-reaction-reconciliation" not in reactions.supervisor.last_success:
                await anyio.lowlevel.checkpoint()
        (job,) = reactions.cog._background_tasks
        assert reactions.services.votes.resolver is reactions.cog
        assert reactions.messages[100].added == ["1️⃣", "2️⃣"]
        assert not job.finished.is_set()
        await reactions.cog.ui_unload()
        assert job.finished.is_set()


async def test_routed_public_reactions_add_and_remove_a_ballot(reactions: ReactionHarness) -> None:
    async with reactions.running():
        with anyio.fail_after(1):
            await reactions.bot.reactions.dispatch_add(reactions.event().payload)
            await reactions.refreshed.get()
            reactions.selected()
            await reactions.bot.reactions.dispatch_remove(reactions.event("REACTION_REMOVE").payload)
            await reactions.refreshed.get()

    assert reactions.services.votes.casts == [(100, VoteActor(7, 70, guild_id=10), "1️⃣")] * 2
    assert reactions.refreshes == [("vote_session", "1")] * 2
    assert reactions.services.permissions.subjects == [Subject(account_id=7, guild_id=10)] * 2


async def test_anonymous_reaction_is_removed_only_after_commit(reactions: ReactionHarness) -> None:
    reactions.anonymous()
    reactions.services.votes.failure = RuntimeError("database unavailable")
    async with reactions.running():
        with pytest.raises(RuntimeError, match="database unavailable"):
            await reactions.cog.on_reaction_add(reactions.event())
        assert reactions.messages[100].removed == []
        reactions.services.votes.failure = None
        await reactions.cog.on_reaction_add(reactions.event())
        with anyio.fail_after(1):
            while not reactions.messages[100].removed:
                await anyio.lowlevel.checkpoint()

    assert reactions.trace == ["committed", "removed"]


async def test_reconciliation_changes_public_selection_but_keeps_it_when_discord_is_unavailable(
    reactions: ReactionHarness,
) -> None:
    reactions.selected()
    message = reactions.messages.pop(100)
    async with reactions.running():
        await reactions.cog.reconcile_message_reactions(100)
        assert reactions.services.votes.casts == []
        assert reactions.refreshes == []
        reactions.messages[100] = message
        message.reactions = [Reaction("1️⃣"), Reaction("2️⃣", (Member(reactions.guilds[10]),))]
        await reactions.cog.reconcile_message_reactions(100)

    assert reactions.services.votes.casts == [(100, VoteActor(7, 70, guild_id=10), "2️⃣")]
    assert reactions.refreshes == [("vote_session", "1")]


async def test_anonymous_recovery_cleans_committed_and_unconsented_reactions_without_replaying(
    reactions: ReactionHarness,
) -> None:
    reactions.anonymous()
    reactions.selected()
    message = reactions.messages[100]
    message.reactions = [Reaction("1️⃣", (Member(reactions.guilds[10]),)), Reaction("2️⃣")]
    async with reactions.running():
        await reactions.cog.reconcile_message_reactions(100)
        reactions.services.accounts.account = replace(reactions.services.accounts.account, consent=None)
        reactions.services.votes.snapshot = replace(reactions.services.votes.snapshot, selections=())
        await reactions.cog.reconcile_message_reactions(100)

    assert reactions.services.votes.casts == []
    assert message.removed == [("1️⃣", 70)] * 2


async def test_multi_guild_alias_recovery_is_idempotent_and_removal_unsets_the_stable_option(
    reactions: ReactionHarness,
) -> None:
    reactions.services.votes.snapshot = poll_snapshot(
        visibility=VoteVisibility.VISIBLE_LIVE,
        messages=(VoteMessage(100, 200, 10), VoteMessage(101, 201, 11)),
        options=GENERIC_OPTIONS
        + tuple(
            replace(option, emoji=emoji, guild_id=11)
            for option, emoji in zip(GENERIC_OPTIONS, ("🔴", "🔵"), strict=True)
        ),
        selections=(VoteSelection(7, 10, "one", "1️⃣", 1),),
    )
    reactions.messages[100].reactions = [Reaction("1️⃣"), Reaction("2️⃣")]
    second = reactions.messages[101]
    second.reactions = [Reaction("🔴", (Member(reactions.guilds[11]),)), Reaction("🔵")]
    async with reactions.running():
        await reactions.cog.reconcile_message_reactions(100)
        await reactions.cog.recover_reaction_add(reactions.event(message_id=101))
        assert reactions.services.votes.casts == []
        assert reactions.refreshes == []
        assert second.removed == []
        await reactions.cog.recover_reaction_remove(reactions.event("REACTION_REMOVE", message_id=101))

    assert reactions.services.votes.casts == [(101, VoteActor(7, 70, guild_id=11), "🔴")]


async def test_clear_and_recovery_restore_options_without_toggling_ballots(reactions: ReactionHarness) -> None:
    reactions.selected()
    message = reactions.messages[100]
    message.forbidden = {"1️⃣"}
    clear = discord.RawReactionClearEvent(cast(Any, {"message_id": 100, "channel_id": 200, "guild_id": 10}))
    emoji = discord.RawReactionClearEmojiEvent(
        cast(Any, {"message_id": 100, "channel_id": 200, "guild_id": 10}), discord.PartialEmoji(name="1️⃣")
    )
    async with reactions.running():
        await reactions.cog.on_reaction_clear(ReactionClearEvent(clear))
        await reactions.cog.on_reaction_clear_emoji(ReactionClearEvent(emoji, "1️⃣"))
        await reactions.cog.recover_reaction_clear(ReactionClearEvent(clear))

    assert reactions.services.votes.casts == []
    assert message.added == ["1️⃣", "2️⃣"] * 3
