"""Prototype A -- automatic read collection, eager refresh, coarse invalidation.

The smallest change that fixes both reproduced bugs. It keeps the shape the package
already has: `_state_changed` walks computeds, recomputes, compares, and propagates
only when a value actually changed. What it removes is `depends=`, replaced by a
ContextVar read collector -- the mechanism `observe_resources` and 40 section 7 use.

Dependency edges are per (instance, descriptor) and rebuilt on every recompute, so a
conditional dependency is exact rather than over-declared.
"""

from contextvars import ContextVar

from immutability import check, equal

_COLLECT: ContextVar[set | None] = ContextVar("collect", default=None)

STATS = {"recomputes": 0, "invalidations": 0, "reads": 0}


def _record(instance: object, descriptor: object) -> None:
    STATS["reads"] += 1
    observed = _COLLECT.get()
    if observed is not None:
        observed.add((id(instance), descriptor))


class untracked:
    """Reads inside this block create no dependency -- for action handlers."""

    def __enter__(self):
        self._token = _COLLECT.set(None)
        return self

    def __exit__(self, *exc):
        _COLLECT.reset(self._token)
        return False


class _State:
    def __init__(self, default):
        self.default = default
        self.name = ""

    def __set_name__(self, owner, name):
        self.name = name
        self.label = f"{owner.__name__}.{name}"
        self.slot = f"_state_{name}"

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        _record(instance, self)
        return instance.__dict__.get(self.slot, self.default)

    def __set__(self, instance, value):
        check(self.label, value)
        if equal(instance.__dict__.get(self.slot, self.default), value):
            return
        instance.__dict__[self.slot] = value
        instance._changed(self)


class _Computed:
    def __init__(self, function):
        self.function = function
        self.name = function.__name__

    def __set_name__(self, owner, name):
        self.name = name
        self.label = f"{owner.__name__}.{name}"

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        cache = instance.__dict__.setdefault("_computed_cache", {})
        if self not in cache:
            self.refresh(instance)
        _record(instance, self)
        return cache[self]

    def refresh(self, instance) -> bool:
        """Recompute, rebuild this node's dependency set, and report whether it changed."""
        cache = instance.__dict__.setdefault("_computed_cache", {})
        edges = instance.__dict__.setdefault("_computed_edges", {})
        previous = cache.get(self, _MISSING)
        observed: set = set()
        token = _COLLECT.set(observed)
        try:
            value = self.function(instance)
        finally:
            _COLLECT.reset(token)
        STATS["recomputes"] += 1
        cache[self] = value
        edges[self] = observed
        return previous is _MISSING or not equal(previous, value)


_MISSING = object()


class Component:
    def _changed(self, descriptor) -> None:
        """Eagerly refresh every computed that observed this node, then invalidate once."""
        edges = self.__dict__.setdefault("_computed_edges", {})
        dirty = [(id(self), descriptor)]
        seen = set()
        while dirty:
            node = dirty.pop()
            if node in seen:
                continue
            seen.add(node)
            for computed in type(self)._computeds:
                if node in edges.get(computed, ()) and self.__dict__.get("_computed_cache", {}).get(
                    computed, _MISSING
                ) is not _MISSING:
                    if computed.refresh(self):
                        dirty.append((id(self), computed))
        self.invalidate()

    def invalidate(self) -> None:
        STATS["invalidations"] += 1

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._computeds = tuple(
            value for value in vars(cls).values() if isinstance(value, _Computed)
        ) + tuple(getattr(cls, "_computeds", ()))


def state(default):
    return _State(default)


def computed(function):
    return _Computed(function)
