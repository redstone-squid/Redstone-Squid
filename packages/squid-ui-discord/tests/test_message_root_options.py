"""The three spellings of the message-root option set cannot drift apart.

``MessageRoot.__init__``, the ``MessageRootOptions`` TypedDict, and the
``MessageRootDefaults`` dataclass each list the same keyword surface by hand. A keyword
added to the constructor but not the other two would be silently unreachable for every
host that mounts through ``ClientRuntime.mount`` or a ``SessionSpec``.
"""

import inspect

from squid_ui_discord.message_root import MessageRoot
from squid_ui_discord.message_root_options import MessageRootDefaults, MessageRootOptions

# The one keyword deliberately absent from the reusable defaults: access identifies the
# actor allowed to use a specific mount and must be supplied at each construction site.
PER_MOUNT_ONLY = {"access"}


def _constructor_keywords() -> dict[str, inspect.Parameter]:
    signature = inspect.signature(MessageRoot.__init__)
    return {
        name: parameter
        for name, parameter in signature.parameters.items()
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
    }


def test_options_and_defaults_mirror_the_constructor() -> None:
    constructor = set(_constructor_keywords()) - PER_MOUNT_ONLY
    assert set(MessageRootOptions.__annotations__) == constructor
    assert set(MessageRootDefaults.__dataclass_fields__) == constructor


def test_defaults_carry_the_constructor_default_values() -> None:
    fields = MessageRootDefaults.__dataclass_fields__
    for name, parameter in _constructor_keywords().items():
        if name in PER_MOUNT_ONLY:
            continue
        assert parameter.default is not inspect.Parameter.empty, f"{name} needs a default to be an option"
        if name == "target":
            # The constructor spells its default as None-then-resolve to keep the type
            # parameter open; the dataclass names the resolved default directly.
            continue
        assert fields[name].default == parameter.default, name
