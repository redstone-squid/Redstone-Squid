"""Page contents and the addresses of neighbouring pages.

A request addresses its page by an offset, for numbered pages, or by a cursor naming the last
row it has seen. Cursors carry that row's sort value, so following one still works after the
row is deleted, filtered out or moved.
"""

import base64
import binascii
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Self, cast
from uuid import UUID

from whenever import Instant

from squid.core.errors import ErrorCode, ValidationError
from squid.core.i18n import tr
from squid.core.validation import FieldErrorCode, SimpleIssue

MAX_PAGE_OFFSET = 10_000
"""Maximum offset accepted by paginated endpoints."""


class InvalidPageCursorError(ValidationError):
    """A cursor is malformed or belongs to another sort. Clients restart from the first page."""

    default_message = tr(t"core-page-cursor-invalid = This page link is out of date.")
    default_title = tr(t"core-errors-page-cursor-invalid = Page link out of date")
    default_code = ErrorCode.INVALID_PAGE_CURSOR
    default_end_user_action = tr(t"core-page-cursor-invalid-action = Go back to the first page.")

    def __init__(self) -> None:
        super().__init__(issues=(SimpleIssue(field="/query/cursor", code=FieldErrorCode.INVALID_VALUE),))


@dataclass(frozen=True, slots=True)
class PageCursor:
    """A row at a page boundary, and the direction to read from it.

    Attributes:
        order: Name of the sort the cursor belongs to.
        key: The row's sort value. Orders by ID alone use the ID.
        id: The row's ID.
        backward: Read the rows before the boundary instead of after it.
    """

    order: str
    key: object
    id: int | UUID
    backward: bool = False

    def encode(self) -> str:
        """Return the opaque token clients send back as `cursor`."""
        payload = json.dumps([self.order, self.backward, _tagged(self.key), _tagged(self.id)], separators=(",", ":"))
        return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")

    @classmethod
    def decode(cls, token: str) -> Self:
        """Read a token from `encode`, raising InvalidPageCursorError for anything else."""
        try:
            order, backward, key, identifier = json.loads(
                base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
            )
            key, identifier = _untagged(key), _untagged(identifier)
        except (binascii.Error, RecursionError, UnicodeDecodeError, TypeError, ValueError) as error:
            raise InvalidPageCursorError from error
        # bool is an int subclass, so check the exact type of the ID.
        if not isinstance(order, str) or not isinstance(backward, bool) or type(identifier) not in (int, UUID):
            raise InvalidPageCursorError
        return cls(order, key, cast(int | UUID, identifier), backward)


def _tagged(value: object) -> object:
    """Wrap values JSON cannot tell apart from strings."""
    match value:
        case Instant():
            return {"instant": value.format_iso()}
        case UUID():
            return {"uuid": str(value)}
        case None | bool() | int() | float() | str():
            return value
        case _:
            msg = f"Cannot encode a {type(value).__name__} sort value in a page cursor"
            raise TypeError(msg)


def _untagged(value: object) -> object:
    if isinstance(value, dict) and len(value) == 1:
        match next(iter(cast(dict[str, object], value).items())):
            case ("instant", str(text)):
                return Instant.parse_iso(text)
            case ("uuid", str(text)):
                return UUID(text)
    if value is None or isinstance(value, bool | int | float | str):
        return value
    raise ValueError(value)


@dataclass(frozen=True, slots=True)
class PageSelector:
    """The single way one request addresses its page."""

    offset: int | None = None
    cursor: PageCursor | None = None


@dataclass(frozen=True, slots=True)
class PageAnchor:
    """An adjacent page address. Exactly one field is set."""

    offset: int | None = None
    cursor: PageCursor | None = None

    def selector(self) -> PageSelector:
        """Return the selector that requests this page."""
        return PageSelector(offset=self.offset, cursor=self.cursor)


@dataclass(frozen=True, slots=True)
class Page[ItemT]:
    """One page of results and the addresses of its neighbours."""

    items: tuple[ItemT, ...]
    # TODO: If profiling shows count queries are costly, make totals optional for callers that only need navigation.
    total: int
    next: PageAnchor | None
    prev: PageAnchor | None


FIRST_PAGE = PageSelector()
"""The selector a request that named no pagination parameter resolves to."""


def offset_page[ItemT](
    rows: Sequence[ItemT], *, offset: int | None, page_size: int, total: int | None = None
) -> Page[ItemT]:
    """Assemble one offset-addressed page.

    Without `total`, `rows` is the whole collection. With it, `rows` starts at `offset`.
    """
    offset = offset or 0
    if total is None:
        total, rows = len(rows), rows[offset:]
    return Page(
        items=tuple(rows[:page_size]),
        total=total,
        next=PageAnchor(offset=offset + page_size)
        if offset + page_size <= MAX_PAGE_OFFSET and offset + page_size < total
        else None,
        prev=PageAnchor(offset=max(offset - page_size, 0)) if offset else None,
    )
