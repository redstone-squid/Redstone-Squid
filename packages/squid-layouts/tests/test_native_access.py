"""`sl.discord.responder`/`native` — the sanctioned escape hatches to Discord's own surfaces."""

import pytest

from squid_layouts import ActionPolicy, Component, FormLike, PressEvent, SubmitHandler, TextLike
from squid_layouts.actions import ActionResponder as ActionResponderProtocol
from squid_layouts.actions import Actor, Visibility
from squid_layouts.discord import Everyone, Mount, native, responder
from squid_layouts.discord.actions import ActionResponder
from squid_layouts.discord.testing import commit_render, fake_interaction
from squid_layouts.primitives import Button, Row


class Portable:
    """A frontend adapter with no Discord underneath, as a second frontend would be."""

    async def acknowledge(self) -> None: ...

    async def notice(self, text: TextLike, *, visibility: Visibility = Visibility.PRIVATE) -> None: ...

    async def redirect(self, url: str) -> None: ...

    async def finish(self) -> None: ...

    async def present_form(
        self,
        form: FormLike,
        *,
        key: str = "form",
        on_submit: SubmitHandler | None = None,
        policy: ActionPolicy | None = None,
    ) -> None: ...

    def invalidate(self) -> None: ...


def test_native_returns_the_interaction_behind_a_discord_event() -> None:
    interaction = fake_interaction(user_id=7)
    event = PressEvent(Actor("7"), ActionResponder(interaction, Mount(Component(), access=Everyone(), timeout=None)))

    assert native(event) is interaction


def test_responder_returns_the_adapter_holding_the_native_surfaces() -> None:
    adapter = ActionResponder(fake_interaction(), Mount(Component(), access=Everyone(), timeout=None))
    event = PressEvent(Actor("7"), adapter)

    assert responder(event) is adapter


def test_responder_rejects_a_portable_responder() -> None:
    event = PressEvent(Actor("7"), Portable(), None, {"frontend": "html"})

    with pytest.raises(LookupError, match="html"):
        responder(event)


def test_a_portable_responder_satisfies_the_protocol_it_claims() -> None:
    """The stub is the point: the protocol must be implementable with no Discord in reach.

    The real check is static — `Portable` is accepted where `ActionResponder` is required,
    which is what stopped holding once `present_form` took a frontend object.
    """
    accepted: ActionResponderProtocol = Portable()

    assert PressEvent(Actor("7"), accepted).responder is accepted


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

    mount = Mount(Inspect(), access=Everyone(), timeout=None)
    commit_render(mount)
    interaction = fake_interaction()

    await mount.dispatch("inspect", interaction)

    assert seen == [interaction]
