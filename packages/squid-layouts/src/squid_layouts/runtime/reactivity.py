"""Transactional reactive state for component trees."""

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Protocol


class ReactiveOwner(Protocol):
    __dict__: dict[str, Any]

    def _state_changed(self) -> None: ...

    def _state_rolled_back(self) -> None: ...


class ReactiveWriteError(RuntimeError):
    """A state mutation was attempted inside a read-only action."""


def _plain(value: Any) -> Any:
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {_plain(key): _plain(item) for key, item in value.items()}
    if isinstance(value, set):
        return {_plain(item) for item in value}
    return deepcopy(value)


@dataclass(slots=True)
class _Snapshot:
    owner: ReactiveOwner
    name: str
    existed: bool
    value: Any


@dataclass(slots=True)
class _Transaction:
    readonly: bool = False
    snapshots: dict[tuple[int, str], _Snapshot] = field(default_factory=dict)
    changed: dict[int, ReactiveOwner] = field(default_factory=dict)

    def record(self, owner: ReactiveOwner, name: str) -> None:
        key = (id(owner), name)
        if key in self.snapshots:
            return
        existed = name in owner.__dict__
        value = _plain(owner.__dict__[name]) if existed else None
        self.snapshots[key] = _Snapshot(owner, name, existed, value)

    def mark_changed(self, owner: ReactiveOwner) -> None:
        if self.readonly:
            message = "parallel-read actions cannot mutate component state"
            raise ReactiveWriteError(message)
        self.changed[id(owner)] = owner

    def commit(self) -> None:
        for owner in self.changed.values():
            owner._state_changed()

    def rollback(self) -> None:
        for snapshot in reversed(tuple(self.snapshots.values())):
            if snapshot.existed:
                snapshot.owner.__dict__[snapshot.name] = _observe(snapshot.value, snapshot.owner, snapshot.name)
            else:
                snapshot.owner.__dict__.pop(snapshot.name, None)
            snapshot.owner._state_rolled_back()


_CURRENT: ContextVar[_Transaction | None] = ContextVar("squid_layouts_transaction", default=None)


def _before(owner: ReactiveOwner, name: str) -> None:
    if current := _CURRENT.get():
        current.record(owner, name)


def _after(owner: ReactiveOwner) -> None:
    if current := _CURRENT.get():
        current.mark_changed(owner)
    else:
        owner._state_changed()


@contextmanager
def transaction() -> Iterator[None]:
    """Coalesce state writes and roll all of them back when the block raises."""
    if _CURRENT.get() is not None:
        yield
        return
    current = _Transaction()
    token = _CURRENT.set(current)
    try:
        yield
    except BaseException:
        _CURRENT.reset(token)
        current.rollback()
        raise
    else:
        _CURRENT.reset(token)
        current.commit()


@contextmanager
def batch() -> Iterator[None]:
    """Coalesce related state writes into one invalidation per component."""
    with transaction():
        yield


@contextmanager
def readonly_transaction() -> Iterator[None]:
    """Roll back and reject any state mutation within the block."""
    if _CURRENT.get() is not None:
        message = "a read-only transaction cannot nest inside a writable transaction"
        raise RuntimeError(message)
    current = _Transaction(readonly=True)
    token = _CURRENT.set(current)
    try:
        yield
    except BaseException:
        _CURRENT.reset(token)
        current.rollback()
        raise
    else:
        _CURRENT.reset(token)
        current.commit()


class _ReactiveMixin:
    _reactive_owner: ReactiveOwner
    _reactive_name: str

    def _bind(self, owner: ReactiveOwner, name: str) -> None:
        self._reactive_owner = owner
        self._reactive_name = name

    def _before(self) -> None:
        _before(self._reactive_owner, self._reactive_name)

    def _after(self) -> None:
        _after(self._reactive_owner)

    def _wrap(self, value: Any) -> Any:
        return _observe(value, self._reactive_owner, self._reactive_name)


class ReactiveList(_ReactiveMixin, list[Any]):
    """A list whose in-place mutations invalidate its owning component."""

    def __init__(self, values: Iterable[Any], owner: ReactiveOwner, name: str) -> None:
        self._bind(owner, name)
        super().__init__(self._wrap(value) for value in values)

    def __setitem__(self, key: int | slice, value: Any) -> None:
        self._before()
        wrapped = [self._wrap(item) for item in value] if isinstance(key, slice) else self._wrap(value)
        super().__setitem__(key, wrapped)
        self._after()

    def __delitem__(self, key: int | slice) -> None:
        self._before()
        super().__delitem__(key)
        self._after()

    def append(self, value: Any) -> None:
        self._before()
        super().append(self._wrap(value))
        self._after()

    def extend(self, values: Iterable[Any]) -> None:
        self._before()
        super().extend(self._wrap(value) for value in values)
        self._after()

    def insert(self, index: int, value: Any) -> None:
        self._before()
        super().insert(index, self._wrap(value))
        self._after()

    def pop(self, index: int = -1) -> Any:
        self._before()
        value = super().pop(index)
        self._after()
        return value

    def remove(self, value: Any) -> None:
        self._before()
        super().remove(value)
        self._after()

    def clear(self) -> None:
        self._before()
        super().clear()
        self._after()

    def reverse(self) -> None:
        self._before()
        super().reverse()
        self._after()

    def sort(self, *, key: Callable[[Any], Any] | None = None, reverse: bool = False) -> None:
        self._before()
        super().sort(key=key, reverse=reverse)
        self._after()

    def __iadd__(self, values: Iterable[Any]):
        self.extend(values)
        return self


class ReactiveDict(_ReactiveMixin, dict[Any, Any]):
    """A dict whose in-place mutations invalidate its owning component."""

    def __init__(self, values: Mapping[Any, Any], owner: ReactiveOwner, name: str) -> None:
        self._bind(owner, name)
        super().__init__((self._wrap(key), self._wrap(value)) for key, value in values.items())

    def __setitem__(self, key: Any, value: Any) -> None:
        self._before()
        super().__setitem__(self._wrap(key), self._wrap(value))
        self._after()

    def __delitem__(self, key: Any) -> None:
        self._before()
        super().__delitem__(key)
        self._after()

    def clear(self) -> None:
        self._before()
        super().clear()
        self._after()

    def pop(self, key: Any, default: Any = ...):
        self._before()
        value = super().pop(key) if default is ... else super().pop(key, default)
        self._after()
        return value

    def popitem(self) -> tuple[Any, Any]:
        self._before()
        value = super().popitem()
        self._after()
        return value

    def setdefault(self, key: Any, default: Any = None) -> Any:
        if key in self:
            return self[key]
        self[key] = default
        return self[key]

    def update(self, values: Mapping[Any, Any] | Iterable[tuple[Any, Any]] = (), **kwargs: Any) -> None:
        incoming = dict(values, **kwargs)
        if not incoming:
            return
        self._before()
        super().update((self._wrap(key), self._wrap(value)) for key, value in incoming.items())
        self._after()


class ReactiveSet(_ReactiveMixin, set[Any]):
    """A set whose in-place mutations invalidate its owning component."""

    def __init__(self, values: Iterable[Any], owner: ReactiveOwner, name: str) -> None:
        self._bind(owner, name)
        super().__init__(self._wrap(value) for value in values)

    def add(self, value: Any) -> None:
        self._before()
        super().add(self._wrap(value))
        self._after()

    def discard(self, value: Any) -> None:
        self._before()
        super().discard(value)
        self._after()

    def remove(self, value: Any) -> None:
        self._before()
        super().remove(value)
        self._after()

    def pop(self) -> Any:
        self._before()
        value = super().pop()
        self._after()
        return value

    def clear(self) -> None:
        self._before()
        super().clear()
        self._after()

    def update(self, *values: Iterable[Any]) -> None:
        if not values:
            return
        self._before()
        wrapped = tuple(tuple(self._wrap(value) for value in group) for group in values)
        super().update(*wrapped)
        self._after()


def _observe(value: Any, owner: ReactiveOwner, name: str) -> Any:
    if isinstance(value, ReactiveList | ReactiveDict | ReactiveSet):
        value = _plain(value)
    if isinstance(value, list):
        return ReactiveList(value, owner, name)
    if isinstance(value, dict):
        return ReactiveDict(value, owner, name)
    if isinstance(value, set):
        return ReactiveSet(value, owner, name)
    return value


_MISSING = object()


class _State:
    def __init__(
        self,
        default: Any = _MISSING,
        *,
        factory: Callable[[], Any] | None = None,
        persist: bool = True,
    ) -> None:
        if default is not _MISSING and factory is not None:
            message = "state accepts either a default or a factory, not both"
            raise TypeError(message)
        if default is _MISSING and factory is None:
            message = "state requires a default or factory"
            raise TypeError(message)
        self._default = default
        self._factory = factory
        self._name = ""
        self.public_name = ""
        self.persist = persist

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = f"__state_{name}"
        self.public_name = name

    def _initial(self) -> Any:
        return self._factory() if self._factory is not None else deepcopy(self._default)

    def __get__(self, instance: ReactiveOwner | None, owner: type | None = None) -> Any:
        if instance is None:
            return self
        if self._name not in instance.__dict__:
            instance.__dict__[self._name] = _observe(self._initial(), instance, self._name)
        return instance.__dict__[self._name]

    def __set__(self, instance: ReactiveOwner, value: Any) -> None:
        if instance.__dict__.get(self._name, _MISSING) is value:
            return
        _before(instance, self._name)
        instance.__dict__[self._name] = _observe(value, instance, self._name)
        _after(instance)


def state(
    default: Any = _MISSING,
    *,
    factory: Callable[[], Any] | None = None,
    persist: bool = True,
) -> Any:
    """Declare observed component state with either a default or per-instance factory."""
    return _State(default, factory=factory, persist=persist)


def _state_fields(owner: ReactiveOwner) -> dict[str, _State]:
    fields: dict[str, _State] = {}
    for cls in reversed(type(owner).__mro__):
        fields.update(
            (name, descriptor)
            for name, descriptor in vars(cls).items()
            if isinstance(descriptor, _State) and descriptor.persist
        )
    return fields


def export_state(owner: ReactiveOwner) -> dict[str, Any]:
    """Return plain values for every persistent state descriptor on an instance."""
    return {name: _plain(getattr(owner, name)) for name in _state_fields(owner)}


def restore_state(owner: ReactiveOwner, values: Mapping[str, Any]) -> None:
    """Restore declared persistent state, rejecting stale or misspelled field names."""
    fields = _state_fields(owner)
    unknown = set(values) - set(fields)
    if unknown:
        message = f"snapshot has unknown state fields: {', '.join(sorted(unknown))}"
        raise ValueError(message)
    with transaction():
        for name, value in values.items():
            setattr(owner, name, deepcopy(value))


class _Computed:
    def __init__(self, function: Callable[[Any], Any]) -> None:
        self._function = function
        self._name = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = f"__computed_{name}"

    def __get__(self, instance: ReactiveOwner | None, owner: type | None = None) -> Any:
        if instance is None:
            return self
        revision = instance.__dict__.get("_state_revision", 0)
        cached = instance.__dict__.get(self._name)
        if cached is None or cached[0] != revision:
            cached = (revision, self._function(instance))
            instance.__dict__[self._name] = cached
        return cached[1]


def computed(function: Callable[[Any], Any]) -> Any:
    """Cache a derived value until this component tree invalidates again."""
    return _Computed(function)
