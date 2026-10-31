# squid-replication

Optional replicated-state integration for Squid actions. The package keeps backend containers
private, exposes immutable Python snapshots, stages mutations as transaction participants, and
routes decoded remote updates through the same runtime commit gate as local actions.

The deterministic `ReferenceBackend` is a reference/conformance backend for counters and tagged sets.
It proves operation identity, idempotent delivery, convergence, semantic inverse planning, and mixed
ordinary/replicated atomicity; it is not a networking or durable-storage product.

The `loro` extra contains the production generalized backend pinned to the version audited in
`docs/plans/68-replicated-backend-report.md`. It exposes named text, list, movable-list, map, tree,
exact counter, and tagged-set containers. The older `LoroTextEngine` and pycrdt adapters remain
conformance spikes and are not production APIs.

```python
from squid_replication import ReferenceBackend, Replica
from squid_reactivity import transaction

scope = Replica("replica-a", backend=ReferenceBackend())
document = scope.open("project-7")

with transaction():
    document.counter("votes").increment(1)
    document.set("tags").add("reviewed")

assert document.counter("votes").value == 1
scope.close()
```

`Replica.close()` ends every document, subscription, and mutation authority it owns.

For Loro, inject one backend per replica incarnation and persist its `peer_id` with that incarnation:

```python
from squid_replication import LoroBackend, Replica

scope = Replica("worker-7:incarnation-42", backend=LoroBackend(peer_id=stored_peer_id))
document = scope.open("submission-123")
```

Every public value is a deeply immutable Python snapshot; mutable Loro containers never escape the
adapter. A history entry automatically leases the frontier needed by its action token. Calling
`document.compact_history()` emits a shallow in-memory document no newer than the oldest lease; a
released token beyond that boundary returns a typed conflict. `document.checkpoint()` returns a full
transport envelope suitable for host-owned authenticated storage, and `document.version()` supplies the
opaque value used by `export_since(version)` during resynchronization.

Undo policy is type-aware. Text reversals use a frontier diff filtered to the affected text roots;
counters and sets emit semantic inverse operations; maps, replacements, and moves require their recorded
action authority still to win. If any guarded path was superseded, planning returns one conflict and the
whole mixed action changes nothing. Raw document-wide frontier reversal is never used by the production
adapter.

The host still owns transport, authentication, authorization, durable storage, and task lifetime. Incoming
envelopes and tokens are schema-checked and size-bounded, but the payload hash is an integrity checksum, not
an authentication mechanism. Representative p50/p95/p99 fixtures and generous regression ceilings live in
`benchmarks/fixtures/loro_document_v1.json`.
