"""Doubles and payload assertions for exercising a message root with no Discord attached.

Two halves. `fake_interaction`, `fake_message`, `delivered_to` and `commit_render` stand in
for the Discord boundary, so a test can send a message root to nowhere and then drive it through
`MessageRoot.dispatch` — the same funnel a real press takes. `payload_problems`, `modal_problems`
and `assert_within_limits` check the other end: they walk the serialized wire payload, not the
Python objects, so they verify exactly what Discord will see, including any chrome discord.py
adds during serialization.

This module is public and versioned like the rest of the package. It is imported by tests
rather than by a running bot, so it is reachable as `squid_ui_discord.testing.X` and promotes no
names to `squid_ui_discord` itself.
"""

from collections.abc import Iterator
from copy import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord

from squid_ui import scene
from squid_ui.planning.limits import COMPONENT_LIMITS, LIMITS, ComponentLimits, DiscordLimits, V2Limits
from squid_ui.planning.target import Target
from squid_ui_discord.delivery import DeliveryResult, EditHandle, MessageDestination, handle_for
from squid_ui_discord.message_payload import MessagePayload
from squid_ui_discord.message_root import AnyMountedView, ClassicMountedView, MessageRoot, MountedView

type ComponentPayload = dict[str, Any]


def without_capabilities[LimitsT: DiscordLimits, BodyT: scene.Body, ModeT, AdapterT](
    target: Target[LimitsT, BodyT, ModeT, AdapterT], *capabilities: str
) -> Target[LimitsT, BodyT, ModeT, AdapterT]:
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


def _fake_message_shape(
    message_id: int, *, ephemeral: bool, channel_id: int, guild_id: int | None, components_v2: bool = True
) -> Any:
    """The read-only half of a message: what it is and where it lives."""
    return SimpleNamespace(
        id=message_id,
        flags=SimpleNamespace(components_v2=components_v2, ephemeral=ephemeral),
        channel=SimpleNamespace(id=channel_id),
        guild=None if guild_id is None else SimpleNamespace(id=guild_id),
        jump_url=f"https://discord.com/channels/{guild_id or '@me'}/{channel_id}/{message_id}",
    )


def fake_interaction(
    user_id: int = 1, *, message_id: int = 99, expired: bool = False, components_v2: bool = True
) -> Any:
    """A minimal interaction double for exercising message roots without Discord.

    `response.is_done()` starts false; flip `interaction.response._done` to simulate a
    consumed response, and set `interaction.response.type` to say what consumed it — a
    non-update type is what makes `delivery.handle_from` refuse the interaction. All
    send/edit surfaces are AsyncMocks.
    """
    response = SimpleNamespace(_done=False, is_done=lambda: response._done, type=None)

    def _responds(kind: discord.InteractionResponseType) -> AsyncMock:
        """A response surface that consumes the response the way discord.py's does."""

        async def record(*args: Any, **kwargs: Any) -> Any:
            response._done = True
            response.type = kind
            return SimpleNamespace(
                resource=None,
                message_id=message_id if kind is discord.InteractionResponseType.channel_message else None,
                is_ephemeral=lambda: bool(kwargs.get("ephemeral", False)),
            )

        return AsyncMock(side_effect=record)

    response.edit_message = _responds(discord.InteractionResponseType.message_update)
    response.defer = _responds(discord.InteractionResponseType.deferred_message_update)
    response.send_message = _responds(discord.InteractionResponseType.channel_message)
    response.send_modal = _responds(discord.InteractionResponseType.modal)
    message = _fake_message_shape(message_id, ephemeral=False, channel_id=5, guild_id=7, components_v2=components_v2)
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        guild_id=message.guild.id if message.guild is not None else None,
        message=message,
        response=response,
        followup=SimpleNamespace(send=AsyncMock(), edit_message=AsyncMock(), delete_message=AsyncMock()),
        original_response=AsyncMock(return_value=message),
        edit_original_response=AsyncMock(return_value=message),
        delete_original_response=AsyncMock(),
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        is_expired=lambda: expired,
    )


def fake_message(
    *,
    message_id: int = 99,
    ephemeral: bool = False,
    channel_id: int = 5,
    guild_id: int | None = 7,
    components_v2: bool = True,
) -> Any:
    """A minimal message double whose `edit` returns itself, as Discord's does.

    `components_v2=False` is a pre-Components-V2 message, which is what the classic-to-V2
    transition needs something to start from.
    """
    message = _fake_message_shape(
        message_id, ephemeral=ephemeral, channel_id=channel_id, guild_id=guild_id, components_v2=components_v2
    )
    message.edit = AsyncMock()
    message.edit.return_value = message
    message.delete = AsyncMock()
    return message


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
    `await message root.send(delivered_to(fake_message()))`.
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


__all__ = [
    "assert_within_limits",
    "commit_classic_render",
    "commit_render",
    "delivered_to",
    "fake_interaction",
    "fake_message",
    "iter_component_payloads",
    "modal_problems",
    "payload_problems",
    "without_capabilities",
]
