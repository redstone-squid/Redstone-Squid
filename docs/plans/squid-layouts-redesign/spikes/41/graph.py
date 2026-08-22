"""Prototype B -- a signal graph behind the same attribute surface.

Each (instance, field) is a cell with a version. Each computed is a derived node
holding the versions of the sources it read last time. A write bumps a version and
marks dependents maybe-dirty; a read of a derived node checks whether any source
version actually moved before recomputing.

That version check is the whole reason to prefer a graph: a computed whose inputs
changed but whose *value* did not stops propagating, and so do the computeds above
it. Work is pull-driven, so a computed nobody reads is never recomputed at all.
"""

from contextvars import ContextVar

from immutability import check, equal

_CONSUMER: ContextVar[object | None] = ContextVar("consumer", default=None)

STATS = {"recomputes": 0, "invalidations": 0, "reads": 0}


class untracked:
    def __enter__(self):
        self._token = _CONSUMER.set(None)
        return self

    def __exit__(self, *exc):
        _CONSUMER.reset(self._token)
        return False


class _Cell:
    __slots__ = ("value", "version", "dependents")

    def __init__(self, value):
        self.value = value
        self.version = 0
        self.dependents: list = []

    def get(self):
        STATS["reads"] += 1
        consumer = _CONSUMER.get()
        if consumer is not None:
            consumer.sources[self] = self.version
            if consumer not in self.dependents:
                self.dependents.append(consumer)
        return self.value

    def set(self, value) -> bool:
        if equal(self.value, value):
            return False
        self.value = value
        self.version += 1
        for dependent in self.dependents:
            dependent.mark()
        return True


class _Derived(_Cell):
    __slots__ = ("function", "owner", "sources", "stale")

    def __init__(self, function, owner):
        super().__init__(None)
        self.function = function
        self.owner = owner
        self.sources: dict = {}
        self.stale = True

    def mark(self) -> None:
        if self.stale:
            return
        self.stale = True
        for dependent in self.dependents:
            dependent.mark()

    def get(self):
        if self.stale:
            self._settle()
        return super().get()

    def _settle(self) -> None:
        # Pull each source first: a stale source may settle to an unchanged value, in
        # which case its version never moved and this node does not recompute either.
        if self.sources:
            for source in tuple(self.sources):
                if isinstance(source, _Derived) and source.stale:
                    source._settle()
            if all(source.version == seen for source, seen in self.sources.items()):
                self.stale = False
                return
        previous_sources = self.sources
        self.sources = {}
        token = _CONSUMER.set(self)
        try:
            value = self.function(self.owner)
        finally:
            _CONSUMER.reset(token)
        STATS["recomputes"] += 1
        for source in previous_sources:
            if source not in self.sources and self in source.dependents:
                source.dependents.remove(self)
        self.stale = False
        # set() bumps the version only on a real change, which is the cut-off that
        # stops propagation dead when a derived value settles back to what it was.
        self.set(value)


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
