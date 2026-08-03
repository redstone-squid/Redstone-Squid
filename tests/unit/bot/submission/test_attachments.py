"""Attachment classification tests."""

import pytest

from squid.bot.submission.attachments import AttachmentKind, classify_attachment
from squid.schematics.errors import InvalidSchematicError, SchematicTooLargeError

MAX_BYTES = 2 * 1024 * 1024


@pytest.mark.parametrize(
    ("filename", "content_type", "expected"),
    [
        # Discord reports no content type for schematics at all, which is why the extension
        # has to be the primary signal rather than a tie-breaker.
        ("door.litematic", None, "schematic"),
        ("door.litematic", "application/octet-stream", "schematic"),
        ("DOOR.LITEMATIC", None, "schematic"),
        ("build.schem", None, "schematic"),
        ("build.schematic", None, "schematic"),
        ("structure.nbt", None, "schematic"),
        ("bedrock.mcstructure", None, "schematic"),
        ("screenshot.png", "image/png", "image"),
        ("clip.mp4", "video/mp4", "video"),
        # Falls back to guessing from the name when Discord tells us nothing.
        ("screenshot.png", None, "image"),
        ("clip.mp4", None, "video"),
        # A schematic renamed to look like an image is still typed by its extension here; the
        # content sniffer downstream is what refuses bytes that do not match.
        ("door.png.litematic", "image/png", "schematic"),
    ],
)
def test_classification(filename: str, content_type: str | None, expected: AttachmentKind) -> None:
    classified = classify_attachment(filename, content_type, 1024, max_bytes=MAX_BYTES)

    assert classified.kind == expected
    assert classified.filename == filename
    assert classified.content_type


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("payload.bin", "application/octet-stream"),
        ("archive.zip", "application/zip"),
        ("world.zip", None),
        ("notes.txt", "text/plain"),
        ("mystery", None),
    ],
)
def test_unsupported_attachments_are_refused_by_name(filename: str, content_type: str | None) -> None:
    with pytest.raises(InvalidSchematicError) as raised:
        classify_attachment(filename, content_type, 1024, max_bytes=MAX_BYTES)

    assert raised.value.public_context["filename"] == filename
    assert raised.value.public_context["accepted_extensions"]


def test_size_is_checked_before_anything_else_so_a_big_upload_is_never_downloaded() -> None:
    with pytest.raises(SchematicTooLargeError) as raised:
        classify_attachment("door.litematic", None, MAX_BYTES + 1, max_bytes=MAX_BYTES)

    assert raised.value.limit == MAX_BYTES


def test_an_oversized_unsupported_file_is_refused_for_its_size_not_its_type() -> None:
    """Size first, so the reason the user sees matches the check that actually ran."""
    with pytest.raises(SchematicTooLargeError):
        classify_attachment("payload.bin", "application/octet-stream", MAX_BYTES + 1, max_bytes=MAX_BYTES)


def test_a_content_type_never_promotes_an_unknown_extension_to_a_schematic() -> None:
    with pytest.raises(InvalidSchematicError):
        classify_attachment("door.dat", "application/x-minecraft-schematic", 1024, max_bytes=MAX_BYTES)
