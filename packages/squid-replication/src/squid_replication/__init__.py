"""Optional immutable-snapshot replicated state for Squid actions."""

from squid_replication.document import (
    PreparedReplicatedInverse,
    ReplicatedChangeToken,
    ReplicatedClosedError,
    ReplicatedCounter,
    ReplicatedDocument,
    ReplicatedResyncRequiredError,
    ReplicatedScope,
    ReplicatedSet,
)
from squid_replication.engine import ReplicatedEngine, StagedReplica
from squid_replication.fake import FakeEngine, FakeSnapshot, FakeVersion
from squid_replication.transport import ReplicatedUpdate

__all__ = [
    "FakeEngine",
    "FakeSnapshot",
    "FakeVersion",
    "PreparedReplicatedInverse",
    "ReplicatedChangeToken",
    "ReplicatedClosedError",
    "ReplicatedCounter",
    "ReplicatedDocument",
    "ReplicatedEngine",
    "ReplicatedResyncRequiredError",
    "ReplicatedScope",
    "ReplicatedSet",
    "ReplicatedUpdate",
    "StagedReplica",
]
