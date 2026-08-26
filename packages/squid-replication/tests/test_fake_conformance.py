"""Replicated SPI conformance against the deterministic fake backend."""

import contextvars
import gc
import json
import uuid
import weakref

import pytest

from squid_ui.runtime import History
from squid_reactivity import (
    ActionCommit,
    ActionLedger,
    ConflictDetail,
    StateOwner,
    ReactiveConflictError,
    add_action_result_sink,
    on_action_commit,
    state,
    strong_read,
    transaction,
)
from squid_reactivity.core import _CURRENT
from squid_reactivity.testing import InterleavingHarness
from squid_replication import (
    FakeEngine,
    PreparedReplicatedInverse,
    ReplicatedChangeToken,
    ReplicatedClosedError,
    ReplicatedResyncRequiredError,
    ReplicatedScope,
    ReplicatedUpdate,
)
from squid_replication.fake import FakeOperation, PreparedFakeUpdate


class LocalModel(StateOwner):
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


def test_remote_import_invalidates_and_has_a_remote_action_result() -> None:
    source = ReplicatedScope("a").open("project")
    target = ReplicatedScope("b").open("project")
    with transaction():
        source.counter("votes").increment(1)
    updates = source.export_since()
    snapshots = []
    target.subscribe(snapshots.append)
    ledger = ActionLedger()
    add_action_result_sink(ledger)

    try:
        target.import_update(updates)
    finally:
        ledger.close()

    assert snapshots[-1].counter("votes") == 1
    assert ledger.results[-1].kind == "remote"
    assert ledger.results[-1].actor is not None
    assert ledger.results[-1].actor.identity == "a"
    assert ledger.results[-1].metadata == ()


def test_scheduler_places_remote_import_before_local_validation() -> None:
    source = ReplicatedScope("a").open("project")
    target = ReplicatedScope("b").open("project")
    model = LocalModel()
    with transaction():
        source.counter("votes").increment(1)
    update = source.export_since()
    schedule = InterleavingHarness()
    schedule.at("transaction.close_staging", lambda: target.import_update(update))

    with schedule.installed(), pytest.raises(ReactiveConflictError), transaction(), strong_read():
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

    with pytest.raises(ReactiveConflictError), transaction(), strong_read():
        assert target.counter("votes").value == 0
        model.selected = True
        _outside(target.import_update, update)

    assert model.selected is False
    assert target.counter("votes").value == 1


def test_a_read_only_replicated_read_does_not_block_an_unrelated_local_write() -> None:
    source = ReplicatedScope("a").open("project")
    target = ReplicatedScope("b").open("project")
    model = LocalModel()
    with transaction():
        source.counter("votes").increment(1)
    update = source.export_since()

    # The same shape as the strong_read() case above, minus the opt-in: a read of a cell this
    # action never writes carries no precondition, so the import does not invalidate the write.
    with transaction():
        assert target.counter("votes").value == 0
        model.selected = True
        _outside(target.import_update, update)

    assert model.selected is True
    assert target.counter("votes").value == 1


def test_action_token_selectively_inverts_counter_and_add_after_remote_work() -> None:
    local = ReplicatedScope("a").open("project")
    remote = ReplicatedScope("b").open("project")
    commits: list[ActionCommit] = []
    with transaction():
        on_action_commit(lambda commit, continuation: commits.append(commit))
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


async def test_undoing_one_removal_leaves_a_concurrent_removal_of_the_same_tag_standing() -> None:
    local = ReplicatedScope("a").open("project")
    remote = ReplicatedScope("b").open("project")
    with transaction():
        local.set("tags").add("shared")
    remote.import_update(local.export_since())
    history = History(HistoryOwner())
    with transaction():
        history.record("drop the tag")
        local.set("tags").discard("shared")
    commits: list[ActionCommit] = []
    with transaction():
        on_action_commit(lambda commit, continuation: commits.append(commit))
        remote.set("tags").discard("shared")
    local.import_update(remote.export_since())

    result = await history.undo()

    assert result.applied
    assert local.set("tags").value == frozenset()

    token = commits[0].participant_changes[0].token
    inverse = token.plan_inverse()
    assert isinstance(inverse, PreparedReplicatedInverse)
    with transaction():
        token.stage_inverse(inverse)
    local.import_update(remote.export_since())

    assert local.set("tags").value == frozenset({"shared"})


async def test_undoing_a_discard_of_an_absent_value_adds_nothing() -> None:
    document = ReplicatedScope("a").open("project")
    history = History(HistoryOwner())
    with transaction():
        history.record("drop a value that was never there")
        document.set("tags").discard("absent")

    result = await history.undo()

    assert result.applied
    assert document.set("tags").value == frozenset()


async def test_undo_and_redo_of_a_discard_round_trip() -> None:
    document = ReplicatedScope("a").open("project")
    tags = document.set("tags")
    with transaction():
        tags.add("mine")
    history = History(HistoryOwner())
    with transaction():
        history.record("drop the tag")
        tags.discard("mine")
    assert tags.value == frozenset()

    undone = await history.undo()
    assert undone.applied
    assert tags.value == frozenset({"mine"})

    redone = await history.redo()
    assert redone.applied
    assert tags.value == frozenset()

    undone_again = await history.undo()
    assert undone_again.applied
    assert tags.value == frozenset({"mine"})


def test_action_token_reloads_against_a_recreated_document() -> None:
    source = ReplicatedScope("a").open("project")
    commits: list[ActionCommit] = []
    with transaction():
        on_action_commit(lambda commit, continuation: commits.append(commit))
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


def test_a_restarted_replica_restores_its_clock_from_imported_history() -> None:
    original = ReplicatedScope("a").open("project")
    with transaction():
        original.counter("votes").increment(2)
        original.set("tags").add("mine")
    history = original.export_since()

    restarted = ReplicatedScope("a").open("project")
    restarted.import_update(history)
    with transaction():
        restarted.counter("votes").increment(5)

    assert restarted.counter("votes").value == 7
    peer = ReplicatedScope("b").open("project")
    peer.import_update(restarted.export_since())
    assert peer.counter("votes").value == 7


def test_a_restarted_replica_that_mutates_before_importing_refuses_the_collision() -> None:
    original = ReplicatedScope("a").open("project")
    with transaction():
        original.counter("votes").increment(2)
    history = original.export_since()

    restarted = ReplicatedScope("a").open("project")
    with transaction():
        restarted.counter("votes").increment(5)

    with pytest.raises(ValueError, match="was reused"):
        restarted.import_update(history)


def test_applying_a_reused_identity_with_different_content_records_nothing() -> None:
    engine = FakeEngine("a")
    recorded = engine.operation("increment", "votes", 2)
    engine.apply(PreparedFakeUpdate(None, (recorded,)))
    forged = FakeOperation(recorded.identity, "increment", "votes", 5)
    fresh = engine.operation("increment", "votes", 1)

    with pytest.raises(ValueError, match="was reused"):
        engine.apply(PreparedFakeUpdate(None, (fresh, forged)))

    assert engine.snapshot().counter("votes") == 2


def test_compaction_epoch_expires_retained_history_tokens_without_fallback() -> None:
    document = ReplicatedScope("a").open("project")
    commits: list[ActionCommit] = []
    with transaction():
        on_action_commit(lambda commit, continuation: commits.append(commit))
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
        on_action_commit(lambda commit, continuation: commits.append(commit))
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
        on_action_commit(lambda commit, continuation: commits.append(commit))
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
    # Both subscriptions, on the same terms: a closed document delivers nothing, so accepting
    # a listener for one would only be a quieter way of never calling it.
    with pytest.raises(ReplicatedClosedError):
        document.subscribe(lambda snapshot: None)
    with pytest.raises(ReplicatedClosedError):
        document.subscribe_updates(lambda update: None)


def test_scope_disposal_clears_documents_subscriptions_pending_exports_and_deduplication() -> None:
    source = ReplicatedScope("source").open("project")
    scope = ReplicatedScope("target")
    document = scope.open("project")

    class Listener:
        def receive(self, value) -> None:
            pass

    listener = Listener()
    listener_ref = weakref.ref(listener)
    document.subscribe(listener.receive)
    document.subscribe_updates(lambda update: None)
    with transaction():
        source.counter("votes").increment(1)
    document.import_update(source.export_since())
    with transaction():
        document.counter("votes").increment(1)
    assert document.subscription_count == 2
    assert document.pending_update_count == 1
    assert document.deduplication_count == 1
    assert scope.active_documents == ("project",)

    del listener
    scope.close()
    gc.collect()

    assert listener_ref() is None
    assert document.subscription_count == 0
    assert document.pending_update_count == 0
    assert document.deduplication_count == 0
    assert scope.active_documents == ()


def test_remote_deduplication_retention_is_bounded() -> None:
    document = ReplicatedScope("a").open("project")

    for index in range(10_005):
        document._remember_update(str(index))

    assert document.deduplication_count == 10_000
    assert "0" not in document._seen_update_ids


def test_pending_outbound_retention_is_bounded() -> None:
    document = ReplicatedScope("source").open("project")

    for _ in range(1_005):
        with transaction():
            document.counter("votes").increment(1)

    assert document.pending_update_count == 1_000
    assert document.dropped_update_count == 5


def test_outbound_overflow_requires_a_resync_before_draining_again() -> None:
    document = ReplicatedScope("source").open("project")
    peer = ReplicatedScope("peer").open("project")

    for _ in range(1_005):
        with transaction():
            document.counter("votes").increment(1)

    assert document.resync_required
    with pytest.raises(ReplicatedResyncRequiredError, match="dropped 5 outbound updates"):
        document.drain_updates()

    peer.import_update(document.export_since())
    document.acknowledge_resync()

    assert peer.counter("votes").value == 1_005
    assert document.drain_updates() == ()
    assert document.dropped_update_count == 0
    assert document.resync_required is False
    with transaction():
        document.counter("votes").increment(1)
    assert len(document.drain_updates()) == 1


def test_a_document_within_its_outbound_bound_never_demands_a_resync() -> None:
    document = ReplicatedScope("source").open("project")

    for _ in range(1_000):
        with transaction():
            document.counter("votes").increment(1)

    assert document.resync_required is False
    assert document.dropped_update_count == 0
    assert len(document.drain_updates()) == 1_000


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


async def test_replicated_redo_and_second_undo_follow_fresh_semantic_tokens() -> None:
    local = ReplicatedScope("a").open("project")
    remote = ReplicatedScope("b").open("project")
    history = History(HistoryOwner())
    with transaction():
        history.record("vote and tag")
        local.counter("votes").increment(2)
        local.set("tags").add("mine")
    remote.import_update(local.export_since())
    with transaction():
        remote.counter("votes").increment(3)
        remote.set("tags").add("theirs")
    local.import_update(remote.export_since())

    undone = await history.undo()
    redone = await history.redo()
    undone_again = await history.undo()

    assert undone.applied and redone.applied and undone_again.applied
    assert local.counter("votes").value == 3
    assert local.set("tags").value == frozenset({"theirs"})
