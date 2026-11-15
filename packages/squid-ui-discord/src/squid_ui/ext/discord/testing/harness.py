"""Discord doubles that record calls without opening a gateway connection."""

import inspect
import itertools
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Self, override

import discord
from squid_ui.ext.discord.delivery import DeliveryResult, EditHandle, MessageDestination, handle_for
from squid_ui.ext.discord.messages.payload import MessagePayload


@dataclass(frozen=True, slots=True)
class CallRecord:
    """One invocation captured by a harness endpoint."""

    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class AsyncCallRecorder:
    """An async endpoint recording every call before returning or raising.

    Configured errors persist across calls until cleared. A callback, when present and no
    error is configured, supplies the result and may be synchronous or asynchronous.
    """

    def __init__(self, *, result: Any = None, error: BaseException | None = None, callback: Any = None) -> None:
        self.records: list[CallRecord] = []
        self.result = result
        self.error = error
        self.callback: Any = callback

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.records.append(CallRecord(args, kwargs))
        if self.error is not None:
            raise self.error
        if self.callback is not None:
            callback_result = self.callback(*args, **kwargs)
            return await callback_result if inspect.isawaitable(callback_result) else callback_result
        return self.result

    @property
    def await_count(self) -> int:
        return len(self.records)

    @property
    def await_args(self) -> Any:
        return self.records[-1] if self.records else None

    @property
    def await_args_list(self) -> list[CallRecord]:
        return self.records

    @property
    def return_value(self) -> Any:
        return self.result

    @return_value.setter
    def return_value(self, value: Any) -> None:
        self.result = value

    @property
    def side_effect(self) -> Any:
        return self.error if self.error is not None else self.callback

    @side_effect.setter
    def side_effect(self, value: Any) -> None:
        self.error = value if isinstance(value, BaseException) else None
        self.callback = value if callable(value) else None

    def assert_awaited_once(self) -> None:
        assert len(self.records) == 1

    def assert_awaited_once_with(self, *args: Any, **kwargs: Any) -> None:
        assert self.records == [CallRecord(args, kwargs)]

    def assert_not_awaited(self) -> None:
        assert not self.records

    def reset_mock(self) -> None:
        """Clear records while retaining the endpoint's configured result and fault."""
        self.records.clear()


@dataclass(slots=True)
class _Identity:
    id: int
    send: AsyncCallRecorder = field(default_factory=AsyncCallRecorder)


@dataclass(frozen=True, slots=True)
class _MessageFlags:
    components_v2: bool
    ephemeral: bool


@dataclass(frozen=True, slots=True)
class _InteractionResult:
    message_id: int | None
    ephemeral: bool
    resource: None = None

    def is_ephemeral(self) -> bool:
        return self.ephemeral


class _InteractionResponseHarness:
    def __init__(self, message_id: int) -> None:
        self._done = False
        self.type: discord.InteractionResponseType | None = None
        self.edit_message = self._responds(discord.InteractionResponseType.message_update, message_id)
        self.defer = self._responds(discord.InteractionResponseType.deferred_message_update, message_id)
        self.send_message = self._responds(discord.InteractionResponseType.channel_message, message_id)
        self.send_modal = self._responds(discord.InteractionResponseType.modal, message_id)
        self.pong = self._responds(discord.InteractionResponseType.pong, message_id)

    def is_done(self) -> bool:
        return self._done

    def _responds(self, kind: discord.InteractionResponseType, message_id: int) -> AsyncCallRecorder:
        endpoint = AsyncCallRecorder()

        async def record(*_args: Any, **kwargs: Any) -> _InteractionResult:
            self._done = True
            # A "thinking" defer is a deferred channel message, the shape discord.py reports for it.
            thinking = kind is discord.InteractionResponseType.deferred_message_update and kwargs.get("thinking")
            self.type = discord.InteractionResponseType.deferred_channel_message if thinking else kind
            response_message_id = message_id if kind is discord.InteractionResponseType.channel_message else None
            return _InteractionResult(response_message_id, bool(kwargs.get("ephemeral", False)))

        endpoint.callback = record
        return endpoint


_FOLLOW_UP_IDS = itertools.count(1_000)


class _FollowupSend(AsyncCallRecorder):
    """A follow-up endpoint that, like discord.py, returns the new message only when asked to wait.

    A configured result or callback still decides the answer; this supplies a fresh message
    only where the recorder would have returned None for a waited send.
    """

    @override
    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        result = await super().__call__(*args, **kwargs)
        if result is None and self.callback is None and kwargs.get("wait"):
            return MessageHarness(message_id=next(_FOLLOW_UP_IDS))
        return result


@dataclass(slots=True)
class _FollowupHarness:
    send: AsyncCallRecorder = field(default_factory=_FollowupSend)
    edit_message: AsyncCallRecorder = field(default_factory=AsyncCallRecorder)
    delete_message: AsyncCallRecorder = field(default_factory=AsyncCallRecorder)


class MessageHarness:
    """A message-shaped source with recorded edit and delete calls.

    Calls do not simulate server-side state changes or deletion. Configure endpoint errors
    explicitly when testing a message that has become unavailable.
    """

    def __init__(
        self,
        *,
        message_id: int = 99,
        ephemeral: bool = False,
        channel_id: int = 5,
        guild_id: int | None = 7,
        components_v2: bool = True,
    ) -> None:
        self.id = message_id
        self.flags = _MessageFlags(components_v2, ephemeral)
        self.channel = _Identity(channel_id)
        self.guild = None if guild_id is None else _Identity(guild_id)
        self.jump_url = f"https://discord.com/channels/{guild_id or '@me'}/{channel_id}/{message_id}"
        self.edit = AsyncCallRecorder(result=self)
        self.delete = AsyncCallRecorder()

    @property
    def source(self) -> Any:
        """The message-shaped object passed to production code."""
        return self

    @property
    def edits(self) -> list[CallRecord]:
        return self.edit.records

    @property
    def deletions(self) -> list[CallRecord]:
        return self.delete.records

    def fail_edits_with(self, error: BaseException | None) -> Self:
        """Inject `error` into subsequent edits, or clear the fault with `None`."""
        self.edit.error = error
        return self


class InteractionHarness:
    """A stateful Discord interaction harness.

    The interaction expires when `expired` is set. Production calls go through `.source`; typed
    response, edit, send, deletion, modal, and deferral records remain available for assertions.
    """

    def __init__(
        self,
        user_id: int = 1,
        *,
        message_id: int = 99,
        expired: bool = False,
        components_v2: bool = True,
        client: Any = None,
    ) -> None:
        self.user = _Identity(user_id)
        self.message_harness = MessageHarness(message_id=message_id, components_v2=components_v2)
        self.message = self.message_harness.source
        self.client = client
        self.channel = self.message.channel
        self.channel_id = self.channel.id
        self.guild = self.message.guild
        self.guild_id = self.message.guild.id if self.message.guild is not None else None
        self.locale = "en-US"
        self.guild_locale = "en-US"
        self.extras: dict[Any, Any] = {}
        self.command: Any = None
        self.response = _InteractionResponseHarness(message_id)
        self.followup = _FollowupHarness()
        self.original_response = AsyncCallRecorder(result=self.message)
        self.edit_original_response = AsyncCallRecorder(result=self.message)
        self.delete_original_response = AsyncCallRecorder()
        self.expires_at = datetime.now(UTC) + timedelta(minutes=15)
        self.expired = expired

    @property
    def source(self) -> Any:
        """The interaction-shaped object passed to production code."""
        return self

    @property
    def edits(self) -> list[CallRecord]:
        return [
            *self.response.edit_message.records,
            *self.followup.edit_message.records,
            *self.edit_original_response.records,
        ]

    @property
    def sends(self) -> list[CallRecord]:
        return [*self.response.send_message.records, *self.followup.send.records]

    @property
    def deletions(self) -> list[CallRecord]:
        return [*self.followup.delete_message.records, *self.delete_original_response.records]

    @property
    def modals(self) -> list[CallRecord]:
        return self.response.send_modal.records

    @property
    def deferrals(self) -> list[CallRecord]:
        return self.response.defer.records

    def is_expired(self) -> bool:
        return self.expired


class ContextHarness:
    """A minimal context source with typed send and reply records."""

    def __init__(self, *, message: MessageHarness | None = None, bot: Any = None, user_id: int = 1) -> None:
        self.message_harness = message if message is not None else MessageHarness()
        self.message = self.message_harness.source
        self.send = AsyncCallRecorder(result=self.message)
        self.reply = AsyncCallRecorder(result=self.message)
        self.bot = bot
        self.interaction: Any = None
        self.command: Any = None
        self.author = _Identity(user_id)
        self.guild: Any = None

    @property
    def source(self) -> Any:
        """The context-shaped object passed to production code."""
        return self

    @property
    def sends(self) -> list[CallRecord]:
        return self.send.records

    @property
    def replies(self) -> list[CallRecord]:
        return self.reply.records


def interaction_harness(
    user_id: int = 1, *, message_id: int = 99, expired: bool = False, components_v2: bool = True
) -> Any:
    """Build an interaction harness with an unconsumed response."""
    return InteractionHarness(user_id, message_id=message_id, expired=expired, components_v2=components_v2)


def message_harness(
    *,
    message_id: int = 99,
    ephemeral: bool = False,
    channel_id: int = 5,
    guild_id: int | None = 7,
    components_v2: bool = True,
) -> Any:
    """Build a message harness whose edit result is its own source."""
    return MessageHarness(
        message_id=message_id,
        ephemeral=ephemeral,
        channel_id=channel_id,
        guild_id=guild_id,
        components_v2=components_v2,
    )


def delivered_to(message: Any, *, handle: EditHandle | None = None) -> MessageDestination:
    """A destination that hands `message` straight back — a send with no Discord in it.

    The message root ends up holding exactly the handle a real send would have given it, so tests
    about editing, refreshing and finishing can start from a delivered message root.
    """

    authority = handle if handle is not None else handle_for(message)

    async def send(payload: MessagePayload) -> DeliveryResult:
        return DeliveryResult(message, authority)

    return send


def http_error(status: int = 500, *, code: int = 0, message: str = "nope") -> discord.HTTPException:
    """A generic Discord failure, for the paths that only care that the write did not land."""

    @dataclass(frozen=True, slots=True)
    class Response:
        status: int
        reason: str

    response = Response(status, message)
    return discord.HTTPException(response, {"code": code, "message": message})  # type: ignore[arg-type]


def stale_http_error() -> discord.HTTPException:
    """Discord's way of saying the credentials behind a write are gone.

    Code 10015 specifically: an unknown webhook is what an expired interaction token looks
    like from the other side, and the recovery path keys on it rather than on the status.
    """
    return http_error(404, code=10015, message="Unknown Webhook")
