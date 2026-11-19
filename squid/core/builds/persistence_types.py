"""Persistence conversion for versioned expandable-door source documents."""

from typing import override

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from squid.core.builds.domain.expansion import DoorExpansion


class DoorExpansionJSON(TypeDecorator[DoorExpansion]):
    """Validate expansion documents on writes and reads; None stores SQL NULL."""

    impl = JSONB(none_as_null=True)
    cache_ok = True

    @property
    @override
    def python_type(self) -> type[DoorExpansion]:
        return DoorExpansion

    @override
    def process_bind_param(self, value: DoorExpansion | None, dialect: Dialect) -> dict[str, object] | None:
        return DoorExpansion.model_validate(value).model_dump(mode="json") if value is not None else None

    @override
    def process_result_value(self, value: object, dialect: Dialect) -> DoorExpansion | None:
        return DoorExpansion.model_validate(value) if value is not None else None
