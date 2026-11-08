"""Small typed harnesses shared by Discord boundary tests."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal, cast, override

import discord

import squid_ui_discord as sd
from squid.accounts.application import AccountService
from squid.bot.errors import UI_ERRORS
from squid.bot.i18n import localization_resolver
from squid.bot.ui import HOST_DEFAULTS
from squid.diagnostics.application import ErrorReportService
from squid.permissions.application import PermissionService
from squid.permissions.domain import Decision, PermissionNode, Reason, Subject
from squid.runtime import BotServices
from squid.suggestions.application import SuggestionService
from squid_reactivity import LocalTopicBus
from squid_ui.text import NEUTRAL, Localization
from squid_ui_discord.testing import AsyncCallRecorder
from squid_ui_discord.testing import InteractionHarness as UIInteractionHarness
from tests.support.services import bot_services, is_configured

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
    type: discord.InteractionResponseType | None = None

    def is_done(self) -> bool:
        return self.done


@dataclass(frozen=True, slots=True)
class _FollowupSource:
    send: AsyncCallRecorder
    delete_message: AsyncCallRecorder


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
    edit_original_response: AsyncCallRecorder = field(default_factory=AsyncCallRecorder)
    original_response: AsyncCallRecorder = field(default_factory=lambda: AsyncCallRecorder(result=_FollowupResult()))

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
    error_reports: ErrorReportService | None = None,
) -> InteractionHarness:
    """Create the minimal interaction contract used by shared error handling.

    The client carries the installed layout host a real interaction always names. It has no
    services by default; pass `error_reports` to assert that a failure was captured.
    """
    send_initial = AsyncCallRecorder(result=_InitialResponseResult())
    send_followup = AsyncCallRecorder(result=_FollowupResult())
    services = bot_services(error_reports=error_reports) if error_reports is not None else None
    client = make_layout_bot(services=services)
    interaction = cast(
        discord.Interaction[discord.Client],
        _InteractionSource(
            response=_ResponseSource(
                response_done,
                send_initial,
                discord.InteractionResponseType.channel_message if response_done else None,
            ),
            followup=_FollowupSource(send_followup, AsyncCallRecorder()),
            client=client,
            user=_Identity(user_id),
            guild_id=guild_id,
            channel_id=channel_id,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        ),
    )
    return InteractionHarness(interaction, send_initial, send_followup)


def make_command_interaction(
    client: discord.Client,
    *,
    name: str,
    options: list[dict[str, Any]] | None = None,
    autocomplete: bool = False,
    guild_id: int | None = None,
    channel_id: int = 20,
    user_id: int = 987654321098765432,
) -> discord.Interaction[discord.Client]:
    """Decode a gateway payload so command dispatch uses discord.py's real state and lookup."""
    state = client._connection  # pyright: ignore[reportPrivateUsage]
    if state.user is None:
        state.user = discord.ClientUser(
            state=state,
            data=cast(Any, {"id": "1", "username": "squid", "discriminator": "0", "avatar": None, "bot": True}),
        )
    user = {"id": str(user_id), "username": "builder", "discriminator": "0", "avatar": None}
    payload: dict[str, Any] = {
        "id": str(discord.utils.time_snowflake(datetime.now(UTC))),
        "application_id": "1",
        "type": 4 if autocomplete else 2,
        "token": "test-interaction-token",
        "version": 1,
        "attachment_size_limit": 10 * 1024 * 1024,
        "locale": "en-GB",
        "data": {"id": "2", "name": name, "type": 1, "options": options or []},
        "channel": {"id": str(channel_id), "type": 0 if guild_id is not None else 1, "name": "builds", "position": 0},
    }
    if guild_id is None:
        payload["user"] = user
    else:
        payload["guild_id"] = str(guild_id)
        payload["member"] = {"user": user, "roles": [], "joined_at": "2026-01-01T00:00:00Z", "flags": 0}
    return discord.Interaction(data=cast(Any, payload), state=state)


class _AutocompletePermissions(PermissionService):
    def __init__(self, allowed_nodes: frozenset[str]) -> None:
        self.allowed_nodes = allowed_nodes

    @override
    async def allows(self, subject: Subject, node: PermissionNode | str) -> bool:
        del subject
        return str(getattr(node, "name", node)) in self.allowed_nodes

    @override
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
class _AccountIds:
    account_id: int

    async def resolve(self, accounts: AccountService, discord_id: int) -> int:
        del accounts, discord_id
        return self.account_id


def make_autocomplete_interaction(
    suggestions: SuggestionService,
    *,
    user_id: int = 1,
    guild_id: int | None = None,
    locale: str = "en-US",
    account_id: int = 11,
    allowed_nodes: frozenset[str] = frozenset(),
) -> discord.Interaction[discord.Client]:
    """Create the interaction contract an autocomplete callback reads.

    Install suggestions, a fixed account resolver, and a permission fake that grants only
    allowed_nodes. The fake ignores the subject and does not exercise the permission engine.
    """

    async def is_owner(_user: object) -> bool:
        return False

    client = make_layout_bot(
        services=bot_services(
            suggestions=suggestions,
            permissions=_AutocompletePermissions(allowed_nodes),
            accounts=_AutocompleteAccounts(),
        ),
        account_ids=_AccountIds(account_id),
        is_owner=is_owner,
    )
    interaction = UIInteractionHarness(user_id=user_id, client=client)
    interaction.guild = None if guild_id is None else _Identity(guild_id)
    interaction.guild_id = guild_id
    interaction.locale = locale
    return cast(discord.Interaction[discord.Client], interaction.source)


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
    """Decode a normal reaction event using discord.py's gateway event class.

    This exercises the library's message ID and emoji representation used by the router.
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
    user: SimpleNamespace

    def get_guild(self, guild_id: int) -> discord.Guild | None:
        del guild_id
        return self.guild


def make_reaction_bot(
    *, guild: discord.Guild | None = None, message: discord.Message | None = None, user_id: int = 99
) -> ReactionBotHarness:
    """Create a bot double logged in as `user_id`, returning the supplied guild and recording message fetches.

    Guild lookup ignores the requested ID; tests requiring real lookup behavior need a client.
    """
    get_or_fetch_message = AsyncCallRecorder(result=message)
    bot = cast(
        "squid.bot.app.RedstoneSquid",
        _ReactionBotSource(guild, get_or_fetch_message, SimpleNamespace(id=user_id)),
    )
    return ReactionBotHarness(bot, get_or_fetch_message)


class FakeClient:
    """A bot double with caller-supplied attributes that sd.install can register.

    The UI runtime keeps weak references to clients, which a SimpleNamespace cannot support.
    """

    def __init__(self, **attributes: Any) -> None:
        self.__dict__.update(attributes)

    @override
    def __repr__(self) -> str:
        return f"FakeClient({', '.join(sorted(self.__dict__))})"


def make_layout_bot(**attributes: Any) -> Any:
    """Install the application UI defaults and error handling on a bot double.

    Panels find their configuration through DiscordUIRuntime.of(source), so tests need a
    registered runtime as well as a session manager.

    Reuse a supplied topic bus or create a local one. Localization uses the supplied settings
    service when present and NEUTRAL otherwise. Expose the runtime as ui and its manager as
    sessions, matching the attributes application code reads from the bot.

    A supplied `services` must be a real `BotServices`; build it with
    `tests.support.services.bot_services` so its doubles are checked against production.
    """
    services = attributes.get("services")
    if services is not None and not isinstance(services, BotServices):
        raise TypeError(f"services must be a BotServices from bot_services(), got {type(services).__name__}")
    bus = attributes.get("topic_bus") or LocalTopicBus()
    client = FakeClient(topic_bus=bus, **{k: v for k, v in attributes.items() if k != "topic_bus"})

    async def resolve(source: sd.contracts.LocalizationSource) -> Localization:
        if services is None or not is_configured(services.settings):
            return NEUTRAL
        return await localization_resolver(source)

    runtime = sd.install(
        cast(discord.Client, client), sd.Config(defaults=HOST_DEFAULTS, bus=bus, localization=resolve, errors=UI_ERRORS)
    )
    # Written through `__dict__` because these are the bot attributes the code under test
    # reads, and the double is a bag of them rather than a class declaring any.
    client.__dict__.update(
        ui=runtime,
        sessions=runtime.sessions,
    )
    return client
