"""A process-local cache of assembled permission rules.

What is cached is the *rule set*, not the decision. One load then answers every
node a command checks, every node a vote actor needs, and any node added later
that an already-cached wildcard covers — which is the difference between one
round trip per command and one per check.

Invalidation is by epoch rather than by time. Any permission write anywhere bumps
a single counter; a watcher notices and clears the whole cache. Clearing
everything is deliberate: a role edit changes the answer for every holder, and a
role *composition* edit changes it for every transitive holder, so per-subject
invalidation would have to know a graph that the writing process does not have in
front of it. `clear()` is O(1) and always correct.

The wall-clock backstop bounds what a dead watcher can cost. Entries older than
`max_age_seconds` are refetched, so the failure mode is a 30-second stale grant
rather than an indefinite one.
"""

from collections import OrderedDict
from dataclasses import dataclass

from whenever import Instant

from squid.observability import add_counter
from squid.permissions.domain import Rule, Subject

MAX_ENTRIES = 4096
"""Bounded so a raid, or a scripted client, cannot grow the cache without limit."""

MAX_AGE_SECONDS = 30.0
"""How stale an entry may be if epoch invalidation has stopped arriving."""

type CacheKey = tuple[int | None, frozenset[int], int | None, bool]


def cache_key(subject: Subject) -> CacheKey:
    """The identity of everything about `subject` that changes its rule set.

    `discord_guild_admin` is part of the key even though the plan's key was
    account, roles and guild alone: the Manage-Server bridge contributes rules to
    the assembled set, so a subject holding it and a subject not holding it do not
    share an answer. `is_bot_owner` is absent because the owner short-circuits
    before any rule is read, so no owner entry is ever stored.
    """
    return (subject.account_id, subject.discord_role_ids, subject.guild_id, subject.discord_guild_admin)


@dataclass(frozen=True, slots=True)
class _Entry:
    rules: tuple[Rule, ...]
    epoch: int
    fetched_at: Instant


class SubjectRuleCache:
    """A bounded LRU of assembled rule sets, keyed by subject identity."""

    def __init__(self, *, max_entries: int = MAX_ENTRIES, max_age_seconds: float = MAX_AGE_SECONDS) -> None:
        self._entries: OrderedDict[CacheKey, _Entry] = OrderedDict()
        self._max_entries = max_entries
        self._max_age_seconds = max_age_seconds
        self._epoch = 0

    @property
    def epoch(self) -> int:
        """The newest permission epoch this process has seen."""
        return self._epoch

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: CacheKey, *, now: Instant | None = None) -> tuple[Rule, ...] | None:
        """The cached rules for `key`, or None if there is nothing usable."""
        entry = self._entries.get(key)
        if entry is None:
            add_counter("permissions.cache.miss")
            return None

        if entry.epoch != self._epoch:
            # Read at an epoch that has since moved: something was granted or
            # revoked between the read and now.
            del self._entries[key]
            add_counter("permissions.cache.stale_epoch_discard")
            add_counter("permissions.cache.miss")
            return None

        instant = now if now is not None else Instant.now()
        if (instant - entry.fetched_at).total("seconds") >= self._max_age_seconds:
            del self._entries[key]
            add_counter("permissions.cache.miss")
            return None

        self._entries.move_to_end(key)
        add_counter("permissions.cache.hit")
        return entry.rules

    def put(self, key: CacheKey, rules: tuple[Rule, ...], *, epoch: int, now: Instant | None = None) -> None:
        """Store the rules assembled for `key`, stamped with the epoch they were read at.

        A load that returns a newer epoch than this process knew about advances the
        cache itself, so entries read before that write are discarded on their next
        read without waiting for the watcher. The watcher is a latency hint here
        too, not the only route to correctness.
        """
        if epoch > self._epoch:
            self._epoch = epoch
        self._entries[key] = _Entry(rules=rules, epoch=epoch, fetched_at=now if now is not None else Instant.now())
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def observe_epoch(self, version: int) -> bool:
        """Adopt an epoch seen elsewhere, clearing everything if it moved.

        Epoch `0` means "not yet known", so the watcher's first successful read
        adopts silently rather than reporting an invalidation nobody caused.
        """
        if version == self._epoch:
            return False
        first_reading = self._epoch == 0
        self._epoch = version
        self.clear(counted=not first_reading)
        return not first_reading

    def clear(self, *, counted: bool = False) -> None:
        """Drop every entry."""
        self._entries.clear()
        if counted:
            add_counter("permissions.cache.invalidation")
