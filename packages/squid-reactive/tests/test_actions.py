"""Action outcomes, version lineage, and aftermath boundaries."""

import contextvars
import gc
import logging
import weakref

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from squid_reactive import (
    ActionCommit,
    ActionContext,
    ActionKind,
    ActionLedger,
    ActionOutcomeCodec,
    ActionOutcomeSnapshot,
    ActionRollback,
    ActionValidationError,
    ActorRef,
    AftermathFailureSnapshot,
    DurableOutcomePolicy,
    DurableOutcomeSink,
    LocalTopicBus,
    Reactive,
    ReactiveConflictError,
    ReactiveWriteError,
    RedactionPolicy,
    Shared,
    add_action_outcome_sink,
    apply_conditional_patches,
    join_action,
    on_action_commit,
    on_action_rollback,
    readonly_transaction,
    relaxed_read,
    state,
    transaction,
)
from squid_reactive.core import _CURRENT


class Preferences(Shared[int]):
    theme: str = state("system")
    locale: str = state("en")


class Counter(Shared[int]):
    value: int = state(0)


class LocalCounter(Reactive):
    value: int = state(0)


def _outside_write(preferences: Preferences, name: str, value: str) -> None:
    outside = contextvars.copy_context()
    outside.run(_CURRENT.set, None)
    outside.run(setattr, preferences, name, value)


def test_each_action_emits_one_identified_terminal_outcome() -> None:
    ledger = ActionLedger()
    add_action_outcome_sink(ledger)
    preferences = Preferences(LocalTopicBus(), 1)
    context = ActionContext.create("change theme")
    try:
        with transaction(action_context=context):
            preferences.theme = "dark"
    finally:
        ledger.close()

    assert len(ledger.outcomes) == 1
    outcome = ledger.outcomes[0]
    assert outcome.action_id == str(context.action_id)
    assert outcome.terminal == "committed"
    assert outcome.changes.cells == 1


def test_outcome_registration_does_not_retain_an_abandoned_sink() -> None:
    ledger = ActionLedger()
    reference = weakref.ref(ledger)
    add_action_outcome_sink(ledger)

    del ledger
    gc.collect()

    assert reference() is None


def test_read_only_actions_emit_terminal_outcomes() -> None:
    ledger = ActionLedger()
    add_action_outcome_sink(ledger)
    try:
        with readonly_transaction():
            pass
        with pytest.raises(RuntimeError, match="read failed"), readonly_transaction():
            raise RuntimeError("read failed")
    finally:
        ledger.close()

    assert [outcome.terminal for outcome in ledger.outcomes] == ["committed", "rolled_back"]


def test_handler_failure_emits_rollback_after_staged_state_dies() -> None:
    preferences = Preferences(LocalTopicBus(), 1)
    seen: list[tuple[str, str]] = []

    with pytest.raises(RuntimeError, match="failed"), transaction():
        preferences.theme = "dark"

        def observe(rollback, aftermath) -> None:
            seen.append((rollback.reason.value, preferences.theme))

        on_action_rollback(observe)
        raise RuntimeError("failed")

    assert seen == [("handler_exception", "system")]


def test_full_strong_read_set_rejects_write_skew() -> None:
    preferences = Preferences(LocalTopicBus(), 1)

    with pytest.raises(ReactiveConflictError), transaction():
        assert preferences.theme == "system"
        preferences.locale = "fr"
        _outside_write(preferences, "theme", "dark")

    assert preferences.locale == "en"
    assert preferences.theme == "dark"


def test_a_b_a_lineage_change_conflicts_even_when_value_matches() -> None:
    preferences = Preferences(LocalTopicBus(), 1)

    with pytest.raises(ReactiveConflictError), transaction():
        seen = preferences.theme
        preferences.locale = seen
        _outside_write(preferences, "theme", "dark")
        _outside_write(preferences, "theme", "system")


@settings(max_examples=30)
@given(st.integers(), st.one_of(st.none(), st.integers()))
def test_conditional_inverse_model_preserves_or_conflicts_without_clobbering(
    committed_value: int, later_value: int | None
) -> None:
    assume(later_value is None or later_value != committed_value)
    counter = Counter(LocalTopicBus(), 1)
    commits: list[ActionCommit] = []
    with transaction():
        on_action_commit(lambda commit, aftermath: commits.append(commit))
        counter.value = committed_value
    inverse = commits[0].patches.inverse()

    if later_value is not None:
        counter.value = later_value
        with pytest.raises(ReactiveConflictError), transaction():
            apply_conditional_patches(inverse)
        assert counter.value == later_value
    else:
        with transaction():
            apply_conditional_patches(inverse)
        assert counter.value == 0


def test_retained_patch_uses_weak_slot_authority() -> None:
    counter = LocalCounter()
    owner = weakref.ref(counter)
    commits: list[ActionCommit] = []
    with transaction():
        on_action_commit(lambda commit, aftermath: commits.append(commit))
        counter.value = 1
    inverse = commits[0].patches.inverse()

    del counter
    gc.collect()

    assert owner() is None
    with pytest.raises(ReactiveConflictError, match="no longer exists"), transaction():
        apply_conditional_patches(inverse)


def test_relaxed_read_is_distinct_from_reactive_untracking() -> None:
    preferences = Preferences(LocalTopicBus(), 1)

    with transaction():
        with relaxed_read():
            assert preferences.theme == "system"
        preferences.locale = "fr"
        _outside_write(preferences, "theme", "dark")

    assert preferences.locale == "fr"


def test_hook_failure_is_isolated_from_commit(caplog: pytest.LogCaptureFixture) -> None:
    preferences = Preferences(LocalTopicBus(), 1)

    def fail(commit, aftermath) -> None:
        raise RuntimeError("hook failed")

    with caplog.at_level(logging.ERROR), transaction():
        on_action_commit(fail)
        preferences.theme = "dark"

    assert preferences.theme == "dark"
    assert "aftermath hook failed" in caplog.text


def test_hook_failure_is_a_bounded_causal_diagnostic_node() -> None:
    ledger = ActionLedger()
    add_action_outcome_sink(ledger)

    def fail(commit, aftermath) -> None:
        raise RuntimeError("secret hook detail")

    try:
        with transaction():
            on_action_commit(fail)
    finally:
        ledger.close()

    assert len(ledger.outcomes) == 1
    failure = ledger.events[-1]
    assert isinstance(failure, AftermathFailureSnapshot)
    assert failure.cause.identity == ledger.outcomes[0].action_id
    assert failure.exception.type_name == "RuntimeError"
    assert failure.exception.message == "[redacted]"


def test_apply_contract_failure_preserves_one_integrity_commit() -> None:
    preferences = Preferences(LocalTopicBus(), 1)
    ledger = ActionLedger()
    add_action_outcome_sink(ledger)

    class BrokenParticipant:
        def prepare(self, view) -> None:
            return None

        def describe_change(self, prepared: None) -> None:
            return None

        def apply(self, prepared: None) -> None:
            raise RuntimeError("adapter broke its infallible apply contract")

        def abort(self, prepared: None, cause: BaseException) -> None:
            pass

        def finalize(self, prepared: None) -> None:
            pass

    try:
        with pytest.raises(RuntimeError, match="infallible apply contract"), transaction():
            preferences.theme = "dark"
            join_action(object(), BrokenParticipant)
    finally:
        ledger.close()

    assert preferences.theme == "dark"
    assert len(ledger.outcomes) == 1
    assert ledger.outcomes[0].terminal == "committed"
    assert ledger.outcomes[0].tags == frozenset({"framework_integrity_failure"})


def test_aftermath_direct_mutation_is_rejected() -> None:
    preferences = Preferences(LocalTopicBus(), 1)
    errors: list[Exception] = []

    def mutate(commit, aftermath) -> None:
        try:
            preferences.theme = "forbidden"
        except Exception as error:
            errors.append(error)

    with transaction():
        on_action_commit(mutate)
        preferences.theme = "dark"

    assert len(errors) == 1
    assert isinstance(errors[0], ReactiveWriteError)
    assert preferences.theme == "dark"


def test_aftermath_recovery_is_a_fresh_causally_linked_action() -> None:
    preferences = Preferences(LocalTopicBus(), 1)
    ledger = ActionLedger()
    add_action_outcome_sink(ledger)

    def recover(rollback, aftermath) -> None:
        with aftermath.start_action("present error"):
            preferences.theme = "recovered"

    try:
        with pytest.raises(RuntimeError), transaction():
            on_action_rollback(recover)
            raise RuntimeError("failed")
    finally:
        ledger.close()

    assert preferences.theme == "recovered"
    assert [outcome.terminal for outcome in ledger.outcomes] == ["rolled_back", "committed"]
    assert ledger.outcomes[1].cause is not None
    assert ledger.outcomes[1].cause.identity == ledger.outcomes[0].action_id
    assert ledger.outcomes[1].root_action_id == ledger.outcomes[0].root_action_id


def test_undo_kind_is_explicit_identity_not_hook_timing() -> None:
    context = ActionContext.create("undo", kind=ActionKind.UNDO)
    ledger = ActionLedger()
    add_action_outcome_sink(ledger)
    try:
        with transaction(action_context=context):
            pass
    finally:
        ledger.close()
    assert ledger.outcomes[0].kind == "undo"


def test_portable_schema_one_round_trips_and_rejects_unknown_versions() -> None:
    ledger = ActionLedger()
    add_action_outcome_sink(ledger)
    try:
        with transaction():
            pass
    finally:
        ledger.close()
    codec = ActionOutcomeCodec()

    assert codec.decode(codec.encode(ledger.outcomes[0])) == ledger.outcomes[0]
    with pytest.raises(ValueError, match="unsupported"):
        codec.decode(b'{"schema":2}')
    with pytest.raises(ValueError, match="corrupt"):
        codec.decode(b"not-json")
    with pytest.raises(ValueError, match="maximum encoded size"):
        codec.decode(b" " * 1_048_577)


def test_application_validation_has_a_distinct_rollback_reason() -> None:
    ledger = ActionLedger()
    add_action_outcome_sink(ledger)
    try:
        with pytest.raises(ActionValidationError), transaction():
            raise ActionValidationError("name is required")
    finally:
        ledger.close()

    assert ledger.outcomes[0].reason == "validation_failed"


def test_portable_snapshot_redacts_by_default_and_can_opt_into_safe_metadata() -> None:
    context = ActionContext.create(
        "sensitive",
        actor=ActorRef("user", "42"),
        metadata={"tenant": "safe", "token": "secret"},
    )
    ledger = ActionLedger()
    rollbacks: list[ActionRollback] = []
    add_action_outcome_sink(ledger)
    try:
        with pytest.raises(RuntimeError), transaction(action_context=context):
            on_action_rollback(lambda rollback, aftermath: rollbacks.append(rollback))
            raise RuntimeError("private failure message")
    finally:
        ledger.close()

    snapshot = ledger.outcomes[0]
    assert snapshot.actor == ActorRef("user", "42")
    assert snapshot.metadata == ()
    assert snapshot.exception is not None and snapshot.exception.message == "[redacted]"
    opted_in = ActionOutcomeSnapshot.from_outcome(
        rollbacks[0], RedactionPolicy(include_metadata=True, include_exception_messages=True)
    )
    assert dict(opted_in.metadata) == {"tenant": "safe", "token": "secret"}
    assert opted_in.exception is not None and opted_in.exception.message == "private failure message"


def test_each_outcome_sink_receives_its_declared_redaction_projection() -> None:
    default = ActionLedger()
    privileged = ActionLedger()
    add_action_outcome_sink(default)
    add_action_outcome_sink(
        privileged,
        policy=RedactionPolicy(include_actor=False, include_metadata=True, include_exception_messages=True),
    )
    context = ActionContext.create("sensitive", actor=ActorRef("user", "42"), metadata={"tenant": "safe"})
    try:
        with pytest.raises(RuntimeError), transaction(action_context=context):
            raise RuntimeError("failure detail")
    finally:
        default.close()
        privileged.close()

    assert default.outcomes[0].actor == ActorRef("user", "42")
    assert default.outcomes[0].metadata == ()
    assert default.outcomes[0].exception is not None
    assert default.outcomes[0].exception.message == "[redacted]"
    assert privileged.outcomes[0].actor is None
    assert dict(privileged.outcomes[0].metadata) == {"tenant": "safe"}
    assert privileged.outcomes[0].exception is not None
    assert privileged.outcomes[0].exception.message == "failure detail"


def test_durable_sink_encodes_outcomes_and_declares_host_policy() -> None:
    encoded: list[bytes] = []
    policy = DurableOutcomePolicy(
        redaction=RedactionPolicy(include_actor=False),
        actor_privacy="omitted",
        encryption="AES-256 at rest",
        retention="30 days",
    )
    sink = DurableOutcomeSink(encoded.append, policy=policy)
    try:
        with transaction(action_context=ActionContext.create(actor=ActorRef("user", "42"))):
            pass
    finally:
        sink.close()

    snapshot = sink.codec.decode(encoded[0])
    assert snapshot.actor is None
    assert sink.policy.retention == "30 days"
    assert sink.policy.value_serialization == "summaries-only"
