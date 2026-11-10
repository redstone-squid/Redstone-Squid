"""One runtime candidate owns subscriptions and the revision captured for delivery."""

from typing import override

import pytest

import squid_ui as sl
from squid_reactivity import CellAddress
from squid_ui.errors import LayoutInvariantError
from squid_ui.primitives import Text
from squid_ui.runtime.owner import ComponentRuntime


class Model(sl.Reactive):
    value: int = sl.state(0)


class Panel(sl.Component[sl.ComponentsV2Target]):
    model: Model = sl.state(opaque=True)

    def __init__(self, model: Model) -> None:
        self.model = model

    @override
    def render(self) -> Text:
        return Text(str(self.model.value))


def test_candidate_preserves_changes_during_delivery_without_a_separate_revision() -> None:
    model = Model()
    runtime = ComponentRuntime(Panel(model))
    tree = runtime.render()
    candidate = runtime.stage(tree)
    model.value = 1
    assert runtime.stage(tree) is candidate
    candidate.commit()

    assert candidate.settled
    assert runtime.dirty
    assert runtime.render(reuse_committed=True).nodes == (Text("1"),)
    with pytest.raises(LayoutInvariantError, match="already settled"):
        candidate.commit()
    runtime.finish()


def test_superseded_candidate_cannot_release_its_successors_subscriptions() -> None:
    first, second, third = Model(), Model(), Model()
    panel = Panel(first)
    runtime = ComponentRuntime(panel)
    runtime.commit(runtime.render())
    panel.model = second
    abandoned = runtime.stage(runtime.render())
    panel.model = third
    successor = runtime.stage(runtime.render())

    assert abandoned.settled
    assert CellAddress(first, "value") in runtime.subscriptions.followed
    assert CellAddress(second, "value") not in runtime.subscriptions.followed
    assert CellAddress(third, "value") in runtime.subscriptions.followed
    with pytest.raises(LayoutInvariantError, match="already settled"):
        abandoned.discard()
    successor.discard()
    assert CellAddress(first, "value") in runtime.subscriptions.followed
    assert CellAddress(third, "value") not in runtime.subscriptions.followed
    runtime.finish()


def test_finish_discards_an_outstanding_candidate() -> None:
    runtime = ComponentRuntime(Panel(Model()))
    candidate = runtime.stage(runtime.render())
    runtime.finish()
    assert candidate.settled
    assert runtime.subscriptions.followed == ()
    with pytest.raises(LayoutInvariantError, match="already settled"):
        candidate.commit()


def test_mount_failure_leaves_the_delivered_candidate_committed() -> None:
    class Broken(Panel):
        @override
        def on_mount(self) -> None:
            raise ValueError("mount failed")

    model = Model()
    runtime = ComponentRuntime(Broken(model))
    candidate = runtime.stage(runtime.render())
    with pytest.raises(ExceptionGroup, match="lifecycle hooks failed"):
        candidate.commit()
    assert candidate.settled
    assert CellAddress(model, "value") in runtime.subscriptions.committed
    with pytest.raises(LayoutInvariantError, match="already settled"):
        candidate.discard()
    model.value = 1
    assert runtime.dirty
    runtime.finish()


def test_discovery_candidate_must_be_discarded_instead_of_committed() -> None:
    class Parent(sl.Component[sl.ComponentsV2Target]):
        def __init__(self) -> None:
            self.child = Panel(Model())

        @override
        def render(self) -> sl.LayoutNode[sl.ComponentsV2Target]:
            return self.boundary(self.child, key="child")

    runtime = ComponentRuntime(Parent())
    candidate = runtime.stage(runtime.render(defer=lambda _: True))
    with pytest.raises(LayoutInvariantError, match="discovery render"):
        candidate.commit()
    assert not candidate.settled
    candidate.discard()
    assert candidate.settled
    runtime.finish()
