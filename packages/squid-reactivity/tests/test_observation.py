"""Dependency capture and write permissions have independent lifetimes."""

import gc
import weakref

import pytest

from squid_reactivity import (
    CellAddress,
    Reactive,
    ReactiveWriteError,
    capture_reads,
    computed,
    observe_reads,
    read_only,
    state,
    untracked,
)


class Model(Reactive):
    value: int = state(0)


class Initialized(Model):
    def __init__(self, value: int) -> None:
        self.value = value


def test_capture_allows_writes_and_read_only_preserves_the_consumer() -> None:
    model = Model()
    with capture_reads() as dependencies:
        model.value = 3
        with read_only():
            assert model.value == 3
        model.value = 4
    assert dependencies.addresses() == (CellAddress(model, "value"),)
    assert not dependencies.current()


def test_nested_captures_restore_the_consumer_after_failure() -> None:
    first, second = Model(), Model()
    with capture_reads() as outer:
        assert first.value == 0
        with pytest.raises(ValueError), capture_reads() as inner:
            assert second.value == 0
            raise ValueError("stop")
        first.value = 1
        assert first.value == 1
    assert outer.addresses() == (CellAddress(first, "value"),)
    assert inner.addresses() == (CellAddress(second, "value"),)
    assert outer.current()


def test_untracked_does_not_grant_write_permission() -> None:
    model = Model()
    with pytest.raises(ReactiveWriteError), read_only(), untracked():
        model.value = 1
    model.value = 2
    assert model.value == 2


def test_initialization_permission_ends_at_constructor_return() -> None:
    with read_only():
        model = Initialized(3)
        with pytest.raises(ReactiveWriteError):
            model.value = 4
    assert model.value == 3


def test_initialization_does_not_grant_permission_to_write_collaborators() -> None:
    collaborator = Model()

    class WritesCollaborator(Initialized):
        def __init__(self) -> None:
            super().__init__(3)
            collaborator.value = 4

    with pytest.raises(ReactiveWriteError), read_only():
        WritesCollaborator()
    assert collaborator.value == 0


def test_initialization_spans_the_complete_subclass_constructor() -> None:
    class Child(Initialized):
        def __init__(self) -> None:
            super().__init__(3)
            self.value = 4

    with read_only():
        model = Child()
        with pytest.raises(ReactiveWriteError):
            model.value = 5
    assert model.value == 4


def test_failed_constructor_ends_initialization_permission() -> None:
    escaped: list[Model] = []

    class Failing(Model):
        def __init__(self) -> None:
            escaped.append(self)
            self.value = 3
            raise ValueError("stop")

    with read_only():
        with pytest.raises(ValueError):
            Failing()
        with pytest.raises(ReactiveWriteError):
            escaped[0].value = 4


def test_nested_read_only_scope_cannot_initialize_an_outer_object() -> None:
    class Nested(Model):
        def __init__(self) -> None:
            self.value = 1
            with read_only(), pytest.raises(ReactiveWriteError):
                self.value = 2
            self.value = 3

    with read_only():
        model = Nested()
    assert model.value == 3


def test_observation_does_not_retain_unread_constructed_objects() -> None:
    with observe_reads() as dependencies:
        model = Initialized(1)
        reference = weakref.ref(model)
        del model
        gc.collect()
        assert reference() is None
    assert not dependencies.sources


def test_computed_cannot_mutate_state_even_without_a_render() -> None:
    class Writes(Model):
        @computed
        def result(self) -> int:
            self.value += 1
            return self.value

    model = Writes()
    with pytest.raises(ReactiveWriteError):
        _ = model.result
    assert model.value == 0
    model.value = 2
    assert model.value == 2


def test_opaque_change_notifications_respect_read_only_scope() -> None:
    class Opaque(Reactive):
        collaborator: object = state(factory=object, opaque=True)

    model = Opaque()
    collaborator = model.collaborator
    with pytest.raises(ReactiveWriteError), read_only():
        model.mutated(collaborator)
