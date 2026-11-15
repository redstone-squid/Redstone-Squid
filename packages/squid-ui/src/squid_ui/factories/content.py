"""Build text, status, time, and figure content."""

from datetime import datetime

from squid_i18n import Text
from squid_ui.assets import FileInput, file
from squid_ui.factories._inputs import TextValue
from squid_ui.palette import Role
from squid_ui.semantic import (
    Code,
    Emphasis,
    Figure,
    Heading,
    Importance,
    MediaItem,
    Metric,
    Note,
    Paragraph,
    ProgressBar,
    Quote,
    Status,
    Timestamp,
    TimeStyle,
    Tone,
    ZonedTimestamp,
)
from squid_ui.temporal import ZonedDateTime


def heading(content: TextValue, *, level: int = 2, importance: Importance = Importance.HIGH) -> Heading:
    """A standalone heading."""
    content = Text(content)
    return Heading(content, level, importance)


def paragraph(content: TextValue, *, importance: Importance = Importance.NORMAL, key: str | None = None) -> Paragraph:
    """A block of prose."""
    content = Text(content)
    return Paragraph(content, importance, key)


def status(content: TextValue, *, tone: Tone | Role = Tone.NEUTRAL, emphasis: Emphasis = Emphasis.NORMAL) -> Status:
    """A short outcome or state message."""
    content = Text(content)
    return Status(content, tone, emphasis)


def note(content: TextValue, *, importance: Importance = Importance.LOW) -> Note:
    """Small print: an id, a timestamp, a caveat the reader may skip."""
    content = Text(content)
    return Note(content, importance)


def code(content: str, *, language: str = "") -> Code:
    """Preformatted code or output."""
    return Code(content, language)


def quote(content: TextValue, *, attribution: TextValue | None = None) -> Quote:
    """Quoted text with optional attribution."""
    content = Text(content)
    attribution = None if attribution is None else Text(attribution)
    return Quote(content, attribution)


def progress(value: float, *, label: TextValue | None = None, maximum: float = 1.0) -> ProgressBar:
    """Completion of a known-length task."""
    label = None if label is None else Text(label)
    return ProgressBar(value, label, maximum)


def metric(value: int | float | str, label: TextValue, *, unit: str | None = None) -> Metric:
    """A single labelled quantity."""
    label = Text(label)
    return Metric(value, label, unit)


def timestamp(
    instant: datetime,
    *,
    style: TimeStyle = TimeStyle.SHORT_DATETIME,
    label: TextValue | None = None,
) -> Timestamp:
    """A typed instant; naive datetimes are rejected before rendering."""
    label = None if label is None else Text(label)
    if instant.tzinfo is None or instant.utcoffset() is None:
        message = "sl.timestamp() requires an aware datetime"
        raise ValueError(message)
    return Timestamp(instant, style, label)


def zoned_timestamp(value: ZonedDateTime, *, label: TextValue | None = None) -> ZonedTimestamp:
    """An exact instant displayed with its IANA timezone identity."""
    label = None if label is None else Text(label)
    return ZonedTimestamp(value, label)


def figure(
    source: MediaItem | FileInput,
    *,
    caption: TextValue | None = None,
    filename: str | None = None,
    media_type: str | None = None,
) -> Figure:
    """One media preview with an optional caption."""
    caption = None if caption is None else Text(caption)
    if isinstance(source, MediaItem) and (filename is not None or media_type is not None):
        message = "figure file metadata belongs on its media_item()"
        raise ValueError(message)
    resolved = source if isinstance(source, MediaItem) else media_item(source, filename=filename, media_type=media_type)
    return Figure(resolved, caption)


def media_item(
    source: FileInput,
    *,
    key: str = "",
    filename: str | None = None,
    media_type: str | None = None,
    description: TextValue | None = None,
    spoiler: bool = False,
) -> MediaItem:
    """Describe one gallery entry. Use this for per-item metadata in a gallery."""
    description = None if description is None else Text(description)
    asset = file(source, filename=filename, media_type=media_type)
    return MediaItem(key, asset.url or asset, description, spoiler)
