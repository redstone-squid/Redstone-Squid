"""Declare readable runtime-owned values and controls on a component class."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, overload

from squid_i18n import Text
from squid_reactivity.internals import State
from squid_ui._presentation import PresentationField, PresentationReference, RecordTarget, ValueBinding
from squid_ui.authoring.semantic import Choice, Choices, Flexibility, Toggle, Tone
from squid_ui.factories._inputs import TextValue
from squid_ui.palette import Role


class ValueHandle[ValueT](PresentationReference[ValueT]):
    @property
    def bind(self) -> ValueBinding[ValueT]:
        return ValueBinding(self.value, self)


@dataclass(frozen=True, slots=True)
class ToggleBinding(Toggle[object]):
    def __call__(
        self,
        *,
        key: str | None = None,
        label: TextValue | None = None,
        available: bool | None = None,
        tone: Tone | Role | None = None,
    ) -> Toggle[object]:
        return replace(
            self,
            key=self.key if key is None else key,
            label=self.label if label is None else Text(label),
            available=self.available if available is None else available,
            tone=self.tone if tone is None else tone,
        )


class ToggleHandle(PresentationReference[bool]):
    def __init__(
        self,
        reference: PresentationReference[bool],
        label: TextValue,
        on_label: TextValue | None,
        off_label: TextValue | None,
        tone: Tone | Role,
        available: bool,
    ) -> None:
        super().__init__(reference.owner, reference.declaration, reference._scope)
        self.label = Text(label)
        self.on_label = None if on_label is None else Text(on_label)
        self.off_label = None if off_label is None else Text(off_label)
        self.tone = tone
        self.available = available

    @property
    def bind(self) -> ToggleBinding:
        return ToggleBinding(
            self.declaration.name,
            self.label,
            ValueBinding(self.value, self),
            self.on_label,
            self.off_label,
            self.tone,
            self.available,
        )


@dataclass(frozen=True, slots=True)
class ChoicesBinding(Choices[object]):
    def __call__(
        self,
        *entries: Choice,
        key: str | None = None,
        minimum: int | None = None,
        maximum: int | None = None,
        flexibility: Flexibility | None = None,
    ) -> Choices[object]:
        return replace(
            self,
            key=self.key if key is None else key,
            choices=entries or self.choices,
            minimum=self.minimum if minimum is None else minimum,
            maximum=self.maximum if maximum is None else maximum,
            flexibility=self.flexibility if flexibility is None else flexibility,
        )


@dataclass(frozen=True, slots=True)
class ChoicesBuilder:
    key: str
    selection: ValueBinding[tuple[str, ...]]
    minimum: int
    maximum: int
    flexibility: Flexibility

    def __call__(
        self,
        first: Choice,
        /,
        *rest: Choice,
        key: str | None = None,
        minimum: int | None = None,
        maximum: int | None = None,
        flexibility: Flexibility | None = None,
    ) -> Choices[object]:
        return Choices(
            self.key if key is None else key,
            (first, *rest),
            self.selection,
            self.minimum if minimum is None else minimum,
            self.maximum if maximum is None else maximum,
            self.flexibility if flexibility is None else flexibility,
        )


class ChoicesHandle[BindingT](PresentationReference[tuple[str, ...]]):
    def __init__(
        self,
        reference: PresentationReference[tuple[str, ...]],
        make_binding: Callable[[ValueBinding[tuple[str, ...]]], BindingT],
    ) -> None:
        super().__init__(reference.owner, reference.declaration, reference._scope)
        self._make_binding = make_binding

    @property
    def bind(self) -> BindingT:
        return self._make_binding(ValueBinding(self.value, self))


_MISSING = object()


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

    def __call__(
        self,
        initial: Any = _MISSING,
        *,
        factory: Callable[[], Any] | None = None,
        persist: bool = False,
        opaque: bool = False,
        record: RecordTarget = None,
        record_label: TextValue | None = None,
    ) -> PresentationField[ValueHandle[Any]]:
        """Declare a placement-owned value; choose an initial value or a factory."""
        if (initial is not _MISSING) == (factory is not None):
            msg = "presentation accepts exactly one initial value or factory"
            raise TypeError(msg)
        value_state = (
            State(initial, persist=persist, opaque=opaque)
            if initial is not _MISSING
            else State(factory=factory, persist=persist, opaque=opaque)
        )
        return PresentationField(
            value_state,
            lambda ref: ValueHandle(ref.owner, ref.declaration, ref._scope),
            record=record,
            record_label=record_label,
        )

    def toggle(
        self,
        label: TextValue,
        *,
        initial: bool = False,
        on_label: TextValue | None = None,
        off_label: TextValue | None = None,
        tone: Tone | Role = Tone.NEUTRAL,
        available: bool = True,
        persist: bool = False,
        record: RecordTarget = None,
        record_label: TextValue | None = None,
    ) -> PresentationField[ToggleHandle]:
        """Declare a boolean value and the toggle that changes it."""
        if not isinstance(initial, bool):
            msg = "a presentation toggle's initial value must be bool"
            raise TypeError(msg)
        return PresentationField(
            State(initial, persist=persist),
            lambda ref: ToggleHandle(ref, label, on_label, off_label, tone, available),
            record=record,
            record_label=label if record_label is None else record_label,
            kind="toggle",
        )

    @overload
    def choices(
        self,
        first: Choice,
        /,
        *rest: Choice,
        initial: tuple[str, ...] = (),
        minimum: int = 1,
        maximum: int = 1,
        flexibility: Flexibility = Flexibility.NORMAL,
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
        flexibility: Flexibility = Flexibility.NORMAL,
        persist: bool = False,
        record: RecordTarget = None,
        record_label: TextValue | None = None,
    ) -> PresentationField[ChoicesHandle[ChoicesBuilder]]: ...

    def choices(
        self,
        *entries: Choice,
        initial: tuple[str, ...] = (),
        minimum: int = 1,
        maximum: int = 1,
        flexibility: Flexibility = Flexibility.NORMAL,
        persist: bool = False,
        record: RecordTarget = None,
        record_label: TextValue | None = None,
    ) -> PresentationField[ChoicesHandle[ChoicesBinding]] | PresentationField[ChoicesHandle[ChoicesBuilder]]:
        """Declare choice state; absent options must be supplied when binding."""
        if not isinstance(initial, tuple) or any(not isinstance(key, str) for key in initial):
            msg = "initial choices must be a tuple of string keys"
            raise TypeError(msg)
        if not 0 <= minimum <= maximum or maximum < 1:
            msg = "choices require 0 <= minimum <= maximum and maximum >= 1"
            raise ValueError(msg)
        if entries:

            def complete(ref: PresentationReference[tuple[str, ...]]) -> ChoicesHandle[ChoicesBinding]:
                return ChoicesHandle(
                    ref,
                    lambda binding: ChoicesBinding(
                        ref.declaration.name, entries, binding, minimum, maximum, flexibility
                    ),
                )

            return PresentationField(
                State(initial, persist=persist), complete, record=record, record_label=record_label, kind="choices"
            )

        def incomplete(ref: PresentationReference[tuple[str, ...]]) -> ChoicesHandle[ChoicesBuilder]:
            return ChoicesHandle(
                ref,
                lambda binding: ChoicesBuilder(ref.declaration.name, binding, minimum, maximum, flexibility),
            )

        return PresentationField(
            State(initial, persist=persist), incomplete, record=record, record_label=record_label, kind="choices"
        )


presentation = PresentationFactory()
