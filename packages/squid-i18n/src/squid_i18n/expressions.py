"""Typed formatting expressions captured by a message's Python template."""

import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from squid_i18n.errors import DeclarationError

type Numeric = int | float | Decimal
type Option = str | int | bool


@dataclass(frozen=True, slots=True)
class NumberExpression:
    value: Numeric
    options: tuple[tuple[str, Option], ...]


@dataclass(frozen=True, slots=True)
class DateTimeExpression:
    value: date | datetime
    options: tuple[tuple[str, Option], ...]


def NUMBER(
    value: Numeric,
    *,
    style: Literal["decimal", "percent", "currency"] = "decimal",
    currency: str | None = None,
    currency_display: Literal["symbol", "code", "name"] | None = None,
    use_grouping: bool | None = None,
    minimum_integer_digits: int | None = None,
    minimum_fraction_digits: int | None = None,
    maximum_fraction_digits: int | None = None,
    minimum_significant_digits: int | None = None,
    maximum_significant_digits: int | None = None,
) -> NumberExpression:
    """Defer locale-aware numeric formatting; options must be literals in tr()."""
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        msg = "NUMBER requires an integer, float, or Decimal"
        raise DeclarationError(msg)
    if (isinstance(value, float) and not math.isfinite(value)) or (
        isinstance(value, Decimal) and not value.is_finite()
    ):
        msg = "NUMBER requires a finite value"
        raise DeclarationError(msg)
    if style not in ("decimal", "percent", "currency") or (style == "currency" and not currency):
        msg = "Currency formatting requires a currency code"
        raise DeclarationError(msg)
    options: dict[str, Option | None] = {
        "style": style,
        "currency": currency,
        "currencyDisplay": currency_display,
        "useGrouping": use_grouping,
        "minimumIntegerDigits": minimum_integer_digits,
        "minimumFractionDigits": minimum_fraction_digits,
        "maximumFractionDigits": maximum_fraction_digits,
        "minimumSignificantDigits": minimum_significant_digits,
        "maximumSignificantDigits": maximum_significant_digits,
    }
    for name, option in options.items():
        if (
            "Digits" in name
            and option is not None
            and (isinstance(option, bool) or not isinstance(option, int) or option < 0)
        ):
            msg = "Digit options must be nonnegative integers"
            raise DeclarationError(msg)
    if (
        minimum_fraction_digits is not None
        and maximum_fraction_digits is not None
        and minimum_fraction_digits > maximum_fraction_digits
    ):
        msg = "Minimum fraction digits exceed maximum fraction digits"
        raise DeclarationError(msg)
    if (
        minimum_significant_digits is not None
        and maximum_significant_digits is not None
        and minimum_significant_digits > maximum_significant_digits
    ):
        msg = "Minimum significant digits exceed maximum significant digits"
        raise DeclarationError(msg)
    return NumberExpression(value, tuple((key, option) for key, option in options.items() if option is not None))


def DATETIME(
    value: date | datetime,
    *,
    date_style: Literal["full", "long", "medium", "short"] | None = None,
    time_style: Literal["full", "long", "medium", "short"] | None = None,
    time_zone: str | None = None,
) -> DateTimeExpression:
    """Defer date/time formatting without changing Discord timestamp semantics."""
    if not isinstance(value, date):
        msg = "DATETIME requires a date or datetime"
        raise DeclarationError(msg)
    if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
        msg = "DATETIME requires an aware datetime"
        raise DeclarationError(msg)
    if time_style is not None and not isinstance(value, datetime):
        msg = "A time style requires a datetime"
        raise DeclarationError(msg)
    options = (("dateStyle", date_style), ("timeStyle", time_style), ("timeZone", time_zone))
    return DateTimeExpression(value, tuple((key, option) for key, option in options if option is not None))


OPTION_NAMES = {
    "style": "style",
    "currency": "currency",
    "currency_display": "currencyDisplay",
    "use_grouping": "useGrouping",
    "minimum_integer_digits": "minimumIntegerDigits",
    "minimum_fraction_digits": "minimumFractionDigits",
    "maximum_fraction_digits": "maximumFractionDigits",
    "minimum_significant_digits": "minimumSignificantDigits",
    "maximum_significant_digits": "maximumSignificantDigits",
    "date_style": "dateStyle",
    "time_style": "timeStyle",
    "time_zone": "timeZone",
}
DEVELOPER_OPTIONS = frozenset({"style", "currency", "timeZone"})
