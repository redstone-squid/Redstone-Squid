"""Prototype C: validate computed values by pulling source versions on reads.

Sources keep no reverse references to readers, so a long-lived source does not keep
a discarded reader alive as it can in graph.py. State writes invalidate the owning
component directly. A computed value checks its source versions when read;
a global epoch avoids repeated checks when nothing has changed.

This prototype is historical design evidence, not the current reactivity implementation.
"""
from typing import override

from contextvars import ContextVar

from immutability import check, equal

_CONSUMER: ContextVar[object | None] = ContextVar("consumer", default=None)

STATS = {"recomputes": 0, "invalidations": 0, "reads": 0}

_EPOCH = [0]
"""Incremented when a cell value changes, including a derived cell's value.

A node already settled in this epoch can skip walking its sources again.
"""


class untracked:
    def __enter__(self):
        self._token = _CONSUMER.set(None)
        return self

    def __exit__(self, *exc):
        _CONSUMER.reset(self._token)
        return False


class _Cell:
    __slots__ = ("value", "version")

    def __init__(self, value):
        self.value = value
        self.version = 0

    def settle(self) -> int:
        return self.version

    def get(self):
        STATS["reads"] += 1
        consumer = _CONSUMER.get()
        if consumer is not None:
            consumer.sources[self] = self.version
        return self.value

    def set(self, value) -> bool:
        if equal(self.value, value):
            return False
        self.value = value
        self.version += 1
        _EPOCH[0] += 1
        return True


class _Derived(_Cell):
    __slots__ = ("function", "owner", "sources", "settled", "epoch")

    def __init__(self, function, owner):
        super().__init__(None)
        self.function = function
        self.owner = owner
        self.sources: dict = {}
        self.settled = False
        self.epoch = -1

    @override
    def settle(self) -> int:
        """Return this node's current version, recomputing only if a source moved."""
        if self.epoch == _EPOCH[0]:
            return self.version
        if self.settled and all(source.settle() == seen for source, seen in self.sources.items()):
            self.epoch = _EPOCH[0]
            return self.version
        self.sources = {}
        token = _CONSUMER.set(self)
        try:
            value = self.function(self.owner)
        finally:
            _CONSUMER.reset(token)
        STATS["recomputes"] += 1
        self.settled = True
        self.epoch = _EPOCH[0]
        self.set(value)
        return self.version

    @override
    def get(self):
        self.settle()
        return super().get()


class _State:
    def __init__(self, default):
        self.default = default

    def __set_name__(self, owner, name):
        self.name = name
        self.label = f"{owner.__name__}.{name}"
        self.slot = f"_cell_{name}"

    def cell(self, instance) -> _Cell:
        cell = instance.__dict__.get(self.slot)
        if cell is None:
            cell = instance.__dict__[self.slot] = _Cell(self.default)
        return cell

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return self.cell(instance).get()

    def __set__(self, instance, value):
        check(self.label, value)
        if self.cell(instance).set(value):
            instance.invalidate()


class _Computed:
    def __init__(self, function):
        self.function = function
        self.name = function.__name__

    def __set_name__(self, owner, name):
        self.name = name
        self.label = f"{owner.__name__}.{name}"
        self.slot = f"_derived_{name}"

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        node = instance.__dict__.get(self.slot)
        if node is None:
            node = instance.__dict__[self.slot] = _Derived(self.function, instance)
        return node.get()


class Component:
    def invalidate(self) -> None:
        STATS["invalidations"] += 1


def state(default):
    return _State(default)


def computed(function):
    return _Computed(function)
