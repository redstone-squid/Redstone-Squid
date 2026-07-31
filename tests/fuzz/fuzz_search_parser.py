"""Coverage-guided fuzz harness for the search query language parser.

SearchQueryParser tokenizes and parses raw search strings typed directly by Discord/API users.
It must never raise anything other than QuerySyntaxError, and any query it accepts must survive
being rendered back to its normalized form and re-parsed identically.
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
    from squid.search.application import QuerySyntaxError, SearchQueryParser


def test_one_input(data: bytes) -> None:
    """Reject invalid queries and verify accepted ones have a stable normalized form."""
    provider = atheris.FuzzedDataProvider(data)
    candidate = provider.ConsumeUnicodeNoSurrogates(4_096)
    try:
        query = SearchQueryParser().parse(candidate)
    except QuerySyntaxError:
        return

    if query.normalized:
        reparsed = SearchQueryParser().parse(query.normalized)
        if reparsed.normalized != query.normalized:
            raise AssertionError("accepted query did not survive normalized round-trip")


def main() -> None:
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
