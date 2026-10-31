"""Stable, feature-owned identities for controls that outlive the bot process.

The ``r`` namespace is an ordinary root :class:`RouteGroup`. Feature modules own child
groups, identities, middleware, and handlers independently; this package re-exports the
small compatibility surface used by existing card renderers.
"""

from squid.bot.routes._root import router, routes
from squid.bot.routes.build_log_consents import build_log_consent, build_log_consents
from squid.bot.routes.builds import build_edit, builds
from squid.bot.routes.polls import poll_close, poll_refresh, polls
from squid.bot.routes.redstoner_roles import redstoner_roles, remove_redstoner_role

__all__ = [
    "build_edit",
    "build_log_consent",
    "build_log_consents",
    "builds",
    "poll_close",
    "poll_refresh",
    "polls",
    "redstoner_roles",
    "remove_redstoner_role",
    "router",
    "routes",
]
