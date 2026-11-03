"""Starboard orchestration over ports that name no framework."""

from dataclasses import dataclass
from math import isfinite

from squid.core.errors import ValidationError
from squid.core.i18n import tr
from squid.reactions.application import RoleWeightPolicy
from squid.reactions.domain import ReactionActor, RoleMultiplier, WeightScope
from squid.starboard.application.ports import (
    EntryKey,
    EntryState,
    PendingVote,
    StarboardRepository,
    StarboardUnitOfWork,
)
from squid.starboard.domain import evaluate_vote
from squid.starboard.domain.models import EDITABLE_SETTINGS
from squid.starboard.models import OriginMessage, StarboardConfig, StarboardEmoji
from squid.unit_of_work import UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class StarboardVoteResult:
    """Plans and source-reaction cleanup requested by one reaction."""

    keys: tuple[EntryKey, ...]
    remove_reaction: bool = False


class StarboardService:
    """Authorize, weight, and atomically plan starboard entries."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory[StarboardUnitOfWork]) -> None:
        self._uow_factory = uow_factory
        self._emoji_cache: dict[int, frozenset[str]] = {}

    @staticmethod
    def _weight_policy(repository: StarboardRepository) -> RoleWeightPolicy:
        async def multipliers(scope: WeightScope) -> tuple[RoleMultiplier, ...]:
            configured = await repository.role_multipliers(scope.scope_id or 0)
            return tuple(RoleMultiplier(scope, role_id, value) for role_id, value in configured.items())

        return RoleWeightPolicy(multipliers, staff_multiplier=1.0)

    async def is_relevant_emoji(self, guild_id: int, emoji: str) -> bool:
        configured = self._emoji_cache.get(guild_id)
        if configured is None:
            async with self._uow_factory() as uow:
                configured = await uow.starboards.relevant_emojis(guild_id)
            self._emoji_cache[guild_id] = configured
        return emoji in configured

    async def record_vote(self, origin: OriginMessage, actor: ReactionActor, emoji: str) -> StarboardVoteResult:
        async with self._uow_factory() as uow:
            weight_policy = self._weight_policy(uow.starboards)
            pending: list[PendingVote] = []
            remove_reaction = False
            for config in await uow.starboards.configs_for_source(origin.guild_id, origin.channel_id):
                verdict = evaluate_vote(config, origin, actor, emoji)
                if verdict.action == "remove_reaction":
                    remove_reaction = remove_reaction or config.remove_invalid_reactions
                    continue
                if verdict.action != "accept" or verdict.direction is None:
                    continue
                scope = WeightScope(config.guild_id, "starboard", config.id)
                role_weight = await weight_policy.calculate(actor, scope)
                option = next(item for item in config.emojis if item.emoji == emoji)
                assert role_weight is not None
                pending.append(PendingVote(config, emoji, verdict.direction, role_weight * option.multiplier))
            keys = await uow.starboards.record_votes(origin, actor.user_id, pending) if pending else ()
            result = StarboardVoteResult(tuple(keys), remove_reaction)
            await uow.commit()
            return result

    async def withdraw_vote(self, origin_message_id: int, user_id: int, emoji: str) -> tuple[EntryKey, ...]:
        async with self._uow_factory() as uow:
            result = tuple(await uow.starboards.withdraw_vote(origin_message_id, user_id, emoji))
            await uow.commit()
            return result

    async def recount(
        self, origin: OriginMessage, reactions: tuple[tuple[ReactionActor, str], ...]
    ) -> tuple[EntryKey, ...]:
        async with self._uow_factory() as uow:
            weight_policy = self._weight_policy(uow.starboards)
            configs = await uow.starboards.configs_for_source(origin.guild_id, origin.channel_id)
            accepted: dict[tuple[int, int], tuple[int, PendingVote]] = {}
            for actor, emoji in reactions:
                for config in configs:
                    verdict = evaluate_vote(config, origin, actor, emoji)
                    if verdict.action != "accept" or verdict.direction is None:
                        continue
                    scope = WeightScope(config.guild_id, "starboard", config.id)
                    role_weight = await weight_policy.calculate(actor, scope)
                    option = next(item for item in config.emojis if item.emoji == emoji)
                    assert role_weight is not None
                    accepted[(actor.user_id, config.id)] = (
                        actor.user_id,
                        PendingVote(config, emoji, verdict.direction, role_weight * option.multiplier),
                    )
            result = tuple(await uow.starboards.recount_votes(origin, tuple(accepted.values())))
            await uow.commit()
            return result

    async def clear_votes(self, origin_message_id: int, emoji: str | None = None) -> tuple[EntryKey, ...]:
        async with self._uow_factory() as uow:
            result = tuple(await uow.starboards.clear_votes(origin_message_id, emoji))
            await uow.commit()
            return result

    async def refresh(self, origin_message_id: int, *, force: bool = False) -> tuple[EntryKey, ...]:
        async with self._uow_factory() as uow:
            result = tuple(await uow.starboards.refresh(origin_message_id, force=force))
            await uow.commit()
            return result

    async def mark_origin_deleted(self, origin_message_id: int) -> tuple[EntryKey, ...]:
        async with self._uow_factory() as uow:
            result = tuple(await uow.starboards.mark_origin_deleted(origin_message_id))
            await uow.commit()
            return result

    async def create_starboard(
        self, guild_id: int, channel_id: int, name: str = "main", *, required: float = 3.0
    ) -> StarboardConfig:
        async with self._uow_factory() as uow:
            config = StarboardConfig(
                id=0,
                guild_id=guild_id,
                channel_id=channel_id,
                name=name.strip(),
                emojis=(
                    StarboardEmoji(emoji="⭐", direction="up", position=0),
                    StarboardEmoji(emoji="💩", direction="down", position=1),
                ),
                required=required,
            )
            created = await uow.starboards.create(config)
            await uow.commit()
            self.invalidate_cache(guild_id)
            return created

    async def delete_starboard(self, guild_id: int, name: str) -> bool:
        async with self._uow_factory() as uow:
            deleted = await uow.starboards.delete(guild_id, name)
            await uow.commit()
            self.invalidate_cache(guild_id)
            return deleted

    async def list_for_guild(self, guild_id: int) -> tuple[StarboardConfig, ...]:
        async with self._uow_factory() as uow:
            return tuple(await uow.starboards.list_for_guild(guild_id))

    async def get(self, guild_id: int, name: str) -> StarboardConfig | None:
        async with self._uow_factory() as uow:
            return await uow.starboards.get(guild_id, name)

    async def update_settings(self, guild_id: int, name: str, **settings: object) -> StarboardConfig | None:
        if not settings or not set(settings) <= set(EDITABLE_SETTINGS) | {"channel_id", "name"}:
            msg = "Unknown or empty starboard setting update."
            raise ValidationError(msg)
        async with self._uow_factory() as uow:
            current = await uow.starboards.get(guild_id, name, for_update=True)
            if current is None:
                return None
            for key, value in settings.items():
                setattr(current, key, value)
            current.validate()
            await uow.commit()
        self.invalidate_cache(guild_id)
        return current

    async def set_emojis(self, config: StarboardConfig, emojis: tuple[StarboardEmoji, ...]) -> None:
        async with self._uow_factory() as uow:
            StarboardConfig(
                id=config.id,
                guild_id=config.guild_id,
                channel_id=config.channel_id,
                name=config.name,
                emojis=emojis,
                required=config.required,
                required_remove=config.required_remove,
            )
            await uow.starboards.set_emojis(config.id, emojis)
            await uow.commit()
            self.invalidate_cache(config.guild_id)

    async def set_role_multiplier(self, config: StarboardConfig, role_id: int, multiplier: float | None) -> None:
        async with self._uow_factory() as uow:
            if multiplier is not None and (not isfinite(multiplier) or multiplier <= 0):
                msg = tr(t"Role multiplier must be finite and greater than zero.")
                raise ValidationError(msg)
            await uow.starboards.set_role_multiplier(config.id, role_id, multiplier)
            await uow.commit()

    async def get_role_multipliers(self, config: StarboardConfig) -> dict[int, float]:
        async with self._uow_factory() as uow:
            return dict(await uow.starboards.role_multipliers(config.id))

    async def entry_state(self, starboard_id: int, origin_message_id: int) -> EntryState | None:
        """Return one entry's config, origin and score, for rendering."""
        async with self._uow_factory() as uow:
            return await uow.starboards.entry_state(starboard_id, origin_message_id)

    async def mark_rendered(self, starboard_id: int, origin_message_id: int, score: float) -> None:
        """Record the score a post now shows, so an unchanged entry is not re-edited."""
        async with self._uow_factory() as uow:
            await uow.starboards.mark_rendered(starboard_id, origin_message_id, score)
            await uow.commit()

    async def disable_channel(self, channel_id: int) -> None:
        async with self._uow_factory() as uow:
            await uow.starboards.disable_channel(channel_id)
            await uow.commit()

    def invalidate_cache(self, guild_id: int) -> None:
        self._emoji_cache.pop(guild_id, None)
