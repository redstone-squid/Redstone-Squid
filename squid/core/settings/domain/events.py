"""Domain events published by guild writes."""

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class GuildVotingChanged:
    event_type: ClassVar[str] = "guild.voting_changed"
    aggregate_kind: ClassVar[str] = "guild"
    schema_version: ClassVar[int] = 1

    aggregate_id: int

    def payload(self) -> dict[str, object]:
        return {}
