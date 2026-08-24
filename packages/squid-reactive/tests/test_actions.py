"""Action outcomes, version lineage, and aftermath boundaries."""

import contextvars
import logging

import pytest

from squid_reactive import (
    ActionContext,
    ActionKind,
    ActionLedger,
    ActionOutcomeCodec,
    LocalTopicBus,
    ReactiveConflictError,
    ReactiveWriteError,
    Shared,
    add_action_outcome_sink,
    on_action_commit,
    on_action_rollback,
    relaxed_read,
    state,
    transaction,
)
from squid_reactive.core import _CURRENT


class Preferences(Shared[int]):
    theme: str = state("system")
    locale: str = state("en")


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
