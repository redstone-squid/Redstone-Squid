"""Replicated SPI conformance against the deterministic fake backend."""

import contextvars
import json
import uuid

import pytest
from squid_replicated import (
    PreparedReplicatedInverse,
    ReplicatedChangeToken,
    ReplicatedClosedError,
    ReplicatedScope,
    ReplicatedUpdate,
)

from squid_layouts.runtime import History
from squid_reactive import (
    ActionCommit,
    ActionLedger,
    ConflictDetail,
    Reactive,
    ReactiveConflictError,
    add_action_outcome_sink,
    on_action_commit,
    state,
    transaction,
)
from squid_reactive.core import _CURRENT
from squid_reactive.testing import InterleavingHarness


class LocalModel(Reactive):
    selected: bool = state(default=False)


class HistoryOwner:
    def __init__(self) -> None:
        self.invalidations = 0

    def invalidate(self) -> None:
        self.invalidations += 1


def _outside(callback, *args) -> None:
    outside = contextvars.copy_context()
    outside.run(_CURRENT.set, None)
    outside.run(callback, *args)


def test_staging_reads_its_branch_without_mutating_canonical_state() -> None:
    document = ReplicatedScope("a").open("project")
    votes = document.counter("votes")

    with transaction():
        votes.increment(2)
        assert votes.value == 2
        seen: list[int] = []
        _outside(lambda: seen.append(votes.value))
        assert seen == [0]

    assert votes.value == 2


def test_failed_mixed_cell_counter_set_action_changes_nothing() -> None:
    document = ReplicatedScope("a").open("project")
    model = LocalModel()

    with pytest.raises(RuntimeError, match="failed"), transaction():
        model.selected = True
        document.counter("votes").increment(1)
        document.set("tags").add("urgent")
        raise RuntimeError("failed")

    assert model.selected is False
    assert document.counter("votes").value == 0
    assert document.set("tags").value == frozenset()


def test_duplicate_and_reordered_delivery_converges() -> None:
    first = ReplicatedScope("a").open("project")
    second = ReplicatedScope("b").open("project")
    with transaction():
        first.counter("votes").increment(2)
        first.set("tags").add("red")
    update_a = first.export_since()
    with transaction():
        second.counter("votes").increment(3)
        second.set("tags").add("blue")
    update_b = second.export_since()

    first.import_update(update_b)
    second.import_update(update_a)
    first.import_update(update_b)
    second.import_update(update_a)

    assert first.snapshot() == second.snapshot()
    assert first.counter("votes").value == 5
    assert first.set("tags").value == frozenset({"red", "blue"})


def test_committed_local_updates_are_routed_and_attributed_after_commit() -> None:
    document = ReplicatedScope("replica-a").open("project")
    observed: list[ReplicatedUpdate] = []
    document.subscribe_updates(observed.append)

    with transaction():
        document.counter("votes").increment(1)
        assert observed == []

    update = observed[0]
    assert update.document_id == "project"
    assert update.backend_id == "squid-fake-v1"
    assert update.source_replica_id == "replica-a"
    assert update.origin_action_id is not None
    assert document.drain_updates() == (update,)


def test_transport_envelope_rejects_wrong_document_and_tampering() -> None:
    source = ReplicatedScope("a").open("source")
    target = ReplicatedScope("b").open("target")
    with transaction():
        source.counter("votes").increment(1)
    encoded = source.export_since()

    with pytest.raises(ValueError, match="targets"):
        target.import_update(encoded)

    body = json.loads(encoded)
    body["hash"] = "0" * 64
    with pytest.raises(ValueError, match="hash"):
        source.import_update(json.dumps(body).encode())


def test_three_replicas_converge_for_every_delivery_order() -> None:
    replicas = [ReplicatedScope(name).open("project") for name in ("a", "b", "c")]
    for index, replica in enumerate(replicas, start=1):
        with transaction():
            replica.counter("votes").increment(index)
            replica.set("tags").add(f"tag-{index}")
    updates = [replica.export_since() for replica in replicas]

    for replica, order in zip(replicas, ((2, 1, 0), (0, 2, 1), (1, 0, 2)), strict=True):
        for index in order:
            replica.import_update(updates[index])

    assert len({replica.snapshot() for replica in replicas}) == 1
    assert replicas[0].counter("votes").value == 6


def test_remote_import_invalidates_and_has_remote_action_outcome() -> None:
    source = ReplicatedScope("a").open("project")
    target = ReplicatedScope("b").open("project")
    with transaction():
        source.counter("votes").increment(1)
    updates = source.export_since()
    snapshots = []
    target.subscribe(snapshots.append)
    ledger = ActionLedger()
    add_action_outcome_sink(ledger)

    try:
        target.import_update(updates)
    finally:
        ledger.close()

    assert snapshots[-1].counter("votes") == 1
    assert ledger.outcomes[-1].kind == "remote"
    assert ledger.outcomes[-1].actor is not None
    assert ledger.outcomes[-1].actor.identity == "a"
    assert ledger.outcomes[-1].metadata == ()


def test_scheduler_places_remote_import_before_local_validation() -> None:
    source = ReplicatedScope("a").open("project")
    target = ReplicatedScope("b").open("project")
    model = LocalModel()
    with transaction():
        source.counter("votes").increment(1)
    update = source.export_since()
    schedule = InterleavingHarness()
    schedule.at("transaction.close_staging", lambda: target.import_update(update))

    with schedule.installed(), pytest.raises(ReactiveConflictError), transaction():
        assert target.counter("votes").value == 0
        model.selected = True

    assert target.counter("votes").value == 1
    assert model.selected is False


def test_superseded_replicated_read_rejects_local_publication() -> None:
    source = ReplicatedScope("a").open("project")
    target = ReplicatedScope("b").open("project")
    model = LocalModel()
    with transaction():
        source.counter("votes").increment(1)
    update = source.export_since()

    with pytest.raises(ReactiveConflictError), transaction():
        assert target.counter("votes").value == 0
        model.selected = True
        _outside(target.import_update, update)

    assert model.selected is False
    assert target.counter("votes").value == 1


def test_action_token_selectively_inverts_counter_and_add_after_remote_work() -> None:
    local = ReplicatedScope("a").open("project")
    remote = ReplicatedScope("b").open("project")
    commits: list[ActionCommit] = []
    with transaction():
        on_action_commit(lambda commit, aftermath: commits.append(commit))
        local.counter("votes").increment(2)
        local.set("tags").add("mine")
    remote.import_update(local.export_since())
    with transaction():
        remote.counter("votes").increment(3)
        remote.set("tags").add("theirs")
    local.import_update(remote.export_since())

    token = commits[0].participant_changes[0].token
    inverse = token.plan_inverse()
    assert isinstance(inverse, PreparedReplicatedInverse)
    with transaction():
        token.stage_inverse(inverse)

    assert local.counter("votes").value == 3
    assert local.set("tags").value == frozenset({"theirs"})


def test_action_token_reloads_against_a_recreated_document() -> None:
    source = ReplicatedScope("a").open("project")
    commits: list[ActionCommit] = []
    with transaction():
        on_action_commit(lambda commit, aftermath: commits.append(commit))
        source.counter("votes").increment(2)
    token = commits[0].participant_changes[0].token
    encoded = token.encode()
    update = source.export_since()

    restored = ReplicatedScope("restored").open("project")
    restored.import_update(update)
    reloaded = ReplicatedChangeToken.decode(restored, encoded)
    inverse = reloaded.plan_inverse()
    assert isinstance(inverse, PreparedReplicatedInverse)
    with transaction():
        reloaded.stage_inverse(inverse)

    assert restored.counter("votes").value == 0


def test_compaction_epoch_expires_retained_history_tokens_without_fallback() -> None:
    document = ReplicatedScope("a").open("project")
    commits: list[ActionCommit] = []
    with transaction():
        on_action_commit(lambda commit, aftermath: commits.append(commit))
        document.counter("votes").increment(2)
    token = commits[0].participant_changes[0].token

    document.expire_history_tokens()
    conflict = token.plan_inverse()

    assert isinstance(conflict, ConflictDetail)
    assert conflict.target_id == "replicated:project:expired"
    assert document.counter("votes").value == 2


def test_prepared_inverse_cannot_cross_a_compaction_epoch() -> None:
    document = ReplicatedScope("a").open("project")
    commits: list[ActionCommit] = []
    with transaction():
        on_action_commit(lambda commit, aftermath: commits.append(commit))
        document.counter("votes").increment(2)
    token = commits[0].participant_changes[0].token
    inverse = token.plan_inverse()
    assert isinstance(inverse, PreparedReplicatedInverse)
    document.expire_history_tokens()

    with pytest.raises(ReactiveConflictError, match="expired"), transaction():
        token.stage_inverse(inverse)

    assert document.counter("votes").value == 2


def test_durable_history_token_rejects_wrong_document_and_schema() -> None:
    source = ReplicatedScope("a").open("source")
    commits: list[ActionCommit] = []
    with transaction():
        on_action_commit(lambda commit, aftermath: commits.append(commit))
        source.counter("votes").increment(1)
    encoded = commits[0].participant_changes[0].token.encode()

    target = ReplicatedScope("b").open("target")
    with pytest.raises(ValueError, match="wrong backend or document"):
        ReplicatedChangeToken.decode(target, encoded)

    payload = json.loads(encoded)
    payload["schema"] = 2
    with pytest.raises(ValueError, match="unsupported or corrupt"):
        ReplicatedChangeToken.decode(source, json.dumps(payload).encode())


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        json.dumps({"backend": "wrong", "schema": 1, "kind": "update", "operations": []}).encode(),
        json.dumps({"backend": "squid-fake-v1", "schema": 2}).encode(),
    ],
)
def test_corrupted_wrong_backend_and_wrong_schema_updates_are_rejected(payload: bytes) -> None:
    document = ReplicatedScope("a").open("project")

    with pytest.raises(ValueError):
        document.import_update(payload)


def test_oversized_updates_are_rejected_before_decoding() -> None:
    document = ReplicatedScope("a").open("project")

    with pytest.raises(ValueError, match="maximum encoded size"):
        document.import_update(b" " * 1_500_001)


def test_duplicate_update_identity_is_ignored_before_backend_prepare() -> None:
    source = ReplicatedScope("a").open("project")
    target = ReplicatedScope("b").open("project")
    with transaction():
        source.counter("votes").increment(1)
    encoded = source.export_since()

    target.import_update(encoded)
    target.import_update(encoded)

    assert target.counter("votes").value == 1


def test_envelope_rejects_invalid_identifiers() -> None:
    update = ReplicatedUpdate(
        "project",
        "squid-fake-v1",
        "a",
        uuid.uuid7(),
        b"payload",
    )
    body = json.loads(update.encode())
    body["update_id"] = "not-a-uuid"

    with pytest.raises(ValueError, match="identifiers"):
        ReplicatedUpdate.decode(json.dumps(body).encode())


def test_scope_disposal_prevents_late_import_and_mutation() -> None:
    scope = ReplicatedScope("a")
    document = scope.open("project")
    scope.close()

    with pytest.raises(ReplicatedClosedError):
        document.import_update(b"{}")
    with pytest.raises(ReplicatedClosedError), transaction():
        document.counter("votes").increment(1)
    with pytest.raises(ReplicatedClosedError):
        document.drain_updates()


async def test_history_coordinates_cell_and_semantic_replicated_inverse() -> None:
    local = ReplicatedScope("a").open("project")
    remote = ReplicatedScope("b").open("project")
    model = LocalModel()
    history = History(HistoryOwner())
    with transaction():
        history.record("vote and select")
        model.selected = True
        local.counter("votes").increment(2)
        local.set("tags").add("mine")
    remote.import_update(local.export_since())
    with transaction():
        remote.counter("votes").increment(3)
        remote.set("tags").add("theirs")
    local.import_update(remote.export_since())

    result = await history.undo()

    assert result.applied
    assert model.selected is False
    assert local.counter("votes").value == 3
    assert local.set("tags").value == frozenset({"theirs"})
