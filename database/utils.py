"""Utility functions for the database module."""

import os
from datetime import datetime, timezone

import requests
from requests_toolbelt import MultipartEncoder

from database.schema import VersionsRecord


def utcnow() -> str:
    """Returns the current time in UTC in the format of a string."""
    current_utc = datetime.now(tz=timezone.utc)
    formatted_time = current_utc.strftime("%Y-%m-%dT%H:%M:%S")
    return formatted_time


# A minimal version of https://github.com/yukinotenshi/pyupload
def upload_to_catbox(filename: str, file: bytes, mimetype: str) -> str:
    """Uploads a file to catbox.moe.

    Args:
        filename: The name of the file.
        file: The file to upload.
        mimetype: The mimetype of the file.

    Returns:
        The link to the uploaded file.
    """
    catbox_url = "https://catbox.moe/user/api.php"
    data = {
        "reqtype": "fileupload",
        "userhash": os.getenv("CATBOX_USERHASH"),
        "fileToUpload": (filename, file, mimetype),
    }
    encoder = MultipartEncoder(fields=data)
    response = requests.post(catbox_url, data=encoder, headers={"Content-Type": encoder.content_type})

    return response.text


def get_version_string(version: VersionsRecord) -> str:
    """Returns a formatted version string."""
    if version["edition"] == "Java":
        return f"{version['major_version']}.{version['minor_version']}.{version['patch_number']}"
    else:
        return f"{version['edition']} {version['major_version']}.{version['minor_version']}.{version['patch_number']}"


def parse_version_string(version_string: str) -> tuple[str, str, str, str]:
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

    return edition, major, minor, patch
