"""Pyrefly fixture for Screen construction and scoped presentation."""

from typing import Any

import squid_ui as sl
from squid_ui_discord import DiscordUI, Screen


class RequiredArguments(Screen):
    def __init__(self, label: str, *, count: int) -> None:
        self.label = label
        self.count = count

    def render(self):
        return sl.heading(f"{self.label}: {self.count}")


async def construction_and_presentation(ui: DiscordUI[object], source: Any) -> None:
    await ui.respond(source, RequiredArguments("ready", count=2))
    await ui.respond(source, RequiredArguments(2, count="wrong"))  # pyrefly: ignore[bad-argument-type]
    await ui.respond(source, RequiredArguments("missing"))  # pyrefly: ignore[missing-argument]
    await ui.respond(source, RequiredArguments("ready", count=2), None)  # pyrefly: ignore[no-matching-overload]
