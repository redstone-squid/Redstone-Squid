"""Declare forms and turn submitted control values into typed application values.

A FormSpec lists fields, initial values, and optional cross-field validation. The
frontend presents it using its own controls, then calls evaluate to parse a submission.
The result retains both parsed values and attempted input so invalid entries can be
shown again without losing what the reader typed.

Subclass Form to declare fields as class attributes and handle submissions in methods.
Its spec method builds the same FormSpec; after parsing, the attributes expose typed
values for validate and on_submit. The frontend owns presentation and retry handling.
"""

import inspect
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import UTC, date, tzinfo
from datetime import datetime as DateTimeValue
from datetime import time as TimeValue
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Literal, NoReturn, Self, cast, overload, override

from squid_i18n import md_tr, source_text, tr
from squid_ui.emoji import EmojiLike, normalize_emoji
from squid_ui.errors import LayoutInvariantError, SquidUiError
from squid_ui.interactions import ActionMode, InvalidSubmitEvent, SubmitEvent
from squid_ui.temporal import (
    AmbiguousLocalTimeError,
    AmbiguousTimeMode,
    InvalidTimezoneOffsetError,
    NonexistentLocalTimeError,
    NonexistentTimeMode,
    ZonedDateTime,
    resolve_local_datetime,
    resolve_offset_datetime,
    timezone_from_name,
)
from squid_ui.text import TextLike

if TYPE_CHECKING:
    from squid_ui.runtime.histories import History


@dataclass(frozen=True, slots=True, init=False)
class FieldError:
    """One validation message associated with one or more field declarations."""

    fields: tuple[FormField[Any], ...]
    message: TextLike

    def __init__(self, *fields: FormField[Any], message: TextLike) -> None:
        if not fields or any(not isinstance(field, FormField) for field in fields):
            error = "FieldError requires one or more form fields"
            raise TypeError(error)
        unique = tuple({id(field): field for field in fields}.values())
        object.__setattr__(self, "fields", unique)
        object.__setattr__(self, "message", message)


@dataclass(frozen=True, slots=True)
class FormError:
    """A validation error concerning the form as a whole."""

    message: TextLike


type FormIssue = FieldError | FormError


class FormValidationMode(StrEnum):
    """What happens after a submitted form fails validation."""

    RETRY = "retry"
    ACCEPT_AND_MARK = "accept_and_mark"


class FieldNotReadyError(SquidUiError, RuntimeError):
    """A form field was read before its submitted value parsed successfully."""


class FormValueError(SquidUiError, ValueError):
    """A user-correctable parse failure, converted to :class:`FieldError`.

    The one failure boundary :meth:`FormSpec.evaluate` treats as validation. Anything else a
    field raises is a bug, and propagates to the mount's error hook rather than being shown to
    the reader as if they had typed something wrong.
    """

    def __init__(self, message: TextLike) -> None:
        self.message = message
        super().__init__(source_text(message, format="plain"))


@dataclass(frozen=True, slots=True)
class FormText:
    """Static text placed in a form's declaration order."""

    content: TextLike


def _invalid(message: TextLike) -> NoReturn:
    raise FormValueError(message)


type PrefillValue = str | tuple[str, ...] | bool | int | float | None
"""Scalar or selected-key values accepted by adapters when prefilling a control."""


def _prefill(value: object) -> PrefillValue:
    """Return a supported scalar or tuple of string keys; unsupported stored values become None."""
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
        return cast(tuple[str, ...], value)
    return None


@dataclass(frozen=True, slots=True)
class FormField[ValueT, RequiredT: bool = bool](ABC):
    """One typed value in a portable form schema.

    The same object can be used dynamically in :class:`FormSpec` or as a descriptor on
    :class:`Form`. Descriptor fields acquire their key and fallback label from the attribute
    name when the class compiles its schema.
    """

    label: TextLike | None = None
    key: str = ""
    description: TextLike | None = None
    required: RequiredT = cast(RequiredT, True)  # noqa: FBT003 -- the cast preserves the generic constructor default.
    default: ValueT | None = None
    _name: str = dataclass_field(default="", init=False, repr=False, compare=False)
    _declaration: FormField[Any] | None = dataclass_field(default=None, repr=False, compare=False)

    def __set_name__(self, owner: type[Form], name: str) -> None:
        del owner
        object.__setattr__(self, "_name", name)

    @overload
    def __get__(self, instance: None, owner: type[Form]) -> Self: ...

    @overload
    def __get__(self: FormField[ValueT, Literal[True]], instance: Form, owner: type[Form]) -> ValueT: ...

    @overload
    def __get__(self, instance: Form, owner: type[Form]) -> ValueT | None: ...

    def __get__(self, instance: Form | None, owner: type[Form]) -> Self | ValueT | None:
        del owner
        if instance is None:
            return self
        if self._name not in instance._values:
            message = f"Form field {self._name!r} has no parsed value"
            raise FieldNotReadyError(message)
        return instance._values[self._name]

    def __set__(self, instance: Form, value: ValueT | None) -> None:
        if value is None and self.required:
            message = f"Required form field {self._name!r} cannot contain None"
            raise ValueError(message)
        instance._values[self._name] = value

    def bind(self, name: str) -> Self:
        """Fill descriptor-derived schema metadata without mutating the declaration."""
        label = self.label if self.label is not None else name.replace("_", " ").capitalize()
        # Pyrefly treats this stored field reference as an invoked descriptor.
        return replace(self, key=self.key or name, label=label, _declaration=self._declaration or self)  # pyrefly: ignore[no-matching-overload]

    @abstractmethod
    def parse(self, raw: object) -> ValueT | None:
        """Parse one submitted adapter value.

        Raise :class:`FormValueError` for anything the reader can correct. Every other
        exception is a programmer error and is left to propagate.
        """

    def format(self, value: object) -> PrefillValue:
        """Convert a stored value back to an adapter-neutral prefill value.

        Takes `object` rather than `ValueT | None` because prefills reach this from untyped
        stores and round-tripped payloads; every override narrows with `isinstance` before
        touching the value and leaves the rest to `_prefill`.
        """
        return _prefill(value)

    def _missing(self, raw: object) -> bool:
        return raw is None or raw == "" or raw == () or raw == []

    def _optional(self, raw: object) -> bool:
        if not self._missing(raw):
            return False
        if self.required:
            _invalid(tr(t"ui-form-required = This field is required."))
        return True


@dataclass(frozen=True, slots=True)
class TextField[RequiredT: (Literal[True], Literal[False]) = Literal[True]](FormField[str, RequiredT]):
    """A single-line text value."""

    placeholder: TextLike | None = None
    minimum: int | None = None
    maximum: int | None = None
    strip: bool = True

    @override
    def parse(self, raw: object) -> str | None:
        if self._optional(raw):
            return None
        value = str(raw)
        if self.strip:
            value = value.strip()
        if self.required and not value:
            _invalid(tr(t"ui-form-required = This field is required."))
        if self.minimum is not None and len(value) < self.minimum:
            minimum = self.minimum
            _invalid(md_tr(t"ui-form-text-minimum = Enter at least {minimum} characters."))
        if self.maximum is not None and len(value) > self.maximum:
            maximum = self.maximum
            _invalid(
                md_tr(
                    t"ui-form-text-maximum = Enter no more than {maximum} characters.",
                )
            )
        return value


@dataclass(frozen=True, slots=True)
class TextAreaField[RequiredT: (Literal[True], Literal[False]) = Literal[True]](TextField[RequiredT]):
    """A multi-line text value."""


@dataclass(frozen=True, slots=True)
class IntField[RequiredT: (Literal[True], Literal[False]) = Literal[True]](FormField[int, RequiredT]):
    """An integer constrained by optional inclusive bounds."""

    minimum: int | None = None
    maximum: int | None = None
    placeholder: TextLike | None = None

    @override
    def parse(self, raw: object) -> int | None:
        if self._optional(raw):
            return None
        try:
            value = int(str(raw).strip())
        except ValueError:
            _invalid(tr(t"ui-form-integer-format = Enter a whole number."))
        if self.minimum is not None and value < self.minimum:
            minimum = self.minimum
            _invalid(
                md_tr(
                    t"ui-form-number-minimum = Enter a value of at least {minimum}.",
                )
            )
        if self.maximum is not None and value > self.maximum:
            maximum = self.maximum
            _invalid(
                md_tr(
                    t"ui-form-number-maximum = Enter a value no greater than {maximum}.",
                )
            )
        return value


@dataclass(frozen=True, slots=True)
class FloatField[RequiredT: (Literal[True], Literal[False]) = Literal[True]](FormField[float, RequiredT]):
    """A finite floating-point number constrained by optional inclusive bounds."""

    minimum: float | None = None
    maximum: float | None = None
    placeholder: TextLike | None = None

    @override
    def parse(self, raw: object) -> float | None:
        if self._optional(raw):
            return None
        try:
            value = float(str(raw).strip())
        except ValueError:
            _invalid(tr(t"ui-form-number-format = Enter a number."))
        if not math.isfinite(value):
            _invalid(tr(t"ui-form-number-finite = Enter a finite number."))
        if self.minimum is not None and value < self.minimum:
            minimum = self.minimum
            _invalid(
                md_tr(
                    t"ui-form-number-minimum = Enter a value of at least {minimum}.",
                )
            )
        if self.maximum is not None and value > self.maximum:
            maximum = self.maximum
            _invalid(
                md_tr(
                    t"ui-form-number-maximum = Enter a value no greater than {maximum}.",
                )
            )
        return value


_DURATION = re.compile(r"^(\d+(?:\.\d+)?)\s*(s|m|h|d|w)$", re.IGNORECASE)
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


@dataclass(frozen=True, slots=True)
class DurationField[RequiredT: (Literal[True], Literal[False]) = Literal[True]](FormField[int, RequiredT]):
    """Parse compact durations into integer seconds, with optional inclusive bounds.

    The default grammar accepts one nonnegative decimal quantity followed by s, m, h, d, or w,
    ignoring unit case and surrounding whitespace. Fractional seconds use Python's
    rounding. A custom parser replaces the grammar and reports bad input with ValueError.
    """

    minimum: int | None = None
    maximum: int | None = None
    placeholder: TextLike | None = None
    parser: Callable[[str], int] | None = dataclass_field(default=None, repr=False, compare=False)
    """Replaces the compact-duration grammar; signals bad input with `ValueError`."""

    @override
    def parse(self, raw: object) -> int | None:
        if self._optional(raw):
            return None
        source = str(raw).strip()
        if self.parser is not None:
            # A custom parser states its complaint in the exception; `ValueError` is what a
            # domain parser naturally raises for input the reader can fix.
            try:
                value = self.parser(source)
            except ValueError as error:
                _invalid(str(error) or "Enter a valid duration.")
        else:
            match = _DURATION.fullmatch(source)
            if match is None:
                _invalid(tr(t"ui-form-duration-format = Enter a duration such as 30m, 12h, or 7d."))
            value = round(float(match.group(1)) * _DURATION_UNITS[match.group(2).lower()])
        if self.minimum is not None and value < self.minimum:
            minimum = self.minimum
            _invalid(
                md_tr(
                    t"ui-form-duration-minimum = Enter a duration of at least {minimum} seconds.",
                )
            )
        if self.maximum is not None and value > self.maximum:
            maximum = self.maximum
            _invalid(
                md_tr(
                    t"ui-form-duration-maximum = Enter a duration no longer than {maximum} seconds.",
                )
            )
        return value

    @override
    def format(self, value: object) -> PrefillValue:
        if not isinstance(value, int):
            return _prefill(value)
        for suffix, unit in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
            if value % unit == 0:
                return f"{value // unit}{suffix}"
        return f"{value}s"


@dataclass(frozen=True, slots=True)
class DateField[RequiredT: (Literal[True], Literal[False]) = Literal[True]](FormField[date, RequiredT]):
    """An ISO-8601 calendar date."""

    minimum: date | None = None
    maximum: date | None = None
    placeholder: TextLike | None = "YYYY-MM-DD"

    @override
    def parse(self, raw: object) -> date | None:
        if self._optional(raw):
            return None
        try:
            value = date.fromisoformat(str(raw).strip())
        except ValueError:
            _invalid(tr(t"ui-form-date-format = Enter a date as YYYY-MM-DD."))
        if self.minimum is not None and value < self.minimum:
            value = self.minimum.isoformat()
            _invalid(
                md_tr(
                    t"ui-form-date-minimum = Enter a date on or after {value}.",
                )
            )
        if self.maximum is not None and value > self.maximum:
            value = self.maximum.isoformat()
            _invalid(
                md_tr(
                    t"ui-form-date-maximum = Enter a date on or before {value}.",
                )
            )
        return value

    @override
    def format(self, value: object) -> PrefillValue:
        return value.isoformat() if isinstance(value, date) else _prefill(value)


@dataclass(frozen=True, slots=True)
class TimeField[RequiredT: (Literal[True], Literal[False]) = Literal[True]](FormField[TimeValue, RequiredT]):
    """An ISO-8601 wall-clock time with optional inclusive bounds."""

    minimum: TimeValue | None = None
    maximum: TimeValue | None = None
    placeholder: TextLike | None = "HH:MM"

    @override
    def parse(self, raw: object) -> TimeValue | None:
        if self._optional(raw):
            return None
        try:
            value = TimeValue.fromisoformat(str(raw).strip())
        except ValueError:
            _invalid(tr(t"ui-form-time-format = Enter a time as HH:MM."))
        if self.minimum is not None and value < self.minimum:
            value = self.minimum.isoformat()
            _invalid(
                md_tr(
                    t"ui-form-time-minimum = Enter a time at or after {value}.",
                )
            )
        if self.maximum is not None and value > self.maximum:
            value = self.maximum.isoformat()
            _invalid(
                md_tr(
                    t"ui-form-time-maximum = Enter a time at or before {value}.",
                )
            )
        return value

    @override
    def format(self, value: object) -> PrefillValue:
        return value.isoformat() if isinstance(value, TimeValue) else _prefill(value)


@dataclass(frozen=True, slots=True)
class DateTimeField[RequiredT: (Literal[True], Literal[False]) = Literal[True]](FormField[DateTimeValue, RequiredT]):
    """An ISO-8601 instant; naive input is resolved in ``timezone``.

    Ambiguous and nonexistent local times reject by default. Explicitly offset
    input already identifies an instant and does not use the local-time policies.
    """

    timezone: tzinfo = UTC
    minimum: DateTimeValue | None = None
    maximum: DateTimeValue | None = None
    placeholder: TextLike | None = "YYYY-MM-DD HH:MM"
    ambiguous: AmbiguousTimeMode = AmbiguousTimeMode.REJECT
    nonexistent: NonexistentTimeMode = NonexistentTimeMode.REJECT

    def __post_init__(self) -> None:
        if not isinstance(self.ambiguous, AmbiguousTimeMode):
            message = "DateTimeField ambiguous must be an AmbiguousTimeMode"
            raise TypeError(message)
        if not isinstance(self.nonexistent, NonexistentTimeMode):
            message = "DateTimeField nonexistent must be a NonexistentTimeMode"
            raise TypeError(message)
        for name, bound in (("minimum", self.minimum), ("maximum", self.maximum)):
            if bound is not None and (bound.tzinfo is None or bound.utcoffset() is None):
                message = f"DateTimeField {name} must be aware"
                raise ValueError(message)

    @override
    def parse(self, raw: object) -> DateTimeValue | None:
        if self._optional(raw):
            return None
        try:
            value = DateTimeValue.fromisoformat(str(raw).strip())
        except ValueError:
            _invalid(tr(t"ui-form-datetime-format = Enter a date and time as YYYY-MM-DD HH:MM."))
        if value.tzinfo is None or value.utcoffset() is None:
            try:
                value = resolve_local_datetime(value, self.timezone, self.ambiguous, self.nonexistent)
            except AmbiguousLocalTimeError:
                _invalid(
                    tr(
                        t"ui-form-time-ambiguous = This local time occurs "
                        t"twice in the selected timezone. Enter a date and time with an explicit UTC "
                        t"offset."
                    )
                )
            except NonexistentLocalTimeError:
                _invalid(tr(t"ui-form-time-nonexistent = This local time does not exist in the selected timezone."))
        instant = value.astimezone(UTC)
        if self.minimum is not None and instant < self.minimum.astimezone(UTC):
            value = self.minimum.isoformat()
            _invalid(
                md_tr(
                    t"ui-form-datetime-minimum = Enter a date and time on or after {value}.",
                )
            )
        if self.maximum is not None and instant > self.maximum.astimezone(UTC):
            value = self.maximum.isoformat()
            _invalid(
                md_tr(
                    t"ui-form-datetime-maximum = Enter a date and time on or before {value}.",
                )
            )
        return value

    @override
    def format(self, value: object) -> PrefillValue:
        return value.isoformat() if isinstance(value, DateTimeValue) else _prefill(value)


@dataclass(frozen=True, slots=True)
class ZonedDateTimeField[RequiredT: (Literal[True], Literal[False]) = Literal[True]](
    FormField[ZonedDateTime, RequiredT]
):
    """Resolve ISO datetime input in one named IANA timezone.

    Naive input follows the ambiguity and gap policies. Explicit offsets must agree
    with that wall time in the named zone. The result retains the zone and normalizes
    the instant to UTC; optional bounds compare instants inclusively.
    """

    timezone: str = "UTC"
    minimum: DateTimeValue | None = None
    maximum: DateTimeValue | None = None
    placeholder: TextLike | None = "YYYY-MM-DD HH:MM"
    ambiguous: AmbiguousTimeMode = AmbiguousTimeMode.REJECT
    nonexistent: NonexistentTimeMode = NonexistentTimeMode.REJECT

    def __post_init__(self) -> None:
        timezone_from_name(self.timezone)
        if not isinstance(self.ambiguous, AmbiguousTimeMode):
            message = "ZonedDateTimeField ambiguous must be an AmbiguousTimeMode"
            raise TypeError(message)
        if not isinstance(self.nonexistent, NonexistentTimeMode):
            message = "ZonedDateTimeField nonexistent must be a NonexistentTimeMode"
            raise TypeError(message)
        for name, bound in (("minimum", self.minimum), ("maximum", self.maximum)):
            if bound is not None and (bound.tzinfo is None or bound.utcoffset() is None):
                message = f"ZonedDateTimeField {name} must be aware"
                raise ValueError(message)

    @override
    def parse(self, raw: object) -> ZonedDateTime | None:
        if self._optional(raw):
            return None
        try:
            submitted = DateTimeValue.fromisoformat(str(raw).strip())
        except ValueError:
            _invalid(tr(t"ui-form-datetime-format = Enter a date and time as YYYY-MM-DD HH:MM."))
        timezone = timezone_from_name(self.timezone)
        try:
            if submitted.tzinfo is None or submitted.utcoffset() is None:
                resolved = resolve_local_datetime(submitted, timezone, self.ambiguous, self.nonexistent)
            else:
                resolved = resolve_offset_datetime(submitted, timezone)
        except AmbiguousLocalTimeError:
            _invalid(
                tr(
                    t"ui-form-time-ambiguous = This local time occurs twice "
                    t"in the selected timezone. Enter a date and time with an explicit UTC offset."
                )
            )
        except NonexistentLocalTimeError:
            _invalid(tr(t"ui-form-time-nonexistent = This local time does not exist in the selected timezone."))
        except InvalidTimezoneOffsetError:
            _invalid(
                tr(
                    t"ui-form-timezone-offset-mismatch = The UTC offset does not match this "
                    t"local time in the selected timezone."
                )
            )

        value = ZonedDateTime(resolved, self.timezone)
        if self.minimum is not None and value.instant < self.minimum.astimezone(UTC):
            value = self.minimum.isoformat()
            _invalid(
                md_tr(
                    t"ui-form-datetime-minimum = Enter a date and time on or after {value}.",
                )
            )
        if self.maximum is not None and value.instant > self.maximum.astimezone(UTC):
            value = self.maximum.isoformat()
            _invalid(
                md_tr(
                    t"ui-form-datetime-maximum = Enter a date and time on or before {value}.",
                )
            )
        return value

    @override
    def format(self, value: object) -> PrefillValue:
        if not isinstance(value, ZonedDateTime):
            return _prefill(value)
        return value.instant.astimezone(timezone_from_name(self.timezone)).isoformat()


@dataclass(frozen=True, slots=True)
class ScaleField[RequiredT: (Literal[True], Literal[False]) = Literal[True]](FormField[int, RequiredT]):
    """One point on an inclusive ordinal scale, such as a 1-5 rating.

    Portable by construction: a target with a radio-group shape draws the whole span, and one
    without draws a number the reader types. `labels` names individual points — the endpoints
    are the usual case — and unnamed points show their number.
    """

    minimum: int = 1
    maximum: int = 5
    labels: Mapping[int, TextLike] | None = None

    def __post_init__(self) -> None:
        if self.maximum <= self.minimum:
            message = f"ScaleField {self.key!r} needs maximum greater than minimum"
            raise ValueError(message)

    @property
    def points(self) -> tuple[int, ...]:
        """Every value on the scale, low to high."""
        return tuple(range(self.minimum, self.maximum + 1))

    def label_for(self, value: int) -> TextLike:
        """The reader-facing text for one point."""
        named = None if self.labels is None else self.labels.get(value)
        return str(value) if named is None else named

    @override
    def parse(self, raw: object) -> int | None:
        if self._optional(raw):
            return None
        try:
            value = int(str(raw).strip())
        except ValueError:
            minimum = self.minimum
            maximum = self.maximum
            _invalid(
                md_tr(
                    t"ui-form-scale-integer = Choose a whole number from {minimum} to {maximum}.",
                )
            )
        if not self.minimum <= value <= self.maximum:
            minimum = self.minimum
            maximum = self.maximum
            _invalid(
                md_tr(
                    t"ui-form-scale-range = Choose a value from {minimum} to {maximum}.",
                )
            )
        return value

    @override
    def format(self, value: object) -> str | None:
        # A string either way: it is the radio option's value and the text input's default.
        return None if value is None else str(value)


@dataclass(frozen=True, slots=True)
class ChoiceOption[ValueT]:
    """One submitted key, reader-facing label, and typed value."""

    key: str
    label: TextLike
    value: ValueT
    description: TextLike | None = None
    emoji: EmojiLike | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "emoji", normalize_emoji(self.emoji))


@dataclass(frozen=True, slots=True)
class ChoiceField[ValueT, RequiredT: (Literal[True], Literal[False]) = Literal[True]](FormField[ValueT, RequiredT]):
    """A single choice mapped from a stable submitted key to a typed value."""

    options: tuple[ChoiceOption[ValueT], ...] = ()

    def __post_init__(self) -> None:
        keys = [option.key for option in self.options]
        if len(set(keys)) != len(keys):
            message = f"ChoiceField option keys must be unique: {keys!r}"
            raise ValueError(message)

    @override
    def parse(self, raw: object) -> ValueT | None:
        if self._optional(raw):
            return None
        key = str(raw)
        option = next((option for option in self.options if option.key == key), None)
        if option is None:
            _invalid(tr(t"ui-form-invalid-choice = Choose one of the available options."))
        return option.value

    @override
    def format(self, value: object) -> PrefillValue:
        option = next((option for option in self.options if option.value == value or option.key == value), None)
        return option.key if option is not None else _prefill(value)


@dataclass(frozen=True, slots=True)
class MultiChoiceField[ValueT, RequiredT: (Literal[True], Literal[False]) = Literal[True]](
    FormField[tuple[ValueT, ...], RequiredT]
):
    """Return distinct selected values in declaration order.

    Repeated keys count once. Missing optional input returns an empty tuple; required
    input must select at least one option even when minimum is zero.
    """

    options: tuple[ChoiceOption[ValueT], ...] = ()
    minimum: int = 0
    maximum: int | None = None

    def __post_init__(self) -> None:
        keys = [option.key for option in self.options]
        if len(set(keys)) != len(keys):
            message = f"MultiChoiceField option keys must be unique: {keys!r}"
            raise ValueError(message)
        maximum = len(self.options) if self.maximum is None else self.maximum
        if self.minimum < 0 or maximum < self.minimum or maximum > len(self.options):
            message = "MultiChoiceField bounds must satisfy 0 <= minimum <= maximum <= len(options)"
            raise ValueError(message)

    @override
    def parse(self, raw: object) -> tuple[ValueT, ...]:
        if self._missing(raw):
            if self.required:
                _invalid(tr(t"ui-form-required = This field is required."))
            return ()
        submitted = tuple(str(value) for value in raw) if isinstance(raw, list | tuple) else (str(raw),)
        by_key = {option.key: option for option in self.options}
        if any(key not in by_key for key in submitted):
            _invalid(tr(t"ui-form-invalid-choices = Choose only from the available options."))
        selected = set(submitted)
        values = tuple(option.value for option in self.options if option.key in selected)
        if len(values) < self.minimum:
            minimum = self.minimum
            _invalid(
                md_tr(
                    t"ui-form-selection-minimum = Choose at least {minimum} options.",
                )
            )
        maximum = len(self.options) if self.maximum is None else self.maximum
        if len(values) > maximum:
            _invalid(md_tr(t"ui-form-selection-maximum = Choose no more than {maximum} options."))
        return values

    @override
    def format(self, value: object) -> tuple[str, ...]:
        submitted = tuple(value) if isinstance(value, list | tuple | set | frozenset) else (value,)
        return tuple(
            option.key
            for option in self.options
            if any(option.key == selected or option.value == selected for selected in submitted)
        )


@dataclass(frozen=True, slots=True)
class UploadedFile:
    """Portable metadata and byte access for a frontend upload."""

    name: str
    media_type: str
    size: int
    url: str
    read: Callable[[], Awaitable[bytes]] = dataclass_field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class BoolField[RequiredT: (Literal[True], Literal[False]) = Literal[True]](FormField[bool, RequiredT]):
    """Parse a checkbox value; missing input means False even when required is True."""

    @override
    def parse(self, raw: object) -> bool:
        if isinstance(raw, bool):
            return raw
        if raw is None or raw == "":
            return False
        normalized = str(raw).strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        _invalid(tr(t"ui-form-invalid-boolean = Choose yes or no."))


@dataclass(frozen=True, slots=True)
class ExtensionField[ValueT, RequiredT: bool = bool](FormField[ValueT, RequiredT]):
    """A frontend-specific field with an optional portable fallback."""

    fallback: FormField[ValueT] | None = None
    capability: ClassVar[str] = ""


@dataclass(frozen=True, slots=True)
class FormResult:
    """Parsed values, original attempted input, and errors from one submission.

    The values mapping contains only fields that parsed successfully. The attempted
    mapping retains raw input, including invalid values, so the frontend can prefill a retry.
    """

    values: Mapping[str, object]
    attempted: Mapping[str, object]
    errors: tuple[FormIssue, ...] = ()


type FormValidator = Callable[
    [Mapping[str, object]],
    Iterable[FormIssue] | Awaitable[Iterable[FormIssue]],
]
type SubmitHandler[ContextT = object] = Callable[[SubmitEvent[ContextT]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class FormSpec[ContextT = object]:
    """Fields and validation rules that a frontend uses to present and evaluate a form.

    Each field needs a unique key for submitted values and a label for display.
    The items sequence preserves display order, including static FormText between fields. Prefills
    override field defaults and are formatted for controls; they are not parsed until
    submission. evaluate parses each field before running the cross-field validator.
    """

    title: TextLike
    items: tuple[FormField[Any] | FormText, ...]
    prefill: Mapping[str, object] = dataclass_field(default_factory=dict)
    validator: FormValidator | None = dataclass_field(default=None, repr=False, compare=False)
    validation: FormValidationMode = FormValidationMode.RETRY
    on_invalid: Callable[[InvalidSubmitEvent[ContextT]], Awaitable[None]] | None = dataclass_field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        normalized = tuple(self.items)
        fields = tuple(item for item in normalized if isinstance(item, FormField))
        if not fields:
            message = "FormSpec needs at least one field"
            raise ValueError(message)
        keys = [field.key for field in fields]
        if any(not key for key in keys):
            message = "FormSpec fields need explicit keys"
            raise ValueError(message)
        if len(set(keys)) != len(keys):
            message = f"FormSpec field keys must be unique: {keys!r}"
            raise ValueError(message)
        if any(field.label is None for field in fields):
            message = "FormSpec fields need labels"
            raise ValueError(message)
        unknown = set(self.prefill) - set(keys)
        if unknown:
            message = f"FormSpec prefill contains unknown keys: {sorted(unknown)!r}"
            raise ValueError(message)
        object.__setattr__(self, "items", normalized)
        object.__setattr__(self, "prefill", MappingProxyType(dict(self.prefill)))

    @property
    def field_keys(self) -> tuple[str, ...]:
        """The submitted keys this schema parses, in declaration order."""
        return tuple(field.key for field in self.items if isinstance(field, FormField))

    async def evaluate(self, attempted: Mapping[str, object]) -> FormResult:
        """Parse all declared fields and return successful values alongside any validation issues.

        Only FormValueError becomes a field issue; unexpected parser exceptions propagate.
        Cross-field validation runs only if every field parsed. Missing keys are passed as
        None, while unknown keys remain in attempted but do not enter parsed values.
        """
        raw = MappingProxyType(dict(attempted))
        values: dict[str, object] = {}
        errors: list[FormIssue] = []
        for field in self.items:
            if not isinstance(field, FormField):
                continue
            try:
                values[field.key] = field.parse(raw.get(field.key))
            except FormValueError as error:
                errors.append(FieldError(field, message=error.message))
        if not errors and self.validator is not None:
            validated = self.validator(MappingProxyType(values))
            issues = await validated if inspect.isawaitable(validated) else validated
            for issue in issues:
                if isinstance(issue, FieldError):
                    resolved: list[FormField[Any]] = []
                    for reference in issue.fields:
                        matched = next(
                            (
                                item
                                for item in self.items
                                # Pyrefly treats this stored field reference as an invoked descriptor.
                                if isinstance(item, FormField) and (reference is item or reference is item._declaration)  # pyrefly: ignore[no-matching-overload]
                            ),
                            None,
                        )
                        if matched is None:
                            message = "FieldError references a field outside this form"
                            raise ValueError(message)
                        resolved.append(matched)
                    issue = FieldError(*resolved, message=issue.message)
                errors.append(issue)
        return FormResult(MappingProxyType(values), raw, tuple(errors))

    def prefill_for(self, field: FormField[Any]) -> PrefillValue:
        """Return the serialized prefill for one field."""
        value = self.prefill.get(field.key, field.default)
        return field.format(value)

    def with_prefill(self, values: Mapping[str, object]) -> FormSpec[ContextT]:
        """Return the same schema seeded with one attempted submission."""
        known = {field.key for field in self.items if isinstance(field, FormField)}
        return replace(self, prefill={key: value for key, value in values.items() if key in known})

    def adapt(self, capabilities: frozenset[str], *, maximum_fields: int | None = None) -> FormSpec[ContextT]:
        """Replace unsupported extension fields with one declared fallback.

        The optional component budget counts all items, including static FormText. Missing
        fallbacks raise LayoutInvariantError. Fallbacks retain the original key and field
        reference so cross-field validation can still identify the declaration.
        """
        if maximum_fields is not None and len(self.items) > maximum_fields:
            message = f"form has {len(self.items)} components; target permits 1-{maximum_fields}"
            raise LayoutInvariantError(message)
        adapted: list[FormField[Any] | FormText] = []
        for field in self.items:
            if isinstance(field, FormText):
                adapted.append(field)
                continue
            if not isinstance(field, ExtensionField) or field.capability in capabilities:
                adapted.append(field)
                continue
            # `FormField` is also the `Form` descriptor, so BasedPyright runs `__get__` over this
            # field's declared type; the slot on a frozen slotted dataclass holds a plain value.
            # Pyrefly treats this stored field reference as an invoked descriptor.
            declared: FormField[Any] | None = field.fallback  # pyright: ignore[reportAttributeAccessIssue]  # pyrefly: ignore[no-matching-overload]
            if declared is None:
                message = f"form field {field.key!r} requires unsupported capability {field.capability!r}"
                raise LayoutInvariantError(message)
            fallback = declared.bind(field.key)
            adapted.append(
                replace(
                    fallback,
                    key=field.key,
                    label=fallback.label if fallback.label is not None else field.label,
                    description=fallback.description if fallback.description is not None else field.description,
                    # Pyrefly treats this stored field reference as an invoked descriptor.
                    _declaration=field._declaration or field,  # pyrefly: ignore[no-matching-overload]
                )
            )
        return replace(self, items=tuple(adapted))


class Form[ContextT = object]:
    """Declare fields as class attributes and read their parsed values in form methods.

    For example, ``quantity = IntField(minimum=1)`` declares a required integer field.
    In validate or on_submit, ``self.quantity`` reads the submitted integer. On the
    class, the same attribute remains the field declaration for use in FieldError.

    Constructor values are raw prefills, not parsed values. Reading a field that has not
    parsed raises FieldNotReadyError. Missing optional values follow the field's contract:
    usually None, but False for BoolField and an empty tuple for MultiChoiceField.
    Validation runs after parsing; the frontend dispatches to on_submit or on_invalid.
    """

    title: ClassVar[TextLike] = "Form"
    validation: ClassVar[FormValidationMode] = FormValidationMode.RETRY
    action_mode: ClassVar[ActionMode] = ActionMode.EXCLUSIVE
    _form_fields: ClassVar[tuple[tuple[str, FormField[Any]], ...]] = ()

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        declarations: dict[str, FormField[Any]] = {}
        for base in reversed(cls.__mro__[1:]):
            declarations.update(getattr(base, "_form_fields", ()))
        for name, value in vars(cls).items():
            if isinstance(value, FormField):
                declarations[name] = value
            elif name in declarations:
                declarations.pop(name)
        cls._form_fields = tuple(declarations.items())

    def __init__(self, **prefill: object) -> None:
        fields = dict(self._form_fields)
        unknown = set(prefill) - set(fields)
        if unknown:
            message = f"unknown form fields: {sorted(unknown)!r}"
            raise TypeError(message)
        self._values: dict[str, Any] = {}
        self._prefill: dict[str, object] = {}
        for name, field in self._form_fields:
            if name in prefill:
                self._prefill[name] = prefill[name]
            elif field.default is not None:
                self._prefill[name] = field.default

    @property
    def prefill(self) -> Mapping[str, object]:
        """Raw initial values, keyed by attribute name; these have not been parsed."""
        return MappingProxyType(self._prefill)

    @classmethod
    def from_submission(cls, event: SubmitEvent[ContextT]) -> Self:
        """Bind an event with no reported errors to a new form instance.

        This does not parse or validate again. The caller must supply values already
        evaluated against this form's schema.
        """
        if event.errors:
            message = "Cannot bind an invalid submission as a validated form"
            raise ValueError(message)
        form = cls()
        form._bind(event.values)
        return form

    def spec(self) -> FormSpec[ContextT]:
        """Compile this instance's declarations and raw prefills."""
        fields = tuple(field.bind(name) for name, field in self._form_fields)
        prefill = {
            field.key: self._prefill[name]
            for (name, _declaration), field in zip(self._form_fields, fields, strict=True)
            if name in self._prefill
        }
        return FormSpec(
            self.title,
            fields,
            prefill,
            validator=self._validate_values,
            validation=self.validation,
            on_invalid=self._invalid,
        )

    def _bind(self, values: Mapping[str, object]) -> None:
        self._values.clear()
        for name, declaration in self._form_fields:
            key = declaration.key or name
            if key in values:
                setattr(self, name, values[key])

    async def _validate_values(self, values: Mapping[str, object]) -> Iterable[FormIssue]:
        self._bind(values)
        result = self.validate()
        return await result if inspect.isawaitable(result) else result

    async def _submit(self, event: SubmitEvent[ContextT]) -> None:
        if event.errors:
            message = "Invalid submissions must use on_invalid"
            raise ValueError(message)
        self._bind(event.values)
        await self.on_submit(event)

    async def _invalid(self, event: InvalidSubmitEvent[ContextT]) -> None:
        self._bind(event.values)
        await self.on_invalid(event)

    async def on_invalid(self, event: InvalidSubmitEvent[ContextT]) -> None:
        """Handle an invalid attempt; the default offers a retry with the attempted input."""
        await event.retry()

    def validate(self) -> Iterable[FormIssue] | Awaitable[Iterable[FormIssue]]:
        """Return cross-field errors after every field has parsed successfully."""
        return ()

    async def on_submit(self, event: SubmitEvent[ContextT]) -> None:
        """Handle a submission after parsing and cross-field validation succeed."""


@dataclass(frozen=True, slots=True)
class FormBinding[ContextT = object]:
    """One render's answer for a form key: what to present, and what to do with it.

    Declared by a `FormTrigger` and carried through planning so a frontend can resolve the
    newest one. A form presented ad hoc from a handler has no render-time binding, and so
    nothing newer to be rebased onto.
    """

    key: str
    spec: FormSpec[ContextT]
    on_submit: SubmitHandler[ContextT]
    mode: ActionMode = ActionMode.EXCLUSIVE
    label: TextLike = ""
    record: History | None = None


type FormLike[ContextT = object] = FormSpec[ContextT] | Form[ContextT]


def bind_form[ContextT = object](
    form: FormLike[ContextT], on_submit: SubmitHandler[ContextT] | None
) -> tuple[FormSpec[ContextT], SubmitHandler[ContextT], ActionMode]:
    """Return a schema, submission handler, and action mode for either form authoring style."""
    if isinstance(form, Form):
        if on_submit is not None:
            message = "a Form instance owns its on_submit method"
            raise TypeError(message)
        return form.spec(), form._submit, form.action_mode
    if on_submit is None:
        message = "presenting a FormSpec requires on_submit"
        raise TypeError(message)
    return form, on_submit, ActionMode.EXCLUSIVE


__all__ = [
    "AmbiguousTimeMode",
    "BoolField",
    "ChoiceField",
    "ChoiceOption",
    "DateField",
    "DateTimeField",
    "DurationField",
    "ExtensionField",
    "FieldError",
    "FieldNotReadyError",
    "FloatField",
    "Form",
    "FormBinding",
    "FormError",
    "FormField",
    "FormIssue",
    "FormLike",
    "FormResult",
    "FormSpec",
    "FormText",
    "FormValidationMode",
    "FormValidator",
    "FormValueError",
    "IntField",
    "MultiChoiceField",
    "NonexistentTimeMode",
    "PrefillValue",
    "ScaleField",
    "SubmitHandler",
    "TextAreaField",
    "TextField",
    "TimeField",
    "UploadedFile",
    "ZonedDateTimeField",
    "bind_form",
]
