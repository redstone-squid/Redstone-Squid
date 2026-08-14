"""The rule-set cache and the epoch watcher that invalidates it."""

from collections.abc import Iterable

from whenever import Instant

from squid.permissions.application import PermissionEpochWatcher, PermissionService, SubjectRuleCache, cache_key
from squid.permissions.application.ports import AssignmentRecord, RoleRecord, SubjectRecords
from squid.permissions.domain import BUILTIN_ROLES_BY_KEY, Rule, Subject

GUILD = 555

BUILTIN_ROWS = tuple(
    RoleRecord(id=index + 1, slug=role.key, guild_id=None, builtin_key=role.key, rank=role.rank, protected=True)
    for index, role in enumerate(BUILTIN_ROLES_BY_KEY.values())
)
ROLE_ID = {row.builtin_key: row.id for row in BUILTIN_ROWS}


class CountingStore:
    """A store whose epoch can be moved the way another process would move it."""

    def __init__(self, *, epoch: int = 1) -> None:
        self.epoch_version = epoch
        self.loads = 0

    async def load_for_subject(
        self,
        *,
        account_id: int | None,
        discord_role_ids: Iterable[int],
        guild_id: int | None,
    ) -> SubjectRecords:
        self.loads += 1
        return SubjectRecords(
            epoch=self.epoch_version,
            roles=BUILTIN_ROWS,
            assignments=(AssignmentRecord(role_id=ROLE_ID["trusted"], subject_account_id=account_id),),
        )

    async def epoch(self) -> int:
        return self.epoch_version


def rule(pattern: str = "build.**") -> tuple[Rule, ...]:
    from squid.permissions.domain import Effect, Pattern

    return (Rule(pattern=Pattern.parse(pattern), effect=Effect.ALLOW),)


class TestSubjectRuleCache:
    def test_a_hit_returns_the_stored_rules(self) -> None:
        cache = SubjectRuleCache()
        key = cache_key(Subject(account_id=7))
        cache.put(key, rule(), epoch=1)

        assert cache.get(key) == rule()

    def test_an_entry_read_before_a_write_is_discarded(self) -> None:
        """The stamp is the epoch the rules were *read* at, not the current one."""
        cache = SubjectRuleCache()
        key = cache_key(Subject(account_id=7))
        cache.put(key, rule(), epoch=1)
        cache.observe_epoch(2)

        assert cache.get(key) is None

    def test_the_wall_clock_backstop_expires_an_entry(self) -> None:
        """A watcher that has died degrades to a TTL, never to unbounded staleness."""
        cache = SubjectRuleCache(max_age_seconds=30)
        key = cache_key(Subject(account_id=7))
        stored_at = Instant.from_utc(2026, 1, 1)
        cache.put(key, rule(), epoch=1, now=stored_at)

        assert cache.get(key, now=stored_at.add(seconds=29)) is not None
        assert cache.get(key, now=stored_at.add(seconds=31)) is None

    def test_the_first_epoch_reading_is_not_an_invalidation(self) -> None:
        cache = SubjectRuleCache()

        assert cache.observe_epoch(4) is False
        assert cache.epoch == 4
        assert cache.observe_epoch(5) is True

    def test_a_newer_epoch_from_a_load_advances_the_cache(self) -> None:
        """A load that saw a write invalidates entries read before it, watcher or no watcher."""
        cache = SubjectRuleCache()
        stale = cache_key(Subject(account_id=7))
        fresh = cache_key(Subject(account_id=8))
        cache.put(stale, rule(), epoch=1)
        cache.put(fresh, rule(), epoch=2)

        assert cache.get(stale) is None
        assert cache.get(fresh) is not None

    def test_the_least_recently_used_entry_is_evicted(self) -> None:
        cache = SubjectRuleCache(max_entries=2)
        first, second, third = (cache_key(Subject(account_id=account)) for account in (1, 2, 3))
        cache.put(first, rule(), epoch=1)
        cache.put(second, rule(), epoch=1)
        cache.get(first)  # first is now the most recently used
        cache.put(third, rule(), epoch=1)

        assert len(cache) == 2
        assert cache.get(second) is None
        assert cache.get(first) is not None

    def test_the_manage_server_bridge_is_part_of_the_key(self) -> None:
        """Two subjects differing only in Manage Server do not share a rule set."""
        assert cache_key(Subject(account_id=7, guild_id=GUILD)) != cache_key(
            Subject(account_id=7, guild_id=GUILD, discord_guild_admin=True)
        )


class TestServiceCaching:
    async def test_a_second_check_reuses_the_loaded_rules(self) -> None:
        store = CountingStore()
        permissions = PermissionService(store, cache=SubjectRuleCache())
        subject = Subject(account_id=7, guild_id=GUILD)

        assert await permissions.allows(subject, "vote.log_delete.cast")
        assert await permissions.allows(subject, "vote.weight.staff")
        assert store.loads == 1

    async def test_a_write_elsewhere_forces_a_reload(self) -> None:
        store = CountingStore()
        cache = SubjectRuleCache()
        permissions = PermissionService(store, cache=cache)
        watcher = PermissionEpochWatcher(store, cache)
        subject = Subject(account_id=7, guild_id=GUILD)

        await permissions.allows(subject, "vote.log_delete.cast")
        store.epoch_version = 2
        await watcher.refresh()
        await permissions.allows(subject, "vote.log_delete.cast")

        assert store.loads == 2

    async def test_the_owner_is_never_cached(self) -> None:
        """Nothing is loaded for the owner, so nothing may be stored under their key."""
        store = CountingStore()
        cache = SubjectRuleCache()
        permissions = PermissionService(store, cache=cache)

        assert await permissions.allows(Subject(account_id=1, is_bot_owner=True), "bot.tree.sync")
        assert store.loads == 0
        assert len(cache) == 0


class TestEpochWatcher:
    async def test_refresh_adopts_the_stored_epoch(self) -> None:
        store = CountingStore(epoch=9)
        cache = SubjectRuleCache()

        await PermissionEpochWatcher(store, cache).refresh()

        assert cache.epoch == 9

    async def test_a_notification_refreshes_through_the_listener(self) -> None:
        class OneShotListener:
            def __init__(self) -> None:
                self.wakes = 0

            async def run(self, on_wake) -> None:
                self.wakes += 1
                await on_wake()

        store = CountingStore(epoch=3)
        cache = SubjectRuleCache()
        listener = OneShotListener()

        await PermissionEpochWatcher(store, cache, listener=listener).listen()

        assert listener.wakes == 1
        assert cache.epoch == 3
