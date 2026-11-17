"""Proposed signatures only; no runtime implementation."""

from typing import Never, Protocol, overload

from squid_ui.authoring.semantic import Choice, Choices, Toggle
from squid_ui.factories._inputs import TextValue

class PresentationReference[ValueT]:
    @property
    def value(self) -> ValueT: ...

class ValueBinding[ValueT]:
    @property
    def initial(self) -> ValueT: ...
    def _commit(self, value: ValueT) -> None: ...

class ValueHandle[ValueT](PresentationReference[ValueT]):
    @property
    def bind(self) -> ValueBinding[ValueT]: ...

class ToggleBinding(Toggle[object]):
    def __call__(self, *, label: TextValue | None = None, available: bool = True) -> Toggle[object]: ...

class ToggleHandle(PresentationReference[bool]):
    @property
    def bind(self) -> ToggleBinding: ...

class ChoicesBuilder:
    def __call__(self, first: Choice, /, *rest: Choice) -> Choices[object]: ...

class ChoicesBinding(Choices[object]):
    def __call__(self, first: Choice, /, *rest: Choice) -> Choices[object]: ...

class ChoicesHandle[BindingT](PresentationReference[tuple[str, ...]]):
    @property
    def bind(self) -> BindingT: ...

class PresentationField[HandleT]:
    @overload
    def __get__(self, instance: None, owner: type | None = None) -> PresentationField[HandleT]: ...
    @overload
    def __get__(self, instance: object, owner: type | None = None) -> HandleT: ...
    def __set__(self, instance: object, value: Never) -> None: ...

class PresentationFactory:
    def __call__[ValueT](self, initial: ValueT) -> PresentationField[ValueHandle[ValueT]]: ...
    def toggle(self, label: TextValue, *, initial: bool = False) -> PresentationField[ToggleHandle]: ...
    @overload
    def choices(
        self,
        first: Choice,
        /,
        *rest: Choice,
        initial: tuple[str, ...] = (),
        minimum: int = 1,
        maximum: int = 1,
    ) -> PresentationField[ChoicesHandle[ChoicesBinding]]: ...
    @overload
    def choices(
        self,
        *,
        initial: tuple[str, ...] = (),
        minimum: int = 1,
        maximum: int = 1,
    ) -> PresentationField[ChoicesHandle[ChoicesBuilder]]: ...

class EventReader(Protocol):
    def read[ValueT](self, reference: PresentationReference[ValueT]) -> ValueT: ...

presentation: PresentationFactory
