"""Pyrefly fixture for Screen construction and scoped presentation."""

from typing import Any, assert_type, override

import squid_ui as sl
import squid_ui_discord as sd
from squid_ui_discord import Scope, Screen


class RequiredArguments(Screen):
    def __init__(self, label: str, *, count: int) -> None:
        self.label = label
        self.count = count

    @override
    def render(self):
        return sl.heading(f"{self.label}: {self.count}")


async def construction_and_presentation(ui: Scope[object], source: Any) -> None:
    request = await ui.request(source)
    await request.respond(RequiredArguments("ready", count=2))
    await request.respond(RequiredArguments(2, count="wrong"))  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[bad-argument-type]
    await request.respond(RequiredArguments("missing"))  # pyright: ignore[reportCallIssue]  # pyrefly: ignore[missing-argument]
    await request.respond(RequiredArguments("ready", count=2), None)  # pyright: ignore[reportCallIssue]  # pyrefly: ignore[no-matching-overload]


class PresetOwner:
    pass


class TypedScreen(sd.Screen[None, PresetOwner]):
    policy = sd.ResponseSpec(session=sd.SessionSpec("typed-screen"))

    @override
    def render(self):
        assert_type(self.owner, PresetOwner)
        assert_type(self.opening, sd.Request[PresetOwner] | None)
        assert_type(self.session, sd.sessions.Session | None)
        if self.opening is not None:
            assert_type(self.opening.owner, PresetOwner)
        return sl.heading("Typed")


class StringResult(sd.Screen[str]):
    @override
    def render(self) -> sl.LayoutNode:
        return sl.heading("Choose")

    async def choose(self, event: sd.PressEvent) -> None:
        self.complete(event, "chosen")
        self.complete(event, 1)  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[bad-argument-type]


class ResultParent(sd.Screen):
    @override
    def render(self) -> sl.LayoutNode:
        return sl.heading("Parent")

    async def open(self, event: sd.PressEvent) -> None:
        async def accepted(result: sd.ScreenResultEvent[str]) -> None:
            assert_type(result.value, str)
            assert_type(result.context, sd.Request[Any])

        await self.open_child(event, StringResult(), on_result=accepted)


async def wait_for_typed_result(screen: StringResult) -> None:
    result = await screen.wait()
    assert_type(result, sd.Completed[str] | sd.Cancelled)
    if isinstance(result, sd.Completed):
        assert_type(result.value, str)
    else:
        assert_type(result.reason, sd.ScreenCancelReason)
