"""Portable field specifications for build submission and editing."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from squid.bot.submission.input import split_values
from squid.bot.submission.parse import format_dimensions, parse_dimensions
from squid.builds.application.editing import BuildEditPatch
from squid.builds.domain import Build, BuildCategory
from squid.core.i18n import tr


@dataclass(frozen=True, slots=True)
class FieldFormat[ValueT]:
    """Parse and display one editor value with an explicit blank-value policy."""

    parser: Callable[[str], ValueT]
    formatter: Callable[[ValueT], str]
    required: bool = False


DIMENSIONS_FORMAT = FieldFormat[tuple[int | None, int | None, int | None]](
    parse_dimensions, format_dimensions, required=True
)
OPTIONAL_INT_FORMAT = FieldFormat[int | None](
    lambda text: int(text) if text else None,
    lambda value: "" if value is None else str(value),
)
OPTIONAL_TEXT_FORMAT = FieldFormat[str | None](lambda text: text or None, lambda value: value or "")
STRING_LIST_FORMAT = FieldFormat[list[str]](split_values, ", ".join, required=True)


class FieldDisplay(StrEnum):
    """Portable preference for a single-line or long-text form control."""

    TEXT = "text"
    PARAGRAPH = "paragraph"


@dataclass(frozen=True, slots=True)
class BuildFieldSpec[ValueT]:
    """Describe one typed patch field independently of a Discord form implementation."""

    key: str
    label: str
    parser: Callable[[str], ValueT]
    formatter: Callable[[ValueT], str]
    reader: Callable[[Build], ValueT]
    patch: Callable[[ValueT], BuildEditPatch]
    placeholder: str
    required: bool = False
    minimum: int | None = None
    maximum: int | None = None
    display: FieldDisplay = FieldDisplay.TEXT
    categories: frozenset[BuildCategory] | None = None

    def applies_to(self, build: Build) -> bool:
        """Whether this field belongs to the build's category."""
        return self.categories is None or build.category in self.categories

    def bind(self, build: Build) -> BoundBuildField[ValueT]:
        """Read this field from a build into a mutable editor value."""
        value = self.reader(build)
        text = "" if value is None else self.formatter(value)
        return BoundBuildField(self, value, text)


@dataclass(slots=True)
class BoundBuildField[ValueT]:
    """One screen-local value bound from a portable build field specification."""

    spec: BuildFieldSpec[ValueT]
    value: ValueT
    current_text: str
    modified: bool = False
    validation_error: str | None = None

    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def summary(self) -> str:
        return f"{self.spec.label}: {self.current_text}"

    def stage(self, text: str) -> None:
        """Parse and retain a proposed form value without mutating the build."""
        self.validation_error = None
        if text == self.current_text:
            return
        try:
            value = self.spec.parser(text)
        except ValueError as error:
            self.validation_error = str(error) or tr(tr(t"Invalid value"))
            return
        self.value = value
        self.current_text = text
        self.modified = True

    def to_patch(self) -> BuildEditPatch:
        """Return this parsed value as its typed patch fragment."""
        return self.spec.patch(self.value)


def field_spec[ValueT](
    key: str,
    format: FieldFormat[ValueT],
    placeholder: str,
    *,
    reader: Callable[[Build], ValueT],
    patch: Callable[[ValueT], BuildEditPatch],
    label: str | None = None,
    required: bool | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
    display: FieldDisplay = FieldDisplay.TEXT,
    categories: frozenset[BuildCategory] | None = None,
    parser: Callable[[str], ValueT] | None = None,
) -> BuildFieldSpec[ValueT]:
    """Describe a patch field with explicit typed read and patch operations."""
    return BuildFieldSpec(
        key=key,
        label=label or key.replace("_", " ").title(),
        parser=format.parser if parser is None else parser,
        formatter=format.formatter,
        reader=reader,
        patch=patch,
        placeholder=placeholder,
        required=format.required if required is None else required,
        minimum=minimum,
        maximum=maximum,
        display=display,
        categories=categories,
    )


__all__ = [
    "DIMENSIONS_FORMAT",
    "OPTIONAL_INT_FORMAT",
    "OPTIONAL_TEXT_FORMAT",
    "STRING_LIST_FORMAT",
    "BoundBuildField",
    "BuildFieldSpec",
    "FieldDisplay",
    "FieldFormat",
    "field_spec",
]
