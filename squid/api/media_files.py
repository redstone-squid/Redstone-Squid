"""Serve stored media bytes through a private temporary file."""

import shutil
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from litestar.background_tasks import BackgroundTask
from litestar.response import File


async def media_file(fetch: Callable[[Path], Awaitable[str]], *, cache_control: str = "no-store") -> File:
    """Stream bytes that `fetch` copies to the given path, then delete the copy.

    `fetch` returns the content type, or raises to abort the response.
    """
    directory = Path(tempfile.mkdtemp(prefix="squid-media-"))
    path = directory / "content"
    try:
        content_type = await fetch(path)
    except BaseException:
        shutil.rmtree(directory)
        raise
    return File(
        path,
        media_type=content_type,
        content_disposition_type="inline",
        headers={"Cache-Control": cache_control, "X-Content-Type-Options": "nosniff"},
        background=BackgroundTask(shutil.rmtree, directory),
    )
