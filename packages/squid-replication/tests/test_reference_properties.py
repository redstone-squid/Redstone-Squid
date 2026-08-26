"""Small property models for the deterministic replicated adapter."""

from hypothesis import given, settings
from hypothesis import strategies as st

from squid_reactivity import ActionCommit, on_action_commit, transaction
from squid_replication import (
    PreparedReplicationInverse,
    ReferenceBackend,
    ReplicatedDocument,
    ReplicationChangeToken,
)
from squid_replication import (
    Replica as _Replica,
)

_REFERENCE_BACKEND = ReferenceBackend()


def Replica(replica_id: str) -> _Replica:
    return _Replica(replica_id, backend=_REFERENCE_BACKEND)


@settings(max_examples=30, deadline=None)
@given(
    amounts=st.tuples(*(st.integers(-20, 20) for _ in range(3))),
    first_order=st.permutations((0, 1, 2)),
    second_order=st.permutations((0, 1, 2)),
    third_order=st.permutations((0, 1, 2)),
)
def test_all_delivery_orders_converge(
    amounts: tuple[int, int, int],
    first_order: list[int],
    second_order: list[int],
    third_order: list[int],
) -> None:
    replicas = [Replica(name).open("project") for name in ("a", "b", "c")]
    for index, (replica, amount) in enumerate(zip(replicas, amounts, strict=True)):
        with transaction():
            replica.counter("votes").increment(amount)
            replica.set("tags").add(f"tag-{index}")
    updates = [replica.export_since() for replica in replicas]

    for replica, order in zip(replicas, (first_order, second_order, third_order), strict=True):
        for index in order:
            replica.import_update(updates[index])

    assert len({replica.snapshot() for replica in replicas}) == 1
    assert replicas[0].counter("votes").value == sum(amounts)


@settings(max_examples=30, deadline=None)
@given(local_amount=st.integers(-20, 20), remote_amount=st.integers(-20, 20))
def test_semantic_inverse_preserves_later_remote_work(local_amount: int, remote_amount: int) -> None:
    local = Replica("local").open("project")
    remote = Replica("remote").open("project")
    commits: list[ActionCommit] = []
    with transaction():
        on_action_commit(lambda commit, continuation: commits.append(commit))
        local.counter("votes").increment(local_amount)
        local.set("tags").add("mine")
    remote.import_update(local.export_since())
    with transaction():
        remote.counter("votes").increment(remote_amount)
        remote.set("tags").add("theirs")
    local.import_update(remote.export_since())

    token = commits[0].participant_changes[0].token
    inverse = token.plan_inverse()
    assert isinstance(inverse, PreparedReplicationInverse)
    with transaction():
        token.stage_inverse(inverse)

    assert local.counter("votes").value == remote_amount
    assert local.set("tags").value == frozenset({"theirs"})


def _sync(replicas: list[ReplicatedDocument], order: list[int]) -> None:
    updates = [replica.export_since() for replica in replicas]
    for replica in replicas:
        for index in order:
            replica.import_update(updates[index])


def _discard(document: ReplicatedDocument, value: str) -> ReplicationChangeToken:
    commits: list[ActionCommit] = []
    with transaction():
        on_action_commit(lambda commit, continuation: commits.append(commit))
        document.set("tags").discard(value)
    return commits[0].participant_changes[0].token


@settings(max_examples=30, deadline=None)
@given(
    removers=st.lists(st.sampled_from((0, 1, 2)), min_size=1, max_size=3, unique=True),
    undone=st.lists(st.sampled_from((0, 1, 2)), max_size=3, unique=True),
    order=st.permutations((0, 1, 2)),
)
def test_a_value_returns_only_when_every_concurrent_removal_is_undone(
    removers: list[int], undone: list[int], order: list[int]
) -> None:
    replicas = [Replica(name).open("project") for name in ("a", "b", "c")]
    with transaction():
        replicas[0].set("tags").add("shared")
    _sync(replicas, order)

    tokens = {index: _discard(replicas[index], "shared") for index in removers}
    _sync(replicas, order)
    assert all(replica.set("tags").value == frozenset() for replica in replicas)

    for index in (index for index in undone if index in tokens):
        inverse = tokens[index].plan_inverse()
        assert isinstance(inverse, PreparedReplicationInverse)
        with transaction():
            tokens[index].stage_inverse(inverse)
    _sync(replicas, order)

    standing = {index for index in tokens if index not in undone}
    assert len({replica.snapshot() for replica in replicas}) == 1
    assert replicas[0].set("tags").value == (frozenset() if standing else frozenset({"shared"}))
