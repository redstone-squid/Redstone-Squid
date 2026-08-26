"""Production-adapter coverage over the pinned Loro binding."""

import uuid
from collections.abc import Callable

import pytest

from squid_reactivity import ActionCommit, ConflictDetail, on_action_commit, transaction
from squid_replication import (
    LoroBackend,
    Replica,
    ReplicatedSnapshot,
    ReplicationChangeToken,
    ReplicationCorruptUpdateError,
)
from squid_ui.runtime import History, HistoryResultStatus


class HistoryOwner:
    def invalidate(self) -> None:
        pass


def _replica(name: str, peer_id: int) -> Replica:
    return Replica(name, backend=LoroBackend(peer_id=peer_id))


def _record(callback: Callable[[], None]) -> ReplicationChangeToken:
    commits: list[ActionCommit] = []
    with transaction():
        on_action_commit(lambda commit, continuation: commits.append(commit))
        callback()
    token = commits[0].participant_changes[0].token
    assert isinstance(token, ReplicationChangeToken)
    return token


def _apply_inverse(token: ReplicationChangeToken) -> None:
    inverse = token.plan_inverse()
    assert not isinstance(inverse, ConflictDetail)
    with transaction():
        token.stage_inverse(inverse)


def test_one_action_spans_every_public_container_and_undoes_exactly() -> None:
    document = _replica("a", 1).open("project")
    movable_id = uuid.uuid7()
    root_id = uuid.uuid7()

    def change() -> None:
        document.counter("votes").increment(2**60)
        document.set("tags").add("reviewed")
        document.text("title").insert(0, "piston")
        document.list("steps").insert(0, {"ticks": [1, 2]})
        document.movable_list("parts").insert(0, "observer", item_id=movable_id)
        document.map("settings").set("mode", {"fast": True})
        document.tree("outline").create(node_id=root_id, metadata={"label": "root"})

    token = _record(change)
    snapshot = document.snapshot()
    assert snapshot.counter("votes") == 2**60
    assert snapshot.sequence("steps")[0]["ticks"] == (1, 2)
    assert snapshot.movable("parts")[0].item_id == movable_id
    assert snapshot.tree("outline").node(root_id).metadata["label"] == "root"

    _apply_inverse(token)

    assert document.snapshot() == ReplicatedSnapshot(
        counters=(("votes", 0),),
        sets=(("tags", frozenset()),),
        texts=(("title", ""),),
        lists=(("steps", ()),),
        movable_lists=(("parts", ()),),
        maps=(("settings", ()),),
        trees=(("outline", document.snapshot().tree("outline")),),
    )


def test_text_and_exact_counter_inverse_preserve_later_changes() -> None:
    document = _replica("a", 1).open("project")
    token = _record(lambda: (document.text("title").insert(0, "A"), document.counter("votes").increment(2**60)))
    with transaction():
        document.text("title").insert(1, "B")
        document.counter("votes").increment(7)

    _apply_inverse(token)

    assert document.text("title").value == "B"
    assert document.counter("votes").value == 7


def test_later_remote_map_winner_conflicts_without_partial_undo() -> None:
    local = _replica("a", 1).open("project")
    remote = _replica("b", 2).open("project")
    token = _record(
        lambda: (
            local.map("settings").set("mode", "A"),
            local.text("title").insert(0, "A"),
            local.counter("votes").increment(1),
        )
    )
    remote.import_update(local.export_since())
    with transaction():
        remote.map("settings").set("mode", "B")
        remote.text("title").insert(1, "B")
        remote.counter("votes").increment(2)
    local.import_update(remote.export_since())
    before = local.snapshot()

    conflict = token.plan_inverse()

    assert isinstance(conflict, ConflictDetail)
    assert conflict.target_id == "replicated:settings:mode"
    assert local.snapshot() == before


@pytest.mark.parametrize("kind", ["list", "movable", "tree"])
def test_later_remote_replacement_or_move_conflicts(kind: str) -> None:
    local = _replica("a", 1).open("project")
    remote = _replica("b", 2).open("project")
    item_id = uuid.uuid7()
    sibling_id = uuid.uuid7()

    with transaction():
        local.list("items").insert(0, "base")
        local.movable_list("movable").insert(0, "base", item_id=item_id)
        local.movable_list("movable").insert(1, "sibling", item_id=sibling_id)
        local.tree("tree").create(node_id=item_id)
        local.tree("tree").create(node_id=sibling_id)
    remote.import_update(local.export_since())

    if kind == "list":
        token = _record(lambda: local.list("items").replace(0, "local"))
        remote.import_update(local.export_since())
        with transaction():
            remote.list("items").replace(0, "remote")
    elif kind == "movable":
        token = _record(lambda: local.movable_list("movable").move(item_id, 1))
        remote.import_update(local.export_since())
        with transaction():
            remote.movable_list("movable").move(item_id, 0)
    else:
        token = _record(lambda: local.tree("tree").move(item_id, parent_id=sibling_id))
        remote.import_update(local.export_since())
        with transaction():
            remote.tree("tree").move(item_id)
    local.import_update(remote.export_since())
    before = local.snapshot()

    assert isinstance(token.plan_inverse(), ConflictDetail)
    assert local.snapshot() == before


def test_nested_public_values_are_deeply_immutable() -> None:
    document = _replica("a", 1).open("project")
    with transaction():
        document.map("settings").set("value", {"items": [1, {"enabled": True}]})
    value = document.map("settings").value["value"]

    with pytest.raises(TypeError):
        value["items"] = ()
    with pytest.raises(TypeError):
        value["items"][1]["enabled"] = False


def test_token_survives_checkpoint_reload() -> None:
    source = _replica("a", 1).open("project")
    token = _record(lambda: source.text("title").insert(0, "A"))
    encoded = token.encode()
    restored = _replica("restored", 3).open("project")
    restored.import_update(source.checkpoint())
    reloaded = ReplicationChangeToken.decode(restored, encoded)

    _apply_inverse(reloaded)

    assert restored.text("title").value == ""


def test_compaction_preserves_leased_tokens_and_expires_released_tokens() -> None:
    document = _replica("a", 1).open("project")
    token = _record(lambda: document.text("title").insert(0, "A"))
    lease = token.retain()
    with transaction():
        document.text("title").insert(1, "B")

    document.compact_history()
    assert not isinstance(token.plan_inverse(), ConflictDetail)

    lease.release()
    document.compact_history()
    assert token.plan_inverse() == ConflictDetail("replicated:loro:expired", 0, 0)


@pytest.mark.asyncio
async def test_history_automatically_leases_and_releases_replication_tokens() -> None:
    document = _replica("a", 1).open("project")
    history = History(HistoryOwner(), limit=1)
    with transaction():
        history.record("insert")
        document.text("title").insert(0, "A")

    document.compact_history()
    result = await history.undo()

    assert result.status is HistoryResultStatus.APPLIED
    assert document.text("title").value == ""
    history.clear()
    document.compact_history()


def test_corrupt_native_update_is_translated_to_an_exception_subclass() -> None:
    document = _replica("a", 1).open("project")

    with pytest.raises(ReplicationCorruptUpdateError):
        document.engine.prepare_remote(b"not a loro update")
