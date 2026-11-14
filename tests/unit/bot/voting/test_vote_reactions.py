"""Reaction adaptation with real consent, actor resolution, routing and task ownership."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal, NoReturn, cast, override

import anyio
import discord
import pytest
from whenever import Instant

from squid.accounts.application import AccountService
from squid.accounts.domain import CURRENT_CONSENT_VERSION, AccountConsent, IdentityProvider
from squid.accounts.models import Account, AccountIdentity
from squid.bot.reactions import ReactionClearEvent, ReactionEvent, ReactionResolver, ReactionRouter
from squid.bot.utils.permissions import AccountIdCache
from squid.bot.voting.vote import VoteCog
from squid.core.actors import Actor
from squid.permissions.application import PermissionService
from squid.permissions.application.ports import PermissionUnitOfWork
from squid.permissions.domain import CheckMode, Decision, PermissionCheck, PermissionNode, Reason
from squid.runtime import BackgroundTaskSupervisor
from squid.settings.application import SettingsService
from squid.voting.application import VoteService
from squid.voting.application.ports import VoteActorResolver
from squid.voting.domain import (
    CastVoteResult,
    VoteKind,
    VoteMessage,
    VoteRejection,
    VoteSelection,
    VoteSessionSnapshot,
    VoteVisibility,
)
from squid.voting.infrastructure.discord_cards import DiscordVoteCards
from squid_ui_discord.testing import MessageHarness
from tests.support.discord import make_layout_bot, make_reaction_payload
from tests.support.services import bot_services
from tests.support.voting import GENERIC_OPTIONS, poll_snapshot

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


@dataclass
class PartialChannel:
    messages: dict[int, Any]

    def get_partial_message(self, message_id: int) -> Any:
        return self.messages.get(message_id, DeletedMessage())


class DeletedMessage:
    """A partial message for a card that is gone, which Discord answers with 404."""

    async def fetch(self) -> NoReturn:
        raise discord.NotFound(cast(Any, NotFoundResponse()), "Unknown Message")

    async def add_reaction(self, emoji: str) -> NoReturn:
        await self.fetch()

    async def remove_reaction(self, emoji: object, user: object) -> NoReturn:
        await self.fetch()


@dataclass(frozen=True)
class NotFoundResponse:
    status: int = 404
    reason: str = "Not Found"


@dataclass
class Member:
    guild: Guild
    id: int = 70
    bot: bool = False
    roles: tuple[discord.Role, ...] = ()

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"


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
    sent: list[dict[str, object]] = field(default_factory=list)
    guild: None = None

    @property
    def channel(self) -> discord.TextChannel:
        return cast(discord.TextChannel, self)

    async def send(self, **kwargs: object) -> discord.Message:
        self.sent.append(kwargs)
        return MessageHarness().source

    async def fetch(self) -> Message:
        return self

    async def add_reaction(self, emoji: str) -> None:
        self.added.append(emoji)
        if emoji in self.forbidden:
            raise discord.Forbidden(cast(Any, ForbiddenResponse()), "Cannot add reaction")

    async def remove_reaction(self, emoji: discord.PartialEmoji | str, user: discord.abc.Snowflake) -> None:
        self.removed.append((str(emoji), user.id))
        self.trace.append("removed")


@dataclass
class Accounts(AccountService):
    account: Account = field(
        default_factory=lambda: Account(
            id=7,
            identities=(AccountIdentity.discord(70),),
            consent=AccountConsent(CURRENT_CONSENT_VERSION, Instant.now()),
        )
    )

    @override
    async def get_account_by_identity(self, provider: IdentityProvider, subject: str) -> Account | None:
        return self.account if self.account.identity_for(provider, subject) else None

    @override
    async def get_account_by_id(self, account_id: int) -> Account | None:
        return self.account if self.account.id == account_id else None


@dataclass
class Permissions(PermissionService):
    subjects: list[Actor] = field(default_factory=list)

    @override
    async def check(
        self,
        subject: Actor,
        *nodes: PermissionNode | str,
        uow: PermissionUnitOfWork | None = None,
        mode: CheckMode = "all",
    ) -> PermissionCheck:
        self.subjects.append(subject)
        return PermissionCheck(
            tuple(Decision(str(node), allowed=False, reason=Reason.DEFAULT) for node in nodes), mode=mode
        )


@dataclass
class Votes(VoteService):
    """Record casts without mutating the snapshot; tests explicitly seed resulting selections."""

    trace: list[str]
    snapshot: VoteSessionSnapshot = field(
        default_factory=lambda: poll_snapshot(
            visibility=VoteVisibility.VISIBLE_LIVE, messages=(VoteMessage(100, 200, 10),)
        )
    )
    casts: list[tuple[Actor, str, str | None]] = field(default_factory=list)
    failure: Exception | None = None
    rejection: VoteRejection | None = None
    resolver: VoteActorResolver | None = None

    @override
    def set_actor_resolver(self, resolver: VoteActorResolver) -> None:
        self.resolver = resolver

    @override
    async def get_session(self, vote_session_id: int) -> VoteSessionSnapshot | None:
        return self.snapshot if vote_session_id == self.snapshot.id else None

    @override
    async def get_session_for_message(self, message_id: int) -> VoteSessionSnapshot | None:
        return self.snapshot if any(message.id == message_id for message in self.snapshot.messages) else None

    @override
    async def list_open(self, kind: VoteKind) -> Sequence[VoteSessionSnapshot]:
        return (self.snapshot,) if self.snapshot.kind is kind else ()

    @override
    async def cast_vote(
        self,
        vote_session_id: int,
        actor: Actor,
        option_id: str,
        *,
        emoji: str | None = None,
        toggle: bool = True,
    ) -> CastVoteResult:
        assert vote_session_id == self.snapshot.id
        if self.failure is not None:
            raise self.failure
        self.casts.append((actor, option_id, emoji))
        self.trace.append("committed" if self.rejection is None else "refused")
        return CastVoteResult(self.snapshot, self.rejection)


class Settings(SettingsService):
    def __init__(self) -> None:
        pass

    @override
    async def get_locale(self, server_id: int) -> str | None:
        return None


class ReactionHarness:
    def __init__(self) -> None:
        self.trace: list[str] = []
        self.votes = Votes(self.trace)
        self.accounts = Accounts()
        self.permissions = Permissions()
        self.services = bot_services(
            votes=self.votes,
            accounts=self.accounts,
            permissions=self.permissions,
            settings=Settings(),
            vote_cards=DiscordVoteCards,
        )
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
            get_partial_messageable=self.partial_messageable,
            refresh_posts=self.refresh_posts,
            is_owner=self.is_owner,
            wait_until_ready=self.wait_until_ready,
        )
        self.bot.reactions = ReactionRouter(self.bot, self.supervisor, concurrency=1)
        self.cog = VoteCog(self.bot)

    @asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        async with self.supervisor.running():
            subscription = self.bot.reactions.subscribe(self.cog.reactions)
            try:
                yield
            finally:
                await self.bot.reactions.close()
                subscription.detach()
                await self.cog.ui_unload()

    async def fetch_message(self, channel_id: int, message_id: int) -> discord.Message | None:
        return cast(discord.Message | None, self.messages.get(message_id))

    def partial_messageable(self, channel_id: int) -> Any:
        """Partial messages resolve to the same fake messages, so removals are recorded on them."""
        return PartialChannel(self.messages)

    async def refresh_posts(self, kind: str, key: str) -> None:
        self.refreshes.append((kind, key))
        self.refreshed.put_nowait(None)

    async def is_owner(self, user: object) -> bool:
        return False

    async def wait_until_ready(self) -> None:
        pass

    def event(
        self,
        action: Literal["REACTION_ADD", "REACTION_REMOVE"] = "REACTION_ADD",
        *,
        message_id: int = 100,
        emoji: str | None = None,
        recovering: bool = False,
    ) -> ReactionEvent:
        guild_id, default_emoji = (10, "1️⃣") if message_id == 100 else (11, "🔴")
        emoji = default_emoji if emoji is None else emoji
        payload = make_reaction_payload(
            message_id=message_id,
            channel_id=message_id + 100,
            guild_id=guild_id,
            user_id=70,
            emoji=emoji,
            event_type=action,
        )
        return ReactionEvent(payload, emoji, ReactionResolver(self.bot, None), recovering=recovering)

    def selected(self) -> None:
        self.votes.snapshot = replace(self.votes.snapshot, selections=(VoteSelection(7, 10, "one", "1️⃣", 1),))

    def anonymous(self) -> None:
        self.votes.snapshot = poll_snapshot(messages=(VoteMessage(100, 200, 10),))


@pytest.fixture
def reactions() -> ReactionHarness:
    return ReactionHarness()


async def test_loading_subscribes_without_starting_a_periodic_recount(reactions: ReactionHarness) -> None:
    """Recounts fan out Discord requests, so nothing runs them on a timer."""
    async with reactions.running():
        await reactions.cog.ui_load()
        assert reactions.votes.resolver is reactions.cog
        assert reactions.cog.reactions._background_tasks == set()
        assert reactions.messages[100].added == []
        await reactions.cog.ui_unload()


async def test_routed_public_reactions_add_and_remove_a_ballot(reactions: ReactionHarness) -> None:
    async with reactions.running():
        with anyio.fail_after(1):
            await reactions.bot.reactions.dispatch_add(reactions.event().payload)
            await reactions.refreshed.get()
            reactions.selected()
            await reactions.bot.reactions.dispatch_remove(reactions.event("REACTION_REMOVE").payload)
            await reactions.refreshed.get()

    assert reactions.votes.casts == [(Actor(account_id=7, discord_user_id=70, guild_id=10), "one", "1️⃣")] * 2
    assert reactions.refreshes == [("vote_session", "1")] * 2
    assert reactions.permissions.subjects == [Actor(account_id=7, discord_user_id=70, guild_id=10)] * 2


async def test_anonymous_reaction_is_removed_only_after_commit(reactions: ReactionHarness) -> None:
    reactions.anonymous()
    reactions.votes.failure = RuntimeError("database unavailable")
    async with reactions.running():
        with pytest.raises(RuntimeError, match="database unavailable"):
            await reactions.cog.reactions.on_reaction_add(reactions.event())
        assert reactions.messages[100].removed == []
        reactions.votes.failure = None
        await reactions.cog.reactions.on_reaction_add(reactions.event())
        with anyio.fail_after(1):
            while not reactions.messages[100].removed:
                await anyio.lowlevel.checkpoint()
        reactions.selected()
        await reactions.cog.reactions.on_reaction_add(reactions.event(recovering=True))
        assert reactions.messages[100].removed == [("1️⃣", 70)] * 2
        assert len(reactions.votes.casts) == 1

    assert reactions.trace == ["committed", "removed", "removed"]


async def test_recount_changes_public_selection_but_keeps_it_when_discord_is_unavailable(
    reactions: ReactionHarness,
) -> None:
    reactions.selected()
    message = reactions.messages.pop(100)
    async with reactions.running():
        await reactions.cog.reactions.recount(1)
        assert reactions.votes.casts == []
        assert reactions.refreshes == []
        reactions.messages[100] = message
        message.reactions = [Reaction("1️⃣"), Reaction("2️⃣", (Member(reactions.guilds[10]),))]
        await reactions.cog.reactions.recount(1)

    assert reactions.votes.casts == [(Actor(account_id=7, discord_user_id=70, guild_id=10), "two", "2️⃣")]
    assert reactions.refreshes == [("vote_session", "1")]


async def test_anonymous_recount_cleans_committed_and_unconsented_reactions_without_replaying(
    reactions: ReactionHarness,
) -> None:
    reactions.anonymous()
    reactions.selected()
    message = reactions.messages[100]
    message.reactions = [Reaction("1️⃣", (Member(reactions.guilds[10]),)), Reaction("2️⃣")]
    async with reactions.running():
        await reactions.cog.reactions.recount(1)
        reactions.accounts.account = replace(reactions.accounts.account, consent=None)
        reactions.votes.snapshot = replace(reactions.votes.snapshot, selections=())
        await reactions.cog.reactions.recount(1)

    assert reactions.votes.casts == []
    assert message.removed == [("1️⃣", 70)] * 2


async def test_multi_guild_alias_recovery_is_idempotent_and_removal_unsets_the_stable_option(
    reactions: ReactionHarness,
) -> None:
    """The two guilds' different emoji both mean option ``one`` in the same session ballot.

    Recovery must recognize the existing selection; removing either alias can withdraw it.
    """
    reactions.votes.snapshot = poll_snapshot(
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
        await reactions.cog.reactions.recount(1)
        await reactions.cog.reactions.on_reaction_add(reactions.event(message_id=101, recovering=True))
        assert reactions.votes.casts == []
        assert reactions.refreshes == []
        assert second.removed == []
        await reactions.cog.reactions.on_reaction_remove(
            reactions.event("REACTION_REMOVE", message_id=101, recovering=True)
        )

    assert reactions.votes.casts == [(Actor(account_id=7, discord_user_id=70, guild_id=11), "one", "🔴")]


async def test_clear_and_recovery_restore_options_without_toggling_ballots(reactions: ReactionHarness) -> None:
    reactions.selected()
    message = reactions.messages[100]
    message.forbidden = {"1️⃣"}
    clear = discord.RawReactionClearEvent(cast(Any, {"message_id": 100, "channel_id": 200, "guild_id": 10}))
    emoji = discord.RawReactionClearEmojiEvent(
        cast(Any, {"message_id": 100, "channel_id": 200, "guild_id": 10}), discord.PartialEmoji(name="1️⃣")
    )
    async with reactions.running():
        await reactions.cog.reactions.on_reaction_clear(ReactionClearEvent(clear))
        await reactions.cog.reactions.on_reaction_clear(ReactionClearEvent(emoji, "1️⃣"))
        await reactions.cog.reactions.on_reaction_clear(ReactionClearEvent(clear))

    assert reactions.votes.casts == []
    assert message.added == ["1️⃣", "2️⃣"] * 3


async def test_unconsented_live_vote_explains_refusal_but_recovery_stays_quiet(reactions: ReactionHarness) -> None:
    reactions.accounts.account = replace(reactions.accounts.account, consent=None)
    message = reactions.messages[100]
    async with reactions.running():
        await reactions.cog.reactions.on_reaction_add(reactions.event(recovering=True))
        assert message.removed == []
        assert message.sent == []
        await reactions.cog.reactions.on_reaction_add(reactions.event())

    assert reactions.votes.casts == []
    assert message.removed == [("1️⃣", 70)]
    assert len(message.sent) == 1
    assert message.sent[0]["delete_after"] == 30
    assert "/account consent" in str(cast(discord.ui.LayoutView, message.sent[0]["view"]).to_components())


async def test_anonymous_refusal_removes_reactions_but_only_live_events_explain_it(reactions: ReactionHarness) -> None:
    reactions.anonymous()
    reactions.votes.rejection = VoteRejection.NOT_ELIGIBLE
    message = reactions.messages[100]
    async with reactions.running():
        await reactions.cog.reactions.on_reaction_add(reactions.event(recovering=True))
        assert message.removed == [("1️⃣", 70)]
        assert message.sent == []
        await reactions.cog.reactions.on_reaction_add(reactions.event())

    assert message.removed == [("1️⃣", 70)] * 2
    assert len(message.sent) == 1
    assert "trusted role" in str(cast(discord.ui.LayoutView, message.sent[0]["view"]).to_components())
    assert reactions.refreshes == []


@pytest.mark.parametrize("recovering", [False, True], ids=["live", "recovery"])
async def test_only_live_public_replacement_takes_back_the_previous_reaction(
    reactions: ReactionHarness, recovering: bool
) -> None:
    reactions.selected()
    handler = reactions.cog.reactions
    async with reactions.running():
        await handler.on_reaction_add(reactions.event(emoji="2️⃣", recovering=recovering))

    assert reactions.votes.casts == [(Actor(account_id=7, discord_user_id=70, guild_id=10), "two", "2️⃣")]
    assert reactions.messages[100].removed == ([] if recovering else [("1️⃣", 70)])
    assert reactions.refreshes == [("vote_session", "1")]
