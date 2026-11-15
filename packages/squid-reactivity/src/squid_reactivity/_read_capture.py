"""Capture the reads needed to reuse a computed result in another transaction.

These footprints are separate from reactive dependencies: untracked reads still
need transaction observations, while topic and signal reads only need version guards.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from squid_reactivity._cells import Signal, _Cell
    from squid_reactivity._core import _Derived, _ReusableResult
    from squid_reactivity.transactions._engine import _TransactionEngine


class ReadRule(Enum):
    """How a read's explicit scopes modify the eventual caller's consistency policy."""

    INHERIT = auto()
    STRONG = auto()
    RELAXED = auto()

    def under(self, outer: ReadRule) -> ReadRule:
        if self is ReadRule.RELAXED or outer is ReadRule.RELAXED:
            return ReadRule.RELAXED
        if self is ReadRule.STRONG or outer is ReadRule.STRONG:
            return ReadRule.STRONG
        return ReadRule.INHERIT


@dataclass(frozen=True, slots=True)
class ReadFootprint:
    """Version guards and policy-relative observations from one successful evaluation."""

    cells: tuple[tuple[_Cell[Any], int], ...]
    signals: tuple[tuple[Signal, int], ...]
    reads: tuple[tuple[_Cell[Any], ReadRule], ...]
    children: tuple[tuple[_Derived, ReadRule, _ReusableResult], ...]

    def current(self, transaction: _TransactionEngine | None) -> bool:
        """Check eligibility without evaluating dependencies or recording observations."""
        for cell, version in self.cells:
            if cell.version != version or (transaction is not None and transaction.staged(cell) is not None):
                return False
        return all(signal.version == version for signal, version in self.signals)

    def replay(self, transaction: _TransactionEngine, *, strong: bool, relaxed: bool) -> None:
        """Record validated reads while preserving the transaction's first observations."""
        if relaxed:
            return
        for cell, rule in self.reads:
            if rule is not ReadRule.RELAXED:
                transaction.observe(cell, strong=strong or rule is ReadRule.STRONG)


_MAX_FOOTPRINT_ENTRIES = 128


@dataclass(slots=True)
class ReadCapture:
    """Build a bounded footprint, abandoning reuse when any read cannot be represented."""

    cells: dict[_Cell[Any], int] = field(default_factory=dict)
    signals: dict[Signal, int] = field(default_factory=dict)
    reads: dict[tuple[_Cell[Any], ReadRule], None] = field(default_factory=dict)
    children: dict[tuple[_Derived, ReadRule], _ReusableResult] = field(default_factory=dict)
    supported: bool = True

    def finish(self) -> ReadFootprint | None:
        if not self.supported:
            return None
        return ReadFootprint(
            tuple(self.cells.items()),
            tuple(self.signals.items()),
            tuple(self.reads),
            tuple((node, rule, result) for (node, rule), result in self.children.items()),
        )

    def reject(self) -> None:
        self.supported = False
        self.cells.clear()
        self.signals.clear()
        self.reads.clear()
        self.children.clear()

    def cell(self, cell: _Cell[Any], version: int) -> None:
        if not self.supported:
            return
        if self.cells.setdefault(cell, version) != version:
            self.reject()
        self._check_size()

    def signal(self, signal: Signal, version: int) -> None:
        if not self.supported:
            return
        if self.signals.setdefault(signal, version) != version:
            self.reject()
        self._check_size()

    def read(self, cell: _Cell[Any], rule: ReadRule) -> None:
        if self.supported and cell.address is not None:
            self.reads[cell, rule] = None
            self._check_size()

    def _check_size(self) -> None:
        if len(self.cells) + len(self.signals) + len(self.reads) + len(self.children) > _MAX_FOOTPRINT_ENTRIES:
            self.reject()


_READ_CAPTURE: ContextVar[tuple[ReadCapture, ReadRule] | None] = ContextVar(
    "squid_reactivity_read_capture", default=None
)
"""The evaluation collecting reusable reads and its policy relative to its caller.

Nested evaluations collect independently and merge their finished footprints at the
call site. Unlike dependency capture, untracked scopes leave this capture active.
"""


@contextmanager
def capture_reads(*, enabled: bool = True) -> Iterator[ReadCapture | None]:
    if not enabled:
        unsupported_read()
        yield None
        return
    capture = ReadCapture()
    with _READ_CAPTURE.set((capture, ReadRule.INHERIT)):
        yield capture


@contextmanager
def read_rule(rule: ReadRule) -> Iterator[None]:
    current = _READ_CAPTURE.get()
    if current is None:
        yield
        return
    capture, outer = current
    with _READ_CAPTURE.set((capture, rule.under(outer))):
        yield


def unsupported_read() -> None:
    if current := _READ_CAPTURE.get():
        current[0].reject()


def capture_active() -> bool:
    return _READ_CAPTURE.get() is not None


def record_cell_guard(cell: _Cell[Any], version: int) -> None:
    if current := _READ_CAPTURE.get():
        current[0].cell(cell, version)


def record_cell_read(cell: _Cell[Any], version: int, *, staged: bool = False) -> None:
    current = _READ_CAPTURE.get()
    if current is None:
        return
    capture, rule = current
    if staged:
        capture.reject()
        return
    capture.cell(cell, version)
    capture.read(cell, rule)


def record_signal(signal: Signal, version: int) -> None:
    if current := _READ_CAPTURE.get():
        current[0].signal(signal, version)


def merge_reads(footprint: ReadFootprint | None) -> None:
    """Include a nested computed's reads under the policy at its call site."""
    current = _READ_CAPTURE.get()
    if current is None:
        return
    capture, outer = current
    if footprint is None:
        capture.reject()
        return
    for cell, version in footprint.cells:
        capture.cell(cell, version)
    for signal, version in footprint.signals:
        capture.signal(signal, version)
    for cell, rule in footprint.reads:
        capture.read(cell, rule.under(outer))


def merge_computed(node: _Derived, result: _ReusableResult | None) -> None:
    """Keep a nested result's graph and merge its guards and observations."""
    current = _READ_CAPTURE.get()
    if current is None:
        return
    capture, rule = current
    if result is None:
        capture.reject()
        return
    merge_reads(result.footprint)
    if capture.supported:
        previous = capture.children.setdefault((node, rule), result)
        if previous is not result:
            capture.reject()
        capture._check_size()
