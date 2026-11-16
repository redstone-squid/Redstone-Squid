"""Screen ownership and results across command and background delivery."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal, override

import anyio
import pytest

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid_ui.ext.discord.testing import ContextHarness, InteractionHarness, MessageHarness


class Owner:
    pass


class Anchor(sd.Screen[None, Owner]):
    @override
    def render(self) -> sl.LayoutNode:
        return sl.heading("Anchor")


@asynccontextmanager
async def _scope() -> AsyncIterator[sd.Scope[Owner]]:
    owner = Owner()
    anchor = Anchor()
    async with sd.testing.stage(anchor, owner=owner) as _ui:
        assert anchor.opening is not None
        yield anchor.opening.scope


class BackgroundScreen(sd.Screen[None, Owner]):
    policy = sd.ResponseSpec(audience="public", access=sd.Everyone(), timeout=None)

    def __init__(self, expected_owner: Owner) -> None:
        self.expected_owner = expected_owner
        self.loaded = False

    @override
    async def on_load(self) -> None:
        assert self.owner is self.expected_owner
        self.loaded = True

    @override
    def render(self) -> sl.LayoutNode:
        assert self.loaded
        assert self.owner is self.expected_owner
        return sl.heading("Background")


type DeliveryKind = Literal["send", "edit", "respond"]


async def _present(scope: sd.Scope[Owner], screen: sd.Screen[None, Owner], kind: DeliveryKind) -> sd.ResponseResult:
    if kind == "send":
        return await scope.send(ContextHarness(message=MessageHarness(message_id=201)).source, screen)
    if kind == "edit":
        return await scope.edit(MessageHarness(message_id=202).source, screen)
    source = InteractionHarness(user_id=7, message_id=203, client=scope.runtime.client).source
    request = await scope.request(source)
    return await request.respond(screen)


@pytest.mark.parametrize("kind", ["send", "edit"])
async def test_background_delivery_binds_owner_before_load_without_an_opening_request(kind: DeliveryKind) -> None:
    class Probe(BackgroundScreen):
        @override
        async def on_load(self) -> None:
            assert self.opening is None
            await super().on_load()

        @override
        def render(self) -> sl.LayoutNode:
            assert self.opening is None
            return super().render()

    async with _scope() as scope:
        screen = Probe(scope.owner)
        result = await _present(scope, screen, kind)

        assert isinstance(result, sd.Presented)
        assert screen.loaded
        assert screen.owner is scope.owner
        assert screen.opening is None
        assert screen.session is None


@pytest.mark.parametrize("first", ["send", "edit", "respond"])
@pytest.mark.parametrize("second", ["send", "edit", "respond"])
async def test_a_screen_cannot_be_reused_across_delivery_entrypoints(first: DeliveryKind, second: DeliveryKind) -> None:
    async with _scope() as scope:
        screen = BackgroundScreen(scope.owner)
        result = await _present(scope, screen, first)
        assert isinstance(result, sd.Presented)

        with pytest.raises(RuntimeError, match="already been presented"):
            await _present(scope, screen, second)

        assert screen.outcome is None
        assert not result.root.finished


async def test_replacing_a_named_session_publishes_the_replacement_reason() -> None:
    class Replaceable(Anchor):
        policy = sd.ResponseSpec(session=sd.SessionSpec("replaceable"), timeout=None)

    async with _scope() as scope:
        first = Replaceable()
        initial = await _present(scope, first, "respond")
        second = Replaceable()
        replacement = await _present(scope, second, "respond")

        assert isinstance(initial, sd.Presented)
        assert isinstance(replacement, sd.Presented)
        with anyio.fail_after(1):
            assert await first.wait() == sd.Cancelled(sd.ScreenCancelReason.REPLACED)
        assert initial.root.finished
        assert second.outcome is None
        assert not replacement.root.finished
        assert second.session is replacement.session


@pytest.mark.parametrize("override", [False, True])
async def test_screen_session_key_controls_which_presentation_is_replaced(override: bool) -> None:
    class Replaceable(Anchor):
        policy = sd.ResponseSpec(session=sd.SessionSpec("replaceable"), timeout=None)

    async with _scope() as scope:
        first = Replaceable()
        first.session_key = sd.SessionKey.custom("replaceable", "first")
        initial = await _present(scope, first, "respond")
        other = Replaceable()
        other.session_key = sd.SessionKey.custom("replaceable", "other")
        separate = await _present(scope, other, "respond")
        assert isinstance(initial, sd.Presented)
        assert isinstance(separate, sd.Presented)
        assert not initial.root.finished
        assert not separate.root.finished

        replacement = Replaceable()
        replacement.session_key = other.session_key if override else first.session_key
        request = await scope.request(InteractionHarness(user_id=7, client=scope.runtime.client).source)
        result = await request.respond(replacement, session_key=first.session_key if override else None)

        assert isinstance(result, sd.Presented)
        assert result.session is not None
        assert result.session.key == first.session_key
        assert await first.wait() == sd.Cancelled(sd.ScreenCancelReason.REPLACED)
        assert not separate.root.finished


async def test_failed_delivery_preserves_the_original_error_when_screen_cleanup_also_fails() -> None:
    class FailingClose(BackgroundScreen):
        @override
        async def on_close(self, outcome: sd.Completed[None] | sd.Cancelled) -> None:
            assert isinstance(outcome, sd.Cancelled)
            raise ValueError("cleanup failed")

    async with _scope() as scope:
        screen = FailingClose(scope.owner)
        destination = ContextHarness()
        destination.send.error = OSError("delivery failed")
        with pytest.raises(BaseExceptionGroup) as raised:
            await scope.send(destination.source, screen)

        assert raised.value.subgroup(OSError) is not None
        assert raised.value.subgroup(ValueError) is not None
        with anyio.fail_after(1):
            assert isinstance(await screen.wait(), sd.Cancelled)
