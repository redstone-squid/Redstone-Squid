"""Translate request decoder errors without copying submitted values or exception messages."""

from collections.abc import Sequence

from litestar.exceptions import ValidationException
from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError as PydanticValidationError

from squid.core.errors import ValidationError
from squid.core.validation import (
    FieldErrorCode,
    SimpleIssue,
    ValidationIssue,
)


class ValidationContext(BaseModel):
    model_config = ConfigDict(extra="allow")

    fields: tuple[ValidationIssue, ...]

    @classmethod
    def from_request_error(cls, error: ValidationException) -> ValidationContext:
        cause = error.__cause__
        while cause is not None:
            if isinstance(cause, PydanticValidationError):
                return cls(fields=ValidationError.from_schema(cause, prefix="/body/").field_issues)
            cause = cause.__cause__

        fields = []
        for item in error.extra or ():
            if isinstance(item, dict) and (key := item.get("key")) and key != "data":
                source = item.get("source", "body")
                field = _pointer(getattr(source, "value", source), str(key).split("."))
                fields.append(SimpleIssue(field=field, code=FieldErrorCode.INVALID_VALUE))
        return cls(fields=tuple(fields))


def _pointer(source: str, path: Sequence[str | int]) -> str:
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in (source, *path))
