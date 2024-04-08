import typing
from typing import Literal, Tuple

OWNER = 'papetoast'
OWNER_ID = 353089661175988224
OWNER_SERVER_ID = 433618741528625152
PREFIX = '!'
BOT_NAME = 'Redstone Squid'
BOT_VERSION = '1.4.0'
SOURCE_CODE_URL = 'https://github.com/Kappeh/Redstone-Squid'
FORM_LINK = 'https://forms.gle/i9Nf6apGgPGTUohr9'

# List of versions for version string parser
VERSIONS_LIST = ['Pre 1.5', '1.5', '1.6', '1.7', '1.8', '1.9', '1.10', '1.11', '1.12', '1.13',
                 '1.13.1 / 1.13.2', '1.14', '1.14.1', '1.15', '1.16', '1.17', '1.18', '1.19', '1.20', '1.20.4']

# Used for both type hinting and command descriptions
SETTABLE_CHANNELS_TYPE = Literal['Smallest', 'Fastest', 'First', 'Builds', 'Vote']
SETTABLE_CHANNELS: Tuple[SETTABLE_CHANNELS_TYPE, ...] = typing.get_args(SETTABLE_CHANNELS_TYPE)
