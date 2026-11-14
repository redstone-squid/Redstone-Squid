"""The two spellings of the message-root option set cannot drift apart.

``MessageRootOptions`` (a TypedDict, so keywords can be forwarded with ``Unpack``) and
``MessageRootConfig`` (a frozen dataclass, so the defaults have one home) list the same
surface by hand, because a TypedDict cannot be derived from a dataclass at type-check time.
A keyword added to one but not the other would be silently unreachable for every host that
mounts through ``DiscordUIRuntime.mount`` or a ``SessionSpec``.
"""

from squid_ui_discord.message_root_contracts import MessageRootConfig
from squid_ui_discord.message_root_options import MessageRootOptions


def test_options_and_config_declare_the_same_surface() -> None:
    assert set(MessageRootOptions.__annotations__) == set(MessageRootConfig.__dataclass_fields__)
