"""The two spellings of the message-root option set cannot drift apart.

``MessageRootOptions`` (a TypedDict, so keywords can be forwarded with ``Unpack``) and
``MessageRootConfig`` (a frozen dataclass, so the defaults have one home) list the same
surface by hand, because a TypedDict cannot be derived from a dataclass at type-check time.
A keyword added to one but not the other would be silently unreachable for every host that
mounts through ``DiscordUIRuntime.mount`` or a ``SessionSpec``.

MessageRoot.__init__ accepts the config, target, and per-root access policy. Checking
that small signature prevents another independently maintained list of options.
"""

import inspect

from squid_ui_discord.message_root import MessageRoot
from squid_ui_discord.message_root_contracts import MessageRootConfig
from squid_ui_discord.message_root_options import MessageRootDefaults, MessageRootOptions

# Access is supplied for each message root; reusable defaults must not silently choose
# who may use a newly mounted component.
PER_MOUNT_ONLY = {"access"}


def test_options_and_config_declare_the_same_surface() -> None:
    assert set(MessageRootOptions.__annotations__) == set(MessageRootConfig.__dataclass_fields__)


def test_every_option_carries_a_default() -> None:
    for name, field in MessageRootConfig.__dataclass_fields__.items():
        has_default = field.default is not inspect.Parameter.empty or field.default_factory is not None
        assert has_default, f"{name} needs a default to be an option"


def test_defaults_inherit_the_config_surface_and_add_only_mounting() -> None:
    assert set(MessageRootDefaults.__dataclass_fields__) == set(MessageRootConfig.__dataclass_fields__)
    assert MessageRootDefaults.mount


def test_the_constructor_names_only_the_config_and_target_discriminator() -> None:
    keywords = {
        name
        for name, parameter in inspect.signature(MessageRoot.__init__).parameters.items()
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
    }
    assert keywords == PER_MOUNT_ONLY | {"config", "target"}
