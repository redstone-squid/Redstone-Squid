"""Shared row projection for materialized and source-backed rankings."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from squid_ui.text import TextLike
from squid_patterns._content import display_text


@dataclass(frozen=True, slots=True)
class RankedEntry:
    """An already-ranked row for callers that do not need projection callbacks."""

    label: TextLike
    value: object
    key: str = ""


type Projector[EntryT] = str | Callable[[EntryT], object]


class RankedRows[EntryT]:
    def __init__(
        self,
        label: Projector[EntryT] | None,
        value: Projector[EntryT] | None,
        identity: Projector[EntryT] | None,
    ) -> None:
        self.label = label
        self.value = value
        self.identity = identity

    @staticmethod
    def project(entry: EntryT, projector: Projector[EntryT]) -> object:
        if callable(projector):
            return projector(entry)
        if isinstance(entry, Mapping):
            try:
                return entry[projector]
            except KeyError as error:
                message = f"ranked entry has no key {projector!r}"
                raise ValueError(message) from error
        try:
            return getattr(entry, projector)
        except AttributeError as error:
            message = f"ranked entry has no attribute {projector!r}"
            raise ValueError(message) from error

    def values(self, entry: RankedEntry | EntryT) -> tuple[object, object]:
        if isinstance(entry, RankedEntry):
            if self.label is not None or self.value is not None:
                message = "projectors cannot be combined with RankedEntry values"
                raise TypeError(message)
            return entry.label, entry.value
        if self.label is None or self.value is None:
            if isinstance(entry, tuple) and len(entry) == 2:
                return entry[0], entry[1]
            message = "ranking needs label and value projectors for non-tuple entries"
            raise TypeError(message)
        return self.project(entry, self.label), self.project(entry, self.value)

    def identity_of(self, entry: RankedEntry | EntryT) -> str:
        if isinstance(entry, RankedEntry) and entry.key:
            return entry.key
        if self.identity is not None and not isinstance(entry, RankedEntry):
            return str(self.project(entry, self.identity))
        return repr(entry)

    def lines(self, entries: tuple[RankedEntry | EntryT, ...], offset: int) -> tuple[str, ...]:
        return tuple(
            f"{rank}. **{display_text(label)}** — {display_text(value)}"
            for rank, entry in enumerate(entries, offset + 1)
            for label, value in (self.values(entry),)
        )
