"""Utility functions for the database module."""

import os
import io
import re
from datetime import datetime, timezone
from collections.abc import Callable
from functools import wraps

import aiohttp

from squid.database.schema import VersionRecord


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


def parse_version_string(version_string: str) -> tuple[str, int, int, int]:
    """Parses a version string into its components.

    A version string is formatted as follows:
    ["Java" | "Bedrock"] major_version.minor_version.patch_number
    """

    pattern = r"^\s*(Java|Bedrock)? ?(\d+)\.(\d+)\.(\d+)\s*$"
    match = re.match(pattern, version_string)
    if not match:
        raise ValueError("Invalid version string format.")

    edition, major, minor, patch = match.groups()
    return edition or "Java", int(major), int(minor), int(patch)


SENTINEL = object()


def callable_cached[T](func: Callable[..., T]) -> Callable[..., T]:
    """
    beartype's fast memoization decorator.

    **Memoize** (i.e., efficiently re-raise all exceptions previously raised by
    the decorated callable when passed the same parameters (i.e., parameters
    that evaluate as equals) as a prior call to that callable if any *or* return
    all values previously returned by that callable otherwise rather than
    inefficiently recalling that callable) the passed callable.

    Specifically, this decorator (in order):

    #. Creates:

       * A local dictionary mapping parameters passed to this callable with the
         values returned by this callable when passed those parameters.
       * A local dictionary mapping parameters passed to this callable with the
         exceptions raised by this callable when passed those parameters.

    #. Creates and returns a closure transparently wrapping this callable with
       memoization. Specifically, this wrapper (in order):

       #. Tests whether this callable has already been called at least once
          with the passed parameters by lookup of those parameters in these
          dictionaries.
       #. If this callable previously raised an exception when passed these
          parameters, this wrapper re-raises the same exception.
       #. Else if this callable returned a value when passed these parameters,
          this wrapper re-returns the same value.
       #. Else, this wrapper:

          #. Calls that callable with those parameters.
          #. If that call raised an exception:

             #. Caches that exception with those parameters in that dictionary.
             #. Raises that exception.

          #. Else:

             #. Caches the value returned by that call with those parameters in
                that dictionary.
             #. Returns that value.

    Caveats
    -------
    **The decorated callable must accept no keyword parameters.** While this
    decorator previously memoized keyword parameters, doing so incurred
    significant performance penalties defeating the purpose of caching. This
    decorator now intentionally memoizes *only* positional parameters.

    **The decorated callable must accept no variadic positional parameters.**
    While memoizing variadic parameters would of course be feasible, this
    decorator has yet to implement support for doing so.

    **The decorated callable should not be a property method** (i.e., either a
    property getter, setter, or deleter subsequently decorated by the
    :class:`property` decorator). Technically, this decorator *can* be used to
    memoize property methods; pragmatically, doing so would be sufficiently
    inefficient as to defeat the intention of memoizing in the first place.

    Efficiency
    ----------
    For efficiency, consider calling the decorated callable with only:

    * **Hashable** (i.e., immutable) arguments. While technically supported,
      every call to the decorated callable passed one or more unhashable
      arguments (e.g., mutable containers like lists and dictionaries) will
      silently *not* be memoized. Equivalently, only calls passed only hashable
      arguments will be memoized. This flexibility enables decorated callables
      to accept unhashable PEP-compliant type hints. Although *all*
      PEP-noncompliant and *most* PEP-compliant type hints are hashable, some
      sadly are not. These include:

      * :pep:`585`-compliant type hints subscripted by one or more unhashable
        objects (e.g., ``collections.abc.Callable[[], str]``, the `PEP
        585`_-compliant type hint annotating piths accepting callables
        accepting no parameters and returning strings).
      * :pep:`586`-compliant type hints subscripted by an unhashable object
        (e.g., ``typing.Literal[[]]``, a literal empty list).
      * :pep:`593`-compliant type hints subscripted by one or more unhashable
        objects (e.g., ``typing.Annotated[typing.Any, []]``, the
        :attr:`typing.Any` singleton annotated by an empty list).

    **This decorator is intentionally not implemented in terms of the stdlib**
    :func:`functools.lru_cache` **decorator,** as that decorator is inefficient
    in the special case of unbounded caching with ``maxsize=None``. Why? Because
    that decorator insists on unconditionally recording irrelevant statistics
    like cache misses and hits. While bounding the number of cached values is
    advisable in the general case (e.g., to avoid exhausting memory merely for
    optional caching), parameters and returns cached by this package are
    sufficiently small in size to render such bounding irrelevant.

    Parameters
    ----------
    func : Callable[T]
        Callable to be memoized.

    Returns
    -------
    Callable[T]
        Closure wrapping this callable with memoization.

    Raises
    ------
    ValueError
        If this callable accepts a variadic positional parameter (e.g.,
        ``*args``).
    """
    assert callable(func), f"{repr(func)} not callable."

    args_flat_to_return_value: dict[tuple, object] = {}
    args_flat_to_return_value_get = args_flat_to_return_value.get

    args_flat_to_exception: dict[tuple, Exception] = {}
    args_flat_to_exception_get = args_flat_to_exception.get

    @wraps(func)
    def _callable_cached(*args):
        f"""
        Memoized variant of the {func.__name__}() callable.

        See Also
        --------
        :func:`.callable_cached`
            Further details.
        """

        args_flat = args[0] if len(args) == 1 else args

        try:
            exception = args_flat_to_exception_get(args_flat)
            if exception:
                raise exception

            return_value = args_flat_to_return_value_get(args_flat, SENTINEL)

            if return_value is not SENTINEL:
                return return_value

            try:
                return_value = args_flat_to_return_value[args_flat] = func(*args)
            except Exception as exception:
                args_flat_to_exception[args_flat] = exception
                raise exception

        except TypeError:
            return func(*args)

        return return_value

    return _callable_cached  # type: ignore[return-value]
