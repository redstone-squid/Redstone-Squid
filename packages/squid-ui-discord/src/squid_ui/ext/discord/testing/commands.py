"""Invoke real command wrappers with gateway-free Discord sources."""

import inspect
from typing import Any, Literal

import discord
from squid_ui.ext.discord.commands import bind_context_menu, context_menu_declaration
from squid_ui.ext.discord.facade import Scope
from squid_ui.ext.discord.runtime import DiscordUIRuntime
from squid_ui.ext.discord.testing.harness import ContextHarness, InteractionHarness


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
        harness: InteractionHarness | ContextHarness = InteractionHarness(client=selected_client, user_id=user_id)
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
