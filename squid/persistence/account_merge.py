"""Move references from an absorbed account to the account kept by a merge.

Each feature's merge adapter first handles conflicts such as two records that
would become duplicates. It can then use move_account_references for the remaining
foreign keys. These updates use the merge's existing database session so they
commit or roll back with the rest of the merge.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import column, table, update
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class AccountMergeContext:
    """IDs of the account to keep (survivor) and the account whose references move (absorbed)."""

    survivor: int
    absorbed: int

    @property
    def parameters(self) -> dict[str, int]:
        return {"survivor": self.survivor, "absorbed": self.absorbed}


@dataclass(frozen=True, slots=True)
class AccountReference:
    """A foreign-key column to reassign when merging accounts."""

    table_name: str
    column_name: str


async def move_account_references(
    session: AsyncSession, references: Sequence[AccountReference], context: AccountMergeContext
) -> None:
    """Reassign references after the owning feature has resolved its conflicts."""
    for reference in references:
        target = table(reference.table_name, column(reference.column_name))
        account_column = target.c[reference.column_name]
        await session.execute(
            update(target).where(account_column == context.absorbed).values({reference.column_name: context.survivor})
        )
