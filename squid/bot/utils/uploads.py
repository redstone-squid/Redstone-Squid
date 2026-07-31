"""Discord transport file-upload helpers."""

import io

import aiohttp

from squid.config import CatboxConfig


async def upload_to_catbox(
    filename: str,
    file: bytes | io.BytesIO,
    mimetype: str,
    config: CatboxConfig,
) -> str:
    """Uploads a file to catbox.moe asynchronously.

    Args:
        filename: The name of the file.
        file: The file to upload.
        mimetype: The mimetype of the file.

    Returns:
        The link to the uploaded file.
    """
    catbox_url = "https://catbox.moe/user/api.php"

    data = aiohttp.FormData()
    data.add_field("reqtype", "fileupload")
    if config.user_hash is not None:
        data.add_field("userhash", config.user_hash.get_secret_value())
    data.add_field("fileToUpload", file, filename=filename, content_type=mimetype)

    async with aiohttp.ClientSession(trust_env=True) as session, session.post(catbox_url, data=data) as response:
        return await response.text()
