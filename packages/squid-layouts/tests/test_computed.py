"""Pull-based computeds: what they read, when they run, and what they keep alive."""

import gc
import weakref

import pytest

from squid_layouts import Component, computed, state
from squid_layouts.runtime import ReactiveCycleError, untracked
from squid_layouts.primitives import Text


class Counter(Component):
    """A component whose computed bodies count their own runs."""

    first: int = state(0)
    second: int = state(0)
    flag: bool = state(default=True)

    def __init__(self) -> None:
        self.runs: dict[str, int] = {}

    def _ran(self, name: str) -> None:
        self.runs[name] = self.runs.get(name, 0) + 1

    def render(self):
        return Text("")


class TestTracking:
    def test_a_computed_needs_no_dependency_declaration(self):
        class Panel(Counter):
            @computed
            def total(self) -> int:
                self._ran("total")
                return self.first + self.second

        panel = Panel()
        assert panel.total == 0
        assert panel.total == 0
        assert panel.runs["total"] == 1

        panel.first = 2

        assert panel.total == 2
        assert panel.runs["total"] == 2

    def test_it_is_never_stale_for_state_no_declaration_would_have_named(self):
        """The defect this replaces: a body reading more than `depends=` said it did."""

        class Panel(Counter):
            @computed
            def total(self) -> int:
                return self.first + self.second

        panel = Panel()
        assert panel.total == 0
        panel.second = 5
        assert panel.total == 5

    def test_a_conditional_dependency_is_the_branch_that_ran(self):
        class Panel(Counter):
            @computed
            def either(self) -> int:
                self._ran("either")
                return self.first if self.flag else self.second

        panel = Panel()
        assert panel.either == 0
        assert panel.runs["either"] == 1

        panel.second = 9  # not read while flag is True

        assert panel.either == 0
        assert panel.runs["either"] == 1

        panel.flag = False

        assert panel.either == 9
        assert panel.runs["either"] == 2

    def test_untracked_reads_do_not_subscribe(self):
        class Panel(Counter):
            @computed
            def sampled(self) -> int:
                self._ran("sampled")
                with untracked():
                    sampled = self.second
                return self.first + sampled

        panel = Panel()
        assert panel.sampled == 0

        panel.second = 3

        assert panel.sampled == 0
        assert panel.runs["sampled"] == 1

    def test_a_computed_reads_another_computed(self):
        class Panel(Counter):
            @computed
            def doubled(self) -> int:
                return self.first * 2

            @computed
            def quadrupled(self) -> int:
                return self.doubled * 2

        panel = Panel()
        assert panel.quadrupled == 0
        panel.first = 3
        assert panel.quadrupled == 12


class TestWork:
    def test_a_diamond_recomputes_its_shared_node_once(self):
        class Panel(Counter):
            @computed
            def shared(self) -> int:
                self._ran("shared")
                return self.first + 1

            @computed
            def left(self) -> int:
                return self.shared * 2

            @computed
            def right(self) -> int:
                return self.shared * 3

            @computed
            def bottom(self) -> int:
                return self.left + self.right

        panel = Panel()
        assert panel.bottom == 5
        assert panel.runs["shared"] == 1

        panel.first = 1

        assert panel.bottom == 10
        assert panel.runs["shared"] == 2

    def test_a_value_that_settles_unchanged_stops_there(self):
        """`normalized` reruns because its input moved; `label` does not, because it did not."""

        class Panel(Counter):
            @computed
            def normalized(self) -> bool:
                self._ran("normalized")
                return self.first > 0

            @computed
            def label(self) -> str:
                self._ran("label")
                return f"positive:{self.normalized}"

        panel = Panel()
        panel.first = 1
        assert panel.label == "positive:True"
        assert panel.runs == {"normalized": 1, "label": 1}

        panel.first = 2

        assert panel.label == "positive:True"
        assert panel.runs == {"normalized": 2, "label": 1}

    def test_a_write_that_changes_nothing_propagates_nothing(self):
        class Panel(Counter):
            @computed
            def total(self) -> int:
                self._ran("total")
                return self.first + self.second

        panel = Panel()
        assert panel.total == 0

        panel.first = 0

        assert panel.total == 0
        assert panel.runs["total"] == 1

    def test_a_computed_nobody_reads_is_never_evaluated(self):
        class Panel(Counter):
            @computed
            def unread(self) -> int:
                self._ran("unread")
                return self.first

            @computed
            def read(self) -> int:
                return self.second

        panel = Panel()
        assert panel.read == 0
        panel.first = 1
        panel.second = 1
        assert panel.read == 1
        assert "unread" not in panel.runs

    def test_repeated_reads_between_writes_walk_nothing(self):
        class Panel(Counter):
            @computed
            def total(self) -> int:
                self._ran("total")
                return self.first + self.second

        panel = Panel()
        for _ in range(10):
            assert panel.total == 0
        assert panel.runs["total"] == 1


class TestFailure:
    def test_a_raising_computed_fails_where_its_value_is_used(self):
        class Panel(Counter):
            @computed
            def broken(self) -> int:
                self._ran("broken")
                message = "no"
                raise RuntimeError(message)

        panel = Panel()
        with pytest.raises(RuntimeError, match="no"):
            _ = panel.broken
        # And again, rather than caching a value nothing verified.
        with pytest.raises(RuntimeError, match="no"):
            _ = panel.broken
        assert panel.runs["broken"] == 2

    def test_a_computed_that_reads_itself_says_so(self):
        class Panel(Counter):
            @computed
            def loop(self) -> int:
                return self.loop

        with pytest.raises(ReactiveCycleError, match=r"cycle: Panel\.loop -> Panel\.loop"):
            _ = Panel().loop


class TestReferences:
    def test_a_dropped_reader_is_collected_while_its_source_lives(self):
        """Why pull rather than a dependent list: components here are per-message."""

        class Source(Component):
            count: int = state(0)

            def render(self):
                return Text("")

        class Reader(Component):
            def __init__(self, source: Source) -> None:
                self.source = source

            @computed
            def doubled(self) -> int:
                return self.source.count * 2

            def render(self):
                return Text("")

        source = Source()
        reader = Reader(source)
        assert reader.doubled == 0
        dropped = weakref.ref(reader)

        del reader
        gc.collect()

        assert dropped() is None
        source.count = 1
        assert source.count == 1

    def test_a_reader_recomputes_when_the_source_it_kept_moves(self):
        class Source(Component):
            count: int = state(0)

            def render(self):
                return Text("")

        class Reader(Component):
            def __init__(self, source: Source) -> None:
                self.source = source
                self.runs = 0

            @computed
            def doubled(self) -> int:
                self.runs += 1
                return self.source.count * 2

            def render(self):
                return Text("")

        source = Source()
        reader = Reader(source)
        assert reader.doubled == 0

        source.count = 4

        assert reader.doubled == 8
        assert reader.runs == 2
