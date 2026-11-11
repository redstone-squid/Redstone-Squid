"""Detached copies of starboard configuration, compared and restored by the starboard editor."""

from dataclasses import dataclass

from squid.starboard.domain import EDITABLE_SETTINGS, StarboardDirection
from squid.starboard.models import StarboardConfig, StarboardEmoji


@dataclass(frozen=True, slots=True)
class StarboardSnapshot:
    id: int
    guild_id: int
    settings: tuple[tuple[str, object], ...]
    emojis: tuple[tuple[str, StarboardDirection, float, int], ...]
    weights: tuple[tuple[int, float], ...]
    revision: int

    @classmethod
    def capture(cls, config: StarboardConfig, weights: dict[int, float]) -> StarboardSnapshot:
        return cls(
            config.id,
            config.guild_id,
            tuple((key, getattr(config, key)) for key in EDITABLE_SETTINGS),
            tuple((item.emoji, item.direction, item.multiplier, item.position) for item in config.emojis),
            tuple(sorted(weights.items())),
            config.revision,
        )

    def config(self) -> StarboardConfig:
        values = dict(self.settings)
        config = StarboardConfig(
            id=self.id,
            guild_id=self.guild_id,
            channel_id=int(str(values["channel_id"])),
            name=str(values["name"]),
            emojis=self.aliases(),
        )
        for key, value in self.settings:
            setattr(config, key, value)
        config.revision = self.revision
        config.validate()
        return config

    def aliases(self) -> tuple[StarboardEmoji, ...]:
        return tuple(
            StarboardEmoji(emoji=emoji, direction=direction, multiplier=weight, position=position)
            for emoji, direction, weight, position in self.emojis
        )
