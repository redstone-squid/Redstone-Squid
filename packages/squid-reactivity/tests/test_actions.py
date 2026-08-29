"""Action outcomes, version lineage, and continuation boundaries."""

import contextvars
import gc
import logging
import weakref

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from squid_reactivity import (
    ActionCommit,
    ActionContext,
    ActionLedger,
    ActionPurpose,
    ActionResultCodec,
    ActionResultSnapshot,
    ActionRollback,
    ActionValidationError,
    ActorRef,
    ContinuationFailureSnapshot,
    DurableResultPolicy,
    DurableResultSink,
    LocalTopicBus,
    ReactiveConflictError,
    ReactiveWriteError,
    RedactionPolicy,
    SharedState,
    StateOwner,
    add_action_result_sink,
    apply_conditional_patches,
    computed,
    enlist,
    on_action_commit,
    on_action_rollback,
    readonly_transaction,
    relaxed_read,
    remove_action_result_sink,
    state,
    strong_read,
    transaction,
)
from squid_reactivity.core import _CURRENT


class Preferences(SharedState[int]):
    theme: str = state("system")
    locale: str = state("en")


class Counter(SharedState[int]):
    value: int = state(0)


class LocalCounter(StateOwner):
    value: int = state(0)


def _outside_write(preferences: Preferences, name: str, value: str) -> None:
    outside = contextvars.copy_context()
    outside.run(_CURRENT.set, None)
    outside.run(setattr, preferences, name, value)


def test_each_action_emits_one_identified_terminal_outcome() -> None:
    ledger = ActionLedger()
    add_action_result_sink(ledger)
    preferences = Preferences(LocalTopicBus(), 1)
    context = ActionContext.create("change theme")
    try:
        with transaction(action_context=context):
            preferences.theme = "dark"
    finally:
        ledger.close()

    assert len(ledger.results) == 1
    result = ledger.results[0]
    assert result.action_id == str(context.action_id)
    assert result.terminal == "committed"
    assert result.changes.cells == 1


def test_outcome_registration_does_not_retain_an_abandoned_sink() -> None:
    ledger = ActionLedger()
    reference = weakref.ref(ledger)
    add_action_result_sink(ledger)

    del ledger
    gc.collect()

    assert reference() is None


def test_read_only_actions_emit_terminal_outcomes() -> None:
    ledger = ActionLedger()
    add_action_result_sink(ledger)
    try:
        with readonly_transaction():
            pass
        with pytest.raises(RuntimeError, match="read failed"), readonly_transaction():
            raise RuntimeError("read failed")
    finally:
        ledger.close()

    assert [result.terminal for result in ledger.results] == ["committed", "rolled_back"]


def test_handler_failure_emits_rollback_after_staged_state_dies() -> None:
    preferences = Preferences(LocalTopicBus(), 1)
    seen: list[tuple[str, str]] = []

    with pytest.raises(RuntimeError, match="failed"), transaction():
        preferences.theme = "dark"

        def observe(rollback, continuation) -> None:
            seen.append((rollback.reason.value, preferences.theme))

        on_action_rollback(observe)
        raise RuntimeError("failed")

    assert seen == [("handler_exception", "system")]


def test_write_skew_is_allowed_by_default() -> None:
    """Reading a shared cell the action does not write costs it nothing by default."""
    preferences = Preferences(LocalTopicBus(), 1)

    with transaction():
        assert preferences.theme == "system"
        preferences.locale = "fr"
        _outside_write(preferences, "theme", "dark")

    assert preferences.locale == "fr"
    assert preferences.theme == "dark"


def test_strong_read_rejects_write_skew() -> None:
    preferences = Preferences(LocalTopicBus(), 1)

    with pytest.raises(ReactiveConflictError), transaction():
        with strong_read():
            assert preferences.theme == "system"
        preferences.locale = "fr"
        _outside_write(preferences, "theme", "dark")

    assert preferences.locale == "en"
    assert preferences.theme == "dark"


def test_a_read_the_action_also_writes_conflicts_without_opting_in() -> None:
    preferences = Preferences(LocalTopicBus(), 1)

    with pytest.raises(ReactiveConflictError), transaction():
        preferences.theme = preferences.theme + "-dim"
        _outside_write(preferences, "theme", "dark")

    assert preferences.theme == "dark"


def test_a_b_a_lineage_change_conflicts_even_when_value_matches() -> None:
    """Lineage, not equality: the cell holds what it held, but not the same version of it."""
    preferences = Preferences(LocalTopicBus(), 1)

    with pytest.raises(ReactiveConflictError), transaction():
        preferences.theme = preferences.theme + "-dim"
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
        on_action_commit(lambda commit, continuation: commits.append(commit))
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
        on_action_commit(lambda commit, continuation: commits.append(commit))
        counter.value = 1
    inverse = commits[0].patches.inverse()

    del counter
    gc.collect()

    assert owner() is None
    with pytest.raises(ReactiveConflictError, match="no longer exists"), transaction():
        apply_conditional_patches(inverse)


def test_relaxed_read_opts_back_out_of_a_strong_read() -> None:
    preferences = Preferences(LocalTopicBus(), 1)

    with transaction(), strong_read():
        with relaxed_read():
            assert preferences.theme == "system"
        preferences.locale = "fr"
        _outside_write(preferences, "theme", "dark")

    assert preferences.locale == "fr"


def test_relaxed_read_drops_the_precondition_on_a_cell_the_action_also_writes() -> None:
    preferences = Preferences(LocalTopicBus(), 1)

    with transaction():
        with relaxed_read():
            seen = preferences.theme
        preferences.theme = seen + "-dim"
        _outside_write(preferences, "theme", "dark")

    assert preferences.theme == "system-dim"


def test_relaxed_read_is_distinct_from_reactive_untracking() -> None:
    """It drops the precondition, not the dependency: the computed still recomputes."""
    preferences = Preferences(LocalTopicBus(), 1)
    runs = 0

    class Banner(StateOwner):
        @computed
        def caption(self) -> str:
            nonlocal runs
            runs += 1
            with relaxed_read():
                return preferences.theme.upper()

    banner = Banner()
    assert banner.caption == "SYSTEM"
    assert runs == 1

    preferences.theme = "dark"

    assert banner.caption == "DARK"
    assert runs == 2


def test_hook_failure_is_isolated_from_commit(caplog: pytest.LogCaptureFixture) -> None:
    preferences = Preferences(LocalTopicBus(), 1)

    def fail(commit, continuation) -> None:
        raise RuntimeError("hook failed")

    with caplog.at_level(logging.ERROR), transaction():
        on_action_commit(fail)
        preferences.theme = "dark"

    assert preferences.theme == "dark"
    assert "continuation hook failed" in caplog.text


def test_hook_failure_is_a_bounded_causal_diagnostic_node() -> None:
    ledger = ActionLedger()
    add_action_result_sink(ledger)

    def fail(commit, continuation) -> None:
        raise RuntimeError("secret hook detail")

    try:
        with transaction():
            on_action_commit(fail)
    finally:
        ledger.close()

    assert len(ledger.results) == 1
    failure = ledger.events[-1]
    assert isinstance(failure, ContinuationFailureSnapshot)
    assert failure.cause.identity == ledger.results[0].action_id
    assert failure.exception.type_name == "RuntimeError"
    assert failure.exception.message == "[redacted]"


def test_sink_and_participant_finalize_failures_are_causal_diagnostic_nodes() -> None:
    class FailingSink:
        def accept(self, result) -> None:
            raise RuntimeError("sink secret")

    class FinalizeFailure:
        def prepare(self, view) -> None:
            return None

        def describe_change(self, prepared: None) -> None:
            return None

        def apply(self, prepared: None) -> None:
            pass

        def abort(self, prepared: None, cause: BaseException) -> None:
            pass

        def finalize(self, prepared: None) -> None:
            raise RuntimeError("finalize secret")

    failing = FailingSink()
    ledger = ActionLedger()
    add_action_result_sink(failing)
    add_action_result_sink(ledger)
    try:
        with transaction():
            enlist(object(), FinalizeFailure)
    finally:
        ledger.close()
        remove_action_result_sink(failing)

    failures = [event for event in ledger.events if isinstance(event, ContinuationFailureSnapshot)]
    assert {failure.stage for failure in failures} == {"result_sink", "participant_finalize"}
    assert all(failure.exception.message == "[redacted]" for failure in failures)
    assert len(ledger.results) == 1


def test_apply_contract_failure_preserves_one_integrity_commit() -> None:
    preferences = Preferences(LocalTopicBus(), 1)
    ledger = ActionLedger()
    add_action_result_sink(ledger)

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
            enlist(object(), BrokenParticipant)
    finally:
        ledger.close()

    assert preferences.theme == "dark"
    assert len(ledger.results) == 1
    assert ledger.results[0].terminal == "committed"
    assert ledger.results[0].tags == frozenset({"framework_integrity_failure"})


def test_continuation_direct_mutation_is_rejected() -> None:
    preferences = Preferences(LocalTopicBus(), 1)
    errors: list[Exception] = []

    def mutate(commit, continuation) -> None:
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


def test_continuation_recovery_is_a_fresh_causally_linked_action() -> None:
    preferences = Preferences(LocalTopicBus(), 1)
    ledger = ActionLedger()
    add_action_result_sink(ledger)

    def recover(rollback, continuation) -> None:
        with continuation.start_action("present error"):
            preferences.theme = "recovered"

    try:
        with pytest.raises(RuntimeError), transaction():
            on_action_rollback(recover)
            raise RuntimeError("failed")
    finally:
        ledger.close()

    assert preferences.theme == "recovered"
    assert [result.terminal for result in ledger.results] == ["rolled_back", "committed"]
    assert ledger.results[1].cause is not None
    assert ledger.results[1].cause.identity == ledger.results[0].action_id
    assert ledger.results[1].root_action_id == ledger.results[0].root_action_id


def test_undo_kind_is_explicit_identity_not_hook_timing() -> None:
    context = ActionContext.create("undo", kind=ActionPurpose.UNDO)
    ledger = ActionLedger()
    add_action_result_sink(ledger)
    try:
        with transaction(action_context=context):
            pass
    finally:
        ledger.close()
    assert ledger.results[0].kind == "undo"


def test_portable_schema_one_round_trips_and_rejects_unknown_versions() -> None:
    ledger = ActionLedger()
    add_action_result_sink(ledger)
    try:
        with transaction():
            pass
    finally:
        ledger.close()
    codec = ActionResultCodec()

    assert codec.decode(codec.encode(ledger.results[0])) == ledger.results[0]
    with pytest.raises(ValueError, match="unsupported"):
        codec.decode(b'{"schema":2}')
    with pytest.raises(ValueError, match="corrupt"):
        codec.decode(b"not-json")
    with pytest.raises(ValueError, match="maximum encoded size"):
        codec.decode(b" " * 1_048_577)


def test_application_validation_has_a_distinct_rollback_reason() -> None:
    ledger = ActionLedger()
    add_action_result_sink(ledger)
    try:
        with pytest.raises(ActionValidationError), transaction():
            raise ActionValidationError("name is required")
    finally:
        ledger.close()

    assert ledger.results[0].reason == "validation_failed"


def test_portable_snapshot_redacts_by_default_and_can_opt_into_safe_metadata() -> None:
    context = ActionContext.create(
        "sensitive",
        actor=ActorRef("user", "42"),
        metadata={"tenant": "safe", "token": "secret"},
    )
    ledger = ActionLedger()
    rollbacks: list[ActionRollback] = []
    add_action_result_sink(ledger)
    try:
        with pytest.raises(RuntimeError), transaction(action_context=context):
            on_action_rollback(lambda rollback, continuation: rollbacks.append(rollback))
            raise RuntimeError("private failure message")
    finally:
        ledger.close()

    snapshot = ledger.results[0]
    assert snapshot.actor == ActorRef("user", "42")
    assert snapshot.metadata == ()
    assert snapshot.exception is not None and snapshot.exception.message == "[redacted]"
    opted_in = ActionResultSnapshot.from_result(
        rollbacks[0], RedactionPolicy(include_metadata=True, include_exception_messages=True)
    )
    assert dict(opted_in.metadata) == {"tenant": "safe", "token": "secret"}
    assert opted_in.exception is not None and opted_in.exception.message == "private failure message"


def test_each_result_sink_receives_its_declared_redaction_projection() -> None:
    default = ActionLedger()
    privileged = ActionLedger()
    add_action_result_sink(default)
    add_action_result_sink(
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

    assert default.results[0].actor == ActorRef("user", "42")
    assert default.results[0].metadata == ()
    assert default.results[0].exception is not None
    assert default.results[0].exception.message == "[redacted]"
    assert privileged.results[0].actor is None
    assert dict(privileged.results[0].metadata) == {"tenant": "safe"}
    assert privileged.results[0].exception is not None
    assert privileged.results[0].exception.message == "failure detail"


def test_durable_sink_encodes_outcomes_and_declares_host_policy() -> None:
    encoded: list[bytes] = []
    policy = DurableResultPolicy(
        redaction=RedactionPolicy(include_actor=False),
        actor_privacy="omitted",
        encryption="AES-256 at rest",
        retention="30 days",
    )
    sink = DurableResultSink(encoded.append, policy=policy)
    try:
        with transaction(action_context=ActionContext.create(actor=ActorRef("user", "42"))):
            pass
    finally:
        sink.close()

    snapshot = sink.codec.decode(encoded[0])
    assert snapshot.actor is None
    assert sink.policy.retention == "30 days"
    assert sink.policy.value_serialization == "summaries-only"


def test_a_strong_read_guards_its_own_version_not_an_earlier_unguarded_one() -> None:
    """The unguarded read was disclaimed by not being guarded, so it is not the precondition."""
    preferences = Preferences(LocalTopicBus(), 1)

    with transaction():
        assert preferences.theme == "system"
        _outside_write(preferences, "theme", "dark")
        with strong_read():
            assert preferences.theme == "dark"
        preferences.locale = "fr"

    assert preferences.locale == "fr"


def test_the_first_strong_read_is_the_one_the_commit_requires() -> None:
    preferences = Preferences(LocalTopicBus(), 1)

    with pytest.raises(ReactiveConflictError), transaction():
        with strong_read():
            assert preferences.theme == "system"
            _outside_write(preferences, "theme", "dark")
            assert preferences.theme == "dark"
        preferences.locale = "fr"

    assert preferences.locale == "en"
