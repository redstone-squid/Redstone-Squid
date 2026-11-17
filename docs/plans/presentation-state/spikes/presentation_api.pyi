"""Proposed signatures only; no runtime implementation."""

from collections.abc import Callable
from typing import Never, Protocol, overload

from squid_ui.authoring.semantic import Choice, Choices, Toggle
from squid_ui.factories._inputs import TextValue
from squid_ui.runtime.histories import History

class HistoryField:
    @overload
    def __get__(self, instance: None, owner: type | None = None) -> HistoryField: ...
    @overload
    def __get__(self, instance: object, owner: type | None = None) -> History: ...

def history(*, limit: int = 20) -> HistoryField: ...

type RecordTarget = HistoryField | History | None

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
    @overload
    def __call__[ValueT](
        self,
        initial: ValueT,
        *,
        persist: bool = False,
        opaque: bool = False,
        record: RecordTarget = None,
        record_label: TextValue | None = None,
    ) -> PresentationField[ValueHandle[ValueT]]: ...
    @overload
    def __call__[ValueT](
        self,
        *,
        factory: Callable[[], ValueT],
        persist: bool = False,
        opaque: bool = False,
        record: RecordTarget = None,
        record_label: TextValue | None = None,
    ) -> PresentationField[ValueHandle[ValueT]]: ...
    def toggle(
        self,
        label: TextValue,
        *,
        initial: bool = False,
        persist: bool = False,
        record: RecordTarget = None,
        record_label: TextValue | None = None,
    ) -> PresentationField[ToggleHandle]: ...
    @overload
    def choices(
        self,
        first: Choice,
        /,
        *rest: Choice,
        initial: tuple[str, ...] = (),
        minimum: int = 1,
        maximum: int = 1,
        persist: bool = False,
        record: RecordTarget = None,
        record_label: TextValue | None = None,
    ) -> PresentationField[ChoicesHandle[ChoicesBinding]]: ...
    @overload
    def choices(
        self,
        *,
        initial: tuple[str, ...] = (),
        minimum: int = 1,
        maximum: int = 1,
        persist: bool = False,
        record: RecordTarget = None,
        record_label: TextValue | None = None,
    ) -> PresentationField[ChoicesHandle[ChoicesBuilder]]: ...

class EventReader(Protocol):
    def read[ValueT](self, reference: PresentationReference[ValueT]) -> ValueT: ...

presentation: PresentationFactory
