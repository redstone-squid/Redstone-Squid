"""Domain-event dispatch background worker."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import squid.bot.app


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Register the domain-event dispatcher."""
    from squid.bot.events.dispatcher import DomainEventCog

    await bot.add_cog(DomainEventCog(bot))
