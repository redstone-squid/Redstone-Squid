"""Classification of Discord attachments before anything is downloaded.

Discord reports a schematic's `content_type` as None or `application/octet-stream`, so the extension is the
primary signal and the content type is advisory. Nothing here reads the file: size comes from the attachment
metadata, and the byte-level check lives in `squid.schematics.domain.formats`.
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
    """Decide what an attachment is.

    Size is checked first; then a schematic extension wins regardless of content type; then an `image/` or
    `video/` type, guessed from the filename when Discord reported none.

    Raises:
        SchematicTooLargeError: the attachment is bigger than `max_bytes`.
        InvalidSchematicError: the attachment is none of the accepted kinds.
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
        # `end_user_action` is translated without parameters, so the accepted extensions ride in
        # `public_context` instead of being interpolated here.
        end_user_action=tr(t"Attach an image, a video, or a Minecraft schematic file."),
    )
