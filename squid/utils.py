"""Utility functions."""

import asyncio
import io
import os
import re
from collections.abc import AsyncIterable, AsyncIterator, Callable, Coroutine, Iterable
from datetime import UTC, datetime
from dataclasses import field, fields
from typing import Any, Literal, overload, Self

import aiohttp

from squid.db.schema import Version


_background_tasks: set[asyncio.Task] = set()
VERSION_PATTERN = re.compile(r"^\W*(Java|Bedrock)? ?(\d+)\.(\d+)\.(\d+)\W*$", re.IGNORECASE)


# https://stackoverflow.com/questions/74714300/paramspec-for-a-pre-defined-function-without-using-generic-callablep
# Note: This is actually less accurate of a typing than Callable[P, T], but see
# https://github.com/microsoft/pyright/discussions/10727, pyright could not resolve overloads properly
def signature_from[Fn: Callable](_original: Fn) -> Callable[[Fn], Fn]:
    """Copies the signature of a function to another function."""

    def _decorator(func: Fn) -> Fn:
        return func

    return _decorator


def fire_and_forget(
    coro: Coroutine[None, None, Any], *, bg_set: set[asyncio.Task[Any] | Any] = _background_tasks
) -> None:
    """Runs a coroutine in the background without waiting for it to finish."""
    task = asyncio.create_task(coro)
    bg_set.add(task)
    task.add_done_callback(bg_set.discard)


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


class FrozenField[T]:
    """A descriptor that makes an attribute immutable after it has been set."""

    __slots__ = ("_private_name",)

    def __init__(self, name: str) -> None:
        self._private_name = "__frozen_" + name

    @overload
    def __get__(self, instance: None, owner: type[object]) -> Self: ...

    @overload
    def __get__(self, instance: object, owner: type[object]) -> T: ...

    def __get__(self, instance: object | None, owner: type[object] | None = None) -> T | Self:
        if instance is None:
            return self
        value = getattr(instance, self._private_name)
        return value

    def __set__(self, instance: object, value: T) -> None:
        if hasattr(instance, self._private_name):
            msg = f"Attribute `{self._private_name[1:]}` is immutable!"
            raise TypeError(msg) from None

        setattr(instance, self._private_name, value)


@signature_from(field)
def frozen_field(**kwargs: Any):
    """A field that is immutable after it has been set. See `dataclasses.field` for more information."""
    metadata = kwargs.pop("metadata", {}) | {"frozen": True}
    return field(**kwargs, metadata=metadata)


def freeze_fields[T](cls: type[T]) -> type[T]:
    """
    A decorator that makes fields of a dataclass immutable, if they have the `frozen` metadata set to True.

    This is done by replacing the fields with FrozenField descriptors.

    Args:
        cls: The class to make immutable, must be a dataclass.

    Raises:
        TypeError: If cls is not a dataclass
    """

    cls_fields = getattr(cls, "__dataclass_fields__", None)
    if cls_fields is None:
        raise TypeError(f"{cls} is not a dataclass")

    params = getattr(cls, "__dataclass_params__")
    # _DataclassParams(init=True,repr=True,eq=True,order=True,unsafe_hash=False,
    #                   frozen=True,match_args=True,kw_only=False,slots=False,
    #                   weakref_slot=False)
    if params.frozen:
        return cls

    for f in fields(cls):  # type: ignore
        if "frozen" in f.metadata:
            setattr(cls, f.name, FrozenField(f.name))
    return cls


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
