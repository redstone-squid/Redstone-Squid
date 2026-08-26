"""Portable form schemas, typed fields, and descriptor-based form sugar."""

import inspect
import math
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import UTC, date, tzinfo
from datetime import datetime as DateTimeValue
from datetime import time as TimeValue
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, NoReturn, Self, overload

from squid_layouts.emoji import EmojiLike, normalize_emoji
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.interactions import ActionMode, SubmitEvent
from squid_layouts.temporal import (
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
from squid_layouts.text import TextLike

if TYPE_CHECKING:
    from squid_layouts.runtime.histories import History


@dataclass(frozen=True, slots=True)
class FieldError:
    """A validation error attached to one field key."""

    key: str
    message: TextLike


@dataclass(frozen=True, slots=True)
class FormError:
    """A validation error concerning the form as a whole."""

    message: TextLike


type FormIssue = FieldError | FormError


class FormValidationMode(StrEnum):
    """What happens after a submitted form fails validation."""

    RETRY = "retry"
    ACCEPT_AND_MARK = "accept_and_mark"


class FormValueError(ValueError):
    """A user-correctable parse failure, converted to :class:`FieldError`.

    The one failure boundary :meth:`FormSpec.evaluate` treats as validation. Anything else a
    field raises is a bug, and propagates to the mount's error hook rather than being shown to
    the reader as if they had typed something wrong.
    """


@dataclass(frozen=True, slots=True)
class FormText:
    """Static text placed in a form's declaration order."""

    content: TextLike


def _invalid(message: str) -> NoReturn:
    raise FormValueError(message)


@dataclass(frozen=True, slots=True)
class FormField[ValueT]:
    """One typed value in a portable form schema.

    The same object can be used dynamically in :class:`FormSpec` or as a descriptor on
    :class:`Form`. Descriptor fields acquire their key and fallback label from the attribute
    name when the class compiles its schema.
    """

    label: TextLike | None = None
    key: str = ""
    description: TextLike | None = None
    required: bool = True
    default: ValueT | None = None
    _name: str = dataclass_field(default="", init=False, repr=False, compare=False)

    def __set_name__(self, owner: type[Form], name: str) -> None:
        del owner
        object.__setattr__(self, "_name", name)

    @overload
    def __get__(self, instance: None, owner: type[Form]) -> Self: ...

    @overload
    def __get__(self, instance: Form, owner: type[Form]) -> ValueT | None: ...

    def __get__(self, instance: Form | None, owner: type[Form]) -> Self | ValueT | None:
        del owner
        if instance is None:
            return self
        return instance.__dict__.get(self._name, self.default)

    def __set__(self, instance: Form, value: ValueT | None) -> None:
        instance.__dict__[self._name] = value

    def bind(self, name: str) -> Self:
        """Fill descriptor-derived schema metadata without mutating the declaration."""
        label = self.label if self.label is not None else name.replace("_", " ").capitalize()
        return replace(self, key=self.key or name, label=label)

    def parse(self, raw: object) -> ValueT | None:
        """Parse one submitted adapter value.

        Raise :class:`FormValueError` for anything the reader can correct. Every other
        exception is a programmer error and is left to propagate.
        """
        raise NotImplementedError

    def format(self, value: object) -> object:
        """Convert a typed value back to an adapter-neutral prefill value."""
        return value

    def _missing(self, raw: object) -> bool:
        return raw is None or raw == "" or raw == () or raw == []

    def _optional(self, raw: object) -> bool:
        if not self._missing(raw):
            return False
        if self.required:
            _invalid("This field is required.")
        return True


@dataclass(frozen=True, slots=True)
class TextField(FormField[str]):
    """A single-line text value."""

    placeholder: TextLike | None = None
    minimum: int | None = None
    maximum: int | None = None
    strip: bool = True

    def parse(self, raw: object) -> str | None:
        if self._optional(raw):
            return None
        value = str(raw)
        if self.strip:
            value = value.strip()
        if self.required and not value:
            _invalid("This field is required.")
        if self.minimum is not None and len(value) < self.minimum:
            _invalid(f"Enter at least {self.minimum} characters.")
        if self.maximum is not None and len(value) > self.maximum:
            _invalid(f"Enter no more than {self.maximum} characters.")
        return value


@dataclass(frozen=True, slots=True)
class TextAreaField(TextField):
    """A multi-line text value."""


@dataclass(frozen=True, slots=True)
class IntField(FormField[int]):
    """An integer constrained by optional inclusive bounds."""

    minimum: int | None = None
    maximum: int | None = None
    placeholder: TextLike | None = None

    def parse(self, raw: object) -> int | None:
        if self._optional(raw):
            return None
        try:
            value = int(str(raw).strip())
        except ValueError:
            _invalid("Enter a whole number.")
        if self.minimum is not None and value < self.minimum:
            _invalid(f"Enter a value of at least {self.minimum}.")
        if self.maximum is not None and value > self.maximum:
            _invalid(f"Enter a value no greater than {self.maximum}.")
        return value


@dataclass(frozen=True, slots=True)
class FloatField(FormField[float]):
    """A finite floating-point number constrained by optional inclusive bounds."""

    minimum: float | None = None
    maximum: float | None = None
    placeholder: TextLike | None = None

    def parse(self, raw: object) -> float | None:
        if self._optional(raw):
            return None
        try:
            value = float(str(raw).strip())
        except ValueError:
            _invalid("Enter a number.")
        if not math.isfinite(value):
            _invalid("Enter a finite number.")
        if self.minimum is not None and value < self.minimum:
            _invalid(f"Enter a value of at least {self.minimum}.")
        if self.maximum is not None and value > self.maximum:
            _invalid(f"Enter a value no greater than {self.maximum}.")
        return value


_DURATION = re.compile(r"^(\d+(?:\.\d+)?)\s*(s|m|h|d|w)$", re.IGNORECASE)
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


@dataclass(frozen=True, slots=True)
class DurationField(FormField[int]):
    """A compact duration such as ``30m``, parsed to seconds."""

    minimum: int | None = None
    maximum: int | None = None
    placeholder: TextLike | None = None
    parser: Callable[[str], int] | None = dataclass_field(default=None, repr=False, compare=False)
    """Replaces the compact-duration grammar; signals bad input with `ValueError`."""

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
                _invalid("Enter a duration such as 30m, 12h, or 7d.")
            value = round(float(match.group(1)) * _DURATION_UNITS[match.group(2).lower()])
        if self.minimum is not None and value < self.minimum:
            _invalid(f"Enter a duration of at least {self.minimum} seconds.")
        if self.maximum is not None and value > self.maximum:
            _invalid(f"Enter a duration no longer than {self.maximum} seconds.")
        return value

    def format(self, value: object) -> object:
        if not isinstance(value, int):
            return value
        for suffix, unit in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
            if value % unit == 0:
                return f"{value // unit}{suffix}"
        return f"{value}s"


@dataclass(frozen=True, slots=True)
class DateField(FormField[date]):
    """An ISO-8601 calendar date."""

    minimum: date | None = None
    maximum: date | None = None
    placeholder: TextLike | None = "YYYY-MM-DD"

    def parse(self, raw: object) -> date | None:
        if self._optional(raw):
            return None
        try:
            value = date.fromisoformat(str(raw).strip())
        except ValueError:
            _invalid("Enter a date as YYYY-MM-DD.")
        if self.minimum is not None and value < self.minimum:
            _invalid(f"Enter a date on or after {self.minimum.isoformat()}.")
        if self.maximum is not None and value > self.maximum:
            _invalid(f"Enter a date on or before {self.maximum.isoformat()}.")
        return value

    def format(self, value: object) -> object:
        return value.isoformat() if isinstance(value, date) else value


@dataclass(frozen=True, slots=True)
class TimeField(FormField[TimeValue]):
    """An ISO-8601 wall-clock time with optional inclusive bounds."""

    minimum: TimeValue | None = None
    maximum: TimeValue | None = None
    placeholder: TextLike | None = "HH:MM"

    def parse(self, raw: object) -> TimeValue | None:
        if self._optional(raw):
            return None
        try:
            value = TimeValue.fromisoformat(str(raw).strip())
        except ValueError:
            _invalid("Enter a time as HH:MM.")
        if self.minimum is not None and value < self.minimum:
            _invalid(f"Enter a time at or after {self.minimum.isoformat()}.")
        if self.maximum is not None and value > self.maximum:
            _invalid(f"Enter a time at or before {self.maximum.isoformat()}.")
        return value

    def format(self, value: object) -> object:
        return value.isoformat() if isinstance(value, TimeValue) else value


@dataclass(frozen=True, slots=True)
class DateTimeField(FormField[DateTimeValue]):
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

    def parse(self, raw: object) -> DateTimeValue | None:
        if self._optional(raw):
            return None
        try:
            value = DateTimeValue.fromisoformat(str(raw).strip())
        except ValueError:
            _invalid("Enter a date and time as YYYY-MM-DD HH:MM.")
        if value.tzinfo is None or value.utcoffset() is None:
            try:
                value = resolve_local_datetime(value, self.timezone, self.ambiguous, self.nonexistent)
            except AmbiguousLocalTimeError:
                _invalid(
                    "This local time occurs twice in the selected timezone. "
                    "Enter a date and time with an explicit UTC offset."
                )
            except NonexistentLocalTimeError:
                _invalid("This local time does not exist in the selected timezone.")
        instant = value.astimezone(UTC)
        if self.minimum is not None and instant < self.minimum.astimezone(UTC):
            _invalid(f"Enter a date and time on or after {self.minimum.isoformat()}.")
        if self.maximum is not None and instant > self.maximum.astimezone(UTC):
            _invalid(f"Enter a date and time on or before {self.maximum.isoformat()}.")
        return value

    def format(self, value: object) -> object:
        return value.isoformat() if isinstance(value, DateTimeValue) else value


@dataclass(frozen=True, slots=True)
class ZonedDateTimeField(FormField[ZonedDateTime]):
    """A local ISO-8601 datetime resolved in one named IANA timezone."""

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

    def parse(self, raw: object) -> ZonedDateTime | None:
        if self._optional(raw):
            return None
        try:
            submitted = DateTimeValue.fromisoformat(str(raw).strip())
        except ValueError:
            _invalid("Enter a date and time as YYYY-MM-DD HH:MM.")
        timezone = timezone_from_name(self.timezone)
        try:
            if submitted.tzinfo is None or submitted.utcoffset() is None:
                resolved = resolve_local_datetime(submitted, timezone, self.ambiguous, self.nonexistent)
            else:
                resolved = resolve_offset_datetime(submitted, timezone)
        except AmbiguousLocalTimeError:
            _invalid(
                "This local time occurs twice in the selected timezone. "
                "Enter a date and time with an explicit UTC offset."
            )
        except NonexistentLocalTimeError:
            _invalid("This local time does not exist in the selected timezone.")
        except InvalidTimezoneOffsetError:
            _invalid("The UTC offset does not match this local time in the selected timezone.")

        value = ZonedDateTime(resolved, self.timezone)
        if self.minimum is not None and value.instant < self.minimum.astimezone(UTC):
            _invalid(f"Enter a date and time on or after {self.minimum.isoformat()}.")
        if self.maximum is not None and value.instant > self.maximum.astimezone(UTC):
            _invalid(f"Enter a date and time on or before {self.maximum.isoformat()}.")
        return value

    def format(self, value: object) -> object:
        if not isinstance(value, ZonedDateTime):
            return value
        return value.instant.astimezone(timezone_from_name(self.timezone)).isoformat()


@dataclass(frozen=True, slots=True)
class ScaleField(FormField[int]):
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

    def parse(self, raw: object) -> int | None:
        if self._optional(raw):
            return None
        try:
            value = int(str(raw).strip())
        except ValueError:
            _invalid(f"Choose a whole number from {self.minimum} to {self.maximum}.")
        if not self.minimum <= value <= self.maximum:
            _invalid(f"Choose a value from {self.minimum} to {self.maximum}.")
        return value

    def format(self, value: object) -> object:
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
class ChoiceField[ValueT](FormField[ValueT]):
    """A single choice mapped from a stable submitted key to a typed value."""

    options: tuple[ChoiceOption[ValueT], ...] = ()

    def __post_init__(self) -> None:
        keys = [option.key for option in self.options]
        if len(set(keys)) != len(keys):
            message = f"ChoiceField option keys must be unique: {keys!r}"
            raise ValueError(message)

    def parse(self, raw: object) -> ValueT | None:
        if self._optional(raw):
            return None
        key = str(raw)
        option = next((option for option in self.options if option.key == key), None)
        if option is None:
            _invalid("Choose one of the available options.")
        return option.value

    def format(self, value: object) -> object:
        option = next((option for option in self.options if option.value == value or option.key == value), None)
        return option.key if option is not None else value


@dataclass(frozen=True, slots=True)
class MultiChoiceField[ValueT](FormField[tuple[ValueT, ...]]):
    """Several declared choices returned in declaration order."""

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

    def parse(self, raw: object) -> tuple[ValueT, ...]:
        if self._missing(raw):
            if self.required:
                _invalid("This field is required.")
            return ()
        submitted = tuple(str(value) for value in raw) if isinstance(raw, list | tuple) else (str(raw),)
        by_key = {option.key: option for option in self.options}
        if any(key not in by_key for key in submitted):
            _invalid("Choose only from the available options.")
        selected = set(submitted)
        values = tuple(option.value for option in self.options if option.key in selected)
        if len(values) < self.minimum:
            _invalid(f"Choose at least {self.minimum} options.")
        maximum = len(self.options) if self.maximum is None else self.maximum
        if len(values) > maximum:
            _invalid(f"Choose no more than {maximum} options.")
        return values

    def format(self, value: object) -> object:
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
class BoolField(FormField[bool]):
    """A boolean checkbox."""

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
        _invalid("Choose yes or no.")


@dataclass(frozen=True, slots=True)
class ExtensionField[ValueT](FormField[ValueT]):
    """A frontend-specific field with an optional portable fallback."""

    fallback: FormField[ValueT] | None = None
    capability: ClassVar[str] = ""


@dataclass(frozen=True, slots=True)
class FormEvaluation:
    """Typed values and validation errors from one submission attempt."""

    values: Mapping[str, object]
    attempted: Mapping[str, object]
    errors: tuple[FormIssue, ...] = ()


type FormValidator = Callable[
    [Mapping[str, object]],
    Iterable[FormIssue] | Awaitable[Iterable[FormIssue]],
]
type SubmitHandler = Callable[[SubmitEvent], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class FormSpec:
    """A frontend-neutral, immutable form schema."""

    title: TextLike
    items: tuple[FormField[Any] | FormText, ...]
    prefill: Mapping[str, object] = dataclass_field(default_factory=dict)
    validator: FormValidator | None = dataclass_field(default=None, repr=False, compare=False)
    validation: FormValidationMode = FormValidationMode.RETRY

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

    async def evaluate(self, attempted: Mapping[str, object]) -> FormEvaluation:
        """Parse every field, then run cross-field validation only after parsing succeeds."""
        raw = MappingProxyType(dict(attempted))
        values: dict[str, object] = {}
        errors: list[FormIssue] = []
        for field in self.items:
            if not isinstance(field, FormField):
                continue
            try:
                values[field.key] = field.parse(raw.get(field.key))
            except FormValueError as error:
                errors.append(FieldError(field.key, str(error)))
        if not errors and self.validator is not None:
            validated = self.validator(MappingProxyType(values))
            issues = await validated if inspect.isawaitable(validated) else validated
            errors.extend(issues)
        return FormEvaluation(MappingProxyType(values), raw, tuple(errors))

    def prefill_for(self, field: FormField[Any]) -> object:
        """Return the serialized prefill for one field."""
        value = self.prefill.get(field.key, field.default)
        return field.format(value)

    def with_prefill(self, values: Mapping[str, object]) -> FormSpec:
        """Return the same schema seeded with one attempted submission."""
        known = {field.key for field in self.items if isinstance(field, FormField)}
        return replace(self, prefill={key: value for key, value in values.items() if key in known})

    def adapt(self, capabilities: frozenset[str], *, maximum_fields: int | None = None) -> FormSpec:
        """Resolve extension fallbacks and enforce a target's explicit form budget."""
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
            if field.fallback is None:
                message = f"form field {field.key!r} requires unsupported capability {field.capability!r}"
                raise LayoutInvariantError(message)
            fallback = field.fallback.bind(field.key)
            adapted.append(
                replace(
                    fallback,
                    key=field.key,
                    label=fallback.label if fallback.label is not None else field.label,
                    description=fallback.description if fallback.description is not None else field.description,
                )
            )
        return replace(self, items=tuple(adapted))


class Form:
    """Descriptor sugar that compiles typed class attributes into a :class:`FormSpec`."""

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
        for name, field in self._form_fields:
            if name in prefill:
                setattr(self, name, prefill[name])
            elif field.default is not None:
                setattr(self, name, field.default)

    def spec(self) -> FormSpec:
        """Compile this instance's declarations and current values."""
        fields = tuple(field.bind(name) for name, field in self._form_fields)
        prefill = {
            field.key: self.__dict__[name]
            for (name, _declaration), field in zip(self._form_fields, fields, strict=True)
            if name in self.__dict__
        }
        return FormSpec(
            self.title,
            fields,
            prefill,
            validator=self._validate_values,
            validation=self.validation,
        )

    def _bind(self, values: Mapping[str, object]) -> None:
        for name, declaration in self._form_fields:
            key = declaration.key or name
            if key in values:
                setattr(self, name, values[key])

    async def _validate_values(self, values: Mapping[str, object]) -> Iterable[FormIssue]:
        self._bind(values)
        result = self.validate()
        return await result if inspect.isawaitable(result) else result

    async def _submit(self, event: SubmitEvent) -> None:
        self._bind(event.values)
        await self.on_submit(event)

    def validate(self) -> Iterable[FormIssue] | Awaitable[Iterable[FormIssue]]:
        """Return cross-field errors after every field has parsed successfully."""
        return ()

    async def on_submit(self, event: SubmitEvent) -> None:
        """Handle a successfully parsed submission, or an accepted invalid one."""


@dataclass(frozen=True, slots=True)
class FormBinding:
    """One render's answer for a form key: what to present, and what to do with it.

    Declared by a `FormTrigger` and carried through planning so a frontend can resolve the
    newest one. A form presented ad hoc from a handler has no render-time binding, and so
    nothing newer to be rebased onto.
    """

    key: str
    spec: FormSpec
    on_submit: SubmitHandler
    mode: ActionMode = ActionMode.EXCLUSIVE
    label: TextLike = ""
    record: History | None = None


type FormLike = FormSpec | Form


def bind_form(form: FormLike, on_submit: SubmitHandler | None) -> tuple[FormSpec, SubmitHandler, ActionMode]:
    """Resolve value-layer and descriptor forms to one presentation binding."""
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
    "FloatField",
    "Form",
    "FormBinding",
    "FormError",
    "FormEvaluation",
    "FormField",
    "FormIssue",
    "FormLike",
    "FormSpec",
    "FormText",
    "FormValidationMode",
    "FormValidator",
    "FormValueError",
    "IntField",
    "MultiChoiceField",
    "NonexistentTimeMode",
    "ScaleField",
    "SubmitHandler",
    "TextAreaField",
    "TextField",
    "TimeField",
    "UploadedFile",
    "ZonedDateTimeField",
    "bind_form",
]
