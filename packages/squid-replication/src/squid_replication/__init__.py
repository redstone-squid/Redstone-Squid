"""Optional immutable-snapshot replicated state for Squid actions."""

from squid_replication.backends.loro import LoroBackend
from squid_replication.document import (
    PreparedReplicationInverse,
    Replica,
    ReplicaClosedError,
    ReplicatedCounter,
    ReplicatedDocument,
    ReplicatedList,
    ReplicatedMap,
    ReplicatedMovableList,
    ReplicatedSet,
    ReplicatedText,
    ReplicatedTree,
    ReplicationBackendIntegrityError,
    ReplicationChangeToken,
    ReplicationCorruptUpdateError,
    ReplicationHistoryLease,
    ReplicationResyncRequiredError,
)
from squid_replication.engine import ReplicaBranch, ReplicationBackend, ReplicationEngine
from squid_replication.model import (
    ReplicatedItem,
    ReplicatedSnapshot,
    ReplicatedTreeNode,
    ReplicatedTreeSnapshot,
    ReplicatedValue,
)
from squid_replication.reference import ReferenceBackend, ReferenceEngine, ReferenceSnapshot, ReferenceVersion
from squid_replication.transport import ReplicationUpdate

__all__ = [
    "LoroBackend",
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
    "ReplicatedItem",
    "ReplicatedList",
    "ReplicatedMap",
    "ReplicatedMovableList",
    "ReplicatedSet",
    "ReplicatedSnapshot",
    "ReplicatedText",
    "ReplicatedTree",
    "ReplicatedTreeNode",
    "ReplicatedTreeSnapshot",
    "ReplicatedValue",
    "ReplicationBackend",
    "ReplicationBackendIntegrityError",
    "ReplicationChangeToken",
    "ReplicationCorruptUpdateError",
    "ReplicationEngine",
    "ReplicationHistoryLease",
    "ReplicationResyncRequiredError",
    "ReplicationUpdate",
]
