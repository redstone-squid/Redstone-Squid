"""Structural verification of built payloads against the limits table.

`assert_within_limits` walks the serialized wire payload — not the Python objects — so it
verifies exactly what Discord will see, including any chrome discord.py adds during
serialization.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord

from squid_layouts.discord.delivery import Destination
from squid_layouts.discord.mount import Mount, MountedView
from squid_layouts.planning.limits import LIMITS, V2Limits

type ComponentPayload = dict[str, Any]


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
                _check_string(problems, component, "label", limits.button_label, "button")
                _check_string(problems, component, "custom_id", limits.custom_id, "button")
            case 3:
                _check_string(problems, component, "placeholder", limits.select_placeholder, "select")
                options = component.get("options", [])
                if len(options) > limits.select_options:
                    problems.append(f"{len(options)} select options (limit {limits.select_options})")
                for option in options:
                    _check_string(problems, option, "label", limits.option_label, "option")
                    _check_string(problems, option, "value", limits.option_value, "option")
                    _check_string(problems, option, "description", limits.option_description, "option")
            case 4:
                _check_string(problems, component, "placeholder", limits.text_input_placeholder, "text input")
                _check_string(problems, component, "value", limits.text_input_value, "text input")
                max_length = component.get("max_length")
                if max_length is not None and max_length > limits.text_input_value:
                    problems.append(f"text input max_length {max_length} (limit {limits.text_input_value})")
            case 5 | 6 | 7 | 8:
                _check_string(problems, component, "placeholder", limits.select_placeholder, "select")
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
                _check_string(problems, component, "label", limits.label_text, "label")
                _check_string(problems, component, "description", limits.label_description, "label")
            case _:
                pass

    return problems


def modal_problems(payload: dict[str, Any], *, limits: V2Limits = LIMITS) -> list[str]:
    """Return every limit violation in a modal payload."""
    problems: list[str] = []
    title = payload.get("title", "")
    if len(title) > limits.modal_title:
        problems.append(f"modal title is {len(title)} chars (limit {limits.modal_title})")
    components = payload.get("components", [])
    if len(components) > limits.modal_components:
        problems.append(f"modal holds {len(components)} components (limit {limits.modal_components})")
    problems.extend(payload_problems(components, limits=limits))
    return problems


def _fake_message_shape(message_id: int, *, ephemeral: bool, channel_id: int, guild_id: int | None) -> Any:
    """The read-only half of a message: what it is and where it lives."""
    return SimpleNamespace(
        id=message_id,
        flags=SimpleNamespace(components_v2=True, ephemeral=ephemeral),
        channel=SimpleNamespace(id=channel_id),
        guild=None if guild_id is None else SimpleNamespace(id=guild_id),
        jump_url=f"https://discord.com/channels/{guild_id or '@me'}/{channel_id}/{message_id}",
    )


def fake_interaction(user_id: int = 1, *, message_id: int = 99, expired: bool = False) -> Any:
    """A minimal interaction double for exercising mounts without Discord.

    `response.is_done()` starts false; flip `interaction.response._done` to simulate a
    consumed response, and set `interaction.response.type` to say what consumed it — a
    non-update type is what makes `delivery.handle_from` refuse the interaction. All
    send/edit surfaces are AsyncMocks.
    """
    response = SimpleNamespace(_done=False, is_done=lambda: response._done, type=None)

    def _responds(kind: discord.InteractionResponseType) -> AsyncMock:
        """A response surface that consumes the response the way discord.py's does."""

        async def record(*args: Any, **kwargs: Any) -> None:
            response._done = True
            response.type = kind

        return AsyncMock(side_effect=record)

    response.edit_message = _responds(discord.InteractionResponseType.message_update)
    response.defer = _responds(discord.InteractionResponseType.deferred_message_update)
    response.send_message = _responds(discord.InteractionResponseType.channel_message)
    response.send_modal = _responds(discord.InteractionResponseType.modal)
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        message=_fake_message_shape(message_id, ephemeral=False, channel_id=5, guild_id=7),
        response=response,
        followup=SimpleNamespace(send=AsyncMock(), edit_message=AsyncMock()),
        edit_original_response=AsyncMock(),
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        is_expired=lambda: expired,
    )


def fake_message(
    *, message_id: int = 99, ephemeral: bool = False, channel_id: int = 5, guild_id: int | None = 7
) -> Any:
    """A minimal message double whose `edit` returns itself, as Discord's does."""
    message = _fake_message_shape(message_id, ephemeral=ephemeral, channel_id=channel_id, guild_id=guild_id)
    message.edit = AsyncMock()
    message.edit.return_value = message
    return message


def delivered_to(message: Any) -> Destination:
    """A destination that hands `message` straight back — a send with no Discord in it.

    The mount ends up holding exactly the handle a real send would have given it, so tests
    about editing, refreshing and finishing can start from a delivered mount.
    """

    async def send(view: discord.ui.LayoutView, files: list[discord.File]) -> Any:
        return message

    return send


def commit_render(mount: Mount, *, disabled: bool = False) -> MountedView:
    """Stage a render and commit it with no Discord delivery — `Mount.send` to nowhere.

    `Mount.build_view` only stages; handlers and the live generation move when a delivery
    lands. Tests that never touch Discord say where that point is with this, rather than
    driving a destination that would only ever hand back `None`.

    Reaches past `send` on purpose: the alternative is making every one of these call sites
    await, for no coverage of anything the real send path does.
    """
    view = mount.build_view(disabled=disabled)
    candidate = mount._pending
    assert candidate is not None and candidate.view is view
    mount._commit(candidate)
    return view


def assert_within_limits(built: discord.ui.LayoutView | discord.ui.Modal, *, limits: V2Limits = LIMITS) -> None:
    """Assert that a built view or modal serializes within every Discord limit."""
    if isinstance(built, discord.ui.Modal):
        problems = modal_problems(built.to_dict(), limits=limits)
    else:
        problems = payload_problems(built.to_components(), limits=limits)
    assert not problems, "; ".join(problems)
