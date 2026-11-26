"""Placement-owned reactive cells shared by presentation declarations and bindings."""

from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Never, cast, overload, override

from squid_reactivity import Reactive, export_state, restore_state
from squid_reactivity.inspection import CellReport, inspect_cells
from squid_reactivity.internals import State
from squid_reactivity.transactions import transaction
from squid_ui.content.text import TextLike
from squid_ui.errors import LayoutInvariantError

if TYPE_CHECKING:
    from squid_ui.interactions import ActionEvent
    from squid_ui.layout import ElementRef
    from squid_ui.runtime.histories import History, HistoryField


type RecordTarget = History | HistoryField | None

_CURRENT: ContextVar[tuple[PresentationScope, bool] | None] = ContextVar("squid_ui_presentation", default=None)


class PresentationModel[ValueT](Reactive):
    """One declaration's ordinary reactive value, owned by a particular placement."""

    value: ValueT
    _on_commit: Callable[[], None] | None = None
    _reactive_internal_attributes = frozenset({"_on_commit"})

    def __init__(self, values: Mapping[str, Any] | None = None) -> None:
        if values is not None:
            restore_state(self, values)

    @override
    def on_state_commit(self, names: frozenset[str]) -> None:
        if self._on_commit is not None:
            self._on_commit()


class PresentationField[HandleT]:
    """A declaration that creates handles, with storage supplied by its placement."""

    def __init__(
        self,
        value_state: State[Any],
        handle: Callable[[PresentationReference[Any]], HandleT],
        *,
        record: RecordTarget = None,
        record_label: TextLike | None = None,
        kind: str = "value",
    ) -> None:
        if record is not None and record_label is None:
            msg = "recording a presentation value requires record_label"
            raise TypeError(msg)
        self.name = ""
        self.kind = kind
        self.state = value_state
        self._handle = handle
        self.record = record
        self.record_label = record_label
        self.model_type = cast(
            type[PresentationModel[Any]],
            type("PresentationValue", (PresentationModel,), {"value": value_state}),
        )

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    @overload
    def __get__(self, instance: None, owner: type | None = None) -> PresentationField[HandleT]: ...

    @overload
    def __get__(self, instance: Reactive, owner: type | None = None) -> HandleT: ...

    def __get__(self, instance: Reactive | None, owner: type | None = None) -> PresentationField[HandleT] | HandleT:
        if instance is None:
            return self
        reference = PresentationReference(instance, self)
        return self._handle(reference)

    def __set__(self, instance: Reactive, value: Never) -> None:
        msg = f"{self.name} is a presentation declaration; bind a control to change it"
        raise AttributeError(msg)

    def history(self, owner: Reactive) -> History | None:
        # History imports the factories; resolve its descriptor only after initialization.
        from squid_ui.runtime.histories import HistoryField

        target = self.record
        if target is None:
            return None
        if isinstance(target, HistoryField):
            return target.__get__(owner, type(owner))
        return target

    def validate(self, value: object) -> None:
        if self.kind == "toggle" and not isinstance(value, bool):
            msg = f"{self.name} must contain a bool"
            raise ValueError(msg)
        if self.kind == "choices" and (
            not isinstance(value, tuple) or any(not isinstance(item, str) for item in value)
        ):
            msg = f"{self.name} must contain a tuple of choice keys"
            raise ValueError(msg)


class PresentationScope:
    """One component placement, kept distinct even when a path is later reused."""

    def __init__(self, owner: Reactive, path: str, cells: PresentationCells) -> None:
        self.owner = owner
        self.path = path
        self.cells = cells
        self.models: dict[str, PresentationModel[Any]] = {}
        self.active = True
        self.histories: set[History] = set()
        self.saved = dict(cells.restored.get(path, {}))
        self.declarations: dict[str, PresentationField[Any]] = {}
        for base in reversed(type(owner).__mro__):
            for name, declaration in vars(base).items():
                if isinstance(declaration, PresentationField):
                    self.declarations[name] = declaration
                elif name in self.declarations:
                    del self.declarations[name]

    @contextmanager
    def activate(self, *, render: bool = False) -> Iterator[None]:
        if not self.active:
            msg = "presentation placement is no longer mounted"
            raise LayoutInvariantError(msg)
        with _CURRENT.set((self, render)):
            yield

    def resolve(self, owner: Reactive) -> PresentationScope:
        if owner is self.owner:
            return self
        ancestors = [
            scope
            for scope in self.cells.scopes.values()
            if scope.owner is owner and (scope.path == "$" or self.path.startswith(scope.path + "."))
        ]
        if len(ancestors) != 1:
            msg = "presentation reference needs an unambiguous ancestor placement"
            raise LayoutInvariantError(msg)
        return ancestors[0]

    def reveal(self, reference: ElementRef[object]) -> None:
        if not self.active or self.cells.on_reveal is None:
            message = "this presentation placement cannot request navigation"
            raise LayoutInvariantError(message)
        self.cells.on_reveal(self, reference)

    def bind[EventT](self, callback: Callable[[EventT], Awaitable[None]]) -> Callable[[EventT], Awaitable[None]]:
        """Retain interaction placement for an ad hoc form's later callback."""

        async def invoke(event: EventT) -> None:
            with self.activate():
                await callback(event)

        return invoke

    def model[ValueT](self, reference: PresentationReference[ValueT]) -> PresentationModel[ValueT]:
        if not self.active:
            msg = "presentation placement is no longer mounted"
            raise LayoutInvariantError(msg)
        name = reference.declaration.name
        if name not in self.models:
            declaration = reference.declaration
            model = declaration.model_type({"value": self.saved[name]} if name in self.saved else None)
            # Materialize the default before a transaction can record an inverse of absence.
            _ = model.value
            declaration.state.cell(model).reversible = declaration.record is not None
            saved = self.saved
            if name in saved:
                if not declaration.state.persist:
                    msg = f"presentation field {self.path}.{name} is not persistent"
                    raise ValueError(msg)
                declaration.validate(model.value)
            model._on_commit = self.cells.on_change
            self.models[name] = model
        return cast(PresentationModel[ValueT], self.models[name])

    def prepare(self) -> None:
        """Create defaults before handlers can first touch a cell inside their transaction."""
        for declaration in self.declarations.values():
            self.model(PresentationReference(self.owner, declaration, self))

    def close(self) -> None:
        self.active = False
        identities = set()
        for name, model in self.models.items():
            cell = self.declarations[name].state.cell(model)
            cell.active = False
            identities.add(f"slot:{cell.identity}")
        for stack in self.histories:
            stack.discard_cells(frozenset(identities))
        self.models.clear()


class PresentationCells:
    """Own placement cells and export only declared persistent values."""

    def __init__(self) -> None:
        self.on_change: Callable[[], None] | None = None
        self.on_reveal: Callable[[PresentationScope, ElementRef[object]], None] | None = None
        self.scopes: dict[tuple[Reactive, str], PresentationScope] = {}
        self._placements: dict[str, PresentationScope] = {}
        self.restored: dict[str, dict[str, Any]] = {}

    def placement(self, owner: Reactive, path: str) -> PresentationScope:
        key = (owner, path)
        if key not in self.scopes:
            scope = PresentationScope(owner, path, self)
            saved = self.restored.get(path, {})
            unknown = set(saved) - set(scope.declarations)
            if unknown:
                msg = f"snapshot has unknown presentation fields at {path}: {sorted(unknown)}"
                raise ValueError(msg)
            if any(not scope.declarations[name].state.persist for name in saved):
                msg = f"snapshot contains nonpersistent presentation fields at {path}"
                raise ValueError(msg)
            self.scopes[key] = scope
        return self.scopes[key]

    def for_key(self, key: str) -> PresentationScope | None:
        matches = [scope for scope in self._placements.values() if key.startswith(scope.path + ".")]
        if matches:
            return max(matches, key=lambda scope: len(scope.path))
        return self._placements.get("$")

    def select(self, components: Mapping[str, Reactive]) -> None:
        """Bind callbacks to the candidate's owners, including replacements at existing paths."""
        self._placements = {path: self.placement(owner, path) for path, owner in components.items()}

    def retain(self, components: Mapping[str, Reactive], *, committed: bool = True) -> bool:
        removed = [key for key in self.scopes if components.get(key[1]) is not key[0]]
        for key in removed:
            self.scopes.pop(key).close()
        self.select(components)
        if committed:
            self.restored.clear()
        return bool(removed)

    def export(self, components: Mapping[str, Reactive] | None = None) -> dict[str, dict[str, Any]]:
        values: dict[str, dict[str, Any]] = {}
        for scope in self.scopes.values():
            if components is not None and components.get(scope.path) is not scope.owner:
                continue
            saved = {}
            for name, declaration in scope.declarations.items():
                if declaration.state.persist:
                    reference = PresentationReference(scope.owner, declaration, scope)
                    saved[name] = export_state(scope.model(reference))["value"]
            if saved:
                values[scope.path] = saved
        return values

    def restore(self, values: Mapping[str, Mapping[str, Any]]) -> None:
        if self.scopes:
            msg = "restore presentation cells before rendering"
            raise RuntimeError(msg)
        self.restored = {path: dict(fields) for path, fields in values.items()}

    def inspect(self) -> dict[str, dict[str, CellReport]]:
        """Report cell identities and versions without reading their values."""
        return {
            scope.path: {name: inspect_cells(model)["value"] for name, model in scope.models.items()}
            for scope in self._placements.values()
            if scope.models
        }

    def validate_paths(self, components: Mapping[str, Reactive]) -> None:
        unknown = set(self.restored) - set(components)
        if unknown:
            msg = f"snapshot has unknown presentation placements: {sorted(unknown)}"
            raise ValueError(msg)


class PresentationReference[ValueT]:
    """A typed reference whose reads resolve in render or interaction scope."""

    def __init__(
        self, owner: Reactive, declaration: PresentationField[Any], scope: PresentationScope | None = None
    ) -> None:
        self.owner = owner
        self.declaration = declaration
        self._scope = scope
        current = _CURRENT.get()
        if scope is None and current is not None:
            with suppress(LayoutInvariantError):
                self._scope = current[0].resolve(owner)

    def resolve(self, scope: PresentationScope | None = None) -> PresentationScope:
        if self._scope is not None:
            return self._scope
        current = _CURRENT.get()
        scope = scope if scope is not None else None if current is None else current[0]
        if scope is None:
            msg = "presentation values require a render or interaction placement"
            raise LayoutInvariantError(msg)
        return scope.resolve(self.owner)

    def read(self, scope: PresentationScope | None = None) -> ValueT:
        resolved = self.resolve(scope)
        return resolved.model(self).value

    def write(self, value: ValueT, scope: PresentationScope | None = None) -> None:
        """Store a value in this placement's cell, recording it when the declaration has a history."""
        resolved = self.resolve(scope)
        with resolved.activate(), transaction() as tx:
            declaration = self.declaration
            stack = declaration.history(self.owner)
            if stack is not None:
                assert declaration.record_label is not None
                stack.record(tx, declaration.record_label)
                resolved.histories.add(stack)
            declaration.validate(value)
            model = resolved.model(self)
            if not declaration.state.cell(model).active:
                msg = "presentation cell has expired"
                raise LayoutInvariantError(msg)
            model.value = value

    @property
    def value(self) -> ValueT:
        current = _CURRENT.get()
        if current is None or not current[1]:
            msg = "read .value during render; use event.read(reference) in an interaction"
            raise LayoutInvariantError(msg)
        return self.read(current[0])


@dataclass(frozen=True, slots=True)
class ValueBinding[ValueT]:
    """One render's value and the placement reference that accepts its writes."""

    value: ValueT
    reference: PresentationReference[ValueT] = field(metadata={"stable_identity": False})

    @property
    def initial(self) -> ValueT:
        return self.value

    async def commit(self, event: ActionEvent[Any], value: ValueT) -> None:
        await event.acknowledge()
        self.reference.write(value)
        event.invalidate()


def presentation_scope() -> PresentationScope | None:
    current = _CURRENT.get()
    return None if current is None else current[0]


def presentation_mutations(owner: Reactive, collaborator: object) -> list[tuple[State[Any], PresentationModel[Any]]]:
    current = _CURRENT.get()
    if current is None:
        return []
    try:
        scope = current[0].resolve(owner)
    except LayoutInvariantError:
        return []
    found = []
    for name, declaration in scope.declarations.items():
        model = scope.models.get(name)
        if declaration.state.opaque and model is not None and model.value is collaborator:
            found.append((declaration.state, model))
    return found
