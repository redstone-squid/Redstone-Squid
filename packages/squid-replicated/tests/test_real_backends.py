"""Two-backend text spike against the same engine-level scenarios."""

import pytest
from squid_replicated.backends.loro import LoroChangeToken, LoroTextEngine, LoroTextOperation
from squid_replicated.backends.pycrdt import PycrdtChangeToken, PycrdtTextEngine, PycrdtTextOperation


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
