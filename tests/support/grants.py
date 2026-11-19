"""Fixed permission grants for screen tests."""

from squid.bot.utils.permissions import Grants
from squid.core.permissions.domain import PermissionNode


def grants(*nodes: PermissionNode, current: bool = True) -> Grants:
    """Grants holding `nodes` at open, whose rechecks all answer `current`."""

    async def check(_node: PermissionNode) -> bool:
        return current

    return Grants(frozenset(nodes), check)
