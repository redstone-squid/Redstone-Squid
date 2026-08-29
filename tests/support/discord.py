"""Small typed harnesses shared by Discord boundary tests."""

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, cast

import discord

from squid.accounts.application import AccountService
from squid.permissions.application import PermissionService
from squid.permissions.domain import Decision, PermissionNode, Reason, Subject
from squid_ui_discord.testing import AsyncCallRecorder

if TYPE_CHECKING:
    import squid.bot.app


@dataclass(frozen=True, slots=True)
class _Identity:
    id: int


@dataclass(frozen=True, slots=True)
class _InitialResponseResult:
    resource: None = None
    message_id: None = None

    def is_ephemeral(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class _FollowupResult:
    id: int = 99


@dataclass(frozen=True, slots=True)
class _ResponseSource:
    done: bool
    send_message: AsyncCallRecorder

    def is_done(self) -> bool:
        return self.done


@dataclass(frozen=True, slots=True)
class _FollowupSource:
    send: AsyncCallRecorder
    delete_message: AsyncCallRecorder


@dataclass(frozen=True, slots=True)
class _ErrorServices:
    error_reports: object


@dataclass(slots=True)
class _InteractionSource:
    response: _ResponseSource
    followup: _FollowupSource
    client: object
    user: _Identity
    guild_id: int | None
    channel_id: int
    expires_at: datetime
    command: None = None
    delete_original_response: AsyncCallRecorder = field(default_factory=AsyncCallRecorder)

    def is_expired(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class InteractionHarness:
    """An interaction together with its observable response methods."""

    interaction: discord.Interaction[discord.Client]
    send_initial: AsyncCallRecorder
    send_followup: AsyncCallRecorder


def make_interaction(
    *,
    response_done: bool = False,
    user_id: int = 1,
    guild_id: int | None = None,
    channel_id: int = 2,
    error_reports: object | None = None,
) -> InteractionHarness:
    """Create the minimal interaction contract used by shared error handling.

    The client carries the installed layout host a real interaction always names. It has no
    services by default; pass `error_reports` to assert that a failure was captured.
    """
    send_initial = AsyncCallRecorder(result=_InitialResponseResult())
    send_followup = AsyncCallRecorder(result=_FollowupResult())
    services = _ErrorServices(error_reports) if error_reports is not None else None
    client = make_layout_bot(services=services)
    interaction = cast(
        discord.Interaction[discord.Client],
        _InteractionSource(
            response=_ResponseSource(response_done, send_initial),
            followup=_FollowupSource(send_followup, AsyncCallRecorder()),
            client=client,
            user=_Identity(user_id),
            guild_id=guild_id,
            channel_id=channel_id,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        ),
    )
    return InteractionHarness(interaction, send_initial, send_followup)


class _AutocompletePermissions(PermissionService):
    def __init__(self, allowed_nodes: frozenset[str]) -> None:
        self.allowed_nodes = allowed_nodes

    async def allows(self, subject: Subject, node: PermissionNode | str) -> bool:
        del subject
        return str(getattr(node, "name", node)) in self.allowed_nodes

    async def decisions(
        self,
        subject: Subject,
        nodes: Iterable[PermissionNode | str],
    ) -> tuple[Decision, ...]:
        del subject
        return tuple(
            Decision(
                node=str(getattr(node, "name", node)),
                allowed=str(getattr(node, "name", node)) in self.allowed_nodes,
                reason=Reason.DEFAULT,
            )
            for node in nodes
        )


class _AutocompleteAccounts(AccountService):
    def __init__(self) -> None:
        pass


@dataclass(frozen=True, slots=True)
class _AutocompleteServices:
    suggestions: object
    permissions: _AutocompletePermissions
    accounts: _AutocompleteAccounts


@dataclass(frozen=True, slots=True)
class _AccountIds:
    account_id: int

    async def resolve(self, accounts: AccountService, discord_id: int) -> int:
        del accounts, discord_id
        return self.account_id


@dataclass(frozen=True, slots=True)
class _AutocompleteClient:
    services: _AutocompleteServices
    account_ids: _AccountIds

    async def is_owner(self, user: object) -> bool:
        del user
        return False


@dataclass(frozen=True, slots=True)
class _AutocompleteSource:
    client: _AutocompleteClient
    user: _Identity
    guild_id: int | None
    locale: str
    channel_id: int = 2
    command: None = None
    guild: None = None


def make_autocomplete_interaction(
    suggestions: object,
    *,
    user_id: int = 1,
    guild_id: int | None = None,
    locale: str = "en-US",
    account_id: int = 11,
    allowed_nodes: frozenset[str] = frozenset(),
) -> discord.Interaction[discord.Client]:
    """Create the interaction contract an autocomplete callback reads.

    Unlike `make_interaction`, this carries a client with a services container, because an
    autocomplete callback answers from `bot.services.suggestions` rather than from a cog.
    `allowed_nodes` is what the permission engine would grant this user.
    """

    client = _AutocompleteClient(
        services=_AutocompleteServices(
            suggestions=suggestions,
            permissions=_AutocompletePermissions(allowed_nodes),
            accounts=_AutocompleteAccounts(),
        ),
        account_ids=_AccountIds(account_id),
    )
    return cast(
        discord.Interaction[discord.Client],
        _AutocompleteSource(
            client=client,
            user=_Identity(user_id),
            guild_id=guild_id,
            locale=locale,
        ),
    )


@dataclass(frozen=True, slots=True)
class MessageHarness:
    """A message together with its observable edit method."""

    message: discord.Message
    edit: AsyncCallRecorder


@dataclass(frozen=True, slots=True)
class _MessageFlags:
    components_v2: bool


@dataclass(frozen=True, slots=True)
class _MessageSource:
    edit: AsyncCallRecorder
    channel: _Identity
    id: int
    flags: _MessageFlags


def make_message(*, channel_id: int = 2, message_id: int = 3, components_v2: bool = False) -> MessageHarness:
    """Create the minimal message contract used by shared error handling."""
    edit = AsyncCallRecorder()
    message = cast(
        discord.Message,
        _MessageSource(
            edit=edit,
            channel=_Identity(channel_id),
            id=message_id,
            flags=_MessageFlags(components_v2),
        ),
    )
    return MessageHarness(message, edit)


def make_reaction_payload(
    *,
    message_id: int = 10,
    channel_id: int = 20,
    guild_id: int | None = 30,
    user_id: int = 40,
    emoji: str = "⭐",
    event_type: Literal["REACTION_ADD", "REACTION_REMOVE"] = "REACTION_ADD",
) -> discord.RawReactionActionEvent:
    """Build the real gateway payload rather than a look-alike namespace.

    The router shards on `message_id` and stringifies `emoji`; a duck-typed stand-in
    would keep passing if discord.py renamed or retyped either one.
    """
    data: dict[str, Any] = {
        "message_id": message_id,
        "channel_id": channel_id,
        "user_id": user_id,
        # ReactionType.normal. Burst reactions arrive as the same event and the router
        # does not branch on the distinction.
        "type": 0,
    }
    if guild_id is not None:
        data["guild_id"] = guild_id
    return discord.RawReactionActionEvent(cast(Any, data), discord.PartialEmoji(name=emoji), event_type)


@dataclass(frozen=True, slots=True)
class ReactionBotHarness:
    """A bot stand-in exposing only what reaction dispatch calls, plus its observable fetch."""

    bot: squid.bot.app.RedstoneSquid
    get_or_fetch_message: AsyncCallRecorder


@dataclass(frozen=True, slots=True)
class _ReactionBotSource:
    guild: discord.Guild | None
    get_or_fetch_message: AsyncCallRecorder

    def get_guild(self, guild_id: int) -> discord.Guild | None:
        del guild_id
        return self.guild


def make_reaction_bot(
    *, guild: discord.Guild | None = None, message: discord.Message | None = None
) -> ReactionBotHarness:
    """Create the two-method bot surface `ReactionRouter` and `ReactionEvent` depend on.

    Keeping the cast here rather than at each call site means the tests state what they
    need from the bot once, instead of repeating an `arg-type` suppression per test.
    """
    get_or_fetch_message = AsyncCallRecorder(result=message)
    bot = cast(
        "squid.bot.app.RedstoneSquid",
        _ReactionBotSource(guild, get_or_fetch_message),
    )
    return ReactionBotHarness(bot, get_or_fetch_message)


class FakeClient:
    """A bot stand-in a layout host can be installed on.

    `sd.install` keys a weak table on the client, which rules plain namespace bags out.
    Everything else about these doubles is unchanged: attributes are whatever the code under
    test reads off a bot.
    """

    def __init__(self, **attributes: Any) -> None:
        self.__dict__.update(attributes)

    def __repr__(self) -> str:
        return f"FakeClient({', '.join(sorted(self.__dict__))})"


def make_layout_bot(**attributes: Any) -> Any:
    """A bot double with the layout runtime installed, the way `RedstoneSquid` installs it.

    Panels reach their chrome, error hook and challenge presenter through
    `ClientRuntime.of(source)`, so a test building one needs a real installation rather than a
    bare `SessionManager`. The installation is weakly keyed, so it leaves with the double.
    """
    import squid_ui_discord as sd
    from squid.bot.i18n import localization_resolver
    from squid.bot.ui import HOST_DEFAULTS
    from squid_reactivity import LocalTopicBus
    from squid_ui.text import NEUTRAL, Localization

    bus = attributes.get("topic_bus") or LocalTopicBus()
    client = FakeClient(topic_bus=bus, **{k: v for k, v in attributes.items() if k != "topic_bus"})

    async def resolve(source: sd.InvocationSource) -> Localization:
        settings = getattr(getattr(client, "services", None), "settings", None)
        return NEUTRAL if settings is None else await localization_resolver(source)

    runtime = sd.install(cast(discord.Client, client), defaults=HOST_DEFAULTS, bus=bus, localization=resolve)
    # Written through `__dict__` because these are the bot attributes the code under test
    # reads, and the double is a bag of them rather than a class declaring any.
    client.__dict__.update(
        client_runtime=runtime,
        sessions=runtime.sessions,
    )
    return client


@asynccontextmanager
async def invocation_scope(source: Any) -> AsyncIterator[Any]:
    """Resolve one invocation inside the ambient scope production dispatch establishes."""
    import squid_ui_discord as sd

    with sd.invocation_scope(source):
        yield await sd.Invocation.of(source)
