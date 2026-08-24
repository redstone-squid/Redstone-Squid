# squid-replicated

Optional replicated-state integration for Squid actions. The package keeps backend containers
private, exposes immutable Python snapshots, stages mutations as transaction participants, and
routes decoded remote updates through the same runtime commit gate as local actions.

The default deterministic engine is a reference/conformance backend for counters and tagged sets.
It proves operation identity, idempotent delivery, convergence, semantic inverse planning, and mixed
ordinary/replicated atomicity; it is not a networking or durable-storage product.

The `loro` and `pycrdt` extras contain experimental text SPI adapters pinned to the versions audited
in `docs/plans/68-replicated-backend-report.md`. Loro is the selected generalized-backend hardening
direction; neither adapter is production-ready. The current conformance evidence covers remote text,
action-addressable inverse tokens, reordered delivery, restart, and multi-container grouping, while
register conflicts, compaction, ownership, security, and representative workloads remain gated.

```python
from squid_replicated import ReplicatedScope
from squid_reactive import transaction

scope = ReplicatedScope("replica-a")
document = scope.open("project-7")

with transaction():
    document.counter("votes").increment(1)
    document.set("tags").add("reviewed")

assert document.counter("votes").value == 1
scope.close()
```

`ReplicatedScope.close()` ends every document, subscription, and mutation authority it owns.
