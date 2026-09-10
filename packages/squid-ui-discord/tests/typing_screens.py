"""Pyrefly fixture for Screen construction and scoped presentation."""

from typing import Any, override

import squid_ui as sl
from squid_ui_discord import DiscordUI, Screen


class RequiredArguments(Screen):
    def __init__(self, label: str, *, count: int) -> None:
        self.label = label
        self.count = count

    @override
    def render(self):
        return sl.heading(f"{self.label}: {self.count}")


async def construction_and_presentation(ui: DiscordUI[object], source: Any) -> None:
    await ui.respond(source, RequiredArguments("ready", count=2))
    await ui.respond(source, RequiredArguments(2, count="wrong"))  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[bad-argument-type]
    await ui.respond(source, RequiredArguments("missing"))  # pyright: ignore[reportCallIssue]  # pyrefly: ignore[missing-argument]
    await ui.respond(source, RequiredArguments("ready", count=2), None)  # pyright: ignore[reportCallIssue]  # pyrefly: ignore[no-matching-overload]
