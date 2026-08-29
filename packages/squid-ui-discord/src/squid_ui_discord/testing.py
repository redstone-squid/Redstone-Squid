"""Doubles and payload assertions for exercising a message root with no Discord attached.

Two halves. `interaction_harness`, `message_harness`, `delivered_to` and `commit_render` stand in
for the Discord boundary, so a test can send a message root to nowhere and then drive it through
`MessageRoot.dispatch` — the same funnel a real press takes. `payload_problems`, `modal_problems`,
`assert_within_limits` and the `payload_*` queries check the other end: they walk the
serialized wire payload, not the Python objects, so they verify exactly what Discord will see,
including any chrome discord.py adds during serialization.

This module is public and versioned like the rest of the package. It is imported by tests
rather than by a running bot, so it is reachable as `squid_ui_discord.testing.X` and promotes no
names to `squid_ui_discord` itself.
"""

import asyncio
import inspect
from collections.abc import Iterator
from copy import copy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Self

import anyio
import discord

from squid_ui import scene
from squid_ui.planning.adapter import AdapterCapability, AdapterProfile
from squid_ui.planning.discord import components_v2_target
from squid_ui.planning.limits import COMPONENT_LIMITS, LIMITS, ComponentLimits, MessageLimits, V2Limits
from squid_ui.planning.target import Target
from squid_ui.planning.types import DiscordAdapter
from squid_ui_discord.delivery import DeliveryResult, EditHandle, MessageDestination, handle_for
from squid_ui_discord.message_payload import MessagePayload
from squid_ui_discord.message_root import MessageRoot
from squid_ui_discord.message_root_scheduler import MessageRootScheduler
from squid_ui_discord.message_root_wiring import AnyMountedView, ClassicMountedView, MountedView
from squid_ui_discord.rendering import render_static

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
    """Return every limit violation in a message component payload."""
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
    """Return every limit violation in a modal payload."""
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
    """A typed async endpoint that records calls and can inject one explicit fault."""

    def __init__(self, *, result: Any = None, error: BaseException | None = None) -> None:
        self.records: list[CallRecord] = []
        self.result = result
        self.error = error
        self.callback: Any = None

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
            self.type = kind
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
    """A stateful Discord message harness.

    `delete` ends the source message's simulated availability. Production calls go through
    `.source`; edit and deletion records remain on the harness for assertions.
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
        self, user_id: int = 1, *, message_id: int = 99, expired: bool = False, components_v2: bool = True
    ) -> None:
        self.user = _Identity(user_id)
        self.message_harness = MessageHarness(message_id=message_id, components_v2=components_v2)
        self.message = self.message_harness.source
        self.guild_id = self.message.guild.id if self.message.guild is not None else None
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
        return [*self.response.edit_message.records, *self.followup.edit_message.records, *self.edit_original_response.records]

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

    def __init__(self, *, message: MessageHarness | None = None) -> None:
        self.message_harness = message if message is not None else MessageHarness()
        self.message = self.message_harness.source
        self.send = AsyncCallRecorder(result=self.message)
        self.reply = AsyncCallRecorder(result=self.message)

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
    """Stage a render and commit it with no Discord delivery — `MessageRoot.send` to nowhere.

    `MessageRoot._stage_view` only stages; handlers and the live generation move when a delivery
    lands. Tests that never touch Discord say where that point is with this, rather than
    driving a destination that would only ever hand back `None`.

    Reaches past `send` on purpose: the alternative is making every one of these call sites
    await, for no coverage of anything the real send path does. For the same reason it runs no
    `on_load` -- a test that wants a loaded render wants the real seam,
    `await message root.send(delivered_to(message_harness()))`.
    """
    view = _commit(message_root, disabled=disabled)
    assert isinstance(view, MountedView), "this message root draws a classic message; use commit_classic_render"
    return view


def commit_classic_render(message_root: MessageRoot, *, disabled: bool = False) -> ClassicMountedView:
    """`commit_render` for a message root whose target draws a classic message.

    A separate function rather than a widened return type: a test knows which kind of message root
    it built, and every V2 caller would otherwise have to narrow a union it can never see.
    """
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
    """Assert that a built view or modal serializes within every Discord limit."""
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
    """Run `scheduler` until its queue is empty, then cancel it.

    Reaches into `_queue` on purpose, the way `commit_render` reaches past `send`: the queue's
    join is the only honest "everything enqueued has been handled" signal, and the alternative
    is polling a render count and hoping. Four test files each carried this, all four reaching
    into the same private, and one of them took a `bus` argument it immediately discarded.
    """
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(scheduler.run)
        await asyncio.wait_for(scheduler._queue.join(), timeout=timeout)
        tasks.cancel_scope.cancel()


__all__ = [
    "CallRecord",
    "ContextHarness",
    "InteractionHarness",
    "MessageHarness",
    "action_row",
    "assert_within_limits",
    "commit_classic_render",
    "commit_render",
    "delivered_to",
    "drain",
    "http_error",
    "interaction_harness",
    "iter_component_payloads",
    "layout_view",
    "message_harness",
    "modal_problems",
    "payload_custom_ids",
    "payload_labels",
    "payload_problems",
    "payload_texts",
    "stale_http_error",
    "static_view",
    "target_profile",
    "without_capabilities",
]
