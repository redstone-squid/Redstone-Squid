"""One per-user budget shared by commands and costly interactive actions."""

import time
from collections import OrderedDict, deque
from math import ceil

from squid.core.errors import RateLimitedError
from squid.observability import add_counter
from squid_i18n import tr


class CommandLimits:
    """Admit 12 units per user in 15 seconds; rejected requests spend nothing.

    Costly work spends four units. Entries expire after their last accepted request,
    and the table admits at most 4096 users at once. This budget is process-local.
    """

    def __init__(self) -> None:
        self._users: OrderedDict[int, deque[tuple[float, int]]] = OrderedDict()

    def check(self, user_id: int, *, cost: int = 1) -> None:
        if not 1 <= cost <= 12:
            message = "Command cost must be between 1 and 12"
            raise ValueError(message)
        now = time.monotonic()
        while self._users and next(iter(self._users.values()))[-1][0] <= now - 15:
            self._users.popitem(last=False)
        events = self._users.get(user_id)
        wait = 0
        if events is None and len(self._users) >= 4096:
            wait = max(1, ceil(next(iter(self._users.values()))[-1][0] + 15 - now))
        else:
            if events is None:
                events = deque()
                self._users[user_id] = events
            while events and events[0][0] <= now - 15:
                events.popleft()
            spent = sum(units for _, units in events)
            if spent + cost > 12:
                for occurred_at, units in events:
                    spent -= units
                    if spent + cost <= 12:
                        wait = max(1, ceil(occurred_at + 15 - now))
                        break
            else:
                events.append((now, cost))
                self._users.move_to_end(user_id)
        if wait:
            add_counter("squid.discord.rate_limit.rejected")
            raise RateLimitedError(wait).with_context(
                message=tr(t"bot-rate-limited = Too many requests. Try again in {wait} seconds.")
            )
