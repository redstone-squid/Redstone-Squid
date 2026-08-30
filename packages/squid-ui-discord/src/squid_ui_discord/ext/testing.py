"""Facade-level staging and command invocation without a Discord gateway."""

import hashlib
import inspect
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, Unpack, cast

import anyio
import discord
from discord.ext import commands

from squid_ui.runtime.component import Component
from squid_ui.target_types import ComponentsV2Target
from squid_ui_discord.config import DiscordUIConfig
from squid_ui_discord.facade import DiscordUI
from squid_ui_discord.message_root import MessageRoot
from squid_ui_discord.response import Presented, ResponseOverrides, ResponseSpec
from squid_ui_discord.runtime import DiscordUIRuntime, install
from squid_ui_discord.testing import (
    ContextHarness,
    InteractionHarness,
    assert_within_limits,
    iter_component_payloads,
    payload_texts,
)


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
                item._values = list(_sequence(value))  # pyrefly: ignore[missing-attribute]
        if remaining:
            names = ", ".join(sorted(remaining))
            message = f"staged form has no fields: {names}"
            raise KeyError(message)
        interaction = _interaction(self._client, self._user_id)
        self._submitted = True
        await self.modal.on_submit(cast(discord.Interaction, interaction.source))
        return interaction

    def assert_within_limits(self) -> None:
        """Assert that the generated modal is valid for Discord."""
        assert_within_limits(self.modal)


def _sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return value
    return (value,)


class StagedUI[ComponentT: Component[ComponentsV2Target]]:
    """A facade-presented component; the stage context ends its root."""

    def __init__(
        self,
        outcome: Presented[ComponentT],
        *,
        client: discord.Client,
        user_id: int,
        interaction: InteractionHarness,
    ) -> None:
        self.outcome = outcome
        self.component = outcome.component
        self.root = outcome.root
        self.session = outcome.session
        self.delivery = outcome.delivery
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
        """Assert that the current generated view is valid for Discord."""
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


def _owner_config(owner: object, configured: DiscordUIConfig | None) -> DiscordUIConfig:
    if configured is not None:
        return configured
    ui = getattr(owner, "ui", None)
    if isinstance(ui, DiscordUI):
        return ui.runtime.config
    if isinstance(ui, DiscordUIRuntime):
        return ui.config
    app_ui = getattr(owner, "app_ui", None)
    if isinstance(app_ui, DiscordUI):
        return app_ui.runtime.config
    return DiscordUIConfig()


def _interaction(client: discord.Client, user_id: int) -> InteractionHarness:
    return InteractionHarness(user_id=user_id, client=client)


@asynccontextmanager
async def stage[OwnerT, ComponentT: Component[ComponentsV2Target]](
    content: ComponentT,
    *,
    owner: OwnerT,
    user_id: int = 1,
    config: DiscordUIConfig | None = None,
    defaults: ResponseSpec | None = None,
    spec: ResponseSpec | None = None,
    **overrides: Unpack[ResponseOverrides],
) -> AsyncIterator[StagedUI[ComponentT]]:
    """Present a component through an isolated owner-scoped facade and run its runtime."""
    client = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    runtime = install(client, _owner_config(owner, config))
    client.ui = runtime  # type: ignore[attr-defined]
    ui = runtime.scope(owner, defaults=defaults)
    interaction = _interaction(client, user_id)
    try:
        result = await ui.respond(interaction.source, content, spec=spec, **overrides)
        if not isinstance(result, Presented):
            message = f"staging live content produced {type(result).__name__}"
            raise TypeError(message)
        staged = StagedUI(cast(Presented[ComponentT], result), client=client, user_id=user_id, interaction=interaction)
        raised: BaseException | None = None
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(runtime.run)
            try:
                yield staged
            except BaseException as error:
                raised = error
            finally:
                tasks.cancel_scope.cancel()
        if raised is not None:
            raise raised
    finally:
        await runtime.close()
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
    for candidate in (getattr(owner, "ui", None), getattr(owner, "app_ui", None)):
        if isinstance(candidate, DiscordUI):
            return candidate.runtime.client
        if isinstance(candidate, DiscordUIRuntime):
            return candidate.client
    if isinstance(owner, discord.Client):
        return owner
    message = "invoke() needs owner UI authority or an explicit installed client"
    raise TypeError(message)


__all__ = ["StagedControl", "StagedForm", "StagedUI", "invoke", "stage"]
