"""Property models spanning the production Loro document surface."""

import uuid

from hypothesis import given, settings
from hypothesis import strategies as st

from squid_reactivity import ActionCommit, ConflictDetail, on_action_commit, transaction
from squid_replication import LoroBackend, PreparedReplicationInverse, Replica, ReplicatedDocument


def _documents() -> list[ReplicatedDocument]:
    return [
        Replica(name, backend=LoroBackend(peer_id=peer)).open("project")
        for name, peer in (("a", 1), ("b", 2), ("c", 3))
    ]


@settings(max_examples=15, deadline=None)
@given(
    amounts=st.tuples(*(st.integers(-(10**30), 10**30) for _ in range(3))),
    first_order=st.permutations((0, 1, 2)),
    second_order=st.permutations((0, 1, 2)),
    third_order=st.permutations((0, 1, 2)),
)
def test_rich_documents_converge_for_delivery_permutations(
    amounts: tuple[int, int, int],
    first_order: list[int],
    second_order: list[int],
    third_order: list[int],
) -> None:
    documents = _documents()
    for index, (document, amount) in enumerate(zip(documents, amounts, strict=True)):
        with transaction():
            document.counter("votes").increment(amount)
            document.set("tags").add(f"tag-{index}")
            document.text("title").insert(0, chr(ord("A") + index))
            document.list("steps").insert(0, {"replica": index})
            document.movable_list("parts").insert(
                0,
                f"part-{index}",
                item_id=uuid.uuid5(uuid.NAMESPACE_URL, f"part-{index}"),
            )
            document.map("reviewers").set(f"reviewer-{index}", {"round": index})
            document.tree("outline").create(
                node_id=uuid.uuid5(uuid.NAMESPACE_URL, f"node-{index}"),
                metadata={"replica": index},
            )
    updates = [document.export_since() for document in documents]

    for document, order in zip(documents, (first_order, second_order, third_order), strict=True):
        for index in order:
            document.import_update(updates[index])

    assert documents[0].snapshot() == documents[1].snapshot() == documents[2].snapshot()
    assert documents[0].counter("votes").value == sum(amounts)


@settings(max_examples=15, deadline=None)
@given(local_amount=st.integers(-(10**30), 10**30), remote_amount=st.integers(-(10**30), 10**30))
def test_semantic_inverse_preserves_later_remote_sequence_and_commutative_work(
    local_amount: int,
    remote_amount: int,
) -> None:
    local, remote, _ = _documents()
    commits: list[ActionCommit] = []
    with transaction():
        on_action_commit(lambda commit, continuation: commits.append(commit))
        local.counter("votes").increment(local_amount)
        local.set("tags").add("mine")
        local.text("title").insert(0, "A")
    remote.import_update(local.export_since())
    with transaction():
        remote.counter("votes").increment(remote_amount)
        remote.set("tags").add("theirs")
        remote.text("title").insert(1, "B")
    local.import_update(remote.export_since())
    token = commits[0].participant_changes[0].token

    inverse = token.plan_inverse()
    assert isinstance(inverse, PreparedReplicationInverse)
    with transaction():
        token.stage_inverse(inverse)

    assert local.counter("votes").value == remote_amount
    assert local.set("tags").value == frozenset({"theirs"})
    assert local.text("title").value == "B"


@settings(max_examples=10, deadline=None)
@given(local_value=st.text(max_size=40), remote_value=st.text(max_size=40))
def test_causally_later_register_write_always_conflicts_the_earlier_action(
    local_value: str,
    remote_value: str,
) -> None:
    local, remote, _ = _documents()
    commits: list[ActionCommit] = []
    with transaction():
        on_action_commit(lambda commit, continuation: commits.append(commit))
        local.map("settings").set("mode", local_value)
    remote.import_update(local.export_since())
    with transaction():
        remote.map("settings").set("mode", remote_value)
    local.import_update(remote.export_since())
    before = local.snapshot()

    inverse = commits[0].participant_changes[0].token.plan_inverse()

    assert isinstance(inverse, ConflictDetail)
    assert local.snapshot() == before
