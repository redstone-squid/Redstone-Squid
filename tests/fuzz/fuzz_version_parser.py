"""Coverage-guided fuzz harness for the public Minecraft version parser."""

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

with atheris.instrument_imports(include=["squid.services.versions"]):
    from squid.services.versions import MinecraftVersion, parse_version_string


def test_one_input(data: bytes) -> None:
    """Reject invalid versions and verify accepted input has a stable canonical form."""
    provider = atheris.FuzzedDataProvider(data)
    candidate = provider.ConsumeUnicodeNoSurrogates(4_096)
    try:
        edition, major, minor, patch = parse_version_string(candidate)
    except ValueError:
        return

    if edition not in {"Java", "Bedrock"} or min(major, minor, patch) < 0:
        raise AssertionError("accepted version violated its domain invariants")

    canonical = str(MinecraftVersion(edition, major, minor, patch))
    if parse_version_string(canonical) != (edition, major, minor, patch):
        raise AssertionError("accepted version did not survive canonical round-trip")


def main() -> None:
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
