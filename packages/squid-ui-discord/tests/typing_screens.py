"""Pins constructor and option checking at the declarative screen boundary. Nothing here runs.

Screen construction happens before ``show`` so each subclass keeps its real ``__init__``
signature. Session option bundles are TypedDicts for the same reason: misspelled or wrongly
typed policy should fail where it is declared, not when a message is opened.
"""

from typing import cast

import squid_ui as sl
import squid_ui_discord as sd


class RequiredArguments(sd.Screen):
    session = "required"

    def __init__(self, label: str, *, count: int) -> None:
        self.label = label
        self.count = count

    def render(self):
        return sl.heading(f"{self.label}: {self.count}")


source = cast(sd.InvocationSource, object())


async def construction_is_checked_before_show() -> None:
    await RequiredArguments("ready", count=2).show(source)
    await RequiredArguments(2, count="wrong").show(source)  # pyrefly: ignore[bad-argument-type]
    await RequiredArguments("missing").show(source)  # pyrefly: ignore[missing-argument]


sd.SessionSpec("typed", options={"timeout": 20, "strict": True})
sd.SessionSpec("typo", options={"timout": 20})  # pyrefly: ignore[bad-typed-dict-key]
sd.SessionSpec("wrong", options={"timeout": "later"})  # pyrefly: ignore[bad-assignment]


class InvalidOptions(sd.Screen):
    options = {"strcit": True}  # pyrefly: ignore[bad-typed-dict-key]

    def render(self):
        return sl.heading("invalid")
