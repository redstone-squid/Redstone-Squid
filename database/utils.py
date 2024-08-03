"""Utility functions for the database module."""

import os
from datetime import datetime, timezone
from typing import Any

import requests
from requests_toolbelt import MultipartEncoder

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
        'reqtype': 'fileupload',
        'userhash': os.getenv('CATBOX_USERHASH'),
        'fileToUpload': (filename, file, mimetype)
    }
    encoder = MultipartEncoder(fields=data)
    response = requests.post(
        catbox_url,
        data=encoder,
        headers={'Content-Type': encoder.content_type}
    )

    return response.text
