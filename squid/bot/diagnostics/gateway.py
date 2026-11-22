"""Bounded gateway history for connection diagnostics."""

from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import discord

from squid.core.observability import add_counter

type GatewayEventKind = Literal["identify", "connect", "ready", "disconnect", "resume"]


@dataclass(frozen=True, slots=True)
class GatewayEvent:
    kind: GatewayEventKind
    occurred_at: datetime
    shard_id: int | None = None


class GatewayHistory:
    """Retain the last 100 events and counts since process startup."""

    def __init__(self) -> None:
        self.events: deque[GatewayEvent] = deque(maxlen=100)
        self.counts: Counter[GatewayEventKind] = Counter()

    def record(self, kind: GatewayEventKind, *, shard_id: int | None = None) -> None:
        self.events.append(GatewayEvent(kind, discord.utils.utcnow(), shard_id))
        self.counts[kind] += 1
        add_counter("squid.discord.gateway.events", attributes={"squid.gateway.event": kind})
