"""Classification of Discord attachments before anything is downloaded.

The accepted input contract permits `content_type=None` and `application/octet-stream`. Neither
value identifies a schematic format, so the **extension is the primary signal** and content type
is advisory. This module deliberately makes no claim about which Discord clients produce either
shape; repository tests prove only that both are handled consistently.

Nothing here reads the file. Size is checked against the attachment metadata first, so an
oversized upload costs us no bandwidth at all, and the real content check happens afterwards
in :mod:`squid.schematics.domain.formats` against the bytes themselves.
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
    """Decide what an attachment is, or raise a translated error explaining why we refuse it.

    Rules, in order:

    1. Size, before anything else, so we never download something we would reject.
    2. A schematic extension wins outright. A missing or generic content type cannot identify
       these formats, so it cannot be required for acceptance.
    3. An `image/` or `video/` content type, falling back to guessing from the filename when
       Discord reported nothing.
    4. Anything else is refused by name, listing what we do take.

    Raises:
        SchematicTooLargeError: the attachment is bigger than `max_bytes`.
        InvalidSchematicError: the attachment is not a type this application accepts.
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

    # `application/octet-stream` reaches here only without a schematic extension, i.e. an
    # opaque blob we have no reason to accept.
    raise InvalidSchematicError(
        tr(t"`{filename}` is not a file type this command accepts."),
        context={"filename": filename, "content_type": content_type},
        public_context={"filename": filename, "accepted_extensions": list(ACCEPTED_EXTENSIONS)},
        # The accepted extensions are carried in the public context rather than interpolated
        # here: `end_user_action` is translated without parameters, so a formatted list would
        # come back with its placeholder intact in every locale but English.
        end_user_action=tr(t"Attach an image, a video, or a Minecraft schematic file."),
    )
