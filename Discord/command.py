from typing import Literal

import Discord.utils as utils


class Param:
    def __init__(self, name, description, dtype=None, min_val=None, max_val=None, min_len=None, max_len=None, optional=False):
        self.name: str = name
        self.description: str = description
        self.dtype: Literal['int', 'num', 'mention', 'channel_mention', 'str', 'text'] = dtype
        self.min_val: float | int = min_val
        self.max_val: float | int = max_val
        self.min_len: float | int = min_len
        self.max_len: float | int = max_len
        self.optional: bool = optional

        self.validate_param()

    def validate_param(self):
        if not isinstance(self.name, str):
            raise Exception('Param.name must be a string.')
        if not isinstance(self.description, str):
            raise Exception('Param.description must be a string.')
        if self.dtype and self.dtype not in ['int', 'num', 'mention', 'channel_mention', 'str', 'text']:
            raise Exception('Invalid Param.dtype.')
        if (self.min_val or self.min_val) and self.dtype not in ['int', 'num']:
            raise Exception('Min and max values can only be specified for int and num params.')
        if self.min_val and not isinstance(self.min_val, (float, int)):
            raise Exception('Param.min_val must be a number.')
        if self.max_val and not isinstance(self.max_val, (float, int)):
            raise Exception('Param.max_val must be a number.')
        if self.min_len and not isinstance(self.min_len, (float, int)):
            raise Exception('Param.min_len must be a number.')
        if self.max_len and not isinstance(self.max_len, (float, int)):
            raise Exception('Param.max_len must be a number.')


class Command:
    _brief: str = None
    _params: list[Param] = None
    _meta = {}
    _perms: list[int] = None
    _roles: list[str] = None
    _servers: list[int] = None
    _perm_role_operator: Literal['And', 'Or'] = None

    def __getitem__(self, i):
        if i in self._meta:
            return self._meta[i]
        return None

    def get_usage_message(self):
        if not self._params:
            return 'No parameters required.'
        usage_message = 'Parameters:\n'
        for i in range(len(self._params)):
            usage_message += '`' + self._params[i].name + '` - '
            if self._params[i].optional:
                usage_message += '_optional_ - '
            usage_message += self._params[i].description
            if i < len(self._params) - 1:
                usage_message += '\n'

        return usage_message

    def validate_params(self):
        self.params_required = 0

        if not self._params:
            return

        optional = False
        text = False

        for param in self._params:
            if text:
                raise Exception('You can only have one text Param and it must be the last.')
            if not param.optional and optional:
                raise Exception('You cannot have an optional Param before a non optional Param.')

            if param.optional:
                optional = True
            else:
                self.params_required += 1
            if param.dtype == 'text':
                text = True

    def validate_execute_command(self, argv):
        if not self._params:
            if argv:
                return utils.warning_embed('Unexpected parameter.', self.get_usage_message())
            return None

        if len(argv) < self.params_required:
            return utils.warning_embed('Missing parameter.', self.get_usage_message())
        if len(argv) > len(self._params) and self._params[-1].dtype != 'text':
            return utils.warning_embed('Unexpected parameter.', self.get_usage_message())

        for i in range(len(self._params)):
            if self._params[i].optional and len(argv) < i:
                break

            if self._params[i].min_val and float(argv[i]) < self._params[i].min_val:
                return utils.warning_embed('Parameter out of bounds.', f'`{self._params[i].name}` must be at least {self._params[i].min_val}.')
            if self._params[i].max_val and float(argv[i]) > self._params[i].max_val:
                return utils.warning_embed('Parameter out of bounds.', f'`{self._params[i].name}` must be at most {self._params[i].max_val}.')
            if self._params[i].min_len and len(argv[i]) < self._params[i].min_len:
                return utils.warning_embed('Incorrect parameter length.', f'`{self._params[i].name}` must have length of at least {self._params[i].min_len}.')
            if self._params[i].max_len and len(argv[i]) > self._params[i].max_len:
                return utils.warning_embed('Incorrect parameter length.', f'`{self._params[i].name}` must have length of at most {self._params[i].max_len}.')
            if not self._params[i].dtype:
                continue
            if self._params[i].dtype == 'int' and not utils.represents_int(argv[i]):
                return utils.warning_embed('Incorrect parameter type.', f'`{self._params[i].name}` must be an integer.')
            if self._params[i].dtype == 'num' and not utils.represents_float(argv[i]):
                return utils.warning_embed('Incorrect parameter type.', f'`{self._params[i].name}` must be a number.')
            if self._params[i].dtype == 'mention' and not utils.represents_user(argv[i]):
                return utils.warning_embed('Incorrect parameter type.', f'`{self._params[i].name}` must be a mention.')
            if self._params[i].dtype == 'channel_mention' and not utils.represents_channel(argv[i]):
                return utils.warning_embed('Incorrect parameter type.', f'`{self._params[i].name}` must be a channel mention.')

        return None

    def get_help(self, description=False):
        if description and self._params:
            return self._brief + '\n\n' + self.get_usage_message()
        elif self._brief:
            return self._brief
        return utils.error_embed('Error.', 'Could not find command\'s help message.')
