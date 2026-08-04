"""Server settings domain values."""

from typing import Literal, TypedDict


class SettingOptions(TypedDict, total=False):
    """A map of settings to their values."""

    Smallest: int | None
    Fastest: int | None
    First: int | None
    Builds: int | None
    Vote: int | None
    Trusted: list[int]


ScalarChannelSetting = Literal["Smallest", "Fastest", "First", "Builds", "Vote"]
ListRoleSetting = Literal["Trusted"]
Setting = Literal["Smallest", "Fastest", "First", "Builds", "Vote", "Trusted"]
