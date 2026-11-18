"""Dependency declaration for the recount command."""

from squid.bot.extensions import ExtensionSpec

extension = ExtensionSpec(id="recount", module="squid.bot.recount_cog", requires=("voting", "starboard"))
