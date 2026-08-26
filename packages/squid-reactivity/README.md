# squid-reactivity

Transactional reactive state for synchronous projections, with no hard dependencies and no
background tasks.

```python
from squid_reactivity import StateOwner, computed, observe_reads, state, transaction


class Counter(StateOwner):
    count: int = state(0)

    @computed
    def doubled(self) -> int:
        return self.count * 2


counter = Counter()
with transaction():
    counter.count += 1

with observe_reads() as read_set:
    assert counter.doubled == 2
```

The package is layered:

- `squid_reactivity.actions` provides sortable action IDs, causal contexts, immutable terminal
  outcomes, bounded ledgers, aftermath authority, and portable redacted schema version 1.
- `squid_reactivity.core` provides state cells, computed values, full strong-read OCC,
  version-conditional patches, staged transaction participants, and the reusable `StateOwner` owner.
- `squid_reactivity.shared_state` provides `SharedState`, whose state fields publish exact `CellAddress`
  values through a host-supplied bus.
- `squid_reactivity.topics` provides portable `Topic` values, tracked `watch()` reads, the small
  synchronous `TopicBus` protocol, `LocalTopicBus`, and committed/staged subscription
  reconciliation.
- `squid_reactivity.resources` is an optional import for tracked asynchronous values. It uses
  only the standard library and runs loads in the caller's task. Cancellation is the host's:
  `abandon_superseded_loads` installs a `LoadScope` factory -- `anyio.CancelScope` satisfies the
  protocol as it stands -- and a superseded generation is then stopped rather than run to
  completion. Uninstalled, it runs on and only its result is discarded.
- `squid_reactivity.operations` separates repeatable definitions from causally identified one-shot
  executions; every retry receives a fresh execution ID and terminal status.

A publishing transaction validates every strongly read addressed cell immediately before its
prepare/apply commit point. A read is strong when the action also writes that cell, when it was
taken inside `strong_read()`, or when it was pinned with `require_version()`; a read-only read is
not validated by default. `relaxed_read()` opts a read out of that validation;
`untracked()` independently opts out of dependency capture. Commit and rollback aftermath hooks are
failure-isolated and cannot mutate through a completed transaction. Recovery starts a new causal action.

`LocalTopicBus.publish()` delivers synchronously in registration order. If a subscriber raises,
the bus reports it through `on_subscriber_error`, continues delivering to the remaining
subscribers, and never lets the subscriber kill the bus. The default hook logs the exception.

The bus deliberately has no queue, run loop, durability, expiry, or bridge policy. A real host
implements the two-method `TopicBus` protocol and owns any loops needed to connect processes or
schedule asynchronous projection refreshes.
