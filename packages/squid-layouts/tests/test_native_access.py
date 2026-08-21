"""`sl.discord.native` — the sanctioned typed escape hatch to the real interaction."""

import pytest

from squid_layouts import Component, PressEvent
from squid_layouts.actions import Actor, Visibility
from squid_layouts.discord import Mount, native
from squid_layouts.discord.actions import ActionResponder
from squid_layouts.discord.testing import commit_render, fake_interaction
from squid_layouts.primitives import Button, Row


class Portable:
    """A frontend adapter with no Discord underneath, as a second frontend would be."""

    async def acknowledge(self) -> None: ...

    async def notice(self, text: str, *, visibility: Visibility = Visibility.PRIVATE) -> None: ...

    async def present_form(self, form: object) -> None: ...

    async def download(self, asset: object) -> None: ...

    async def redirect(self, url: str) -> None: ...

    async def finish(self) -> None: ...

    def invalidate(self) -> None: ...


def test_native_returns_the_interaction_behind_a_discord_event() -> None:
    interaction = fake_interaction(user_id=7)
    event = PressEvent(Actor("7"), ActionResponder(interaction, Mount(Component(), timeout=None)))

    assert native(event) is interaction


def test_native_rejects_a_portable_responder_by_name() -> None:
    event = PressEvent(Actor("7"), Portable(), None, {"frontend": "html"})

    with pytest.raises(LookupError, match="html"):
        native(event)


def test_native_names_the_responder_type_when_context_omits_the_frontend() -> None:
    with pytest.raises(LookupError, match="Portable"):
        native(PressEvent(Actor("7"), Portable()))


async def test_handlers_reach_the_dispatching_interaction_through_native() -> None:
    seen: list[object] = []

    class Inspect(Component):
        def render(self):
            return Row((Button(label="inspect", on_click=self.inspect, key="inspect"),))

        async def inspect(self, event) -> None:
            seen.append(native(event))

    mount = Mount(Inspect(), timeout=None)
    commit_render(mount)
    interaction = fake_interaction()

    await mount.dispatch("inspect", interaction)

    assert seen == [interaction]
