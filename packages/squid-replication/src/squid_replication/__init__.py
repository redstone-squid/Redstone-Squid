"""Optional immutable-snapshot replicated state for Squid actions."""

from squid_replication.document import (
    PreparedReplicationInverse,
    Replica,
    ReplicaClosedError,
    ReplicatedCounter,
    ReplicatedDocument,
    ReplicatedSet,
    ReplicationChangeToken,
    ReplicationResyncRequiredError,
)
from squid_replication.engine import ReplicaBranch, ReplicationBackend, ReplicationEngine
from squid_replication.reference import ReferenceBackend, ReferenceEngine, ReferenceSnapshot, ReferenceVersion
from squid_replication.transport import ReplicationUpdate

__all__ = [
    "PreparedReplicationInverse",
    "ReferenceBackend",
    "ReferenceEngine",
    "ReferenceSnapshot",
    "ReferenceVersion",
    "Replica",
    "ReplicaBranch",
    "ReplicaClosedError",
    "ReplicatedCounter",
    "ReplicatedDocument",
    "ReplicatedSet",
    "ReplicationBackend",
    "ReplicationChangeToken",
    "ReplicationEngine",
    "ReplicationResyncRequiredError",
    "ReplicationUpdate",
]
