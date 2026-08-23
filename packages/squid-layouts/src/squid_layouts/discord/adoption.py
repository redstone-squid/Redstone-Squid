"""Squid takes over an *unsent* discord.py view.

A live view — one that has been sent and will edit its own message — stays rejected, and
`docs/plans/squid-layouts-redesign/90-deferred.md` records why: two writers on one message make
budget measurement unsound. An unsent view claims nothing. It is items and callbacks that have
not met Discord, so Squid can translate the items into its own exact primitives, become the
sole writer, and keep the legacy object as a model plus a set of handlers. Renderer ownership,
the property the rejection protects, is preserved rather than traded away: Squid constructs
every item it draws.

The seam is the interaction proxy. `await interaction.response.edit_message(view=self)` is the
last line of nearly every discord.py callback, and here it means "I am done mutating; flush" --
it performs no HTTP, and the mount answers the interaction itself. Everything that would make
the legacy object a second writer raises `AdoptionError` instead of being quietly swallowed.
"""

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import discord

from squid_layouts.discord.actions import ActionResponder, responder, selected_entities
from squid_layouts.discord.mount import _CHANNEL_TYPES
from squid_layouts.entity import EntityKind, EntityRef, EntityType
from squid_layouts.errors import LayoutError
from squid_layouts.interactions import EntitySelectionEvent, PressEvent, SelectionEvent, Visibility
from squid_layouts.primitives.nodes import Button, EntitySelect, LinkButton, Node, Option, Row, SelectMenu
from squid_layouts.primitives.styles import ActionStyle
from squid_layouts.runtime.component import Component
from squid_layouts.runtime.reactivity import state

type Item = discord.ui.Item[Any]
type KeyFactory = Callable[[Item], str]

_ROWS = 5
"""Discord's action-row budget for a classic message, and the width of one row."""

_UNSET = object()


class AdoptionError(LayoutError):
    """An adopted view was asked to do something only a message's owner may do."""


_STYLES = {
    discord.ButtonStyle.primary: ActionStyle.PRIMARY,
    discord.ButtonStyle.secondary: ActionStyle.SECONDARY,
    discord.ButtonStyle.success: ActionStyle.SUCCESS,
    discord.ButtonStyle.danger: ActionStyle.DANGER,
}

_ENTITY_FAMILIES: tuple[tuple[type, EntityType], ...] = (
    (discord.ui.UserSelect, EntityType.USER),
    (discord.ui.RoleSelect, EntityType.ROLE),
    (discord.ui.ChannelSelect, EntityType.CHANNEL),
    (discord.ui.MentionableSelect, EntityType.MENTIONABLE),
)

_ENTITY_SELECTS = tuple(cls for cls, _ in _ENTITY_FAMILIES)
# `discord.ui.BaseSelect` is not re-exported from `discord.ui`, and naming the five concrete
# classes keeps this off a private module path.
_SELECTS = (discord.ui.Select, *_ENTITY_SELECTS)

# Inverted rather than written out a second time: one table cannot drift from itself.
_PORTABLE_CHANNEL_TYPES = {native: portable for portable, native in _CHANNEL_TYPES.items()}

_DEFAULT_KINDS = {
    discord.SelectDefaultValueType.user: EntityKind.USER,
    discord.SelectDefaultValueType.role: EntityKind.ROLE,
    discord.SelectDefaultValueType.channel: EntityKind.CHANNEL,
}


def adopt(
    view: discord.ui.View,
    *,
    keys: KeyFactory | None = None,
    discard_timeout: bool = False,
) -> Component:
    """Draw an unsent discord.py view as a Squid component, keeping its callbacks.

    The view becomes a model: Squid builds its own controls from `view.children` on every
    render, dispatches to `item.callback`, and owns the message. Mutating `self` in a callback
    is how an adopted view changes -- the adapter reports the mutation to the runtime after
    every callback returns.

    Three differences an author will notice, none of them silent:

    * **The mount owns the timeout.** `view.timeout` is ignored, because a mount's timeout
      interacts with its expiry policy, session lifetime and disable-on-finish behaviour. An
      overridden `on_timeout` is refused outright unless `discard_timeout=True` says its
      cleanup is disposable.
    * **In-place mutations do not roll back.** A callback that raises reaches the mount's error
      hook with component state restored, but the view keeps whatever the callback wrote to it
      before raising. Holding a mutable collaborator costs exactly this.
    * **Generated custom ids are positional.** A view that rebuilds its items with
      `clear_items()` and gives them no explicit `custom_id` is identified by position, so
      reordering controls moves per-control state between them. Pass `keys=` for such a view.

    Args:
        view: An unsent, never-dispatched classic `discord.ui.View`.
        keys: Names each item instead of the `custom_id`/position default.
        discard_timeout: Accept that an overridden `on_timeout` will never run.

    Returns:
        A `Component` that composes like any other -- mount it, put it in a `Screen`, or embed
        it in a larger Squid screen with `self.boundary(child, key=...)`.

    Raises:
        AdoptionError: The view is live, finished, a `LayoutView`, holds an item with no
            portable translation, or overrides `on_timeout` without `discard_timeout=True`.
    """
    if isinstance(view, discord.ui.LayoutView):
        message = (
            "adopt() takes a classic discord.ui.View; a LayoutView is content, and content is "
            "what the semantic layer is for -- rewrite it with sl.section, or keep it and use "
            "sl.discord.contribute(document, to=view) to measure a region of it"
        )
        raise AdoptionError(message)
    if not isinstance(view, discord.ui.View):
        message = f"adopt() takes a discord.ui.View, not {type(view).__name__}"
        raise AdoptionError(message)
    if view.is_dispatching():
        message = (
            "this view is already dispatching, so Discord routes its clicks to it; adopting a "
            "live view stays rejected because two writers on one message make measurement unsound"
        )
        raise AdoptionError(message)
    if view.is_finished():
        message = "this view has already stopped, so its handlers are retired; adopt an unsent view"
        raise AdoptionError(message)
    # `isinstance`, not `is not None`: `message` is an ordinary name, and a view with a button
    # callback called `message` would otherwise be refused for holding its own bound method.
    if isinstance(getattr(view, "message", None), discord.Message):
        message = "this view already holds a message, so it has been sent; adopt an unsent view"
        raise AdoptionError(message)
    if _overrides(view, "on_timeout") and not discard_timeout:
        message = (
            f"{type(view).__name__} overrides on_timeout, and the mount owns the timeout, so it "
            "would never run; move the cleanup to a mount finish hook, or pass "
            "discard_timeout=True to say it is disposable"
        )
        raise AdoptionError(message)

    adopted = _AdoptedView(view, keys=keys)
    # Translate once here so a view that cannot be drawn says so at the adopt() call site
    # rather than from inside the first render, where the traceback names none of this.
    adopted.render()
    return adopted


class _AdoptedView(Component):
    """A component whose whole model is one legacy view."""

    _view: discord.ui.View = state(persist=False, opaque=True)

    def __init__(self, view: discord.ui.View, *, keys: KeyFactory | None = None) -> None:
        self._view = view
        self._keys = keys

    def render(self) -> list[Node]:
        children = list(self._view.children)
        keys = self._key_map(children)
        nodes: list[Node] = []
        for row in _pack(children):
            pending: list[Button | LinkButton] = []
            for index in row:
                node = self._node(children[index], keys[index])
                if isinstance(node, Button | LinkButton):
                    pending.append(node)
                    continue
                if pending:
                    nodes.append(Row(tuple(pending)))
                    pending = []
                nodes.append(node)
            if pending:
                nodes.append(Row(tuple(pending)))
        return nodes

    # --- translation ---------------------------------------------------------------------

    def _key_map(self, children: Sequence[Item]) -> list[str]:
        """Name every child, refusing a collision rather than sharing a handler between two."""
        keys: list[str] = []
        seen: set[str] = set()
        for index, item in enumerate(children):
            key = self._key(item, index)
            if key in seen:
                message = f"two adopted controls share the key {key!r}; pass keys= to tell them apart"
                raise AdoptionError(message)
            seen.add(key)
            keys.append(key)
        return keys

    def _key(self, item: Item, index: int) -> str:
        if self._keys is not None:
            return self._keys(item)
        # discord.py hands every dispatchable item a random custom_id when the author gave
        # none, so the id alone is not identity: `_provided_custom_id` is what says which
        # happened. `.` is the boundary-path separator, so an author's id cannot carry one.
        custom_id = getattr(item, "custom_id", None)
        if getattr(item, "_provided_custom_id", False) and isinstance(custom_id, str) and custom_id:
            return custom_id.replace(".", "-")
        return f"adopted-{index}"

    def _node(self, item: Item, key: str) -> Node:
        if isinstance(item, discord.ui.DynamicItem):
            message = (
                f"{type(item).__name__} is a DynamicItem, whose identity is its custom id; that is "
                "what sl.discord.Router is for, and a router's controls already survive a restart"
            )
            raise AdoptionError(message)
        if isinstance(item, discord.ui.Button):
            return self._button(item, key)
        if isinstance(item, discord.ui.Select):
            return self._select(item, key)
        if isinstance(item, _ENTITY_SELECTS):
            return self._entity_select(item, key)
        message = (
            f"{type(item).__name__} has no portable translation; adopt() draws buttons and selects, "
            "and everything else in a view is content the semantic layer should own"
        )
        raise AdoptionError(message)

    def _button(self, item: discord.ui.Button[Any], key: str) -> Button | LinkButton:
        label = item.label or ""
        if item.sku_id is not None:
            message = f"button {key!r} is a premium button, which has no portable node"
            raise AdoptionError(message)
        if item.url is not None:
            return LinkButton(label=label, url=item.url)
        style = _STYLES.get(item.style)
        if style is None:
            message = f"button {key!r} has style {item.style!r}, which has no portable equivalent"
            raise AdoptionError(message)
        return Button(
            label=label,
            on_click=self._press(key),
            key=key,
            style=style,
            emoji=None if item.emoji is None else str(item.emoji),
            disabled=item.disabled,
        )

    def _select(self, item: discord.ui.Select[Any], key: str) -> SelectMenu:
        return SelectMenu(
            options=tuple(
                Option(
                    label=option.label,
                    value=option.value,
                    description=option.description,
                    default=option.default,
                )
                for option in item.options
            ),
            on_select=self._selection(key),
            key=key,
            placeholder=item.placeholder,
            min_values=item.min_values,
            max_values=item.max_values,
            disabled=item.disabled,
        )

    def _entity_select(self, item: Any, key: str) -> EntitySelect:
        entity_type = next((family for cls, family in _ENTITY_FAMILIES if isinstance(item, cls)), None)
        if entity_type is None:
            message = f"select {key!r} is a {type(item).__name__}, which has no portable entity family"
            raise AdoptionError(message)
        channel_types: tuple[Any, ...] = ()
        if entity_type is EntityType.CHANNEL:
            channel_types = tuple(
                _PORTABLE_CHANNEL_TYPES[native]
                for native in getattr(item, "channel_types", ())
                if native in _PORTABLE_CHANNEL_TYPES
            )
        return EntitySelect(
            entity_type=entity_type,
            on_select=self._entity_selection(key),
            key=key,
            placeholder=item.placeholder,
            default_values=tuple(
                EntityRef(_DEFAULT_KINDS[value.type], value.id)
                for value in item._underlying.default_values
                if value.type in _DEFAULT_KINDS
            ),
            channel_types=channel_types,
            min_values=item.min_values,
            max_values=item.max_values,
            disabled=item.disabled,
        )

    # --- dispatch ------------------------------------------------------------------------

    def _press(self, key: str) -> Callable[[PressEvent], Awaitable[None]]:
        async def on_click(event: PressEvent) -> None:
            await self._invoke(key, event, None)

        return on_click

    def _selection(self, key: str) -> Callable[[SelectionEvent], Awaitable[None]]:
        async def on_select(event: SelectionEvent) -> None:
            await self._invoke(key, event, list(event.values))

        return on_select

    def _entity_selection(self, key: str) -> Callable[[EntitySelectionEvent], Awaitable[None]]:
        async def on_select(event: EntitySelectionEvent) -> None:
            await self._invoke(key, event, list(selected_entities(event)))

        return on_select

    async def _invoke(self, key: str, event: Any, values: list[Any] | None) -> None:
        """Run one legacy callback behind the proxy, then tell the runtime the view moved."""
        view = self._view
        children = list(view.children)
        keys = self._key_map(children)
        item = next((child for child, name in zip(children, keys, strict=True) if name == key), None)
        if item is None:
            message = f"no adopted control is named {key!r} any more; a render that is not a function of the view"
            raise AdoptionError(message)

        answers = responder(event)
        proxy = _InteractionProxy(self, answers, view)
        try:
            try:
                if _overrides(view, "interaction_check") and not await view.interaction_check(proxy):
                    # discord.py's contract is that a refusing check has already answered the reader.
                    return
                if values is not None:
                    # Never the item discord.py dispatched -- Squid built the control that was clicked --
                    # so `values` reaches the legacy select through the same field discord.py fills.
                    item._values = values  # pyrefly: ignore[missing-attribute]
                await item.callback(proxy)
            except Exception as error:
                if not _overrides(view, "on_error"):
                    raise
                await view.on_error(proxy, error, item)
        finally:
            # In the `finally` on purpose: a check or callback that raised still mutated the view,
            # and `mutated` cannot undo that. Reporting it is what keeps the next render honest.
            self.mutated(view)
            if view.is_finished():
                await answers.finish()

    def _adopt_modal(self, modal: discord.ui.Modal, mount: Any) -> None:
        """Put the proxy in front of a modal's submit, which is a second interaction.

        Without this the modal's own `edit_message(view=self)` reaches Discord directly, which
        is the live second writer adoption exists to avoid. The submit runs outside the mount's
        dispatch funnel -- no author lock, no generation check, no transaction -- so the mount
        is refreshed out of band once it returns, the same bargain routed handlers strike.
        """
        view = self._view
        submit = modal.on_submit

        async def on_submit(interaction: discord.Interaction) -> None:
            proxy = _InteractionProxy(self, ActionResponder(interaction, mount), view)
            try:
                await submit(proxy)
            finally:
                self.mutated(view)
            if not interaction.response.is_done():
                await interaction.response.defer()
            await mount.refresh()

        modal.on_submit = on_submit


def _overrides(view: discord.ui.View, name: str) -> bool:
    """Whether this view supplies its own `name`, rather than inheriting discord.py's."""
    return getattr(type(view), name) is not getattr(discord.ui.View, name)


def _width(item: Item) -> int:
    """A select owns a whole row; a button costs one fifth of one."""
    return _ROWS if isinstance(item, _SELECTS) else 1


def _pack(children: Sequence[Item]) -> list[list[int]]:
    """Lay children out the way discord.py's `_ViewWeights` does, from public attributes.

    Explicitly-rowed items are placed first, in row order, then the rest go into the first row
    with space. Within a row the result is put back into `view.children` order, because that is
    what `View.to_components` renders after grouping by row.
    """
    weights = [0] * _ROWS
    rows: list[list[int]] = [[] for _ in range(_ROWS)]
    ordered = sorted(enumerate(children), key=lambda pair: (_ROWS if pair[1].row is None else pair[1].row, pair[0]))
    for index, item in ordered:
        width = _width(item)
        if item.row is not None:
            row = item.row
            if weights[row] + width > _ROWS:
                message = f"item {index} does not fit at row {row}, which discord.py would refuse too"
                raise AdoptionError(message)
        else:
            row = next((candidate for candidate, weight in enumerate(weights) if weight + width <= _ROWS), -1)
            if row < 0:
                message = f"item {index} has no open row left; a classic view holds five rows"
                raise AdoptionError(message)
        weights[row] += width
        rows[row].append(index)
    return [sorted(row) for row in rows if row]


class _ProxyResponse:
    """`interaction.response`, with the calls a second writer would make removed."""

    def __init__(self, proxy: _InteractionProxy) -> None:
        self._proxy = proxy
        self._done = False

    def is_done(self) -> bool:
        # The proxy's own flag, not the interaction's: a swallowed `edit_message` performs no
        # HTTP but must still read as answered, or the callback's next branch is wrong.
        return self._done

    async def edit_message(self, **fields: Any) -> None:
        view = fields.pop("view", _UNSET)
        if fields:
            named = ", ".join(sorted(fields))
            message = (
                f"edit_message({named}=...) is not available to an adopted view: the mount owns this "
                "message's payload, so put the content in the component's render"
            )
            raise AdoptionError(message)
        if view is not _UNSET and view is not self._proxy.view:
            shown = "None" if view is None else type(view).__name__
            message = (
                f"edit_message(view={shown}) would replace the adopted view with a different screen; "
                "push one with sl.discord.Navigator, or end this one with responder(event).finish()"
            )
            raise AdoptionError(message)
        self._proxy.flush_requested = True
        self._done = True

    async def defer(self, *_args: Any, **_kwargs: Any) -> None:
        await self._proxy.responder.acknowledge()
        self._done = True

    async def send_message(self, content: str | None = None, *, ephemeral: bool = False, **fields: Any) -> None:
        carried = sorted(name for name, value in fields.items() if value is not None)
        if carried:
            named = ", ".join(carried)
            message = (
                f"send_message({named}=...) is not available to an adopted view; a plain notice goes "
                "through responder(event).notice(), and a richer message through followup.send()"
            )
            raise AdoptionError(message)
        visibility = Visibility.PRIVATE if ephemeral else Visibility.PUBLIC
        await self._proxy.responder.notice(content or "", visibility=visibility)
        self._done = True

    async def send_modal(self, modal: discord.ui.Modal) -> None:
        if self._proxy.interaction.response.is_done():
            message = (
                "a modal has to be an interaction's first response, and this one has already been "
                "answered; open the modal before deferring or replying"
            )
            raise AdoptionError(message)
        self._proxy.component._adopt_modal(modal, self._proxy.responder.mount)
        await self._proxy.responder.send_modal(modal)
        self._done = True

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        message = (
            f"interaction.response.{name} is not available to an adopted view; the mount owns this "
            "interaction's response"
        )
        raise AdoptionError(message)


class _ProxyFollowup:
    """`interaction.followup`, which legitimately addresses a *different* message."""

    def __init__(self, proxy: _InteractionProxy) -> None:
        self._proxy = proxy

    async def send(self, *args: Any, **kwargs: Any) -> Any:
        # A swallowed `edit_message` leaves the real interaction unanswered, and a followup on an
        # unanswered interaction 404s. Acknowledging first costs one defer and keeps the legacy
        # call working; the mount then flushes through the followup, as it already does for a
        # hand-rolled defer.
        await self._proxy.acknowledge_upstream()
        return await self._proxy.interaction.followup.send(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._proxy.interaction.followup, name)


class _ProxyMessage:
    """`interaction.message`, readable but not writable: the mount writes it."""

    def __init__(self, message: Any) -> None:
        self._message = message

    async def edit(self, *_args: Any, **_kwargs: Any) -> Any:
        message = (
            "interaction.message.edit(...) is a second writer on the mount's message; mutate the "
            "view and let the flush draw it"
        )
        raise AdoptionError(message)

    async def delete(self, *_args: Any, **_kwargs: Any) -> Any:
        message = "interaction.message.delete() is the mount's to do; use responder(event).finish()"
        raise AdoptionError(message)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._message, name)


class _InteractionProxy:
    """What a legacy callback receives instead of `discord.Interaction`."""

    def __init__(self, component: _AdoptedView, answers: ActionResponder, view: discord.ui.View) -> None:
        self.__dict__["interaction"] = answers.interaction
        self.component = component
        self.responder = answers
        self.view = view
        self.flush_requested = False
        self.response = _ProxyResponse(self)
        self.followup = _ProxyFollowup(self)

    @property
    def message(self) -> _ProxyMessage | None:
        real = self.interaction.message
        return None if real is None else _ProxyMessage(real)

    async def acknowledge_upstream(self) -> None:
        """Answer the real interaction, for a call that needs it answered."""
        if not self.interaction.response.is_done():
            await self.responder.acknowledge()

    async def original_response(self) -> Any:
        return await self.interaction.original_response()

    async def edit_original_response(self, *_args: Any, **_kwargs: Any) -> Any:
        message = (
            "edit_original_response(...) is a second writer on the mount's message; mutate the view "
            "and let the flush draw it"
        )
        raise AdoptionError(message)

    async def delete_original_response(self, *_args: Any, **_kwargs: Any) -> Any:
        message = "delete_original_response() is the mount's to do; use responder(event).finish()"
        raise AdoptionError(message)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        # Everything Discord-factual -- user, guild, data, client, channel, locale -- is the
        # real interaction's, and a callback reading it is reading the truth.
        return getattr(self.__dict__["interaction"], name)
