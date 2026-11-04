"""Server settings domain values."""

from typing import Literal, TypedDict


class SettingOptions(TypedDict, total=False):
    """Channel IDs by setting name; None means that channel is not configured."""

    Smallest: int | None
    Fastest: int | None
    First: int | None
    Builds: int | None
    Vote: int | None


ScalarChannelSetting = Literal["Smallest", "Fastest", "First", "Builds", "Vote"]
Setting = Literal["Smallest", "Fastest", "First", "Builds", "Vote"]
"""Names of configurable channel destinations. Permissions are managed separately."""
