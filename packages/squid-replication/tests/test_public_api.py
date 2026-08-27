"""Snapshot the supported root namespace."""

import squid_replication


def test_public_api_snapshot() -> None:
    expected = {
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
        "UnsupportedReplicationContainerError",
    }
    assert set(squid_replication.__all__) == expected
    assert all(hasattr(squid_replication, name) for name in expected)
