"""Portable form schemas, typed fields, and descriptor-based form sugar."""

import inspect
import math
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import date
from enum import StrEnum
from types import MappingProxyType
from typing import Any, ClassVar, NoReturn, Self, overload

from squid_layouts.actions import ActionPolicy, SubmitEvent
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.text import TextLike


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


class FormValidationPolicy(StrEnum):
    """What happens after a submitted form fails validation."""

    RETRY = "retry"
    ACCEPT_AND_MARK = "accept_and_mark"


class _FieldValueError(ValueError):
    """A user-correctable parse failure, converted to :class:`FieldError`."""


def _invalid(message: str) -> NoReturn:
    raise _FieldValueError(message)


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
        """Parse one submitted adapter value."""
        raise NotImplementedError

    def format_prefill(self, value: object) -> object:
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

    def parse(self, raw: object) -> int | None:
        if self._optional(raw):
            return None
        source = str(raw).strip()
        if self.parser is not None:
            value = self.parser(source)
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

    def format_prefill(self, value: object) -> object:
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

    def format_prefill(self, value: object) -> object:
        return value.isoformat() if isinstance(value, date) else value


@dataclass(frozen=True, slots=True)
class ChoiceOption[ValueT]:
    """One submitted key, reader-facing label, and typed value."""

    key: str
    label: TextLike
    value: ValueT
    description: TextLike | None = None


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

    def format_prefill(self, value: object) -> object:
        option = next((option for option in self.options if option.value == value or option.key == value), None)
        return option.key if option is not None else value


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
    fields: tuple[FormField[Any], ...]
    prefill: Mapping[str, object] = dataclass_field(default_factory=dict)
    validator: FormValidator | None = dataclass_field(default=None, repr=False, compare=False)
    validation_policy: FormValidationPolicy = FormValidationPolicy.RETRY

    def __post_init__(self) -> None:
        normalized = tuple(self.fields)
        keys = [field.key for field in normalized]
        if any(not key for key in keys):
            message = "FormSpec fields need explicit keys"
            raise ValueError(message)
        if len(set(keys)) != len(keys):
            message = f"FormSpec field keys must be unique: {keys!r}"
            raise ValueError(message)
        if any(field.label is None for field in normalized):
            message = "FormSpec fields need labels"
            raise ValueError(message)
        unknown = set(self.prefill) - set(keys)
        if unknown:
            message = f"FormSpec prefill contains unknown keys: {sorted(unknown)!r}"
            raise ValueError(message)
        object.__setattr__(self, "fields", normalized)
        object.__setattr__(self, "prefill", MappingProxyType(dict(self.prefill)))

    async def evaluate(self, attempted: Mapping[str, object]) -> FormEvaluation:
        """Parse every field, then run cross-field validation only after parsing succeeds."""
        raw = MappingProxyType(dict(attempted))
        values: dict[str, object] = {}
        errors: list[FormIssue] = []
        for field in self.fields:
            try:
                values[field.key] = field.parse(raw.get(field.key))
            except _FieldValueError as error:
                errors.append(FieldError(field.key, str(error)))
            except Exception as error:
                errors.append(FieldError(field.key, str(error) or type(error).__name__))
        if not errors and self.validator is not None:
            validated = self.validator(MappingProxyType(values))
            issues = await validated if inspect.isawaitable(validated) else validated
            errors.extend(issues)
        return FormEvaluation(MappingProxyType(values), raw, tuple(errors))

    def prefill_for(self, field: FormField[Any]) -> object:
        """Return the serialized prefill for one field."""
        value = self.prefill.get(field.key, field.default)
        return field.format_prefill(value)

    def with_prefill(self, values: Mapping[str, object]) -> FormSpec:
        """Return the same schema seeded with one attempted submission."""
        known = {field.key for field in self.fields}
        return replace(self, prefill={key: value for key, value in values.items() if key in known})

    def adapt(self, capabilities: frozenset[str], *, maximum_fields: int | None = None) -> FormSpec:
        """Resolve extension fallbacks and enforce a target's explicit form budget."""
        if maximum_fields is not None and not 1 <= len(self.fields) <= maximum_fields:
            message = f"form has {len(self.fields)} fields; target permits 1-{maximum_fields}"
            raise LayoutInvariantError(message)
        adapted: list[FormField[Any]] = []
        for field in self.fields:
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
        return replace(self, fields=tuple(adapted))


class Form:
    """Descriptor sugar that compiles typed class attributes into a :class:`FormSpec`."""

    title: ClassVar[TextLike] = "Form"
    validation_policy: ClassVar[FormValidationPolicy] = FormValidationPolicy.RETRY
    action_policy: ClassVar[ActionPolicy] = ActionPolicy.EXCLUSIVE
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
            validation_policy=self.validation_policy,
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


type FormLike = FormSpec | Form


def bind_form(form: FormLike, on_submit: SubmitHandler | None) -> tuple[FormSpec, SubmitHandler, ActionPolicy]:
    """Resolve value-layer and descriptor forms to one presentation binding."""
    if isinstance(form, Form):
        if on_submit is not None:
            message = "a Form instance owns its on_submit method"
            raise TypeError(message)
        return form.spec(), form._submit, form.action_policy
    if on_submit is None:
        message = "presenting a FormSpec requires on_submit"
        raise TypeError(message)
    return form, on_submit, ActionPolicy.EXCLUSIVE


# Short names stay available under ``sl.forms`` without colliding with semantic ``Choice``.
Text = TextField
TextArea = TextAreaField
Int = IntField
Float = FloatField
Duration = DurationField
Date = DateField
Choice = ChoiceField
Bool = BoolField
