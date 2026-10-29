"""Shared SQL primitives for feature-owned phases of one account merge."""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import column, table, update
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class AccountMergeContext:
    """The surviving and absorbed accounts in a caller-owned transaction."""

    survivor: int
    absorbed: int

    @property
    def parameters(self) -> dict[str, int]:
        return {"survivor": self.survivor, "absorbed": self.absorbed}


@dataclass(frozen=True, slots=True)
class AccountReference:
    """An account foreign key whose merge disposition is explicitly owned."""

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
