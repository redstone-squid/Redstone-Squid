"""Portable field specifications for build submission and editing."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from types import UnionType
from typing import TYPE_CHECKING, Any

from beartype.door import is_bearable

from squid.bot.submission.parse import get_formatter_and_parser_for_type
from squid.builds.application.editing import BuildEditPatch
from squid.builds.domain import Build, BuildCategory
from squid.core.i18n import tr

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _accepts(value: object, value_type: type[Any] | UnionType) -> bool:
    """Whether a runtime type hint accepts a value.

    A field's ``value_type`` is a runtime hint rather than a class -- ``str | None`` is a
    ``UnionType`` -- which is exactly what beartype takes, but neither checker's signature for
    ``is_bearable`` spells a union of the two. The suppression lives here rather than at every
    call site; ``squid.bot.submission.parse`` carries the same one at the same boundary.
    """
    return is_bearable(value, value_type)  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[bad-argument-type]


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
    value_type: type[ValueT] | UnionType
    placeholder: str
    required: bool = False
    minimum: int | None = None
    maximum: int | None = None
    display: FieldDisplay = FieldDisplay.TEXT
    categories: frozenset[BuildCategory] | None = None

    @classmethod
    def typed(
        cls,
        key: str,
        value_type: type[ValueT] | UnionType,
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
        """Build a specification from the shared formatter/parser registry."""
        formatter, default_parser = get_formatter_and_parser_for_type(
            # Same runtime-hint boundary as `_accepts`: the registry dispatches on hints, not classes.
            value_type  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[bad-argument-type]
        )
        return cls(
            key,
            label or key.replace("_", " ").title(),
            default_parser if parser is None else parser,
            formatter,
            reader,
            patch,
            value_type,
            placeholder,
            required=not _accepts(None, value_type) if required is None else required,
            minimum=minimum,
            maximum=maximum,
            display=display,
            categories=categories,
        )

    def applies_to(self, build: Build) -> bool:
        """Whether this field belongs to the build's category."""
        return self.categories is None or build.category in self.categories

    def bind(self, build: Build) -> BoundBuildField[ValueT]:
        """Read this field from a build into a mutable editor value."""
        value = self.reader(build)
        if not _accepts(value, self.value_type):
            logger.error("Invalid hint for %s: %s", self.key, type(value))
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
    value_type: type[ValueT] | UnionType,
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
    return BuildFieldSpec.typed(
        key,
        value_type,
        placeholder,
        reader=reader,
        patch=patch,
        label=label,
        required=required,
        minimum=minimum,
        maximum=maximum,
        display=display,
        categories=categories,
        parser=parser,
    )


__all__ = ["BoundBuildField", "BuildFieldSpec", "FieldDisplay", "field_spec"]
