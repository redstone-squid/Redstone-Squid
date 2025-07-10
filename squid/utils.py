"""Utility functions."""

import asyncio
import io
import os
import re
from collections.abc import Callable, Coroutine, AsyncIterator, AsyncIterable, Iterable
from datetime import UTC, datetime
from typing import Any, Literal

import aiohttp

from squid.db.schema import Version


_background_tasks: set[asyncio.Task] = set()
VERSION_PATTERN = re.compile(r"^\W*(Java|Bedrock)? ?(\d+)\.(\d+)\.(\d+)\W*$", re.IGNORECASE)


# https://stackoverflow.com/questions/74714300/paramspec-for-a-pre-defined-function-without-using-generic-callablep
def signature_from[**P, T](_original: Callable[P, T]) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Copies the signature of a function to another function."""

    def _decorator(func: Callable[P, T]) -> Callable[P, T]:
        return func

    return _decorator


def fire_and_forget(coro: Coroutine[None, None, Any]) -> None:
    """Runs a coroutine in the background without waiting for it to finish."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _aiterator[T](it: Iterable[T]) -> AsyncIterator[T]:
    for item in it:
        yield item


def async_iterator[T](it: Iterable[T] | AsyncIterable[T]) -> AsyncIterator[T]:
    """Wraps an Iterable or AsyncIterable into an AsyncIterator."""
    try:
        iterator = iter(it)  # pyright: ignore
        return _aiterator(iterator)
    except TypeError:
        # If it is an AsyncIterable, we can directly use it
        if isinstance(it, AsyncIterable):
            return it.__aiter__()
        else:
            raise TypeError(f"Expected Iterable or AsyncIterable, got {type(it)}")


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


def parse_version_string(version_string: str) -> tuple[Literal["Java", "Bedrock"], int, int, int]:
    """Parses a version string into its components. Defaults to Java edition if no edition is specified in the string.

    A version string is formatted as follows:
    ["Java" | "Bedrock"] major_version.minor_version.patch_number
    """

    match = VERSION_PATTERN.match(version_string)
    if not match:
        msg = "Invalid version string format."
        raise ValueError(msg)

    edition, major, minor, patch = match.groups()
    return edition or "Java", int(major), int(minor), int(patch)  # type: ignore


def parse_time_string(time_string: str | None) -> int | None:
    """Parses a time string into an integer.

    Args:
        time_string: The time string to parse.

    Returns:
        The time in ticks.
    """
    # TODO: parse "ticks"
    if time_string is None:
        return None
    time_string = time_string.replace("s", "").replace("~", "").strip()
    try:
        return int(float(time_string) * 20)
    except ValueError:
        return None
