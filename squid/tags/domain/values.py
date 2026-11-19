"""Parse and format the scalar values assigned to valued tags."""

from decimal import Decimal, InvalidOperation
from typing import Protocol

from squid.core.errors import ValidationError
from squid.tags.domain.models import TagValue, TagValueType
from squid_i18n import md_tr


class ValuedTag(Protocol):
    """The parts of a tag definition that decide which values it accepts."""

    @property
    def display_name(self) -> str: ...

    @property
    def value_type(self) -> TagValueType: ...


def coerce_assignment_value(definition: ValuedTag, raw_value: str | None) -> TagValue:
    """Parse a tag value without converting units.

    Numeric values must be finite decimals in the tag's canonical unit. Boolean
    values accept true/false, yes/no, or 1/0 without regard to letter case.

    Raises:
        ValidationError: The tag takes no value and one was given, or it takes a value
            and `raw_value` is missing, blank, or not of its type.
    """
    display_name = definition.display_name
    if definition.value_type is TagValueType.NONE:
        if raw_value not in {None, ""}:
            raise ValidationError(md_tr(t"tags-does-not-accept-value = {display_name} does not accept a value."))
        return None
    if raw_value is None or not raw_value.strip():
        value_type = definition.value_type.value
        raise ValidationError(md_tr(t"tags-requires-value = {display_name} requires a {value_type} value."))
    value = raw_value.strip()
    if definition.value_type is TagValueType.TEXT:
        return value
    if definition.value_type is TagValueType.BOOLEAN:
        normalized = value.casefold()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
        raise ValidationError(md_tr(t"tags-expects-true-false = {display_name} expects true or false."))
    try:
        numeric = Decimal(value)
    except InvalidOperation as error:
        raise ValidationError(
            md_tr(
                t"tags-expects-number-canonical = {display_name} expects a number in its canonical unit.",
            )
        ) from error
    if not numeric.is_finite():
        raise ValidationError(md_tr(t"tags-expects-finite-number = {display_name} expects a finite number."))
    return numeric


def assignment_value(value: TagValue) -> str | None:
    """Format a stored value the way `coerce_assignment_value` reads it back."""
    if value is None:
        return None
    return str(value).lower() if isinstance(value, bool) else str(value)
