"""Classification of Discord attachments before anything is downloaded.

Discord reports `content_type=None` for `.litematic` files, which is why the submission flow
used to raise a bare `AssertionError` on them and why the message-scraping listener silently
dropped them. The rules here replace both: the **extension is the primary signal** and the
content type is advisory, because the one thing Discord will not tell us is exactly the thing
we care about.

Nothing here reads the file. Size is checked against the attachment metadata first, so an
oversized upload costs us no bandwidth at all, and the real content check happens afterwards
in :mod:`squid.schematics.domain.formats` against the bytes themselves.
"""

import mimetypes
from dataclasses import dataclass
from typing import Literal

from squid.core.i18n import _
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
    2. A schematic extension wins outright. Discord sends `None` or
       `application/octet-stream` for these, so waiting for a content type would reject every
       one of them.
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
        _("`{filename}` is not a file type this command accepts."),
        message_params={"filename": filename},
        context={"filename": filename, "content_type": content_type},
        public_context={"filename": filename, "accepted_extensions": list(ACCEPTED_EXTENSIONS)},
        # The accepted extensions are carried in the public context rather than interpolated
        # here: `end_user_action` is translated without parameters, so a formatted list would
        # come back with its placeholder intact in every locale but English.
        end_user_action=_("Attach an image, a video, or a Minecraft schematic file."),
    )
