"""SQLAlchemy types for application persistence."""

from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import override

from sqlalchemy import DateTime, SmallInteger, Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator
from whenever import Instant

__all__ = ["InstantUTC", "IntEnumSmallInt", "StrEnumText"]


class InstantUTC(TypeDecorator[Instant]):
    """Store UTC timestamps while exposing them as Whenever instants.

    Adapted from Advanced Alchemy's ``DateTimeUTC`` type. See
    ``THIRD_PARTY_LICENSES/advanced-alchemy.txt`` for its MIT license.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    @property
    @override
    def python_type(self) -> type[Instant]:
        return Instant

    @override
    def process_bind_param(self, value: Instant | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.to_stdlib()

    @override
    def process_result_value(self, value: datetime | None, dialect: Dialect) -> Instant | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return Instant(value)


class StrEnumText[EnumT: StrEnum](TypeDecorator[EnumT]):
    """Store a `StrEnum` as plain text while exposing the member to the ORM.

    Deliberately not `sqlalchemy.Enum`: the shipped columns are `TEXT` guarded by
    check constraints, and a native PostgreSQL enum type would make every added
    member a migration. What this buys over a bare `Text` column is that a row read
    back is a member rather than a string the caller has to remember to re-wrap.
    """

    impl = Text
    cache_ok = True

    def __init__(self, enum_type: type[EnumT]) -> None:
        super().__init__()
        self._enum_type = enum_type

    @property
    @override
    def python_type(self) -> type[EnumT]:
        return self._enum_type

    @override
    def process_bind_param(self, value: EnumT | str | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return self._enum_type(value).value

    @override
    def process_result_value(self, value: str | None, dialect: Dialect) -> EnumT | None:
        if value is None:
            return None
        return self._enum_type(value)


class IntEnumSmallInt[EnumT: IntEnum](TypeDecorator[EnumT]):
    """Store an `IntEnum` as a small integer while exposing the member to the ORM.

    A bare `SmallInteger` column annotated `Mapped[SomeIntEnum]` reads back as a plain
    `int`: the explicit column type wins over the annotation, and nothing coerces. With
    an `IntEnum` that failure is close to silent, because every comparison against a
    member still comes out right; only member-only attributes like `.name` blow up, and
    only in whichever caller happens to want one. This type makes the annotation true.
    """

    impl = SmallInteger
    cache_ok = True

    def __init__(self, enum_type: type[EnumT]) -> None:
        super().__init__()
        self._enum_type = enum_type

    @property
    @override
    def python_type(self) -> type[EnumT]:
        return self._enum_type

    @override
    def process_bind_param(self, value: EnumT | int | None, dialect: Dialect) -> int | None:
        if value is None:
            return None
        return self._enum_type(value).value

    @override
    def process_result_value(self, value: int | None, dialect: Dialect) -> EnumT | None:
        if value is None:
            return None
        return self._enum_type(value)
