"""Reversible cell changes and weak references to their live targets."""

import weakref
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from squid_reactivity.cells import _MISSING, _Cell
from squid_reactivity.errors import (
    ReactiveConflictError,
)
from squid_reactivity.transactions.results import (
    ConflictDetail,
)
from squid_reactivity.transactions.state import (
    ReactiveStateOwner,
)


@dataclass(frozen=True, slots=True)
class SlotValue:
    """A state slot value that distinguishes absence from a present ``None``."""

    present: bool
    value: Any = None

    @classmethod
    def from_raw(cls, value: Any) -> SlotValue:
        return cls(value is not _MISSING, None if value is _MISSING else value)

    def raw(self) -> Any:
        return self.value if self.present else _MISSING


@dataclass(frozen=True, slots=True, init=False)
class CellTarget:
    """Refer to a reversible state slot without keeping its owner alive.

    The opaque identity remains available after collection. History can therefore
    report which target expired even when the cell and its readable address are gone.
    """

    _owner: weakref.ReferenceType[ReactiveStateOwner]
    _cell: weakref.ReferenceType[_Cell]
    name: str
    identity: str

    def __init__(self, owner: ReactiveStateOwner, name: str, cell: _Cell) -> None:
        object.__setattr__(self, "_owner", weakref.ref(owner))
        object.__setattr__(self, "_cell", weakref.ref(cell))
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "identity", f"slot:{cell.identity}")

    def resolve(self, expected_version: int) -> tuple[ReactiveStateOwner, _Cell]:
        """Return the live slot or report an expired conditional inverse."""
        owner = self._owner()
        cell = self._cell()
        if owner is None or cell is None:
            detail = ConflictDetail(self.identity, expected_version, -1)
            raise ReactiveConflictError(detail, f"{self.identity} no longer exists; nothing was published")
        return owner, cell

    def live_cell(self) -> _Cell | None:
        """Return the slot for non-mutating diagnostics, or ``None`` after collection."""
        return self._cell()


@dataclass(frozen=True, slots=True)
class CellPatch:
    """One state replacement with exact before/after version lineage."""

    target: CellTarget
    before: SlotValue
    after: SlotValue
    before_version: int
    after_version: int

    def inverse(self) -> ConditionalCellPatch:
        return ConditionalCellPatch(self.target, self.before, self.after_version)


@dataclass(frozen=True, slots=True)
class ConditionalCellPatch:
    """A replacement applicable only while the target has the expected version."""

    target: CellTarget
    value: SlotValue
    expected_version: int


@dataclass(frozen=True, slots=True)
class CellPatchSet:
    """Immutable physical changes from one committed transaction."""

    patches: tuple[CellPatch, ...] = ()

    def __iter__(self) -> Iterator[CellPatch]:
        return iter(self.patches)

    def __len__(self) -> int:
        return len(self.patches)

    def addresses(self) -> tuple[Any, ...]:
        found: list[Any] = []
        seen: set[int] = set()
        for patch in self.patches:
            cell = patch.target.live_cell()
            address = None if cell is None else cell.address
            if address is None or id(address) in seen:
                continue
            seen.add(id(address))
            found.append(address)
        return tuple(found)

    def inverse(self) -> tuple[ConditionalCellPatch, ...]:
        return tuple(patch.inverse() for patch in reversed(self.patches))
