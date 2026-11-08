"""Voting application services."""

import logging
from collections.abc import Sequence
from math import isfinite

from whenever import Instant

from squid.unit_of_work import UnitOfWorkFactory
from squid.voting.application.policies import RoleVoteWeightPolicy
from squid.voting.application.ports import VoteActorResolver, VoteUnitOfWork, VoteWeightPolicy
from squid.voting.domain import (
    DEFAULT_GENERIC_EMOJIS,
    DEFAULT_VOTE_OPTIONS,
    MAX_POLL_DURATION_SECONDS,
    MIN_POLL_DURATION_SECONDS,
    CastVoteResult,
    DeleteLogVoteTarget,
    EmojiPreset,
    PollScope,
    RoleWeight,
    VoteActor,
    VoteChange,
    VoteChoice,
    VoteKind,
    VoteOption,
    VoteRefreshResult,
    VoteRejection,
    VoteSelection,
    VoteSessionSnapshot,
    VoteVisibility,
    normalize_vote_options,
)
from squid.voting.errors import InvalidVoteConfigurationError

logger = logging.getLogger(__name__)


class VoteService:
    """Check voter eligibility and manage voting sessions through the repository.

    Discord membership and resolved capabilities determine each ballot's weight.
    Existing weights are refreshed before a ballot changes or a poll closes, so
    role changes can affect the outcome. The repository applies those weights and
    any threshold closure together; this service commits the transaction.

    Results describe the resulting voting session. Publishing its Discord messages
    and acting on an approved build or deletion vote happen outside this service.
    """

    def __init__(
        self,
        policy: VoteWeightPolicy | None = None,
        actor_resolver: VoteActorResolver | None = None,
        *,
        uow_factory: UnitOfWorkFactory[VoteUnitOfWork],
        build_owner_guild_id: int | None = None,
    ):
        self._uow_factory = uow_factory
        self._policy = policy or RoleVoteWeightPolicy(self.get_role_weights)
        self._actor_resolver = actor_resolver
        self._build_owner_guild_id = build_owner_guild_id

    def set_actor_resolver(self, resolver: VoteActorResolver) -> None:
        """Attach the presentation adapter that can resolve current guild-member facts."""
        self._actor_resolver = resolver

    def _owner_guild_id(self, snapshot: VoteSessionSnapshot) -> int | None:
        """Return the guild whose role weights govern this session.

        Role ids only mean anything inside one guild, so a session shared across
        guilds has to pick one authority; otherwise any guild hosting a card could
        set its own multipliers and decide the shared outcome.
        """
        match snapshot.kind:
            case VoteKind.BUILD:
                return self._build_owner_guild_id
            case VoteKind.DELETE_LOG:
                return snapshot.target.server_id if isinstance(snapshot.target, DeleteLogVoteTarget) else None
            case VoteKind.GENERIC:
                return snapshot.poll.guild_id if snapshot.poll is not None else None

    async def _weight_actor(
        self, actor: VoteActor, snapshot: VoteSessionSnapshot, owner_guild_id: int | None
    ) -> VoteActor | None:
        """Resolve the voter in the guild whose role weights govern the session.

        With no owner guild, retain the supplied actor for eligibility checks. With no
        resolver, use an actor without roles or capabilities for a different owner
        guild. A failed lookup returns None; casting and refresh handle that separately.
        """
        if owner_guild_id is None or actor.guild_id == owner_guild_id:
            return actor
        if self._actor_resolver is None:
            return VoteActor(account_id=actor.account_id, discord_id=actor.discord_id, guild_id=owner_guild_id)
        return await self._actor_resolver.resolve(actor.account_id, owner_guild_id, snapshot.kind)

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
        async with self._uow_factory() as uow:
            options = normalize_vote_options(options, kind=VoteKind.BUILD)
            result = await uow.votes.create_build_session(
                author_account_id=author_account_id,
                pass_threshold=pass_threshold,
                fail_threshold=fail_threshold,
                build_id=build_id,
                changes=changes,
                options=options,
            )
            await uow.commit()
            return result

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
        """Return the earliest review session for a build, creating one if none exists.

        The repository serializes creation per build so event retries and concurrent
        submission paths reuse the same session, including after it has closed.
        """
        async with self._uow_factory() as uow:
            options = normalize_vote_options(options, kind=VoteKind.BUILD)
            result = await uow.votes.get_or_create_build_submission_session(
                author_account_id=author_account_id,
                pass_threshold=pass_threshold,
                fail_threshold=fail_threshold,
                build_id=build_id,
                changes=changes,
                options=options,
            )
            await uow.commit()
            return result

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
        async with self._uow_factory() as uow:
            options = normalize_vote_options(options, kind=VoteKind.DELETE_LOG)
            result = await uow.votes.create_delete_log_session(
                author_account_id=author_account_id,
                pass_threshold=pass_threshold,
                fail_threshold=fail_threshold,
                message_id=message_id,
                channel_id=channel_id,
                server_id=server_id,
                options=options,
            )
            await uow.commit()
            return result

    async def create_generic_poll(
        self,
        *,
        author_account_id: int,
        question: str,
        visibility: VoteVisibility,
        duration_seconds: int,
        options: Sequence[VoteOption],
        guild_id: int | None = None,
        scope: PollScope = PollScope.GUILD,
        now: Instant | None = None,
    ) -> int:
        """Validate and persist a poll with a deadline derived from its duration.

        Publication is separate, so creating a poll does not require a Discord message.
        Network polls require an owning guild and options usable in every guild.
        """
        async with self._uow_factory() as uow:
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
            if scope is PollScope.NETWORK:
                if guild_id is None:
                    # The owning guild weighs the ballots cast on every card.
                    msg = "A network poll must belong to a guild."
                    raise InvalidVoteConfigurationError(msg)
                if any(option.guild_id is not None for option in options):
                    # Guild-scoped aliases would leave the other guilds' cards unvotable.
                    msg = "Network poll options must not be scoped to one guild."
                    raise InvalidVoteConfigurationError(msg)
            result = await uow.votes.create_generic_session(
                author_account_id=author_account_id,
                question=question.strip(),
                visibility=visibility,
                deadline=(now or Instant.now()).add(seconds=duration_seconds),
                options=options,
                guild_id=guild_id,
                scope=scope,
            )
            await uow.commit()
            return result

    async def get_session(self, vote_session_id: int) -> VoteSessionSnapshot | None:
        async with self._uow_factory() as uow:
            return await uow.votes.get_by_id(vote_session_id)

    async def get_session_for_message(self, message_id: int) -> VoteSessionSnapshot | None:
        """Return the session a Discord card displays, so the bot can turn a message into a session."""
        async with self._uow_factory() as uow:
            return await uow.votes.get_by_message(message_id)

    async def attach_message(self, vote_session_id: int, message_id: int) -> None:
        """Attach an observed presentation message to a session idempotently."""
        async with self._uow_factory() as uow:
            await uow.votes.attach_message(vote_session_id, message_id)
            await uow.commit()

    async def list_open(self, kind: VoteKind) -> Sequence[VoteSessionSnapshot]:
        async with self._uow_factory() as uow:
            return await uow.votes.list_open(kind)

    async def cast_vote(
        self, vote_session_id: int, actor: VoteActor, option_id: str, *, emoji: str | None = None
    ) -> CastVoteResult:
        """Validate eligibility, refresh weights, and toggle or replace the actor's ballot.

        The ballot is cast in `actor.guild_id`, which must be a guild the session is
        published in; that guild's aliases decide which emoji the option goes by.
        `emoji` picks one alias when the caller knows it, as a reaction does;
        otherwise the guild's first alias for the option is used.

        Selecting the same option again removes the ballot, even with another alias.
        The repository serializes the mutation and any threshold closure. An unresolved
        owner-guild lookup uses base weight 1 for the new ballot, before the option
        multiplier. Unresolved existing ballots retain their cached weights.
        """
        snapshot = await self.get_session(vote_session_id)
        if snapshot is None:
            return CastVoteResult(session=None, rejection=VoteRejection.NOT_FOUND)
        if not snapshot.is_open:
            return CastVoteResult(session=snapshot, rejection=VoteRejection.CLOSED)
        if actor.guild_id not in {message.guild_id for message in snapshot.messages}:
            return CastVoteResult(session=snapshot, rejection=VoteRejection.WRONG_GUILD)
        option = snapshot.option_by_id(option_id, actor.guild_id, emoji=emoji)
        if option is None:
            return CastVoteResult(session=snapshot, rejection=VoteRejection.INVALID_OPTION)

        # The option's alias comes from where the ballot was cast; role weights come from the owner guild.
        owner_guild_id = self._owner_guild_id(snapshot)
        weight_actor = await self._weight_actor(actor, snapshot, owner_guild_id)
        if weight_actor is None:
            # The owner guild is unreachable right now. Land the ballot at the
            # default weight rather than letting a Discord blip veto the vote; the
            # next refresh corrects it.
            logger.warning(
                "Vote session %s could not resolve account %s in owner guild %s; weighting at 1.0",
                snapshot.id,
                actor.account_id,
                owner_guild_id,
                extra={"squid.vote.session_id": snapshot.id},
            )
            weight = 1.0
        else:
            calculated = await self._policy.calculate(weight_actor, snapshot)
            if calculated is None:
                return CastVoteResult(session=snapshot, rejection=VoteRejection.NOT_ELIGIBLE)
            # Without a designated authority no guild's role table may speak for the
            # session, but eligibility still had to be judged.
            weight = calculated if owner_guild_id is not None else 1.0
        if not isfinite(weight) or weight <= 0:
            msg = "Vote policies must return a positive finite weight."
            raise InvalidVoteConfigurationError(msg)
        weight *= option.multiplier

        # Refresh earlier ballots too: role changes may alter the score that closes this vote.
        refreshed, unresolved = await self._calculate_refresh(snapshot, replacing=weight_actor)
        if unresolved:
            logger.warning(
                "Vote session %s refresh retained cached weights for accounts %s",
                snapshot.id,
                unresolved,
                extra={"squid.vote.session_id": snapshot.id},
            )
        async with self._uow_factory() as uow:
            mutation = await uow.votes.cast_vote(
                vote_session_id, actor.account_id, actor.guild_id, option, weight, refreshed
            )
            await uow.commit()
        if mutation is None:
            # The session closed or vanished after it was read above.
            latest = await self.get_session(vote_session_id)
            rejection = VoteRejection.CLOSED if latest is not None else VoteRejection.NOT_FOUND
            return CastVoteResult(session=latest, rejection=rejection)
        return CastVoteResult(
            session=mutation.session,
            previous_weight=mutation.previous_weight,
            current_weight=mutation.current_weight,
            just_closed=mutation.just_closed,
        )

    async def refresh(self, vote_session_id: int) -> VoteRefreshResult:
        """Recalculate resolvable ballot weights and check the session's existing thresholds."""
        snapshot = await self.get_session(vote_session_id)
        if snapshot is None:
            return VoteRefreshResult(None)
        return await self._refresh_snapshot(snapshot)

    async def _refresh_snapshot(self, snapshot: VoteSessionSnapshot) -> VoteRefreshResult:
        weights, unresolved = await self._calculate_refresh(snapshot)
        async with self._uow_factory() as uow:
            mutation = await uow.votes.refresh_weights(snapshot.id, weights)
            await uow.commit()
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

    async def close(self, vote_session_id: int, actor: VoteActor) -> CastVoteResult:
        """Refresh and close a generic poll if its author or an authorized actor requests it."""
        snapshot = await self.get_session(vote_session_id)
        if snapshot is None:
            return CastVoteResult(None, VoteRejection.NOT_FOUND)
        rejection = snapshot.can_close(actor)
        if rejection is not None:
            return CastVoteResult(snapshot, rejection)
        return await self._close(snapshot)

    async def _close(self, snapshot: VoteSessionSnapshot) -> CastVoteResult:
        """Refresh weights so the final tally is current, then close as cancelled."""
        await self._refresh_snapshot(snapshot)
        async with self._uow_factory() as uow:
            mutation = await uow.votes.close(snapshot.id)
            await uow.commit()
        if mutation is None:
            return CastVoteResult(snapshot, VoteRejection.CLOSED)
        return CastVoteResult(mutation.session, just_closed=mutation.just_closed)

    async def close_due(self, now: Instant | None = None) -> Sequence[VoteSessionSnapshot]:
        """Close all expired generic polls; safe to call repeatedly after restarts."""
        closed: list[VoteSessionSnapshot] = []
        async with self._uow_factory() as uow:
            due = await uow.votes.list_due(now or Instant.now())
        for snapshot in due:
            result = await self._close(snapshot)
            if result.just_closed and result.session is not None:
                closed.append(result.session)
        return closed

    async def emoji_preset(self, guild_id: int, kind: VoteKind) -> EmojiPreset:
        async with self._uow_factory() as uow:
            preset = await uow.votes.get_emoji_preset(guild_id, kind)
            if preset is not None:
                return preset
            if kind is VoteKind.GENERIC:
                options = tuple(
                    VoteOption(
                        emoji, VoteChoice.GENERIC, identifier=str(index), guild_id=guild_id, label=f"Option {index}"
                    )
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
        async with self._uow_factory() as uow:
            await uow.votes.set_emoji_preset(EmojiPreset(guild_id, kind, normalize_vote_options(options, kind=kind)))
            await uow.commit()

    async def get_role_weights(self, guild_id: int, kind: VoteKind) -> Sequence[RoleWeight]:
        async with self._uow_factory() as uow:
            return await uow.votes.get_role_weights(guild_id, kind)

    async def set_role_weight(self, weight: RoleWeight) -> None:
        async with self._uow_factory() as uow:
            await uow.votes.set_role_weight(weight)
            await uow.commit()
        await self._refresh_kind(weight.guild_id, weight.kind)

    async def remove_role_weight(self, guild_id: int, kind: VoteKind, role_id: int) -> None:
        async with self._uow_factory() as uow:
            await uow.votes.remove_role_weight(guild_id, kind, role_id)
            await uow.commit()
        await self._refresh_kind(guild_id, kind)

    async def reset_configuration(self, guild_id: int, kind: VoteKind | None = None) -> None:
        async with self._uow_factory() as uow:
            await uow.votes.reset_configuration(guild_id, kind)
            await uow.commit()
        for current_kind in (kind,) if kind is not None else tuple(VoteKind):
            await self._refresh_kind(guild_id, current_kind)

    async def _refresh_kind(self, guild_id: int, kind: VoteKind) -> None:
        """Refresh open sessions whose owner guild supplies the changed role weights."""
        for snapshot in await self.list_open(kind):
            if self._owner_guild_id(snapshot) == guild_id:
                await self._refresh_snapshot(snapshot)

    async def _calculate_refresh(
        self, snapshot: VoteSessionSnapshot, *, replacing: VoteActor | None = None
    ) -> tuple[dict[int, float], list[int]]:
        """Calculate replacement weights without changing stored ballots.

        A zero removes an ineligible voter. Unresolved accounts are returned separately
        and omitted from the weight map, preserving their stored weights. Without a
        resolver, return no updates.
        """
        if self._actor_resolver is None:
            return {}, []
        owner_guild_id = self._owner_guild_id(snapshot)
        weights: dict[int, float] = {}
        unresolved: list[int] = []
        for selection in snapshot.selections:
            actor = replacing if replacing is not None and replacing.account_id == selection.account_id else None
            actor = actor or await self._actor_resolver.resolve(
                selection.account_id,
                owner_guild_id if owner_guild_id is not None else selection.guild_id or 0,
                snapshot.kind,
            )
            if actor is None:
                unresolved.append(selection.account_id)
                continue
            weight = await self._policy.calculate(actor, snapshot)
            if weight is None:
                weights[selection.account_id] = 0
            elif isfinite(weight) and weight > 0:
                # Mirror the cast path: no authority guild means no role multipliers.
                scored = weight if owner_guild_id is not None else 1.0
                weights[selection.account_id] = scored * self._option_multiplier(snapshot, selection)
        return weights, unresolved

    @staticmethod
    def _option_multiplier(snapshot: VoteSessionSnapshot, selection: VoteSelection) -> float:
        """Return the multiplier of the option this ballot picked.

        Matched against the guild the ballot was cast in, which still aliases
        options even though it no longer decides the voter's weight.
        """
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
        return option.multiplier if option is not None else 1
