import io
import os
from datetime import UTC, datetime

import aiohttp

from squid.versions.infrastructure.models import Version


def utcnow() -> str:
    """Returns the current time in UTC in the format of a string."""
    current_utc = datetime.now(tz=UTC)
    return current_utc.strftime("%Y-%m-%dT%H:%M:%S")


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

    async with aiohttp.ClientSession(trust_env=True) as session, session.post(catbox_url, data=data) as response:
        return await response.text()


def get_version_string(version: Version, no_edition: bool = False) -> str:
    """Returns a formatted version string."""
    if no_edition:
        return f"{version.major_version}.{version.minor_version}.{version.patch_number}"
    return f"{version.edition} {version.major_version}.{version.minor_version}.{version.patch_number}"
