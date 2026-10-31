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
    await ui.respond(source, RequiredArguments("ready", count=2))
    await ui.respond(source, RequiredArguments(2, count="wrong"))  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[bad-argument-type]
    await ui.respond(source, RequiredArguments("missing"))  # pyright: ignore[reportCallIssue]  # pyrefly: ignore[missing-argument]
    await ui.respond(source, RequiredArguments("ready", count=2), None)  # pyright: ignore[reportCallIssue]  # pyrefly: ignore[no-matching-overload]


class PresetOwner:
    pass


class TypedPreset(sd.UserSessionScreen[PresetOwner], session_name="typed-preset"):
    @override
    def render(self):
        assert_type(self.opening.owner, PresetOwner)
        return sl.heading("Typed")
