"""Coverage-guided fuzz harness for the search cursor codec.

CursorCodec.decode() is the one place untrusted, attacker-controlled input (the `cursor` query
parameter) reaches the search stack directly. It must never raise anything other than
InvalidCursorError, no matter how malformed the token is.
"""

import importlib
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol, cast


class _FuzzedDataProvider(Protocol):
    def ConsumeUnicodeNoSurrogates(self, count: int) -> str: ...


class _Atheris(Protocol):
    def instrument_imports(self, *, include: list[str]) -> AbstractContextManager[None]: ...
    def FuzzedDataProvider(self, data: bytes) -> _FuzzedDataProvider: ...
    def Setup(self, args: list[str], test_one_input: Callable[[bytes], None]) -> None: ...
    def Fuzz(self) -> None: ...


atheris = cast(_Atheris, importlib.import_module("atheris"))

with atheris.instrument_imports(include=["squid.search.application"]):
    from squid.search.application import CursorCodec, InvalidCursorError

_SECRET = b"atheris fuzz harness cursor secret"
_codec = CursorCodec(_SECRET)


def test_one_input(data: bytes) -> None:
    """Decoding arbitrary text must never raise anything but InvalidCursorError."""
    provider = atheris.FuzzedDataProvider(data)
    candidate = provider.ConsumeUnicodeNoSurrogates(4_096)
    try:
        _codec.decode(candidate)
    except InvalidCursorError:
        return


def main() -> None:
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
