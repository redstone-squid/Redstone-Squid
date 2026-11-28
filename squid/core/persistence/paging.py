"""SQL for reading one page of a listing by offset or cursor."""

from dataclasses import dataclass, replace
from typing import Any, cast

from sqlalchemy import ColumnElement, Select, SQLColumnExpression, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from squid.core.pagination import InvalidPageCursorError, Page, PageAnchor, PageCursor, PageSelector, offset_page

_KEY = "keyset_key"
_ID = "keyset_id"


@dataclass(frozen=True, slots=True)
class KeysetOrder:
    """A named total display order: `key`, then `id` in the same direction, with unknown keys last.

    Pass the ID column itself as `key` to order by ID alone. `name` ties cursors to this order,
    so a cursor from another sort is rejected instead of seeking somewhere arbitrary.
    """

    name: str
    key: SQLColumnExpression[Any]
    id: SQLColumnExpression[Any]
    descending: bool

    async def page(
        self,
        session: AsyncSession,
        statement: Select[*tuple[Any, ...]],
        selector: PageSelector,
        *,
        page_size: int,
        total: int,
    ) -> Page[Any]:
        """Read the page of a filtered `statement` that `selector` addresses.

        Items are the statement's rows, unwrapped to a single value when it selects one entity or
        column. Cursor and first pages link their neighbours by cursor; offset pages by offset.

        Raises:
            InvalidPageCursorError: The cursor belongs to another order or has a mistyped key.
        """
        cursor = selector.cursor
        if cursor is not None:
            statement = statement.where(self._beyond(self._checked(cursor), cursor))
        backward = cursor is not None and cursor.backward
        width = len(statement.column_descriptions)
        statement = self._ordered(statement.add_columns(self.key.label(_KEY), self.id.label(_ID)), backward)
        if selector.offset:
            statement = statement.offset(selector.offset)
        # One row past the page proves whether another page follows. Joined eager loads repeat
        # rows per child, so rows are deduplicated by ID.
        result = await session.execute(statement.limit(page_size + 1))
        rows = list(result.unique(lambda row: row[-1]).all())
        overflow = len(rows) > page_size
        rows = rows[:page_size]
        if backward:
            rows.reverse()
        items = tuple(row[0] if width == 1 else tuple(row[:width]) for row in rows)
        if selector.offset is not None:
            return offset_page(items, offset=selector.offset, page_size=page_size, total=total)
        if not rows:
            return Page(items, total, next=None, prev=None)
        first, last = (PageCursor(self.name, row[-2], row[-1]) for row in (rows[0], rows[-1]))
        return Page(
            items,
            total,
            # Arriving backward from a later page, there is always something ahead to go back to.
            next=PageAnchor(cursor=last) if overflow or backward else None,
            prev=PageAnchor(cursor=replace(first, backward=True))
            if (overflow if backward else cursor is not None)
            else None,
        )

    def _checked(self, cursor: PageCursor) -> object:
        """Return the cursor's key after checking that it can belong to this order."""
        if cursor.order != self.name or not _fits(self.id, cursor.id):
            raise InvalidPageCursorError
        if self.key is self.id:
            if type(cursor.key) is not type(cursor.id) or cursor.key != cursor.id:
                raise InvalidPageCursorError
            return cursor.id
        if cursor.key is not None and not _fits(self.key, cursor.key):
            raise InvalidPageCursorError
        return cursor.key

    def _ordered[S: Select[*tuple[Any, ...]]](self, statement: S, backward: bool) -> S:
        descending = self.descending != backward
        identifier = self.id.desc() if descending else self.id.asc()
        if self.key is self.id:
            return statement.order_by(identifier)
        key = self.key.desc() if descending else self.key.asc()
        # Reading backward reverses the display order, which also moves unknown keys first.
        return statement.order_by(key.nulls_first() if backward else key.nulls_last(), identifier)

    def _beyond(self, anchor_key: object, cursor: PageCursor) -> ColumnElement[bool]:
        """Select rows after the cursor in display order, or before it when reading backward."""
        backward = cursor.backward

        def further(column: SQLColumnExpression[Any], value: object) -> ColumnElement[bool]:
            return column < value if self.descending != backward else column > value

        if self.key is self.id:
            return further(self.id, cursor.id)
        same_key_further_id = and_(self.key == anchor_key, further(self.id, cursor.id))
        if anchor_key is None:
            within_unknown = and_(self.key.is_(None), further(self.id, cursor.id))
            return or_(self.key.is_not(None), within_unknown) if backward else within_unknown
        known_further = or_(further(self.key, anchor_key), same_key_further_id)
        return known_further if backward else or_(known_further, self.key.is_(None))


def _fits(column: SQLColumnExpression[Any], value: object) -> bool:
    """Whether a decoded cursor value can be compared with `column` without a database error."""
    try:
        # Mapped attributes and expressions both carry a SQL type; the protocol just doesn't say so.
        expected = cast(ColumnElement[Any], column).type.python_type
    except NotImplementedError:
        return isinstance(value, str | int | float)
    return isinstance(value, expected) or (expected is float and type(value) is int)
