from inspect import iscoroutinefunction
from typing import List, Literal

from Discord.command import Command, Param
import Discord.utils as utils


class CommandLeaf(Command):
    def __init__(self, function, brief, params=None, perms=None, roles=None, servers=None,
                 perm_role_operator: Literal['And', 'Or'] = 'And', **kwargs):
        self._brief: str = brief
        self._meta: dict[str, ...] = kwargs
        self._params: list[Param] = params
        self._perms: list[int] = perms
        self._roles: list[str] = roles
        self._servers: list[int] = servers
        self._perm_role_operator: Literal['And', 'Or'] = perm_role_operator

        if perm_role_operator != 'And' and perm_role_operator != 'Or':
            # TODO: More specific exception.
            raise Exception('perm_role_operator must be \'And\' or \'Or\'')

        self._function = function

        self.validate_params()

    async def execute(self, argv, kwargs):
        if callable(self._function):
            if iscoroutinefunction(self._function):
                return await self._function(*argv, **kwargs)
            else:
                return self._function(*argv, **kwargs)
        return utils.error_embed('Error.', 'Could not find a callable in the command object.')
