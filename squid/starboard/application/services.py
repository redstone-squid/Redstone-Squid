"""Starboard orchestration over ports that name no framework."""

from dataclasses import dataclass, replace
from math import isfinite

from squid.core.errors import ValidationError
from squid.core.i18n import tr
from squid.reactions.application import RoleWeightPolicy
from squid.reactions.domain import ReactionActor, RoleMultiplier, WeightScope
from squid.starboard.application.ports import EntryKey, EntryState, PendingVote, StarboardRepository
from squid.starboard.domain import OriginMessage, StarboardConfig, StarboardEmoji, evaluate_vote


@dataclass(frozen=True, slots=True)
class StarboardVoteResult:
    """Plans and source-reaction cleanup requested by one reaction."""

    keys: tuple[EntryKey, ...]
    remove_reaction: bool = False


class StarboardService:
    """Authorize, weight, and atomically plan starboard entries."""

    def __init__(self, repository: StarboardRepository) -> None:
        self._repository = repository
        self._emoji_cache: dict[int, frozenset[str]] = {}

        async def multipliers(scope: WeightScope) -> tuple[RoleMultiplier, ...]:
            configured = await self._repository.role_multipliers(scope.scope_id or 0)
            return tuple(RoleMultiplier(scope, role_id, value) for role_id, value in configured.items())

        self._weight_policy = RoleWeightPolicy(multipliers, staff_multiplier=1.0)

    async def is_relevant_emoji(self, guild_id: int, emoji: str) -> bool:
        configured = self._emoji_cache.get(guild_id)
        if configured is None:
            configured = await self._repository.relevant_emojis(guild_id)
            self._emoji_cache[guild_id] = configured
        return emoji in configured

    async def record_vote(self, origin: OriginMessage, actor: ReactionActor, emoji: str) -> StarboardVoteResult:
        pending: list[PendingVote] = []
        remove_reaction = False
        for config in await self._repository.configs_for_source(origin.guild_id, origin.channel_id):
            verdict = evaluate_vote(config, origin, actor, emoji)
            if verdict.action == "remove_reaction":
                remove_reaction = remove_reaction or config.remove_invalid_reactions
                continue
            if verdict.action != "accept" or verdict.direction is None:
                continue
            scope = WeightScope(config.guild_id, "starboard", config.id)
            role_weight = await self._weight_policy.calculate(actor, scope)
            option = next(item for item in config.emojis if item.emoji == emoji)
            assert role_weight is not None
            pending.append(PendingVote(config, emoji, verdict.direction, role_weight * option.multiplier))
        keys = await self._repository.record_votes(origin, actor.user_id, pending) if pending else ()
        return StarboardVoteResult(tuple(keys), remove_reaction)

    async def withdraw_vote(self, origin_message_id: int, user_id: int, emoji: str) -> tuple[EntryKey, ...]:
        return tuple(await self._repository.withdraw_vote(origin_message_id, user_id, emoji))

    async def recount(
        self, origin: OriginMessage, reactions: tuple[tuple[ReactionActor, str], ...]
    ) -> tuple[EntryKey, ...]:
        configs = await self._repository.configs_for_source(origin.guild_id, origin.channel_id)
        accepted: dict[tuple[int, int], tuple[int, PendingVote]] = {}
        for actor, emoji in reactions:
            for config in configs:
                verdict = evaluate_vote(config, origin, actor, emoji)
                if verdict.action != "accept" or verdict.direction is None:
                    continue
                scope = WeightScope(config.guild_id, "starboard", config.id)
                role_weight = await self._weight_policy.calculate(actor, scope)
                option = next(item for item in config.emojis if item.emoji == emoji)
                assert role_weight is not None
                accepted[(actor.user_id, config.id)] = (
                    actor.user_id,
                    PendingVote(config, emoji, verdict.direction, role_weight * option.multiplier),
                )
        return tuple(await self._repository.recount_votes(origin, tuple(accepted.values())))

    async def clear_votes(self, origin_message_id: int, emoji: str | None = None) -> tuple[EntryKey, ...]:
        return tuple(await self._repository.clear_votes(origin_message_id, emoji))

    async def refresh(self, origin_message_id: int, *, force: bool = False) -> tuple[EntryKey, ...]:
        return tuple(await self._repository.refresh(origin_message_id, force=force))

    async def mark_origin_deleted(self, origin_message_id: int) -> tuple[EntryKey, ...]:
        return tuple(await self._repository.mark_origin_deleted(origin_message_id))

    async def create_starboard(
        self, guild_id: int, channel_id: int, name: str = "main", *, required: float = 3.0
    ) -> StarboardConfig:
        config = StarboardConfig(
            0,
            guild_id,
            channel_id,
            name.strip(),
            (StarboardEmoji("⭐", "up", position=0), StarboardEmoji("💩", "down", position=1)),
            required=required,
        )
        created = await self._repository.create(config)
        self.invalidate_cache(guild_id)
        return created

    async def delete_starboard(self, guild_id: int, name: str) -> bool:
        deleted = await self._repository.delete(guild_id, name)
        self.invalidate_cache(guild_id)
        return deleted

    async def list_for_guild(self, guild_id: int) -> tuple[StarboardConfig, ...]:
        return tuple(await self._repository.list_for_guild(guild_id))

    async def get(self, guild_id: int, name: str) -> StarboardConfig | None:
        return await self._repository.get(guild_id, name)

    async def update_settings(self, guild_id: int, name: str, **settings: object) -> StarboardConfig | None:
        current = await self._repository.get(guild_id, name)
        if current is None:
            return None
        replace(current, **settings)  # type: ignore[arg-type]
        updated = await self._repository.update(guild_id, name, settings)
        self.invalidate_cache(guild_id)
        return updated

    async def set_emojis(self, config: StarboardConfig, emojis: tuple[StarboardEmoji, ...]) -> None:
        StarboardConfig(
            config.id,
            config.guild_id,
            config.channel_id,
            config.name,
            emojis,
            required=config.required,
            required_remove=config.required_remove,
        )
        await self._repository.set_emojis(config.id, emojis)
        self.invalidate_cache(config.guild_id)

    async def set_role_multiplier(self, config: StarboardConfig, role_id: int, multiplier: float | None) -> None:
        if multiplier is not None and (not isfinite(multiplier) or multiplier <= 0):
            msg = tr(t"Role multiplier must be finite and greater than zero.")
            raise ValidationError(msg)
        await self._repository.set_role_multiplier(config.id, role_id, multiplier)

    async def get_role_multipliers(self, config: StarboardConfig) -> dict[int, float]:
        return dict(await self._repository.role_multipliers(config.id))

    async def entry_state(self, starboard_id: int, origin_message_id: int) -> EntryState | None:
        """Return one entry's config, origin and score, for rendering."""
        return await self._repository.entry_state(starboard_id, origin_message_id)

    async def mark_rendered(self, starboard_id: int, origin_message_id: int, score: float) -> None:
        """Record the score a post now shows, so an unchanged entry is not re-edited."""
        await self._repository.mark_rendered(starboard_id, origin_message_id, score)

    async def disable_channel(self, channel_id: int) -> None:
        await self._repository.disable_channel(channel_id)

    def invalidate_cache(self, guild_id: int) -> None:
        self._emoji_cache.pop(guild_id, None)
