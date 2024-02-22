from inspect import iscoroutinefunction
from Discord.command import Command
import Discord.utils as utils


class Command_Leaf(Command):
    def __init__(self, function, brief, params=None, perms=None, roles=None, servers=None, perm_role_operator='And',
                 **kwargs):
        self._brief = brief
        self._meta = kwargs
        self._params = params
        self._perms = perms
        self._roles = roles
        self._servers = servers
        self._perm_role_operator = perm_role_operator

        if perm_role_operator != 'And' and perm_role_operator != 'Or':
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
