"""Reuse a subject's permission rules across checks in one process.

PermissionService assembles a rule set from direct grants, permission-role
assignments, and the Discord Manage Server bridge. Caching that set lets several
capability checks share one database load. Decisions themselves are not cached:
the resolver still checks each rule's expiry and matches it against the requested
node, including nodes added to the catalogue after a wildcard was granted.

Permission writes advance a shared database counter, called the permission epoch.
PermissionEpochWatcher observes that counter through polling and notification
hints. A changed epoch invalidates every subject's cached rules. This is deliberate:
a permission role can include other roles, so editing one role may affect people
who hold it indirectly as well as its direct holders.

Entries also expire after `max_age_seconds` (30 seconds by default). This forces
a new load even if the watcher has stopped reporting changes.
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
    """Include every subject fact that changes its assembled rules.

    The Manage Server flag contributes bridge rules. The owner flag is omitted
    because the service bypasses the cache for owners.
    """
    return (subject.account_id, subject.discord_role_ids, subject.guild_id, subject.discord_guild_admin)


@dataclass(frozen=True, slots=True)
class _Entry:
    rules: tuple[Rule, ...]
    epoch: int
    fetched_at: Instant


class SubjectRuleCache:
    """Cache assembled rule sets, evicting the least recently used entry when full.

    Each entry records the database epoch at which it was loaded and the fetch time.
    `get` discards entries from a different epoch or older than `max_age_seconds`.
    """

    def __init__(self, *, max_entries: int = MAX_ENTRIES, max_age_seconds: float = MAX_AGE_SECONDS) -> None:
        self._entries: OrderedDict[CacheKey, _Entry] = OrderedDict()
        self._max_entries = max_entries
        self._max_age_seconds = max_age_seconds
        self._epoch = 0

    @property
    def epoch(self) -> int:
        """The permission epoch currently used to validate entries."""
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
        """Store rules with the epoch observed before their database load.

        A newer epoch advances the cache epoch. Older entries are discarded on their
        next lookup, even if the watcher has not yet observed the change.
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
