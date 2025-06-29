"""Utility functions for the database module."""

import io
import os
import re
from datetime import datetime, timezone
from typing import Literal

import aiohttp

from squid.db.schema import Version

VERSION_PATTERN = re.compile(r"^\W*(Java|Bedrock)? ?(\d+)\.(\d+)\.(\d+)\W*$", re.IGNORECASE)


def utcnow() -> str:
    """Returns the current time in UTC in the format of a string."""
    current_utc = datetime.now(tz=timezone.utc)
    formatted_time = current_utc.strftime("%Y-%m-%dT%H:%M:%S")
    return formatted_time


async def upload_to_catbox(filename: str, file: bytes | io.BytesIO, mimetype: str) -> str:
    """Uploads a file to catbox.moe asynchronously.

    Args:
        filename: The name of the file.
        file: The file to upload.
        mimetype: The mimetype of the file.

    Returns:
        The link to the uploaded file.
    """
    catbox_url = "https://catbox.moe/user/api.php"
    userhash = os.getenv("CATBOX_USERHASH")

    data = aiohttp.FormData()
    data.add_field("reqtype", "fileupload")
    if userhash:
        data.add_field("userhash", userhash)
    data.add_field("fileToUpload", file, filename=filename, content_type=mimetype)

    async with aiohttp.ClientSession(trust_env=True) as session:
        async with session.post(catbox_url, data=data) as response:
            response_text = await response.text()
            return response_text


def get_version_string(version: Version, no_edition: bool = False) -> str:
    """Returns a formatted version string."""
    if no_edition:
        return f"{version.major_version}.{version.minor_version}.{version.patch_number}"
    else:
        return f"{version.edition} {version.major_version}.{version.minor_version}.{version.patch_number}"


def parse_version_string(version_string: str) -> tuple[Literal["Java", "Bedrock"], int, int, int]:
    """Parses a version string into its components. Defaults to Java edition if no edition is specified in the string.

    A version string is formatted as follows:
    ["Java" | "Bedrock"] major_version.minor_version.patch_number
    """

    match = VERSION_PATTERN.match(version_string)
    if not match:
        raise ValueError("Invalid version string format.")

    edition, major, minor, patch = match.groups()
    return edition or "Java", int(major), int(minor), int(patch)  # type: ignore
