"""Sentinel values and types for the bot."""

import sys
from enum import Enum
from typing import override

_registry: dict[str, "Sentinel"] = {}


class Sentinel:
    """Unique sentinel values."""

    _name: str
    _repr: str
    _bool_value: bool
    _module_name: str

    def __new__(cls, name: str, repr: str | None = None, bool_value: bool = True, module_name: str | None = None):
        repr = repr if repr else f"<{name.split('.')[-1]}>"
        if module_name is None:
            try:
                module_name = sys._getframe(1).f_globals.get("__name__", "__main__")  # type: ignore
            except (AttributeError, ValueError):
                module_name = __name__
            assert module_name is not None

        registry_key = f"{module_name}-{name}"

        sentinel = _registry.get(registry_key, None)
        if sentinel is not None:
            return sentinel

        sentinel = super().__new__(cls)
        sentinel._name = name
        sentinel._repr = repr
        sentinel._bool_value = bool_value
        sentinel._module_name = module_name

        return _registry.setdefault(registry_key, sentinel)

    @override
    def __eq__(self, other: object) -> bool:
        return False

    @override
    def __repr__(self):
        return self._repr

    @override
    def __hash__(self) -> int:
        return 0

    def __bool__(self):
        return self._bool_value

    @override
    def __reduce__(self):
        return (
            self.__class__,
            (
                self._name,
                self._repr,
                self._module_name,
            ),
        )


class MissingType(Enum):
    MISSING = Sentinel("MISSING", repr="...")


class DefaultType(Enum):
    DEFAULT = Sentinel("DEFAULT")


MISSING = MissingType.MISSING
DEFAULT = DefaultType.DEFAULT
