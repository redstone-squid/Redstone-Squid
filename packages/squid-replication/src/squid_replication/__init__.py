"""Optional immutable-snapshot replicated state for Squid actions."""

from squid_replication.document import (
    PreparedReplicationInverse,
    ReplicationChangeToken,
    ReplicaClosedError,
    ReplicatedCounter,
    ReplicatedDocument,
    ReplicationResyncRequiredError,
    Replica,
    ReplicatedSet,
)
from squid_replication.engine import ReplicationEngine, ReplicaBranch
from squid_replication.reference import ReferenceEngine, ReferenceSnapshot, ReferenceVersion
from squid_replication.transport import ReplicationUpdate

__all__ = [
    "ReferenceEngine",
    "ReferenceSnapshot",
    "ReferenceVersion",
    "PreparedReplicationInverse",
    "ReplicationChangeToken",
    "ReplicaClosedError",
    "ReplicatedCounter",
    "ReplicatedDocument",
    "ReplicationEngine",
    "ReplicationResyncRequiredError",
    "Replica",
    "ReplicatedSet",
    "ReplicationUpdate",
    "ReplicaBranch",
]
