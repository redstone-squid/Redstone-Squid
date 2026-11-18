"""The voting extension's public recount interface."""

from dataclasses import dataclass
from typing import Protocol

from squid.bot.extensions import ExtensionSpec


class VoteRecount(Protocol):
    def is_recounting(self, vote_session_id: int) -> bool: ...

    async def recount(self, vote_session_id: int) -> bool: ...


@dataclass(frozen=True, slots=True)
class VotingExports:
    recount: VoteRecount


extension: ExtensionSpec[VotingExports] = ExtensionSpec(id="voting", module="squid.bot.voting.cog")
