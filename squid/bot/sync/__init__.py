"""Discord reconciliation background worker."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import squid.bot.app


async def setup(bot: "squid.bot.app.RedstoneSquid") -> None:
    """Register the durable reconciliation worker."""
    from squid.bot.sync.reconciler import ReconciliationCog

    await bot.add_cog(ReconciliationCog(bot))
