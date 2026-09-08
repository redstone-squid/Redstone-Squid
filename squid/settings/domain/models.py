"""Server settings domain values."""

from typing import Literal, TypedDict


class SettingOptions(TypedDict, total=False):
    """Settings to channel ids; `None` clears one, and an absent key leaves it alone."""

    Smallest: int | None
    Fastest: int | None
    First: int | None
    Builds: int | None
    Vote: int | None


ScalarChannelSetting = Literal["Smallest", "Fastest", "First", "Builds", "Vote"]
Setting = Literal["Smallest", "Fastest", "First", "Builds", "Vote"]
"""Every configurable server setting. All of them name a channel; who may do what is a permission node instead."""
