from inspect import iscoroutinefunction
from Discord.command import Command
from Discord.command_leaf import Command_Leaf
import Discord.utils as utils
import Discord.permissions as perms

class Command_Branch(Command):

    def __init__(self, brief, function = None, params = None, perms = None, roles = None, servers = None, perm_role_operator = 'And', **kwargs):
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
        self._commands = {}

        self.validate_params()
    
    def get_command(self, cmd, *argv):
        if not isinstance(cmd, str):
            return None, None
        cmd = cmd.lower()

        if cmd in self._commands:
            if len(argv) == 0 or isinstance(self._commands[cmd], Command_Leaf):
                return self._commands[cmd], argv
            return self._commands[cmd].get_command(*argv)
        
        return None, None

    def validate_add_command(self, cmd_string, cmd):
        if not isinstance(cmd_string, str):
            raise ValueError('Command name must be a string.')
        if len(cmd_string) == 0:
            raise ValueError('Command name must have at least length 1.')
        if self.get_command(cmd_string)[0] != None:
            raise ValueError('Command already exists.')
        if not (isinstance(cmd, Command_Leaf) or isinstance(cmd, Command_Branch)):
            raise ValueError('Command must be a Command Node or Command Branch.')

    def add_command(self, cmd_string, cmd):
        self.validate_add_command(cmd_string, cmd)
        cmd_string = cmd_string.lower()

        self._commands[cmd_string] = cmd

    async def execute(self, cmd_to_execute, *argv, **kwargs):
        cmd_argv = cmd_to_execute.split(' ')
        cmd, argv_return = self.get_command(*cmd_argv)

        if cmd is None:
            return utils.error_embed('Error.', 'Unable to find command.')

        if cmd._servers and not int(argv[2].guild.id) in cmd._servers:
            return utils.error_embed('Insufficient Permissions.', 'This command can only be executed on certain servers.')

        roles_validated = perms.validate_roles(argv[2].author.roles, cmd._roles, argv[2].channel.permissions_for(argv[2].author))
        permissions_validated = perms.validate_permissions(argv[2].channel.permissions_for(argv[2].author), cmd._perms)

        if cmd._perm_role_operator == 'And':
            if not roles_validated:
                return utils.error_embed('Insufficient Permissions.', 'This command can only be executed by certain roles.')
            if not permissions_validated:
                return utils.error_embed('Insufficient Permissions.', 'You do not have the permissions required to execute that command.')    
        else:
            if not roles_validated and not permissions_validated:
                return utils.error_embed('Insufficient Permissions.', 'You do not have the permissions required to execute that command.')

        error = cmd.validate_execute_command(argv_return)
        if error:
            return error

        if cmd._function and callable(cmd._function):
            if iscoroutinefunction(cmd._function):
                # TODO: Add error handling for failing here.
                return await cmd._function(*argv, **kwargs)
            else:
                return cmd._function(*argv, **kwargs)
        
        return utils.error_embed('Error.', 'could not find a callable in the command object.')
        
    def get_help_message(self, *argv):
        cmd = self
        if len(argv) != 0:
            cmd, _ = self.get_command(*argv)
        if cmd is None:
            return utils.error_embed('Error.', 'Unable to find command. Use help to get a list of avaliable commands.')

        if isinstance(cmd, Command_Leaf):
            return '`{}` - {}\n'.format(' '.join(argv), cmd.get_help(True))
        else:
            result = '{}\n\nCommands:\n'.format(cmd.get_help())
            for scmd in cmd._commands.keys():
                result += '`{}` - {}\n'.format(scmd, cmd._commands[scmd].get_help())
            return result