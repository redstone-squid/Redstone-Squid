"""Discord Components V2 target profile."""

from squid_layouts.limits import LIMITS, V2Limits
from squid_layouts.target import TargetProfile


class DiscordV2Target(TargetProfile):
    """Discord Components V2 capabilities and resource limits."""

    def __init__(self, limits: V2Limits = LIMITS) -> None:
        super().__init__(
            id="discord.components-v2",
            version=1,
            capabilities=frozenset(
                {
                    "actions.buttons",
                    "actions.select",
                    "forms.modal",
                    "layout.container",
                    "layout.gallery",
                    "layout.section",
                }
            ),
            limits=limits,
        )


DISCORD_V2 = DiscordV2Target()
