# squid-reactive

Transactional reactive state for synchronous projections, with no hard dependencies and no
background tasks.

```python
from squid_reactive import Reactive, computed, observe_reads, state, transaction


class Counter(Reactive):
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

- `squid_reactive.core` provides state cells, computed values, transactions, OCC, action
  participants, read observation, and the reusable `Reactive` owner.
- `squid_reactive.shared` provides `Shared`, whose state fields publish exact `CellAddress`
  values through a host-supplied bus.
- `squid_reactive.topics` provides portable `Topic` values, tracked `watch()` reads, the small
  synchronous `TopicBus` protocol, `LocalTopicBus`, and committed/staged subscription
  reconciliation.
- `squid_reactive.resources` is an optional import for tracked asynchronous values. It uses
  only the standard library and runs loads in the caller's task.

`LocalTopicBus.publish()` delivers synchronously in registration order. If a subscriber raises,
the bus reports it through `on_subscriber_error`, continues delivering to the remaining
subscribers, and never lets the subscriber kill the bus. The default hook logs the exception.

The bus deliberately has no queue, run loop, durability, expiry, or bridge policy. A real host
implements the two-method `TopicBus` protocol and owns any loops needed to connect processes or
schedule asynchronous projection refreshes.
