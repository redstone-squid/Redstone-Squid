"""Apply starboard policy and commit votes, scores, and configuration changes."""

from collections.abc import Collection
from dataclasses import dataclass
from math import isfinite

from squid.core.errors import ValidationError
from squid.core.i18n import tr
from squid.reactions.application import RoleWeightPolicy
from squid.reactions.domain import ReactionActor, RoleMultiplier, WeightScope
from squid.starboard.application.history import StarboardSnapshot
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
    """Entry keys needing a refresh and whether the source reaction should be removed."""

    keys: tuple[EntryKey, ...]
    remove_reaction: bool = False


class StarboardService:
    """Evaluate reactions and keep each starboard entry's votes and score together.

    One reaction can affect several boards that watch the source channel. Each
    board applies its own eligibility rules and role/emoji multipliers. Upvotes
    add weight and downvotes subtract it; the repository saves the votes and
    recalculates the score in the same transaction.

    Returned entry keys tell the bot which displays need refreshing. The renderer
    decides whether to create, update, or remove the Discord post from the score
    and the board's publication thresholds.
    """

    def __init__(self, *, uow_factory: UnitOfWorkFactory[StarboardUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    @staticmethod
    def _weight_policy(repository: StarboardRepository) -> RoleWeightPolicy:
        async def multipliers(scope: WeightScope) -> tuple[RoleMultiplier, ...]:
            configured = await repository.role_multipliers(scope.scope_id or 0)
            return tuple(RoleMultiplier(scope, role_id, value) for role_id, value in configured.items())

        return RoleWeightPolicy(multipliers, staff_multiplier=1.0)

    async def is_relevant_emoji(self, guild_id: int, emoji: str) -> bool:
        # Every process observes committed alias changes, including edits whose UI refresh failed.
        async with self._uow_factory() as uow:
            return emoji in await uow.starboards.relevant_emojis(guild_id)

    async def record_vote(self, origin: OriginMessage, actor: ReactionActor, emoji: str) -> StarboardVoteResult:
        """Record accepted votes across matching boards in one transaction.

        A member has one vote per origin and board, so another emoji replaces it.
        Removal is requested if any rejecting board asks for cleanup, even when
        another board accepts the same reaction.
        """
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
        """Replace the origin's stored votes from a complete reaction snapshot.

        Later accepted reactions win when a member has several emojis on the same
        board. The repository removes old votes across all boards for this origin,
        including votes whose boards no longer accept the source.
        """
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

    async def snapshot(self, starboard_id: int) -> StarboardSnapshot | None:
        async with self._uow_factory() as uow:
            config = await uow.starboards.get_by_id(starboard_id, for_update=True)
            if config is None:
                return None
            return StarboardSnapshot.capture(config, dict(await uow.starboards.role_multipliers(starboard_id)))

    async def reserve_id(self) -> int:
        """Allocate an unused identity before issuing a creation command."""
        async with self._uow_factory() as uow:
            return await uow.starboards.reserve_id()

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
            return created

    async def delete_starboard(self, guild_id: int, name: str) -> bool:
        async with self._uow_factory() as uow:
            deleted = await uow.starboards.delete(guild_id, name)
            await uow.commit()
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
            if "name" in settings:
                await uow.starboards.lock_names(guild_id)
            for key, value in settings.items():
                setattr(current, key, value)
            current.validate()
            await uow.commit()
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

    async def set_role_multiplier(self, config: StarboardConfig, role_id: int, multiplier: float | None) -> None:
        async with self._uow_factory() as uow:
            if multiplier is not None and (not isfinite(multiplier) or multiplier <= 0):
                msg = tr(t"starboard-role-multiplier-finite = Role multiplier must be finite and greater than zero.")
                raise ValidationError(msg)
            await uow.starboards.set_role_multiplier(config.id, role_id, multiplier)
            await uow.commit()

    async def entry_state(self, starboard_id: int, origin_message_id: int) -> EntryState | None:
        """Return one entry's config, origin and score, for rendering."""
        async with self._uow_factory() as uow:
            return await uow.starboards.entry_state(starboard_id, origin_message_id)

    async def mark_rendered(self, starboard_id: int, origin_message_id: int, score: float) -> None:
        """Record the score a post now shows, so an unchanged entry is not re-edited."""
        async with self._uow_factory() as uow:
            await uow.starboards.mark_rendered(starboard_id, origin_message_id, score)
            await uow.commit()

    async def referenced_channels(self, guild_id: int) -> frozenset[int]:
        """Return the guild's channels that enabled boards post to or read from.

        Sources that cover every channel in the guild are not listed. Disabled boards are left
        out because nothing posts to them, so a deleted destination is not checked again after
        `forget_channels` has disabled its board.
        """
        async with self._uow_factory() as uow:
            return await uow.starboards.referenced_channels(guild_id)

    async def forget_channels(self, guild_id: int, channel_ids: Collection[int]) -> None:
        """Stop using channels of this guild that Discord has deleted.

        A board whose destination is gone is disabled rather than deleted, so an administrator
        can point it at a new channel and keep its configuration. A source channel that is gone
        is removed from every board that read from it.
        """
        async with self._uow_factory() as uow:
            await uow.starboards.forget_channels(guild_id, channel_ids)
            await uow.commit()
