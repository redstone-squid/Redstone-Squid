"""Typed outcomes and action events for composed screens."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from squid_ui.interactions import ActionEvent
from squid_ui_discord.request import Request


class ScreenCancelReason(StrEnum):
    TIMEOUT = "timeout"
    PARENT_CLOSED = "parent_closed"
    SCOPE_CLOSED = "scope_closed"
    REPLACED = "replaced"
    CLOSED = "closed"
    USER = "user"
    SHUTDOWN = "shutdown"
    FAILURE = "failure"


@dataclass(frozen=True, slots=True)
class Completed[ResultT]:
    """A screen's accepted result."""

    value: ResultT


@dataclass(frozen=True, slots=True)
class Cancelled:
    """A screen ended without accepting a result."""

    reason: ScreenCancelReason = ScreenCancelReason.CLOSED


@dataclass(frozen=True, slots=True, kw_only=True)
class ScreenResultEvent[ResultT](ActionEvent[Request[Any]]):
    """A child result dispatched with fresh authority for the parent screen."""

    value: ResultT


__all__ = ["Cancelled", "Completed", "ScreenCancelReason", "ScreenResultEvent"]
