"""Voting application services."""

import logging
from collections.abc import Sequence
from math import isfinite

from whenever import Instant

from squid.voting.application.policies import RoleVoteWeightPolicy
from squid.voting.application.ports import VoteActorResolver, VoteRepository, VoteWeightPolicy
from squid.voting.domain import (
    DEFAULT_GENERIC_EMOJIS,
    DEFAULT_VOTE_OPTIONS,
    MAX_POLL_DURATION_SECONDS,
    MIN_POLL_DURATION_SECONDS,
    CastVoteResult,
    EmojiPreset,
    RoleWeight,
    VoteActor,
    VoteChange,
    VoteChoice,
    VoteKind,
    VoteOption,
    VoteRefreshResult,
    VoteRejection,
    VoteSessionSnapshot,
    VoteVisibility,
    normalize_vote_options,
)
from squid.voting.errors import InvalidVoteConfigurationError

logger = logging.getLogger(__name__)


class VoteService:
    """Own voting authorization, policy evaluation, configuration, and closure."""

    def __init__(
        self,
        repository: VoteRepository,
        policy: VoteWeightPolicy | None = None,
        actor_resolver: VoteActorResolver | None = None,
    ):
        self._repository = repository
        provider = getattr(repository, "get_role_weights", self._empty_role_weights)
        self._policy = policy or RoleVoteWeightPolicy(provider)
        self._actor_resolver = actor_resolver

    def set_actor_resolver(self, resolver: VoteActorResolver) -> None:
        """Attach the presentation adapter that can resolve current guild-member facts."""
        self._actor_resolver = resolver

    @staticmethod
    async def _empty_role_weights(guild_id: int, kind: str) -> Sequence[RoleWeight]:
        return ()

    async def start_build_vote(
        self,
        *,
        author_account_id: int,
        pass_threshold: int,
        fail_threshold: int,
        build_id: int,
        changes: Sequence[VoteChange],
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
    ) -> int:
        """Create a build vote and its target atomically."""
        options = normalize_vote_options(options, kind=VoteKind.BUILD)
        return await self._repository.create_build_session(
            author_account_id=author_account_id,
            pass_threshold=pass_threshold,
            fail_threshold=fail_threshold,
            build_id=build_id,
            changes=changes,
            options=options,
        )

    async def ensure_build_submission_vote(
        self,
        *,
        author_account_id: int,
        pass_threshold: int,
        fail_threshold: int,
        build_id: int,
        changes: Sequence[VoteChange],
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
    ) -> int:
        """Return the one initial review session for a submitted build.

        Domain events are delivered at least once and the legacy Discord submit path
        can race the event consumer. The repository serializes both callers by build
        so they converge on one session.
        """
        options = normalize_vote_options(options, kind=VoteKind.BUILD)
        return await self._repository.get_or_create_build_submission_session(
            author_account_id=author_account_id,
            pass_threshold=pass_threshold,
            fail_threshold=fail_threshold,
            build_id=build_id,
            changes=changes,
            options=options,
        )

    async def start_delete_log_vote(
        self,
        *,
        author_account_id: int,
        pass_threshold: int,
        fail_threshold: int,
        message_id: int,
        channel_id: int,
        server_id: int,
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
    ) -> int:
        """Create a message-deletion vote and its target atomically."""
        options = normalize_vote_options(options, kind=VoteKind.DELETE_LOG)
        return await self._repository.create_delete_log_session(
            author_account_id=author_account_id,
            pass_threshold=pass_threshold,
            fail_threshold=fail_threshold,
            message_id=message_id,
            channel_id=channel_id,
            server_id=server_id,
            options=options,
        )

    async def create_generic_poll(
        self,
        *,
        author_account_id: int,
        question: str,
        visibility: VoteVisibility,
        duration_seconds: int,
        options: Sequence[VoteOption],
        guild_id: int | None = None,
        now: Instant | None = None,
    ) -> int:
        """Create a generic poll independently of where it will be shown.

        Creation takes a duration rather than a deadline, and takes no channel at all:
        the card is a `discord_posts` row that the reconciler adopts afterwards, so a
        Discord outage after this returns loses a message, not a poll.
        """
        if not question.strip():
            msg = "Poll question cannot be empty."
            raise InvalidVoteConfigurationError(msg)
        if not MIN_POLL_DURATION_SECONDS <= duration_seconds <= MAX_POLL_DURATION_SECONDS:
            msg = "Poll duration must be between 1 minute and 30 days."
            raise InvalidVoteConfigurationError(msg, context={"duration_seconds": duration_seconds})
        options = normalize_vote_options(options, kind=VoteKind.GENERIC)
        if guild_id is not None and any(option.guild_id not in (None, guild_id) for option in options):
            msg = "Poll options must belong to the poll guild."
            raise InvalidVoteConfigurationError(msg)
        return await self._repository.create_generic_session(
            author_account_id=author_account_id,
            question=question.strip(),
            visibility=visibility,
            deadline=(now or Instant.now()).add(seconds=duration_seconds),
            options=options,
            guild_id=guild_id,
        )

    async def get_session(self, message_id: int) -> VoteSessionSnapshot | None:
        return await self._repository.get_by_message(message_id)

    async def get_session_by_id(self, vote_session_id: int) -> VoteSessionSnapshot | None:
        return await self._repository.get_by_id(vote_session_id)

    async def list_open(self, kind: VoteKind) -> Sequence[VoteSessionSnapshot]:
        return await self._repository.list_open(kind)

    async def cast_vote_by_session(
        self,
        vote_session_id: int,
        actor: VoteActor,
        option_id: str,
    ) -> CastVoteResult:
        """Cast a vote using transport-neutral session and option identifiers."""
        snapshot = await self._repository.get_by_id(vote_session_id)
        if snapshot is None:
            return CastVoteResult(session=None, rejection=VoteRejection.NOT_FOUND)
        message = next((item for item in snapshot.messages if item.guild_id == actor.guild_id), None)
        if message is None:
            return CastVoteResult(session=snapshot, rejection=VoteRejection.WRONG_GUILD)
        option = snapshot.option_by_id(option_id, message.guild_id)
        if option is None:
            return CastVoteResult(session=snapshot, rejection=VoteRejection.INVALID_OPTION)
        return await self.cast_vote(message.id, actor, option.emoji)

    async def cast_vote(self, message_id: int, actor: VoteActor, emoji: str) -> CastVoteResult:
        snapshot = await self._repository.get_by_message(message_id)
        if snapshot is None:
            return CastVoteResult(session=None, rejection=VoteRejection.NOT_FOUND)
        if not snapshot.is_open:
            return CastVoteResult(session=snapshot, rejection=VoteRejection.CLOSED)
        message = next((item for item in snapshot.messages if item.id == message_id), None)
        message_guild_id = message.guild_id if message is not None else actor.guild_id
        if actor.guild_id and message_guild_id and actor.guild_id != message_guild_id:
            return CastVoteResult(session=snapshot, rejection=VoteRejection.WRONG_GUILD)

        option = snapshot.option_by_emoji(emoji, message_guild_id)
        if option is None:
            return CastVoteResult(session=snapshot, rejection=VoteRejection.INVALID_OPTION)
        weight = await self._policy.calculate(actor, snapshot, emoji)
        if weight is None:
            return CastVoteResult(session=snapshot, rejection=VoteRejection.NOT_ELIGIBLE)
        if not isfinite(weight) or weight <= 0:
            msg = "Vote policies must return a positive finite weight."
            raise InvalidVoteConfigurationError(msg)
        weight *= option.multiplier

        refreshed, unresolved = await self._calculate_refresh(snapshot, replacing=actor)
        if unresolved:
            logger.warning(
                "Vote session %s refresh retained cached weights for accounts %s",
                snapshot.id,
                unresolved,
                extra={"squid.vote.session_id": snapshot.id},
            )
        mutation = await self._repository.cast_vote(
            message_id,
            actor.account_id,
            actor.discord_id,
            actor.guild_id or message_guild_id,
            option.id,
            emoji,
            weight,
            refreshed,
        )
        if mutation is None:
            latest = await self._repository.get_by_message(message_id)
            rejection = VoteRejection.CLOSED if latest is not None else VoteRejection.NOT_FOUND
            return CastVoteResult(session=latest, rejection=rejection)
        return CastVoteResult(
            session=mutation.session,
            previous_weight=mutation.previous_weight,
            current_weight=mutation.current_weight,
            just_closed=mutation.just_closed,
        )

    async def refresh(self, message_id: int) -> VoteRefreshResult:
        """Explicitly recompute all resolvable cached weights and thresholds."""
        snapshot = await self._repository.get_by_message(message_id)
        if snapshot is None:
            return VoteRefreshResult(None)
        weights, unresolved = await self._calculate_refresh(snapshot)
        mutation = await self._repository.refresh_weights(snapshot.id, weights)
        if unresolved:
            logger.warning(
                "Vote session %s refresh retained cached weights for users %s",
                snapshot.id,
                unresolved,
                extra={"squid.vote.session_id": snapshot.id},
            )
        return VoteRefreshResult(
            mutation.session if mutation is not None else snapshot,
            tuple(unresolved),
            mutation.just_closed if mutation is not None else False,
        )

    async def close(self, message_id: int, actor: VoteActor) -> CastVoteResult:
        """Close a generic poll when requested by its creator or guild staff."""
        snapshot = await self._repository.get_by_message(message_id)
        if snapshot is None:
            return CastVoteResult(None, VoteRejection.NOT_FOUND)
        rejection = snapshot.can_close(actor)
        if rejection is not None:
            return CastVoteResult(snapshot, rejection)
        await self.refresh(message_id)
        mutation = await self._repository.close(message_id)
        if mutation is None:
            return CastVoteResult(snapshot, VoteRejection.CLOSED)
        return CastVoteResult(mutation.session, just_closed=mutation.just_closed)

    async def close_due(self, now: Instant | None = None) -> Sequence[VoteSessionSnapshot]:
        """Close all expired generic polls; safe to call repeatedly after restarts."""
        closed: list[VoteSessionSnapshot] = []
        for snapshot in await self._repository.list_due(now or Instant.now()):
            if snapshot.messages:
                await self.refresh(snapshot.messages[0].id)
            else:
                weights, unresolved = await self._calculate_refresh(snapshot)
                await self._repository.refresh_weights(snapshot.id, weights)
                if unresolved:
                    logger.warning(
                        "Due poll %s retained unresolved cached weights for %s",
                        snapshot.id,
                        unresolved,
                        extra={"squid.vote.session_id": snapshot.id},
                    )
            mutation = await self._repository.close_by_id(snapshot.id)
            if mutation is not None and mutation.just_closed:
                closed.append(mutation.session)
        return closed

    async def emoji_preset(self, guild_id: int, kind: VoteKind) -> EmojiPreset:
        preset = await self._repository.get_emoji_preset(guild_id, kind)
        if preset is not None:
            return preset
        if kind is VoteKind.GENERIC:
            options = tuple(
                VoteOption(emoji, VoteChoice.GENERIC, identifier=str(index), guild_id=guild_id, label=f"Option {index}")
                for index, emoji in enumerate(DEFAULT_GENERIC_EMOJIS, 1)
            )
        else:
            options = tuple(
                VoteOption(
                    option.emoji,
                    option.choice,
                    identifier=option.identifier,
                    guild_id=guild_id,
                    position=option.position,
                )
                for option in DEFAULT_VOTE_OPTIONS
            )
        return EmojiPreset(guild_id, kind, options)

    async def set_emoji_preset(self, guild_id: int, kind: VoteKind, options: Sequence[VoteOption]) -> None:
        await self._repository.set_emoji_preset(EmojiPreset(guild_id, kind, normalize_vote_options(options, kind=kind)))

    async def get_role_weights(self, guild_id: int, kind: VoteKind) -> Sequence[RoleWeight]:
        return await self._repository.get_role_weights(guild_id, kind)

    async def set_role_weight(self, weight: RoleWeight) -> None:
        await self._repository.set_role_weight(weight)
        await self._refresh_kind(weight.guild_id, weight.kind)

    async def remove_role_weight(self, guild_id: int, kind: VoteKind, role_id: int) -> None:
        await self._repository.remove_role_weight(guild_id, kind, role_id)
        await self._refresh_kind(guild_id, kind)

    async def reset_configuration(self, guild_id: int, kind: VoteKind | None = None) -> None:
        await self._repository.reset_configuration(guild_id, kind)
        for current_kind in (kind,) if kind is not None else tuple(VoteKind):
            await self._refresh_kind(guild_id, current_kind)

    async def _refresh_kind(self, guild_id: int, kind: VoteKind) -> None:
        for snapshot in await self.list_open(kind):
            if any(message.guild_id == guild_id for message in snapshot.messages):
                await self.refresh(snapshot.messages[0].id)

    async def _calculate_refresh(
        self, snapshot: VoteSessionSnapshot, *, replacing: VoteActor | None = None
    ) -> tuple[dict[int, float], list[int]]:
        if self._actor_resolver is None:
            return {}, []
        weights: dict[int, float] = {}
        unresolved: list[int] = []
        for selection in snapshot.selections:
            actor = replacing if replacing is not None and replacing.account_id == selection.account_id else None
            actor = actor or await self._actor_resolver.resolve(
                selection.account_id,
                selection.discord_id,
                selection.guild_id,
                snapshot.kind,
            )
            if actor is None:
                unresolved.append(selection.account_id)
                continue
            weight = await self._policy.calculate(actor, snapshot, selection.emoji)
            if weight is None:
                weights[selection.account_id] = 0
            elif isfinite(weight) and weight > 0:
                option = next(
                    (
                        item
                        for item in snapshot.options
                        if item.identifier == selection.option_id
                        and item.emoji == selection.emoji
                        and item.guild_id in (None, selection.guild_id)
                    ),
                    None,
                )
                weights[selection.account_id] = weight * (option.multiplier if option is not None else 1)
        return weights, unresolved
