"""Owner identity and lifetime for the explicit Discord facade."""

from typing import Any, cast

import discord
import pytest

import squid_ui as sl
import squid_ui_discord as sd


class FakeClient:
    pass


class Owner:
    pass


class Panel(sl.Component[sl.ComponentsV2Target]):
    def render(self):
        return sl.heading("Panel")


def runtime() -> sd.DiscordUIRuntime[Any]:
    return sd.install(cast(discord.Client, FakeClient()))


def test_scope_reuses_exact_owner_identity() -> None:
    installed = runtime()
    owner = Owner()

    assert installed.scope(owner) is installed.scope(owner)
    assert installed.scope(Owner()) is not installed.scope(owner)


def test_scope_rejects_none_and_conflicting_defaults() -> None:
    installed = runtime()
    owner = Owner()
    installed.scope(owner, defaults=sd.ResponseSpec(timeout=10))

    with pytest.raises(TypeError, match="None cannot own"):
        installed.scope(None)
    with pytest.raises(ValueError, match="different response defaults"):
        installed.scope(owner, defaults=sd.ResponseSpec(timeout=20))


async def test_scope_close_finishes_tracked_roots_and_allows_a_new_scope() -> None:
    installed = runtime()
    owner = Owner()
    ui = installed.scope(owner)
    root = installed.mount(Panel(), access=sd.Everyone())
    installed._track(ui, root)

    await ui.close()

    assert root.finished
    assert installed.scope(owner) is not ui


async def test_runtime_close_closes_every_scope() -> None:
    installed = runtime()
    first = installed.scope(Owner())
    second = installed.scope(Owner())

    await installed.close()

    assert first.closed
    assert second.closed


def test_runtime_has_no_delivery_methods() -> None:
    installed = runtime()

    assert not hasattr(installed, "respond")
    assert not hasattr(installed, "send")
    assert not hasattr(installed, "edit")
