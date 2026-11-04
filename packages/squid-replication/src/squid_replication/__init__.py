"""Share action state between replicas while keeping reads immutable.

A Replica opens named documents using a chosen backend. Actions edit a document's
containers inside a reactive transaction; commit publishes a new snapshot and an
update for the host to transport. Another replica imports that update into its own
document. The backend merges concurrent changes and supplies selective undo tokens,
so undo can reverse one action without restoring the whole document to an old copy.
"""

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
    ReplicationError,
    ReplicationHistoryLease,
    ReplicationResyncRequiredError,
    UnsupportedReplicationContainerError,
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
    "ReplicationError",
    "ReplicationHistoryLease",
    "ReplicationResyncRequiredError",
    "ReplicationUpdate",
    "UnsupportedReplicationContainerError",
]
