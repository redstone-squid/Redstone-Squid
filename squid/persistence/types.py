"""SQLAlchemy types for application persistence."""

from datetime import UTC, datetime
from typing import override

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator
from whenever import Instant

__all__ = ["InstantUTC"]


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
