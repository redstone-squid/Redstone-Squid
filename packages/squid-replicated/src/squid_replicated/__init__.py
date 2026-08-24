"""Optional immutable-snapshot replicated state for Squid actions."""

from squid_replicated.document import (
    ReplicatedChangeToken,
    ReplicatedClosedError,
    ReplicatedCounter,
    ReplicatedDocument,
    ReplicatedScope,
    ReplicatedSet,
)
from squid_replicated.engine import ReplicatedEngine, StagedReplica
from squid_replicated.fake import FakeEngine, FakeSnapshot, FakeVersion

__all__ = [
    "FakeEngine",
    "FakeSnapshot",
    "FakeVersion",
    "ReplicatedChangeToken",
    "ReplicatedClosedError",
    "ReplicatedCounter",
    "ReplicatedDocument",
    "ReplicatedEngine",
    "ReplicatedScope",
    "ReplicatedSet",
    "StagedReplica",
]
