from inspect import iscoroutinefunction
from src.command import Command
import src.utils as utils

class Command_Leaf(Command):

    def __init__(self, function, brief, params = None, perms = None, **kwargs):
        self._brief = brief
        self._meta = kwargs
        self._params = params
        self._perms = perms

        self._function = function

        self.validate_params()
    
    async def execute(self, argv, kwargs):
        if callable(self._function):
            if iscoroutinefunction(self._function):
                return await self._function(*argv, **kwargs)
            else:
                return self._function(*argv, **kwargs)
        return utils.error_embed('Error.', 'Could not find a callable in the command object.')