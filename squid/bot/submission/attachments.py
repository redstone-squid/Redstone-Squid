"""Classify Discord attachments from metadata before downloading them.

Schematic extensions take precedence over content type, including missing or generic
content types. Classification does not validate bytes; callers must check downloaded
size and pass schematic contents through the schematic format validator.
"""

import mimetypes
from dataclasses import dataclass
from typing import Literal

from squid.core.i18n import tr
from squid.schematics.domain.formats import SCHEMATIC_EXTENSIONS
from squid.schematics.errors import InvalidSchematicError, SchematicTooLargeError

type AttachmentKind = Literal["image", "video", "schematic"]

ACCEPTED_EXTENSIONS: tuple[str, ...] = tuple(sorted(SCHEMATIC_EXTENSIONS))


@dataclass(frozen=True, slots=True)
class ClassifiedAttachment:
    """What one attachment is, decided without reading it."""

    kind: AttachmentKind
    filename: str
    content_type: str
    """The type to send onward. Synthesised when Discord reported none."""


def classify_attachment(filename: str, content_type: str | None, size: int, *, max_bytes: int) -> ClassifiedAttachment:
    """Classify an attachment within the declared size limit.

    Check schematic extensions first, then image/video content types. Guess a media
    type from the filename only when content_type is missing; an explicit generic
    content type does not receive that fallback.

    Raises:
        SchematicTooLargeError: Declared size exceeds max_bytes.
        InvalidSchematicError: Neither the extension nor content type is accepted.
    """
    if size > max_bytes:
        raise SchematicTooLargeError(actual=size, limit=max_bytes, measure="file size")

    lowered = filename.lower()
    for extension in SCHEMATIC_EXTENSIONS:
        if lowered.endswith(extension):
            return ClassifiedAttachment("schematic", filename, content_type or "application/octet-stream")

    resolved = content_type or mimetypes.guess_type(filename)[0]
    if resolved is not None:
        if resolved.startswith("image/"):
            return ClassifiedAttachment("image", filename, resolved)
        if resolved.startswith("video/"):
            return ClassifiedAttachment("video", filename, resolved)

    raise InvalidSchematicError(
        tr(t"`{filename}` is not a file type this command accepts."),
        context={"filename": filename, "content_type": content_type},
        public_context={"filename": filename, "accepted_extensions": list(ACCEPTED_EXTENSIONS)},
        # The accepted extensions are carried in the public context rather than interpolated
        # here: `end_user_action` is translated without parameters, so a formatted list would
        # come back with its placeholder intact in every locale but English.
        end_user_action=tr(t"Attach an image, a video, or a Minecraft schematic file."),
    )
