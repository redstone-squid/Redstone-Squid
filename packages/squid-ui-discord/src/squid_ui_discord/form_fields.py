"""Discord-specific form declarations and their submission value contracts."""

from dataclasses import dataclass
from typing import ClassVar, Literal, cast, override

from squid_ui.capabilities import Capability
from squid_ui.entity import EntityType
from squid_ui.forms import ChoiceOption, ExtensionField, FormField, FormValueError, MultiChoiceField, UploadedFile
from squid_ui.text import Text, TextLike


@dataclass(frozen=True, slots=True)
class EntityField[RequiredT: (Literal[True], Literal[False]) = Literal[True]](ExtensionField[object, RequiredT]):
    """A Discord user, role, channel, or mentionable picker."""

    entity_type: EntityType = EntityType.USER
    minimum: int = 1
    maximum: int = 1
    placeholder: TextLike | None = None
    capability: ClassVar[str] = Capability.FORMS_DISCORD_ENTITY

    def __post_init__(self) -> None:
        FormField.__post_init__(self)
        object.__setattr__(self, "placeholder", None if self.placeholder is None else Text(self.placeholder))

    @override
    def parse(self, raw: object) -> object | None:
        values = tuple(raw) if isinstance(raw, list | tuple) else (() if raw is None else (raw,))
        if not values:
            if self.required:
                message = "This field is required."
                raise FormValueError(message)
            return None if self.maximum == 1 else ()
        return values[0] if self.maximum == 1 else values


@dataclass(frozen=True, slots=True)
class FileField[RequiredT: (Literal[True], Literal[False]) = Literal[True]](ExtensionField[object, RequiredT]):
    """Modal uploads represented as metadata and lazily readable file contents.

    Parsing checks attachment counts. It does not download the bytes; callers explicitly
    read each returned UploadedFile when needed.
    """

    minimum: int = 1
    maximum: int = 1
    capability: ClassVar[str] = Capability.FORMS_DISCORD_FILE

    @override
    def parse(self, raw: object) -> UploadedFile | tuple[UploadedFile, ...] | None:
        values = tuple(raw) if isinstance(raw, list | tuple) else (() if raw is None else (raw,))
        if not values:
            if self.required:
                message = "This field is required."
                raise FormValueError(message)
            return None if self.maximum == 1 else ()
        if len(values) < self.minimum:
            message = f"Upload at least {self.minimum} files."
            raise FormValueError(message)
        if len(values) > self.maximum:
            message = f"Upload no more than {self.maximum} files."
            raise FormValueError(message)
        if not all(isinstance(value, UploadedFile) for value in values):
            message = "Discord file adapter submitted a non-upload value"
            raise TypeError(message)
        # Established by the `all(...)` above; neither checker narrows a tuple through it.
        uploads = cast(tuple[UploadedFile, ...], values)
        return uploads[0] if self.maximum == 1 else uploads


@dataclass(frozen=True, slots=True)
class CheckboxGroupField[ValueT, RequiredT: (Literal[True], Literal[False]) = Literal[True]](
    ExtensionField[tuple[ValueT, ...], RequiredT]
):
    """A Discord-native group of typed checkbox choices."""

    options: tuple[ChoiceOption[ValueT], ...] = ()
    minimum: int = 0
    maximum: int | None = None
    capability: ClassVar[str] = Capability.FORMS_DISCORD_CHECKBOX_GROUP

    def __post_init__(self) -> None:
        FormField.__post_init__(self)
        self._portable_field()

    @override
    def parse(self, raw: object) -> tuple[ValueT, ...]:
        """Parse selected keys with the portable multi-choice contract."""
        return self._portable_field().parse(raw)

    @override
    def format(self, value: object) -> tuple[str, ...]:
        """Format a prefill with the portable multi-choice contract."""
        return self._portable_field().format(value)

    def _portable_field(self) -> MultiChoiceField[ValueT, RequiredT]:
        """Build the portable value contract behind this native presentation."""
        return MultiChoiceField(
            label=self.label,
            key=self.key,
            description=self.description,
            required=self.required,
            default=self.default,
            options=self.options,
            minimum=self.minimum,
            maximum=self.maximum,
        )
