"""Present real components in an isolated runtime and exercise their controls."""

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any, Unpack, cast, overload

import discord
from discord.ext import commands
from squid_ui.content.text import NEUTRAL, Localization
from squid_ui.errors import LayoutError
from squid_ui.ext.discord.config import Config
from squid_ui.ext.discord.delivery import DeliveryResult
from squid_ui.ext.discord.facade import Scope
from squid_ui.ext.discord.messages.wiring import control_id
from squid_ui.ext.discord.request import Request
from squid_ui.ext.discord.response import Presented, ResponseOverrides
from squid_ui.ext.discord.runtime import DiscordUIRuntime, install
from squid_ui.ext.discord.target_types import ComponentsV2Target
from squid_ui.ext.discord.testing.harness import InteractionHarness
from squid_ui.ext.discord.testing.payloads import assert_within_limits, iter_component_payloads, payload_texts
from squid_ui.ext.discord.testing.queries import index_tree, query
from squid_ui.runtime.definition import Component
from squid_ui.selectors import Match, Query, Selector, TreeIndex, select


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

    def __init__(
        self,
        modal: discord.ui.Modal,
        *,
        client: discord.Client,
        user_id: int,
        check: Callable[[], None] | None = None,
    ) -> None:
        self.modal = modal
        self._check = check
        assert_within_limits(modal)
        self._client = client
        self._user_id = user_id
        self._submitted = False

    async def submit(self, values: Mapping[str, object]) -> InteractionHarness:
        """Populate native modal controls and invoke their real submission callback once."""
        if self._submitted:
            message = "this staged form was already submitted"
            raise RuntimeError(message)
        remaining = dict(values)
        for match in query(self.modal, select(discord.ui.Item).has_attr("custom_id")).all():
            item = match.node
            key = getattr(item, "custom_id", None)
            if not isinstance(key, str) or key not in remaining:
                continue
            value = remaining.pop(key)
            if isinstance(item, discord.ui.TextInput):
                item._value = str(value)  # pyrefly: ignore[missing-attribute]
            elif isinstance(item, discord.ui.RadioGroup):
                selected = _sequence(value)
                item._value = str(selected[0]) if selected else None  # pyrefly: ignore[missing-attribute]
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
        interaction = InteractionHarness(client=self._client, user_id=self._user_id)
        self._submitted = True
        await self.modal.on_submit(cast(discord.Interaction, interaction.source))
        if self._check is not None:
            self._check()
        return interaction

    def assert_within_limits(self) -> None:
        """Check the generated modal against the supported serialization size limits."""
        assert_within_limits(self.modal)


def _sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return value
    return (value,)


class StagedUI[ComponentT: Component[ComponentsV2Target, Request[Any, Any]]]:
    """A facade-presented component; the stage context ends its root.

    Control lookups and actions accept a full semantic key or a unique
    dot-delimited suffix.
    """

    def __init__(
        self,
        result: Presented[ComponentT],
        *,
        client: discord.Client,
        user_id: int,
        interaction: InteractionHarness,
        layout_errors: list[LayoutError] | None = None,
    ) -> None:
        self._layout_errors = layout_errors if layout_errors is not None else []
        self.result = result
        self.component = result.component
        self.root = result.root
        self.session = result.session
        self.delivery = result.delivery
        self.interaction = interaction
        self._client = client
        self._user_id = user_id

    def index(self) -> TreeIndex:
        """Capture the currently delivered Discord views, including continuation messages.

        Pending candidates and undelivered component state are excluded. No refresh or
        render runs. Returned nodes are native Discord objects, not authored controls.
        """
        return index_tree(tuple(view for view, _, _ in self._views()))

    @overload
    def query[T](
        self,
        selector: Selector[T],
        *,
        view: object | None = None,
        within: Match[Any] | None = None,
        include_scope: bool = False,
        localization: Localization = NEUTRAL,
    ) -> Query[T]: ...

    @overload
    def query(
        self,
        selector: str,
        *,
        types: Mapping[str, type] | None = None,
        view: object | None = None,
        within: Match[Any] | None = None,
        include_scope: bool = False,
        localization: Localization = NEUTRAL,
    ) -> Query[object]: ...

    def query[T](
        self,
        selector: Selector[T] | str,
        *,
        types: Mapping[str, type] | None = None,
        view: object | None = None,
        within: Match[Any] | None = None,
        include_scope: bool = False,
        localization: Localization = NEUTRAL,
    ) -> Query[T] | Query[object]:
        """Inspect delivered native controls without rendering or refreshing.

        Pass view explicitly to inspect a candidate view or a previously captured
        index. Use the same index for scoped queries and order comparisons.
        """
        captured = self.index() if view is None else view
        if isinstance(selector, str):
            return query(
                captured,
                selector,
                types=types,
                within=within,
                include_scope=include_scope,
                localization=localization,
            )
        if types is not None:
            message = "types is only supported for string selectors"
            raise ValueError(message)
        return query(captured, selector, within=within, include_scope=include_scope, localization=localization)

    def texts(self) -> list[str]:
        """Return display texts from the current serialized Discord payload.

        A root that spread over several messages returns their texts in reading order.
        """
        return [text for view, _, _ in self._views() for text in payload_texts(view)]

    def control(self, key: str) -> StagedControl:
        """Select one currently rendered control by stable semantic key."""
        return self._located(key)[0]

    def _located(self, key: str) -> tuple[StagedControl, int, int | None]:
        """A control with the generation and message id a press on it would carry."""
        keys = self.root.snapshot().handler_keys
        if key not in keys:
            matches = [candidate for candidate in keys if candidate.endswith(f".{key}")]
            if len(matches) > 1:
                message = f"ambiguous control {key!r}; matching semantic keys: {', '.join(matches)}"
                raise KeyError(message)
            if matches:
                key = matches[0]
        for view, generation, message_id in self._views():
            expected = control_id(self.root.id, generation, key)
            payloads = iter_component_payloads(view.to_components())
            payload = next((candidate for candidate in payloads if candidate.get("custom_id") == expected), None)
            if payload is not None:
                return StagedControl(key, expected, payload), generation, message_id
        available = ", ".join(keys)
        message = f"no rendered control {key!r}; available semantic keys: {available}"
        raise KeyError(message)

    def _press_source(self, message_id: int | None, user_id: int | None) -> InteractionHarness:
        actor = self._user_id if user_id is None else user_id
        if message_id is None:
            return InteractionHarness(client=self._client, user_id=actor)
        return InteractionHarness(user_id=actor, client=self._client, message_id=message_id)

    async def press(self, key: str, *, user_id: int | None = None) -> InteractionHarness:
        """Dispatch one button or form trigger through ``MessageRoot.dispatch``."""
        control, generation, message_id = self._located(key)
        if control.payload.get("type") != 2:
            message = f"control {key!r} is not a button"
            raise TypeError(message)
        interaction = self._press_source(message_id, user_id)
        await self.root.dispatch(
            control.key,
            cast(discord.Interaction, interaction.source),
            generation=generation,
        )
        self.assert_within_limits()
        return interaction

    async def select(
        self,
        key: str,
        values: Sequence[str],
        *,
        user_id: int | None = None,
    ) -> InteractionHarness:
        """Dispatch string choices through ``MessageRoot.dispatch``."""
        control, generation, message_id = self._located(key)
        if control.payload.get("type") not in {3, 5, 6, 7, 8}:
            message = f"control {key!r} is not a select"
            raise TypeError(message)
        interaction = self._press_source(message_id, user_id)
        await self.root.dispatch(
            control.key,
            cast(discord.Interaction, interaction.source),
            list(values),
            generation=generation,
        )
        self.assert_within_limits()
        return interaction

    async def press_for_form(self, key: str, *, user_id: int | None = None) -> StagedForm:
        """Press a semantic form trigger and return the generated native modal."""
        actor = self._user_id if user_id is None else user_id
        interaction = await self.press(key, user_id=actor)
        records = interaction.modals
        if len(records) != 1 or not records[0].args or not isinstance(records[0].args[0], discord.ui.Modal):
            message = f"control {key!r} did not open exactly one modal"
            raise AssertionError(message)
        return StagedForm(records[0].args[0], client=self._client, user_id=actor, check=self.assert_within_limits)

    def assert_within_limits(self) -> None:
        """Check the current view against the supported serialization size limits."""
        if self._layout_errors:
            error = self._layout_errors.pop(0)
            raise error
        if not self.root.finished:
            for view, _, _ in self._views():
                assert_within_limits(view)

    def _view(self) -> discord.ui.LayoutView:
        view = self.root._candidates.view  # pyrefly: ignore[missing-attribute]
        if not isinstance(view, discord.ui.LayoutView):
            message = "the staged root has no current Components V2 view"
            raise TypeError(message)
        return view

    def _views(self) -> list[tuple[discord.ui.LayoutView, int, int | None]]:
        """Each message's live view, with its generation and, after the first, its message id."""
        views: list[tuple[discord.ui.LayoutView, int, int | None]] = [(self._view(), self.root.generation, None)]
        slots = self.root._continuations.slots
        for live, slot in zip(self.root._candidates.continuations, slots, strict=False):
            if isinstance(live.view, discord.ui.LayoutView):
                views.append((live.view, live.generation, slot.message_id))
        return views


def _owner_config(owner: object, configured: Config | None) -> Config:
    if configured is not None:
        return configured
    ui = getattr(owner, "ui", None)
    if isinstance(ui, Scope):
        return ui.runtime.config
    if isinstance(ui, DiscordUIRuntime):
        return ui.config
    return Config()


@asynccontextmanager
async def stage[OwnerT, ComponentT: Component[ComponentsV2Target, Request[Any, Any]]](
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
    configured = _owner_config(owner, config)
    layout_errors: list[LayoutError] = []

    async def observe(request: Request[Any], error: Exception, result: DeliveryResult | None) -> None:
        if isinstance(error, LayoutError):
            layout_errors.append(error)
        if configured.errors.observe is not None:
            await configured.errors.observe(request, error, result)

    runtime = install(client, replace(configured, errors=replace(configured.errors, observe=observe)))
    client.ui = runtime  # type: ignore[attr-defined]
    scope = runtime.scope(owner)
    interaction = InteractionHarness(client=client, user_id=user_id)
    try:
        async with runtime.running():
            result = await (await scope.request(interaction.source)).respond(content, **overrides)
            if layout_errors:
                raise layout_errors.pop(0)
            if not isinstance(result, Presented):
                message = f"staging live content produced {type(result).__name__}"
                raise TypeError(message)
            staged = StagedUI(
                result,
                client=client,
                user_id=user_id,
                interaction=interaction,
                layout_errors=layout_errors,
            )
            staged.assert_within_limits()
            yield staged
    finally:
        await client.close()
