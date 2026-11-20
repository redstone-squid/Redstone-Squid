"""Consumers can widen their view of an event while its concrete context stays fixed."""

# pyright: reportUnnecessaryTypeIgnoreComment=true
from dataclasses import replace

from squid_ui.interactions import ActionEvent, ActionEventView


def replace_context(event: ActionEvent[object]) -> ActionEvent[object]:
    return replace(event, context=object())


def read_context(event: ActionEventView[object]) -> object:
    return event.context


def check(event: ActionEvent[str]) -> None:
    read_context(event)
    replace_context(event)  # pyright: ignore[reportArgumentType]  # Must reject replacing a narrower context.
