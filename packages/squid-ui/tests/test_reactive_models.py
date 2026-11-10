"""Models and components share dependency tracking without host metadata."""

import gc
import weakref
from typing import override

import squid_ui as sl
from squid_reactivity import CellAddress, Reactive, transaction
from squid_ui.primitives import Text
from squid_ui.runtime.owner import ComponentRuntime


class Search(sl.Reactive):
    query: str = sl.state("")

    @sl.computed
    def label(self) -> str:
        return self.query.strip().upper()


class Reader(sl.Component[sl.ComponentsV2Target]):
    def __init__(self, search: Search) -> None:
        self.search = search
        self.renders = 0

    @override
    def render(self) -> Text:
        self.renders += 1
        return Text(self.search.label)


def test_component_extends_the_public_reactive_base() -> None:
    assert sl.Reactive is Reactive
    assert issubclass(sl.Component, Reactive)
    assert not hasattr(sl.runtime, "Reactive")


def test_nested_model_invalidates_multiple_runtimes_without_a_bus() -> None:
    search = Search()
    readers = [Reader(search), Reader(search)]
    runtimes = [ComponentRuntime(reader) for reader in readers]
    for runtime in runtimes:
        runtime.commit(runtime.render())
    with transaction():
        search.query = " first "
    for runtime in runtimes:
        assert runtime.dirty
        tree = runtime.render(reuse_committed=True)
        assert tree.nodes == (Text("FIRST"),)
        runtime.commit(tree)
    runtimes[0].finish()
    search.query = "second"
    assert not runtimes[0].subscriptions.followed
    assert runtimes[1].dirty


def test_a_sibling_reading_component_state_is_invalidated_with_its_owner() -> None:
    class Source(sl.Component[sl.ComponentsV2Target]):
        count: int = sl.state(0)

        @override
        def render(self) -> Text:
            return Text(str(self.count))

    source = Source()

    class Sibling(sl.Component[sl.ComponentsV2Target]):
        @override
        def render(self) -> Text:
            return Text(f"also {source.count}")

    class Parent(sl.Component[sl.ComponentsV2Target]):
        def __init__(self) -> None:
            self.sibling = Sibling()

        @override
        def render(self):
            return [self.boundary(source, key="source"), self.boundary(self.sibling, key="sibling")]

    runtime = ComponentRuntime(Parent())
    runtime.commit(runtime.render())
    source.count = 1
    assert runtime.render(reuse_committed=True).nodes == (Text("1"), Text("also 1"))


def test_discard_preserves_old_reads_and_releases_prospective_reads() -> None:
    first, second = Search(), Search()

    class Switch(sl.Component[sl.ComponentsV2Target]):
        search: Search = sl.state(opaque=True)

        def __init__(self) -> None:
            self.search = first

        @override
        def render(self) -> Text:
            return Text(self.search.label)

    panel = Switch()
    runtime = ComponentRuntime(panel)
    runtime.commit(runtime.render())
    panel.search = second
    candidate = runtime.render()
    assert CellAddress(first, "query") in runtime.subscriptions.followed
    assert CellAddress(second, "query") in runtime.subscriptions.followed
    runtime.discard()
    assert CellAddress(first, "query") in runtime.subscriptions.followed
    assert CellAddress(second, "query") not in runtime.subscriptions.followed
    runtime.commit(candidate)
    assert CellAddress(first, "query") not in runtime.subscriptions.followed
    assert CellAddress(second, "query") in runtime.subscriptions.followed


def test_a_retained_model_does_not_keep_an_abandoned_runtime_alive() -> None:
    search = Search()
    runtime = ComponentRuntime(Reader(search))
    runtime.commit(runtime.render())
    reference = weakref.ref(runtime)
    del runtime
    gc.collect()
    assert reference() is None
    search.query = "usable"


def test_models_created_in_an_action_need_no_super_initializer() -> None:
    class Model(sl.Reactive):
        value: int = sl.state()

        def __init__(self, value: int) -> None:
            self.value = value

    with transaction():
        model = Model(3)
    assert model.value == 3
