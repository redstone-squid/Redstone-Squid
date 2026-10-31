"""Explicit authorization policies for Discord message roots."""

from collections.abc import Awaitable, Callable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Protocol

import discord

from squid_ui.text import TextLike


@dataclass(frozen=True, slots=True)
class Allowed:
    """An access policy admitted the interaction."""


@dataclass(frozen=True, slots=True)
class Denied:
    """An access policy refused the interaction, optionally with host-supplied wording."""

    reason: TextLike | None = None


type AccessDecision = Allowed | Denied


class AccessPolicy(Protocol):
    """Asynchronously decide whether an interaction may enter a mount's dispatch funnel."""

    async def check(self, interaction: discord.Interaction) -> AccessDecision: ...


@dataclass(frozen=True, slots=True)
class Everyone:
    """Allow every interaction."""

    async def check(self, interaction: discord.Interaction) -> AccessDecision:
        return Allowed()


@dataclass(frozen=True, slots=True)
class Owner:
    """Allow exactly one Discord user ID."""

    user_id: int

    async def check(self, interaction: discord.Interaction) -> AccessDecision:
        return Allowed() if interaction.user.id == self.user_id else Denied()


@dataclass(frozen=True, slots=True, init=False)
class Users:
    """Allow a fixed set of Discord user IDs."""

    user_ids: frozenset[int]

    def __init__(self, user_ids: AbstractSet[int]) -> None:
        object.__setattr__(self, "user_ids", frozenset(user_ids))

    async def check(self, interaction: discord.Interaction) -> AccessDecision:
        return Allowed() if interaction.user.id in self.user_ids else Denied()


@dataclass(frozen=True, slots=True)
class Check:
    """Delegate access to an application-provided asynchronous check."""

    callback: Callable[[discord.Interaction], Awaitable[AccessDecision]]

    async def check(self, interaction: discord.Interaction) -> AccessDecision:
        return await self.callback(interaction)
