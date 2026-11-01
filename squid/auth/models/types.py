"""Storage types for authentication values."""

from typing import override

from sqlalchemy import ARRAY, Text
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator

from squid.core.errors import DataIntegrityError
from squid.permissions.domain import InvalidPatternError, Pattern


class PermissionPatterns(TypeDecorator[frozenset[Pattern]]):
    """Store permission patterns as a sorted text array."""

    impl = ARRAY(Text)
    cache_ok = True

    @override
    def process_bind_param(self, value: frozenset[Pattern] | None, dialect: Dialect) -> list[str] | None:
        return None if value is None else sorted(pattern.raw for pattern in value)

    @override
    def process_result_value(self, value: list[str] | None, dialect: Dialect) -> frozenset[Pattern] | None:
        if value is None:
            return None
        try:
            return frozenset(Pattern.parse(raw) for raw in value)
        except InvalidPatternError as error:
            message = "A stored API key carries an unparsable permission pattern."
            raise DataIntegrityError(message) from error
