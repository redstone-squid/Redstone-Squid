"""Small property models for the deterministic replicated adapter."""

from hypothesis import given, settings
from hypothesis import strategies as st
from squid_replicated import PreparedReplicatedInverse, ReplicatedScope

from squid_reactive import ActionCommit, on_action_commit, transaction


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
    replicas = [ReplicatedScope(name).open("project") for name in ("a", "b", "c")]
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
    local = ReplicatedScope("local").open("project")
    remote = ReplicatedScope("remote").open("project")
    commits: list[ActionCommit] = []
    with transaction():
        on_action_commit(lambda commit, aftermath: commits.append(commit))
        local.counter("votes").increment(local_amount)
        local.set("tags").add("mine")
    remote.import_update(local.export_since())
    with transaction():
        remote.counter("votes").increment(remote_amount)
        remote.set("tags").add("theirs")
    local.import_update(remote.export_since())

    token = commits[0].participant_changes[0].token
    inverse = token.plan_inverse()
    assert isinstance(inverse, PreparedReplicatedInverse)
    with transaction():
        token.stage_inverse(inverse)

    assert local.counter("votes").value == remote_amount
    assert local.set("tags").value == frozenset({"theirs"})
