"""Utility functions for the database module."""

import os
from datetime import datetime, timezone

import aiohttp
import requests
from requests_toolbelt import MultipartEncoder

from database.schema import VersionRecord


def utcnow() -> str:
    """Returns the current time in UTC in the format of a string."""
    current_utc = datetime.now(tz=timezone.utc)
    formatted_time = current_utc.strftime("%Y-%m-%dT%H:%M:%S")
    return formatted_time


async def upload_to_catbox(filename: str, file: bytes, mimetype: str) -> str:
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
    data.add_field('reqtype', 'fileupload')
    if userhash:
        data.add_field('userhash', userhash)
    data.add_field('fileToUpload', file, filename=filename, content_type=mimetype)

    async with aiohttp.ClientSession() as session:
        async with session.post(catbox_url, data=data) as response:
            response_text = await response.text()
            return response_text


def get_version_string(version: VersionRecord, no_edition: bool = False) -> str:
    """Returns a formatted version string."""
    if no_edition:
        return f"{version['major_version']}.{version['minor_version']}.{version['patch_number']}"
    else:
        return f"{version['edition']} {version['major_version']}.{version['minor_version']}.{version['patch_number']}"


def get_version_tuple(version: VersionRecord) -> tuple[str, int, int, int]:
    """Returns a version tuple."""
    return version["edition"], version["major_version"], version["minor_version"], version["patch_number"]


def parse_version_string(version_string: str) -> tuple[str, int, int, int]:
    """Parses a version string into its components.

    A version string is formatted as follows:
    ["Java" | "Bedrock"] major_version.minor_version.patch_number
    """

    try:
        edition_and_major, minor, patch = version_string.split(".")
    except ValueError:
        raise ValueError("Invalid version string format.")

    if " " in edition_and_major:
        edition, major = edition_and_major.split(" ")
    else:
        edition = "Java"
        major = edition_and_major

    return edition, int(major), int(minor), int(patch)
