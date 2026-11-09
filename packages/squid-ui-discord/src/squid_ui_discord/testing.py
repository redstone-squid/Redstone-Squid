"""Gateway-free Discord doubles and assertions for serialized component payloads.

Use ``stage`` to present a component through an isolated running UI runtime and drive its
controls. Lower-level tests can send a MessageRoot to ``delivered_to(message_harness())``
and call dispatch with an ``interaction_harness``. The harnesses record sends and edits.

The ``payload_*`` queries inspect serialized controls, while ``assert_within_limits``
checks supported size limits. These helpers do not implement Discord's complete validation
or network behavior. Import them from ``squid_ui_discord.testing``; the package root does
not re-export testing helpers.
"""

import asyncio
import hashlib
import inspect
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager
from copy import copy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Self, Unpack, cast

import anyio
import discord
from discord.ext import commands

from squid_ui import scene
from squid_ui.planning.adapter import AdapterCapability, AdapterProfile
from squid_ui.planning.discord import components_v2_target
from squid_ui.planning.limits import COMPONENT_LIMITS, LIMITS, ComponentLimits, MessageLimits, V2Limits
from squid_ui.planning.target import Target
from squid_ui.runtime.component import Component
from squid_ui.target_types import ComponentsV2Target, DiscordAdapter
from squid_ui_discord.commands import bind_context_menu, context_menu_declaration
from squid_ui_discord.config import Config
from squid_ui_discord.delivery import DeliveryResult, EditHandle, MessageDestination, handle_for
from squid_ui_discord.facade import Scope
from squid_ui_discord.message_payload import MessagePayload
from squid_ui_discord.message_root import MessageRoot
from squid_ui_discord.message_root_scheduler import MessageRootScheduler
from squid_ui_discord.message_root_wiring import AnyMountedView, ClassicMountedView, MountedView
from squid_ui_discord.rendering import render_static
from squid_ui_discord.request import Request
from squid_ui_discord.response import Presented, ResponseOverrides
from squid_ui_discord.runtime import DiscordUIRuntime, install

type ComponentPayload = dict[str, Any]
type BuiltView = discord.ui.LayoutView | discord.ui.View | list[ComponentPayload]
"""Anything the payload queries read: either kind of built view, or a payload already taken."""


def without_capabilities[LimitsT: MessageLimits, BodyT: scene.Body, RenderTargetT, AdapterT](
    target: Target[LimitsT, BodyT, RenderTargetT, AdapterT], *capabilities: str
) -> Target[LimitsT, BodyT, RenderTargetT, AdapterT]:
    """A copy of `target` whose dialect declares fewer protocol capabilities.

    Every degradation path is reached by a target that lacks something, and a protocol
    capability is a fact about the dialect rather than a field on the target — so removing
    one means a dialect that declares less. Adapter capabilities are not affected; use
    `Target.restrict_adapter_capabilities` for those.
    """
    reduced = copy(target.dialect)
    reduced.capabilities = frozenset(target.dialect.capabilities) - {*capabilities}
    return replace(target, dialect=reduced)


def iter_component_payloads(components: list[ComponentPayload]) -> Iterator[ComponentPayload]:
    """Yield every component dict in a payload tree, depth first."""
    for component in components:
        yield component
        yield from iter_component_payloads(component.get("components", []))
        for key in ("accessory", "component"):
            nested = component.get(key)
            if nested is not None:
                yield from iter_component_payloads([nested])


def _check_string(problems: list[str], component: ComponentPayload, key: str, limit: int, where: str) -> None:
    value = component.get(key)
    if isinstance(value, str) and len(value) > limit:
        problems.append(f"{where} {key} is {len(value)} chars (limit {limit})")


def payload_problems(components: list[ComponentPayload], *, limits: V2Limits = LIMITS) -> list[str]:
    """Report the component counts and string lengths checked by this helper."""
    problems: list[str] = []
    flattened = list(iter_component_payloads(components))

    if len(flattened) > limits.total_components:
        problems.append(f"{len(flattened)} components (limit {limits.total_components})")

    total_text = sum(len(c.get("content", "")) for c in flattened if c.get("type") == 10)
    if total_text > limits.total_text:
        problems.append(f"total display text is {total_text} chars (limit {limits.total_text})")

    for component in flattened:
        match component.get("type"):
            case 2:
                _check_string(problems, component, "label", limits.components.button_label, "button")
                _check_string(problems, component, "custom_id", limits.components.custom_id, "button")
            case 3:
                _check_string(problems, component, "placeholder", limits.components.select_placeholder, "select")
                options = component.get("options", [])
                if len(options) > limits.components.select_options:
                    problems.append(f"{len(options)} select options (limit {limits.components.select_options})")
                for option in options:
                    _check_string(problems, option, "label", limits.components.option_label, "option")
                    _check_string(problems, option, "value", limits.components.option_value, "option")
                    _check_string(problems, option, "description", limits.components.option_description, "option")
            case 4:
                _check_string(
                    problems, component, "placeholder", limits.components.text_input_placeholder, "text input"
                )
                _check_string(problems, component, "value", limits.components.text_input_value, "text input")
                max_length = component.get("max_length")
                if max_length is not None and max_length > limits.components.text_input_value:
                    problems.append(f"text input max_length {max_length} (limit {limits.components.text_input_value})")
            case 5 | 6 | 7 | 8:
                _check_string(problems, component, "placeholder", limits.components.select_placeholder, "select")
            case 9:
                texts = component.get("components", [])
                if len(texts) > limits.section_texts:
                    problems.append(f"section holds {len(texts)} texts (limit {limits.section_texts})")
            case 12:
                items = component.get("items", [])
                if len(items) > limits.gallery_items:
                    problems.append(f"{len(items)} gallery items (limit {limits.gallery_items})")
                for item in items:
                    _check_string(problems, item, "description", limits.gallery_item_description, "gallery item")
            case 18:
                _check_string(problems, component, "label", limits.components.label_text, "label")
                _check_string(problems, component, "description", limits.components.label_description, "label")
            case _:
                pass

    return problems


def modal_problems(payload: dict[str, Any], *, limits: ComponentLimits = COMPONENT_LIMITS) -> list[str]:
    """Report supported modal size checks; this is not complete Discord schema validation."""
    problems: list[str] = []
    title = payload.get("title", "")
    if len(title) > limits.modal_title:
        problems.append(f"modal title is {len(title)} chars (limit {limits.modal_title})")
    components = payload.get("components", [])
    if len(components) > limits.modal_components:
        problems.append(f"modal holds {len(components)} components (limit {limits.modal_components})")
    # A modal holds no gallery or section, so only the component half of the V2
    # table can matter; carry the caller's over so an overridden cap still applies.
    problems.extend(payload_problems(components, limits=replace(LIMITS, components=limits)))
    return problems


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


@dataclass(slots=True)
class _FollowupHarness:
    send: AsyncCallRecorder = field(default_factory=AsyncCallRecorder)
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


def commit_render(message_root: MessageRoot, *, disabled: bool = False) -> MountedView:
    """Commit a staged V2 render without loading components or delivering to Discord.

    This advances handlers and generation directly for synchronous render assertions.
    Use ``await root.send(delivered_to(message_harness()))`` to exercise the load and
    send lifecycle.
    """
    view = _commit(message_root, disabled=disabled)
    assert isinstance(view, MountedView), "this message root draws a classic message; use commit_classic_render"
    return view


def commit_classic_render(message_root: MessageRoot, *, disabled: bool = False) -> ClassicMountedView:
    """Commit a classic render without loading or delivery, asserting its native view type."""
    view = _commit(message_root, disabled=disabled)
    assert isinstance(view, ClassicMountedView), "this message root draws a Components V2 message; use commit_render"
    return view


def _commit(message_root: MessageRoot, *, disabled: bool) -> AnyMountedView:
    view = message_root._stage_view(disabled=disabled)
    candidate = message_root._pending
    assert candidate is not None and candidate.view is view
    message_root._commit(candidate)
    return view


def assert_within_limits(built: discord.ui.LayoutView | discord.ui.Modal, *, limits: V2Limits = LIMITS) -> None:
    """Assert that the serialized view or modal passes the supported size checks."""
    if isinstance(built, discord.ui.Modal):
        problems = modal_problems(built.to_dict(), limits=limits.components)
    else:
        problems = payload_problems(built.to_components(), limits=limits)
    assert not problems, "; ".join(problems)


# --- Payload queries ------------------------------------------------------------------------


def _payloads(built: BuiltView) -> list[ComponentPayload]:
    components = built if isinstance(built, list) else built.to_components()
    return list(iter_component_payloads(components))


def payload_texts(built: BuiltView) -> list[str]:
    """Every display text Discord will receive, in order.

    Reads the serialized payload rather than walking `discord.ui` objects. The two agree on
    content, but only this one keeps agreeing when discord.py changes how it builds the tree --
    and, unlike matching a substring against `str(view.to_components())`, it cannot pass
    because the string turned up in a custom id or a placeholder.
    """
    return [component["content"] for component in _payloads(built) if component.get("type") == 10]


def payload_labels(built: BuiltView) -> list[str]:
    """Every button label Discord will receive, in order."""
    return [component["label"] for component in _payloads(built) if component.get("type") == 2 and "label" in component]


def payload_custom_ids(built: BuiltView) -> list[str]:
    """Every custom id Discord will receive, in order, across every interactive component."""
    return [component["custom_id"] for component in _payloads(built) if "custom_id" in component]


# --- Target and view construction -----------------------------------------------------------


def target_profile(
    name: str = "test", *, capabilities: frozenset[AdapterCapability] = frozenset(), limits: V2Limits = LIMITS
) -> Any:
    """A V2 target whose adapter supplies exactly `capabilities` and no extensions.

    Capabilities that are not Discord protocol facts belong to the adapter axis, which is what
    lets a test vary them without inventing a dialect. Use `without_capabilities` to take a
    *protocol* capability away from a target that already exists.
    """
    return components_v2_target(AdapterProfile(DiscordAdapter, name, ">=1", capabilities=capabilities), limits=limits)


def static_view(*args: Any, **kwargs: Any) -> discord.ui.LayoutView:
    """The drawn layout of a sessionless V2 document, for tests that only read components."""
    return render_static(*args, **kwargs).layout


def layout_view(*items: discord.ui.Item[Any], timeout: float | None = None) -> discord.ui.LayoutView:
    """A host-owned discord.py view holding `items` -- the shape adoption and conform start from."""
    view = discord.ui.LayoutView(timeout=timeout)
    for item in items:
        view.add_item(item)
    return view


def action_row(*items: Any) -> discord.ui.ActionRow[Any]:
    """A discord.py action row holding `items`."""
    row: discord.ui.ActionRow[Any] = discord.ui.ActionRow()
    for item in items:
        row.add_item(item)
    return row


# --- Failure injection ----------------------------------------------------------------------


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


# --- Scheduling -----------------------------------------------------------------------------


async def drain(scheduler: MessageRootScheduler, *, timeout: float = 1) -> None:
    """Run the scheduler until its queue join completes, then cancel it.

    The timeout is in seconds. Newly queued work also contributes to the join, so tests
    must avoid producers that enqueue forever.
    """
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(scheduler.run)
        await asyncio.wait_for(scheduler._queue.join(), timeout=timeout)
        tasks.cancel_scope.cancel()


# --- Commands -------------------------------------------------------------------------------


async def invoke_context_menu(owner: object, method: Any, interaction: Any, target: Any) -> None:
    """Run one `@sd.context_menu` method through the dispatch its registered menu would use.

    `method` is the declared function, unbound or bound; the defer and return handling of its
    declaration apply, so a test sees the same interaction traffic as production.
    """
    callback: Any = getattr(method, "__func__", method)
    declaration = context_menu_declaration(callback)
    if declaration is None:
        message = "the method carries no @sd.context_menu declaration"
        raise TypeError(message)
    menu = bind_context_menu(owner, callback, declaration)
    await menu.callback(interaction, target)


@dataclass(frozen=True, slots=True)
class StagedControl:
    """One currently rendered control selected by its semantic key."""

    key: str
    custom_id: str
    payload: Mapping[str, Any]

    @property
    def label(self) -> str | None:
        """The rendered label, when this control has one."""
        return cast(str | None, self.payload.get("label"))


class StagedForm:
    """A generated Discord modal whose authority ends after ``submit()``."""

    def __init__(self, modal: discord.ui.Modal, *, client: discord.Client, user_id: int) -> None:
        self.modal = modal
        self._client = client
        self._user_id = user_id
        self._submitted = False

    async def submit(self, values: Mapping[str, object]) -> InteractionHarness:
        """Populate native modal controls and invoke their real submission callback once."""
        if self._submitted:
            message = "this staged form was already submitted"
            raise RuntimeError(message)
        remaining = dict(values)
        for item in self.modal.walk_children():
            key = getattr(item, "custom_id", None)
            if not isinstance(key, str) or key not in remaining:
                continue
            value = remaining.pop(key)
            if isinstance(item, discord.ui.TextInput):
                item._value = str(value)  # pyrefly: ignore[missing-attribute]
            elif isinstance(item, discord.ui.Checkbox):
                item._value = bool(value)  # pyrefly: ignore[missing-attribute]
            elif isinstance(item, discord.ui.Select):
                item._values = [str(entry) for entry in _sequence(value)]  # pyrefly: ignore[missing-attribute]
            else:
                item._values = list(_sequence(value))  # pyright: ignore[reportAttributeAccessIssue]  # pyrefly: ignore[missing-attribute]
        if remaining:
            names = ", ".join(sorted(remaining))
            message = f"staged form has no fields: {names}"
            raise KeyError(message)
        interaction = _interaction(self._client, self._user_id)
        self._submitted = True
        await self.modal.on_submit(cast(discord.Interaction, interaction.source))
        return interaction

    def assert_within_limits(self) -> None:
        """Check the generated modal against the supported serialization size limits."""
        assert_within_limits(self.modal)


def _sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return value
    return (value,)


class StagedUI[ComponentT: Component[ComponentsV2Target, Request]]:
    """A facade-presented component; the stage context ends its root."""

    def __init__(
        self,
        result: Presented[ComponentT],
        *,
        client: discord.Client,
        user_id: int,
        interaction: InteractionHarness,
    ) -> None:
        self.result = result
        self.component = result.component
        self.root = result.root
        self.session = result.session
        self.delivery = result.delivery
        self.interaction = interaction
        self._client = client
        self._user_id = user_id

    def texts(self) -> list[str]:
        """Return display texts from the current serialized Discord payload."""
        return payload_texts(self._view())

    def control(self, key: str) -> StagedControl:
        """Select one currently rendered control by stable semantic key."""
        expected = _custom_id(self.root, key)
        payloads = iter_component_payloads(self._view().to_components())
        payload = next((candidate for candidate in payloads if candidate.get("custom_id") == expected), None)
        if payload is None:
            available = ", ".join(self.root.snapshot().handler_keys)
            message = f"no rendered control {key!r}; available semantic keys: {available}"
            raise KeyError(message)
        return StagedControl(key, expected, payload)

    async def press(self, key: str, *, user_id: int | None = None) -> InteractionHarness:
        """Dispatch one button or form trigger through ``MessageRoot.dispatch``."""
        control = self.control(key)
        if control.payload.get("type") != 2:
            message = f"control {key!r} is not a button"
            raise TypeError(message)
        interaction = _interaction(self._client, self._user_id if user_id is None else user_id)
        await self.root.dispatch(
            key,
            cast(discord.Interaction, interaction.source),
            generation=self.root.generation,
        )
        return interaction

    async def select(
        self,
        key: str,
        values: Sequence[str],
        *,
        user_id: int | None = None,
    ) -> InteractionHarness:
        """Dispatch string choices through ``MessageRoot.dispatch``."""
        control = self.control(key)
        if control.payload.get("type") not in {3, 5, 6, 7, 8}:
            message = f"control {key!r} is not a select"
            raise TypeError(message)
        interaction = _interaction(self._client, self._user_id if user_id is None else user_id)
        await self.root.dispatch(
            key,
            cast(discord.Interaction, interaction.source),
            list(values),
            generation=self.root.generation,
        )
        return interaction

    async def press_for_form(self, key: str, *, user_id: int | None = None) -> StagedForm:
        """Press a semantic form trigger and return the generated native modal."""
        actor = self._user_id if user_id is None else user_id
        interaction = await self.press(key, user_id=actor)
        records = interaction.modals
        if len(records) != 1 or not records[0].args or not isinstance(records[0].args[0], discord.ui.Modal):
            message = f"control {key!r} did not open exactly one modal"
            raise AssertionError(message)
        return StagedForm(records[0].args[0], client=self._client, user_id=actor)

    def assert_within_limits(self) -> None:
        """Check the current view against the supported serialization size limits."""
        assert_within_limits(self._view())

    def _view(self) -> discord.ui.LayoutView:
        view = self.root._view  # pyrefly: ignore[missing-attribute]
        if not isinstance(view, discord.ui.LayoutView):
            message = "the staged root has no current Components V2 view"
            raise TypeError(message)
        return view


def _custom_id(root: MessageRoot, key: str) -> str:
    prefix = f"ctl:{root.id}:{root.generation}:"
    candidate = f"{prefix}{key}"
    if len(candidate) <= 100:
        return candidate
    return f"{prefix}#{hashlib.blake2s(key.encode()).hexdigest()[:12]}"


def _owner_config(owner: object, configured: Config | None) -> Config:
    if configured is not None:
        return configured
    ui = getattr(owner, "ui", None)
    if isinstance(ui, Scope):
        return ui.runtime.config
    if isinstance(ui, DiscordUIRuntime):
        return ui.config
    return Config()


def _interaction(client: discord.Client, user_id: int) -> InteractionHarness:
    return InteractionHarness(user_id=user_id, client=client)


@asynccontextmanager
async def stage[OwnerT, ComponentT: Component[ComponentsV2Target, Request]](
    content: ComponentT,
    *,
    owner: OwnerT,
    user_id: int = 1,
    config: Config | None = None,
    **overrides: Unpack[ResponseOverrides],
) -> AsyncIterator[StagedUI[ComponentT]]:
    """Present a component in an isolated UI runtime for the context's lifetime.

    For example, ``async with stage(screen, owner=cog) as ui`` lets a test call
    ``await ui.press("save")`` and inspect ``ui.texts()``. Exit closes the runtime and
    its message roots, even if an assertion fails.
    """
    client = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    runtime = install(client, _owner_config(owner, config))
    client.ui = runtime  # type: ignore[attr-defined]
    scope = runtime.scope(owner)
    interaction = _interaction(client, user_id)
    try:
        async with anyio.create_task_group() as tasks:
            await tasks.start(runtime.run)
            try:
                result = await (await scope.request(interaction.source)).respond(content, **overrides)
                if not isinstance(result, Presented):
                    message = f"staging live content produced {type(result).__name__}"
                    raise TypeError(message)
                staged = StagedUI(
                    cast(Presented[ComponentT], result), client=client, user_id=user_id, interaction=interaction
                )
                yield staged
            finally:
                with anyio.CancelScope(shield=True):
                    await runtime.close()
                tasks.cancel_scope.cancel()
    finally:
        await client.close()


async def invoke(
    command: object,
    *args: object,
    owner: object | None = None,
    client: discord.Client | None = None,
    user_id: int = 1,
    source: Literal["interaction", "context"] = "interaction",
    **kwargs: object,
) -> InteractionHarness | ContextHarness:
    """Invoke a real outward Squid command wrapper with a gateway-free native source."""
    callback = getattr(command, "callback", command)
    if not callable(callback):
        message = "invoke() needs a command or async command callback"
        raise TypeError(message)
    selected_client = client or _client_for(owner)
    if source == "interaction":
        harness: InteractionHarness | ContextHarness = _interaction(selected_client, user_id)
    else:
        harness = ContextHarness(bot=selected_client, user_id=user_id)
    positional = (harness.source, *args) if owner is None else (owner, harness.source, *args)
    result = callback(*positional, **kwargs)
    if not inspect.isawaitable(result):
        message = "invoke() command callback did not return an awaitable"
        raise TypeError(message)
    await result
    return harness


def _client_for(owner: object | None) -> discord.Client:
    for candidate in (getattr(owner, "ui", None),):
        if isinstance(candidate, Scope):
            return candidate.runtime.client
        if isinstance(candidate, DiscordUIRuntime):
            return candidate.client
    if isinstance(owner, discord.Client):
        return owner
    message = "invoke() needs owner UI authority or an explicit installed client"
    raise TypeError(message)


__all__ = [
    "CallRecord",
    "ContextHarness",
    "InteractionHarness",
    "MessageHarness",
    "StagedControl",
    "StagedForm",
    "StagedUI",
    "action_row",
    "assert_within_limits",
    "commit_classic_render",
    "commit_render",
    "delivered_to",
    "drain",
    "http_error",
    "interaction_harness",
    "invoke",
    "invoke_context_menu",
    "iter_component_payloads",
    "layout_view",
    "message_harness",
    "modal_problems",
    "payload_custom_ids",
    "payload_labels",
    "payload_problems",
    "payload_texts",
    "stage",
    "stale_http_error",
    "static_view",
    "target_profile",
    "without_capabilities",
]
