"""Two-backend text spike against the same engine-level scenarios."""

from itertools import permutations

import pytest

# Both engines are exercised by the same parametrized scenarios, so the module needs both
# backends rather than either. They are optional extras and the engines only import them when
# instantiated, so without this the whole file fails at construction instead of skipping.
pytest.importorskip("loro", reason="install squid-replication[loro] to run the real backends")
pytest.importorskip("pycrdt", reason="install squid-replication[pycrdt] to run the real backends")

from squid_replication.backends.loro import (
    LoroChangeToken,
    LoroTextEngine,
    LoroTextOperation,
)
from squid_replication.backends.pycrdt import (
    PycrdtChangeToken,
    PycrdtTextEngine,
    PycrdtTextOperation,
)


@pytest.mark.parametrize(
    ("factory", "operation", "decode"),
    [
        (LoroTextEngine, LoroTextOperation, LoroChangeToken.decode),
        (PycrdtTextEngine, PycrdtTextOperation, PycrdtChangeToken.decode),
    ],
)
def test_non_latest_action_inverse_preserves_later_text(factory, operation, decode) -> None:
    engine = factory()
    action_a = engine.branch()
    action_a.apply(operation("insert", 0, "A"))
    assert engine.snapshot() == ""
    prepared_a = action_a.prepare(engine.version())
    token = engine.apply(prepared_a)
    assert token is not None

    action_b = engine.branch()
    action_b.apply(operation("insert", 1, "B"))
    engine.apply(action_b.prepare(engine.version()))
    assert engine.snapshot() == "AB"

    reloaded = decode(token.encode())
    engine.apply(engine.plan_inverse(reloaded))

    assert engine.snapshot() == "B"


@pytest.mark.parametrize("factory", [LoroTextEngine, PycrdtTextEngine])
def test_export_import_round_trip_is_idempotent(factory) -> None:
    source = factory()
    branch = source.branch()
    operation = (
        LoroTextOperation("insert", 0, "hello")
        if factory is LoroTextEngine
        else PycrdtTextOperation("insert", 0, "hello")
    )
    branch.apply(operation)
    source.apply(branch.prepare(source.version()))
    update = source.export_since()
    target = factory()

    target.apply(target.prepare_remote(update))
    target.apply(target.prepare_remote(update))

    assert target.snapshot() == "hello"


@pytest.mark.parametrize(
    ("factory", "operation"),
    [
        (LoroTextEngine, LoroTextOperation),
        (PycrdtTextEngine, PycrdtTextOperation),
    ],
)
def test_stale_same_replica_branch_is_rejected_before_id_collision(factory, operation) -> None:
    engine = factory()
    base = engine.version()
    first = engine.branch()
    second = engine.branch()
    first.apply(operation("insert", 0, "A"))
    second.apply(operation("insert", 0, "B"))
    engine.apply(first.prepare(base))

    with pytest.raises(RuntimeError, match="changed after this branch was staged"):
        second.prepare(base)

    assert engine.snapshot() == "A"


def test_loro_actions_reuse_one_peer_identity() -> None:
    engine = LoroTextEngine()
    for index in range(100):
        branch = engine.branch()
        branch.apply(LoroTextOperation("insert", index, "x"))
        engine.apply(branch.prepare(engine.version()))

    assert len(engine.doc.oplog_vv.to_spans().inner()) == 1


def test_pycrdt_actions_keep_the_state_vector_bounded() -> None:
    engine = PycrdtTextEngine()
    for index in range(100):
        branch = engine.branch()
        branch.apply(PycrdtTextOperation("insert", index, "x"))
        engine.apply(branch.prepare(engine.version()))

    assert len(engine.version()) < 20


@pytest.mark.parametrize(
    ("factory", "operation"),
    [
        (LoroTextEngine, LoroTextOperation),
        (PycrdtTextEngine, PycrdtTextOperation),
    ],
)
def test_selective_inverse_preserves_remote_text_and_converges(factory, operation) -> None:
    action_replica = factory()
    action = action_replica.branch()
    action.apply(operation("insert", 0, "A"))
    token = action_replica.apply(action.prepare(action_replica.version()))
    assert token is not None

    remote_replica = factory()
    remote_replica.apply(remote_replica.prepare_remote(action_replica.export_since()))
    remote = remote_replica.branch()
    remote.apply(operation("insert", 1, "B"))
    remote_replica.apply(remote.prepare(remote_replica.version()))
    action_replica.apply(action_replica.prepare_remote(remote_replica.export_since(action_replica.version())))

    inverse_base = action_replica.version()
    action_replica.apply(action_replica.plan_inverse(token))
    remote_replica.apply(remote_replica.prepare_remote(action_replica.export_since(inverse_base)))

    assert action_replica.snapshot() == "B"
    assert remote_replica.snapshot() == "B"


@pytest.mark.parametrize(
    ("factory", "operation"),
    [
        (LoroTextEngine, LoroTextOperation),
        (PycrdtTextEngine, PycrdtTextOperation),
    ],
)
def test_three_replica_inverse_converges_for_every_delivery_order(factory, operation) -> None:
    replicas = []
    updates = []
    token = None
    for value in "ABC":
        replica = factory()
        branch = replica.branch()
        branch.apply(operation("insert", 0, value))
        change = replica.apply(branch.prepare(replica.version()))
        replicas.append(replica)
        updates.append(replica.export_since())
        if value == "A":
            token = change
    assert token is not None

    action_replica = replicas[0]
    action_replica.apply(action_replica.prepare_remote(updates[1]))
    action_replica.apply(action_replica.prepare_remote(updates[2]))
    inverse_base = action_replica.version()
    action_replica.apply(action_replica.plan_inverse(token))
    expected = action_replica.snapshot()
    updates.append(action_replica.export_since(inverse_base))

    for delivery_order in permutations(updates):
        target = factory()
        for update in delivery_order:
            target.apply(target.prepare_remote(update))
        assert target.snapshot() == expected


@pytest.mark.parametrize(
    ("factory", "operation", "decode"),
    [
        (LoroTextEngine, LoroTextOperation, LoroChangeToken.decode),
        (PycrdtTextEngine, PycrdtTextOperation, PycrdtChangeToken.decode),
    ],
)
def test_delete_inverse_token_survives_restart(factory, operation, decode) -> None:
    engine = factory()
    seed = engine.branch()
    seed.apply(operation("insert", 0, "abc"))
    engine.apply(seed.prepare(engine.version()))
    deletion = engine.branch()
    deletion.apply(operation("delete", 1, 1))
    token = engine.apply(deletion.prepare(engine.version()))
    assert token is not None

    restarted = factory()
    restarted.apply(restarted.prepare_remote(engine.export_since()))
    restarted.apply(restarted.plan_inverse(decode(token.encode())))

    assert restarted.snapshot() == "abc"


def test_loro_frontier_token_groups_multiple_container_types() -> None:
    loro = pytest.importorskip("loro")
    document = loro.LoroDoc()
    document.get_text("text")
    document.get_map("meta")
    document.get_counter("count")
    before = document.oplog_frontiers
    branch = document.fork()
    branch.peer_id = document.peer_id
    base = document.oplog_vv
    branch.get_text("text").insert(0, "A")
    branch.get_map("meta").insert("key", "A")
    branch.get_counter("count").increment(2)
    branch.commit()
    after = branch.oplog_frontiers
    document.import_(branch.export(loro.ExportMode.Updates(base)))

    later = document.fork()
    later.peer_id = document.peer_id
    later_base = document.oplog_vv
    later.get_text("text").insert(1, "B")
    later.get_map("meta").insert("other", "B")
    later.get_counter("count").increment(3)
    later.commit()
    document.import_(later.export(loro.ExportMode.Updates(later_base)))

    inverse = document.fork()
    inverse.peer_id = document.peer_id
    inverse_base = document.oplog_vv
    inverse.apply_diff(inverse.diff(after, before))
    inverse.commit()
    document.import_(inverse.export(loro.ExportMode.Updates(inverse_base)))

    assert document.get_text("text").to_string() == "B"
    assert document.get_map("meta").get_value() == {"other": "B"}
    assert document.get_counter("count").value == 3


def test_pycrdt_stack_item_groups_multiple_container_types() -> None:
    pycrdt = pytest.importorskip("pycrdt")
    text = pycrdt.Text()
    metadata = pycrdt.Map()
    document = pycrdt.Doc({"text": text, "metadata": metadata}, skip_gc=True)
    undo = pycrdt.UndoManager(scopes=[text, metadata], capture_timeout_millis=0)
    with document.transaction():
        text.insert(0, "A")
        metadata["key"] = "A"
    assert len(undo.undo_stack) == 1
    item = undo.undo_stack[0]

    with document.transaction():
        text.insert(1, "B")
        metadata["other"] = "B"
    retained = pycrdt.StackItem(document, item.deletions, item.insertions)
    targeted = pycrdt.UndoManager(scopes=[text, metadata], capture_timeout_millis=0, undo_stack=[retained])

    assert targeted.undo()
    assert text.to_py() == "B"
    assert metadata.to_py() == {"other": "B"}


def test_loro_frontier_diff_would_clobber_a_later_map_register_write() -> None:
    loro = pytest.importorskip("loro")
    document = loro.LoroDoc()
    metadata = document.get_map("metadata")
    metadata.insert("key", "base")
    document.commit()
    before = document.oplog_frontiers
    action = document.fork()
    action.peer_id = document.peer_id
    base = document.oplog_vv
    action.get_map("metadata").insert("key", "A")
    action.commit()
    after = action.oplog_frontiers
    document.import_(action.export(loro.ExportMode.Updates(base)))

    remote = document.fork()
    remote_base = document.oplog_vv
    remote.get_map("metadata").insert("key", "B")
    remote.commit()
    document.import_(remote.export(loro.ExportMode.Updates(remote_base)))
    inverse = document.fork()
    inverse.peer_id = document.peer_id
    inverse_base = document.oplog_vv
    inverse.apply_diff(inverse.diff(after, before))
    inverse.commit()
    document.import_(inverse.export(loro.ExportMode.Updates(inverse_base)))

    assert metadata.get_value() == {"key": "base"}


def test_pycrdt_gc_discards_content_required_by_a_delete_inverse() -> None:
    pycrdt = pytest.importorskip("pycrdt")
    text = pycrdt.Text()
    document = pycrdt.Doc({"text": text}, skip_gc=False)
    text.insert(0, "A")
    undo = pycrdt.UndoManager(scopes=[text], capture_timeout_millis=0)
    del text[0:1]
    item = undo.undo_stack[0]

    restarted_text = pycrdt.Text()
    restarted = pycrdt.Doc({"text": restarted_text}, skip_gc=False)
    restarted.apply_update(document.get_update())
    retained = pycrdt.StackItem(restarted, item.deletions, item.insertions)
    targeted = pycrdt.UndoManager(scopes=[restarted_text], capture_timeout_millis=0, undo_stack=[retained])

    assert targeted.undo()
    assert restarted_text.to_py() == ""


def test_loro_shallow_snapshot_expires_an_older_frontier_token() -> None:
    loro = pytest.importorskip("loro")
    engine = LoroTextEngine()
    action = engine.branch()
    action.apply(LoroTextOperation("insert", 0, "A"))
    token = engine.apply(action.prepare(engine.version()))
    shallow = engine.doc.export(loro.ExportMode.ShallowSnapshot(loro.Frontiers.decode(token.after)))
    compacted = LoroTextEngine()
    compacted.doc.import_(shallow)

    with pytest.raises(BaseException, match="before the shallow history") as error:
        compacted.plan_inverse(token)

    assert type(error.value) is BaseException
