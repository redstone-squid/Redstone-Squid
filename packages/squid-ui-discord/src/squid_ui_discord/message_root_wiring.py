"""Scene controls wired to discord.py: mounted views, buttons, and selects.

Each wired control carries its mount, key, and render generation, and funnels its callback
into :meth:`MessageRoot.dispatch`; nothing here decides behaviour beyond that routing.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict

import discord

# `discord.ui.select` names the decorator, so the submodule holding `BaseSelect` is only
# reachable by importing from it directly.
from discord.ui.select import BaseSelect

from squid_ui import scene
from squid_ui.entity import ConversationType, EntityKind, EntityRef, EntityType
from squid_ui.errors import DrawInvariantError
from squid_ui_discord.emoji import discord_emoji

if TYPE_CHECKING:
    from squid_ui_discord.message_root import AnyMessageRoot


class _MountedBehaviour:
    """What a mounted view does, independently of which components it holds.

    A mixin over discord.py's `BaseView`, which both `View` and `LayoutView` derive from, so
    the two mounted views differ in exactly one thing: their component vocabulary. Timeout,
    dispatchability, the error hook, and the mount back-reference are the same behaviour in
    both message modes, and a second copy of them would be a second thing to keep in step.
    """

    _root: AnyMessageRoot

    def __init__(self, message_root: AnyMessageRoot, timeout: float | None) -> None:
        super().__init__(timeout=timeout)  # type: ignore[call-arg]
        self._root = message_root

    async def on_timeout(self) -> None:
        await self._root.handle_timeout()

    def is_dispatchable(self) -> bool:
        # A mount wants storing even when it draws nothing dispatchable, because
        # `store_view` is gated on this and `add_view` is what starts the timeout task.
        # A document of nothing but routed controls would otherwise never time out.
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        await self._root.handle_error(interaction, error, f"item:{type(item).__name__}")


class MountedView(_MountedBehaviour, discord.ui.LayoutView):
    """One render generation of a mounted component, as a Components V2 message."""


class ClassicMountedView(_MountedBehaviour, discord.ui.View):
    """One render generation of a mounted component, as a classic message's controls."""


type AnyMountedView = MountedView | ClassicMountedView


def _custom_id(message_root_id: str, generation: int, key: str) -> str:
    """A per-render-unique control id for ``key``, within Discord's 100-char limit.

    Nested components produce long dotted keys, and truncating those makes two controls
    collide — Discord rejects the message and, worse, a click could route to the wrong
    handler. Digest the key instead; dispatch itself goes by the in-process key.

    The generation is part of the id because discord.py registers a replacement view before
    the mount stops its predecessor. Reusing ids lets the predecessor unregister the new
    view's controls when it stops, leaving visible buttons with no callback.
    """
    prefix = f"ctl:{message_root_id}:{generation}:"
    custom_id = f"{prefix}{key}"
    if len(custom_id) <= 100:
        return custom_id
    return f"{prefix}#{hashlib.blake2s(key.encode()).hexdigest()[:12]}"


class _WiredButton(discord.ui.Button[AnyMountedView]):
    def __init__(self, node: scene.Button, message_root: AnyMessageRoot, key: str, generation: int) -> None:
        super().__init__(
            style=getattr(discord.ButtonStyle, node.style.value),
            label=node.label,
            emoji=discord_emoji(node.emoji),
            disabled=node.disabled,
            custom_id=_custom_id(message_root.id, generation, key),
        )
        self._root = message_root
        self._key = key
        self._generation = generation

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._root.dispatch(self._key, interaction, generation=self._generation)


class _WiredSelect(discord.ui.Select[AnyMountedView]):
    def __init__(self, node: scene.Select, message_root: AnyMessageRoot, key: str, generation: int) -> None:
        super().__init__(
            placeholder=node.placeholder,
            min_values=node.min_values,
            max_values=node.max_values,
            disabled=node.disabled,
            custom_id=_custom_id(message_root.id, generation, key),
            options=[
                discord.SelectOption(
                    label=option.label,
                    value=option.value,
                    description=option.description,
                    default=option.default,
                    emoji=discord_emoji(option.emoji),
                )
                for option in node.options
            ],
        )
        self._root = message_root
        self._key = key
        self._generation = generation

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._root.dispatch(self._key, interaction, self.values, generation=self._generation)


_CONVERSATION_TYPES = {
    ConversationType.GUILD_TEXT: discord.ChannelType.text,
    ConversationType.GUILD_VOICE: discord.ChannelType.voice,
    ConversationType.GUILD_CATEGORY: discord.ChannelType.category,
    ConversationType.GUILD_ANNOUNCEMENT: discord.ChannelType.news,
    ConversationType.GUILD_ANNOUNCEMENT_THREAD: discord.ChannelType.news_thread,
    ConversationType.GUILD_PUBLIC_THREAD: discord.ChannelType.public_thread,
    ConversationType.GUILD_PRIVATE_THREAD: discord.ChannelType.private_thread,
    ConversationType.GUILD_STAGE_VOICE: discord.ChannelType.stage_voice,
    ConversationType.GUILD_FORUM: discord.ChannelType.forum,
    ConversationType.GUILD_MEDIA: discord.ChannelType.media,
}


def _default_value(value: EntityRef) -> discord.SelectDefaultValue:
    kind = {
        EntityKind.USER: discord.SelectDefaultValueType.user,
        EntityKind.ROLE: discord.SelectDefaultValueType.role,
        EntityKind.CONVERSATION: discord.SelectDefaultValueType.channel,
    }[value.kind]
    if not isinstance(value.id, int):
        message = "discord.py entity selects require integer snowflake ids"
        raise DrawInvariantError(message)
    return discord.SelectDefaultValue(id=value.id, type=kind)


def _entity_ref(value: object) -> EntityRef:
    if isinstance(value, discord.Role):
        return EntityRef(EntityKind.ROLE, value.id)
    if isinstance(value, discord.User | discord.Member):
        return EntityRef(EntityKind.USER, value.id)
    if isinstance(value, discord.abc.GuildChannel | discord.Thread):
        return EntityRef(EntityKind.CONVERSATION, value.id)
    message = f"unsupported resolved entity {type(value).__name__}"
    raise TypeError(message)


@dataclass(frozen=True, slots=True)
class _EntityValues:
    refs: tuple[EntityRef, ...]
    resolved: tuple[object, ...]


type _SelectionValues = list[str] | _EntityValues | None


class _EntityDispatch:
    _root: AnyMessageRoot
    _key: str
    _generation: int

    def _wire(self, message_root: AnyMessageRoot, key: str, generation: int) -> None:
        self._root = message_root
        self._key = key
        self._generation = generation

    async def _dispatch(self, interaction: discord.Interaction, values: Sequence[object]) -> None:
        resolved = tuple(values)
        await self._root.dispatch(
            self._key,
            interaction,
            _EntityValues(tuple(_entity_ref(value) for value in resolved), resolved),
            generation=self._generation,
        )


class _EntitySelectKwargs(TypedDict):
    """The constructor arguments every entity select shares.

    Spelled out rather than left as `dict[str, object]` because these are splatted into four
    different discord.py select constructors, and an erased mapping makes every parameter of
    every one of them unverifiable.
    """

    placeholder: str | None
    min_values: int
    max_values: int
    disabled: bool
    custom_id: str
    default_values: list[discord.SelectDefaultValue]


def _entity_kwargs(
    node: scene.EntitySelect, message_root: AnyMessageRoot, key: str, generation: int
) -> _EntitySelectKwargs:
    return {
        "placeholder": node.placeholder,
        "min_values": node.min_values,
        "max_values": node.max_values,
        "disabled": node.disabled,
        "custom_id": _custom_id(message_root.id, generation, key),
        "default_values": [_default_value(value) for value in node.default_values],
    }


class _WiredUserSelect(_EntityDispatch, discord.ui.UserSelect[AnyMountedView]):
    async def callback(self, interaction: discord.Interaction) -> None:
        await self._dispatch(interaction, self.values)


class _WiredRoleSelect(_EntityDispatch, discord.ui.RoleSelect[AnyMountedView]):
    async def callback(self, interaction: discord.Interaction) -> None:
        await self._dispatch(interaction, self.values)


class _WiredChannelSelect(_EntityDispatch, discord.ui.ChannelSelect[AnyMountedView]):
    async def callback(self, interaction: discord.Interaction) -> None:
        await self._dispatch(interaction, self.values)


class _WiredMentionableSelect(_EntityDispatch, discord.ui.MentionableSelect[AnyMountedView]):
    async def callback(self, interaction: discord.Interaction) -> None:
        await self._dispatch(interaction, self.values)


def _wired_entity_select(
    node: scene.EntitySelect, message_root: AnyMessageRoot, key: str, generation: int
) -> BaseSelect[Any]:
    kwargs = _entity_kwargs(node, message_root, key, generation)
    if node.entity_type is EntityType.USER:
        item = _WiredUserSelect(**kwargs)
    elif node.entity_type is EntityType.ROLE:
        item = _WiredRoleSelect(**kwargs)
    elif node.entity_type is EntityType.CONVERSATION:
        try:
            channel_types = [_CONVERSATION_TYPES[value] for value in node.conversation_types]
        except KeyError as error:
            message = f"discord.py does not support conversation type {error.args[0].value!r}"
            raise DrawInvariantError(message) from error
        item = _WiredChannelSelect(channel_types=channel_types, **kwargs)
    else:
        item = _WiredMentionableSelect(**kwargs)
    item._wire(message_root, key, generation)
    return item


def _disable_all(view: discord.ui.LayoutView | discord.ui.View) -> None:
    children = view.walk_children() if isinstance(view, discord.ui.LayoutView) else view.children
    for item in children:
        target = item.item if isinstance(item, discord.ui.DynamicItem) else item
        if isinstance(target, discord.ui.Button | discord.ui.Select) or hasattr(target, "disabled"):
            target.disabled = True  # pyrefly: ignore  # guarded by hasattr
