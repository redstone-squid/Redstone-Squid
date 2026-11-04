"""Discord component requests expose native facts without leaking the responder."""

from collections.abc import Awaitable, Callable
from typing import Any, cast, override

import discord
import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid_ui import Component, PressEvent, TextLike
from squid_ui.forms import FormLike, SubmitHandler
from squid_ui.interactions import ActionMode, Actor, Visibility
from squid_ui.interactions import ActionResponder as ActionResponderProtocol
from squid_ui.primitives import Button, Row
from squid_ui.testing import text_component
from squid_ui_discord import Everyone, MessageRoot
from squid_ui_discord.actions import ActionResponder
from squid_ui_discord.testing import commit_render, interaction_harness


class Client:
    pass


def installed_interaction(*, user_id: int = 1) -> Any:
    client = Client()
    sd.install(cast(discord.Client, client))
    interaction = interaction_harness(user_id=user_id)
    interaction.client = client
    return interaction


class Portable:
    """A frontend adapter with no Discord underneath, as a second frontend would be."""

    async def acknowledge(self) -> None: ...

    async def notice(self, text: TextLike, *, visibility: Visibility = Visibility.PRIVATE) -> None: ...

    async def redirect(self, url: str) -> None: ...

    def finish_on_success(self) -> None: ...

    def on_success[ValueT](
        self, callback: Callable[[], Awaitable[ValueT]], *, name: str | None = None
    ) -> sl.runtime.PostCommitOperation[ValueT]:
        raise AssertionError

    async def present_form(
        self,
        form: FormLike,
        *,
        key: str = "form",
        on_submit: SubmitHandler | None = None,
        mode: ActionMode | None = None,
        label: TextLike = "",
        record=None,
    ) -> None: ...

    def invalidate(self) -> None: ...


async def test_request_returns_the_interaction_behind_a_discord_event() -> None:
    interaction = installed_interaction(user_id=7)
    event = PressEvent(
        Actor("7"),
        ActionResponder(interaction, MessageRoot(text_component(), access=Everyone(), timeout=None)),
        context=None,
    )

    assert (await sd.request(event)).interaction is interaction


def test_a_portable_responder_satisfies_the_protocol_it_claims() -> None:
    """The annotated assignment checks protocol conformance without Discord-specific types.

    The runtime assertion verifies that the event retains the responder.
    """
    accepted: ActionResponderProtocol = Portable()

    assert PressEvent(Actor("7"), accepted, context=None).responder is accepted


async def test_request_rejects_a_portable_responder_by_name() -> None:
    event = PressEvent(Actor("7"), Portable(), None, {"frontend": "html"}, context=None)

    with pytest.raises(LookupError, match="html"):
        await sd.request(event)


async def test_request_names_the_responder_type_when_context_omits_the_frontend() -> None:
    with pytest.raises(LookupError, match="Portable"):
        await sd.request(PressEvent(Actor("7"), Portable(), context=None))


async def test_handlers_reach_the_dispatching_interaction_through_request() -> None:
    seen: list[object] = []

    class Inspect(Component[sl.ComponentsV2Target]):
        @override
        def render(self):
            return Row((Button(label="inspect", on_click=self.inspect, key="inspect"),))

        async def inspect(self, event) -> None:
            seen.append((await sd.request(event)).interaction)

    message_root = MessageRoot(Inspect(), access=Everyone(), timeout=None)
    commit_render(message_root)
    interaction = installed_interaction()

    await message_root.dispatch("inspect", interaction)

    assert seen == [interaction]
