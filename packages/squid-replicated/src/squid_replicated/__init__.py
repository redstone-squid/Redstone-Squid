"""Optional immutable-snapshot replicated state for Squid actions."""

from squid_replicated.document import (
    PreparedReplicatedInverse,
    ReplicatedChangeToken,
    ReplicatedClosedError,
    ReplicatedCounter,
    ReplicatedDocument,
    ReplicatedScope,
    ReplicatedSet,
)
from squid_replicated.engine import ReplicatedEngine, StagedReplica
from squid_replicated.fake import FakeEngine, FakeSnapshot, FakeVersion
from squid_replicated.transport import ReplicatedUpdate

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
    "ReplicatedScope",
    "ReplicatedSet",
    "ReplicatedUpdate",
    "StagedReplica",
]
