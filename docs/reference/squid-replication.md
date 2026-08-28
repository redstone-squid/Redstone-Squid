# squid-replication

Optional replicated-state backends: CRDT-backed containers that merge concurrent edits across
processes. The reference backend is dependency-free; install `squid-replication[loro]` or
`[pycrdt]` for the native engines.

```python
import squid_replication as sq
```

## Replicas

::: squid_replication.Replica

::: squid_replication.ReplicaBranch

::: squid_replication.ReplicatedDocument

::: squid_replication.ReplicatedSnapshot

::: squid_replication.ReplicationUpdate

::: squid_replication.ReplicationChangeToken

::: squid_replication.ReplicationHistoryLease

::: squid_replication.PreparedReplicationInverse

## Containers

::: squid_replication.ReplicatedValue

::: squid_replication.ReplicatedMap

::: squid_replication.ReplicatedList

::: squid_replication.ReplicatedMovableList

::: squid_replication.ReplicatedSet

::: squid_replication.ReplicatedText

::: squid_replication.ReplicatedCounter

::: squid_replication.ReplicatedTree

::: squid_replication.ReplicatedTreeNode

::: squid_replication.ReplicatedTreeSnapshot

::: squid_replication.ReplicatedItem

## Backends

::: squid_replication.ReplicationBackend

::: squid_replication.ReplicationEngine

::: squid_replication.ReferenceBackend

::: squid_replication.ReferenceEngine

::: squid_replication.ReferenceSnapshot

::: squid_replication.ReferenceVersion

::: squid_replication.LoroBackend

## Errors

Every deliberate failure derives from `ReplicationError` alongside its stdlib base.

::: squid_replication.ReplicationError

::: squid_replication.ReplicaClosedError

::: squid_replication.ReplicationResyncRequiredError

::: squid_replication.ReplicationCorruptUpdateError

::: squid_replication.ReplicationBackendIntegrityError

::: squid_replication.UnsupportedReplicationContainerError
