"""External media references retained independently of uploaded submission artifacts."""

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from squid.core.errors import ValidationError

type SubmissionLinkType = Literal["image", "video", "world-download"]

LINK_FIELDS: dict[str, SubmissionLinkType] = {
    "image_urls": "image",
    "video_urls": "video",
    "world_download_urls": "world-download",
}


@dataclass(frozen=True, slots=True)
class SubmissionLink:
    """A public HTTP(S) reference, without claiming an uploaded or verified artifact."""

    url: str
    media_type: SubmissionLinkType

    def __post_init__(self) -> None:
        if not valid_link_url(self.url) or self.media_type not in LINK_FIELDS.values():
            message = "Submission links require an absolute HTTP(S) URL and a supported media type."
            raise ValidationError(message)


def valid_link_url(value: str) -> bool:
    """Accept absolute web URLs, rejecting whitespace and malformed host syntax."""
    if any(character.isspace() or ord(character) < 32 for character in value):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False
