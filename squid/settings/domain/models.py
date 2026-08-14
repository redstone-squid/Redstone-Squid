"""Server settings domain values."""

from typing import Literal, TypedDict


class SettingOptions(TypedDict, total=False):
    """A map of settings to their values."""

    Smallest: int | None
    Fastest: int | None
    First: int | None
    Builds: int | None
    Vote: int | None


ScalarChannelSetting = Literal["Smallest", "Fastest", "First", "Builds", "Vote"]
Setting = Literal["Smallest", "Fastest", "First", "Builds", "Vote"]
"""Every configurable server setting.

`Trusted` used to live here: a role list that doubled as an authorization tier.
Permissions are now nodes granted with `/perm`, so a server's settings are about
configuration again rather than about who may do what."""
