"""SQL for paging a listing by offset or by an anchor row's ID."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, Select, SQLColumnExpression, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from squid.core.pagination import PageAnchorNotFoundError, PageSelector


@dataclass(frozen=True, slots=True)
class KeysetOrder:
    """A total display order: `key`, then `id` in the same direction, with unknown keys last.

    Pass the ID column itself as `key` to order by ID alone.
    """

    key: SQLColumnExpression[Any]
    id: SQLColumnExpression[Any]
    descending: bool

    async def apply[S: Select[Any]](self, session: AsyncSession, statement: S, selector: PageSelector[Any]) -> S:
        """Order a filtered `statement` and move it to the page `selector` addresses.

        A `before_id` page reads in reversed display order, so callers reverse its rows. Unless the
        order is by ID alone, the anchor's key is read from `statement`, and an anchor outside its
        rows raises PageAnchorNotFoundError.
        """
        anchor_id = selector.after_id if selector.after_id is not None else selector.before_id
        backward = selector.before_id is not None
        if anchor_id is not None:
            if self.key is self.id:
                anchor_key = anchor_id
            else:
                anchor = (
                    await session.execute(statement.with_only_columns(self.key).where(self.id == anchor_id))
                ).one_or_none()
                if anchor is None:
                    raise PageAnchorNotFoundError(selector)
                anchor_key = anchor[0]
            statement = statement.where(self._beyond(anchor_key, anchor_id, backward=backward))
        descending = self.descending != backward
        if self.key is self.id:
            statement = statement.order_by(self.id.desc() if descending else self.id.asc())
        else:
            key = self.key.desc() if descending else self.key.asc()
            # Reading backward reverses the display order, which also moves unknown keys first.
            key = key.nulls_first() if backward else key.nulls_last()
            statement = statement.order_by(key, self.id.desc() if descending else self.id.asc())
        return statement.offset(selector.offset) if selector.offset else statement

    def _beyond(self, anchor_key: object, anchor_id: object, *, backward: bool) -> ColumnElement[bool]:
        """Select rows after the anchor in display order, or before it when reading backward."""

        def further(column: SQLColumnExpression[Any], value: object) -> ColumnElement[bool]:
            return column < value if self.descending != backward else column > value

        if self.key is self.id:
            return further(self.id, anchor_id)
        same_key_further_id = and_(self.key == anchor_key, further(self.id, anchor_id))
        if anchor_key is None:
            within_unknown = and_(self.key.is_(None), further(self.id, anchor_id))
            return or_(self.key.is_not(None), within_unknown) if backward else within_unknown
        known_further = or_(further(self.key, anchor_key), same_key_further_id)
        return known_further if backward else or_(known_further, self.key.is_(None))
