"""Voting application services."""

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from math import isfinite

import anyio
from whenever import Instant

from squid.core.actors import ActingContext, Actor, UserActor, WorkerActor, application_operation
from squid.core.errors import AuthorizationError
from squid.core.permissions.application import PermissionService
from squid.core.permissions.domain.catalogue import (
    VOTE_LOG_DELETE_CAST,
    VOTE_POLL_CAST,
    VOTE_POLL_CLOSE_ANY,
    VOTE_POLL_CREATE,
    VOTE_POLL_NETWORK_CREATE,
    VOTE_POLL_RECOUNT,
    VOTE_WEIGHT_STAFF,
)
from squid.core.unit_of_work import UnitOfWorkFactory
from squid.core.voting.application.policies import RoleVoteWeightPolicy
from squid.core.voting.application.ports import VoteActorResolver, VoteUnitOfWork, VoteWeightPolicy
from squid.core.voting.domain import (
    DEFAULT_GENERIC_EMOJIS,
    DEFAULT_VOTE_OPTIONS,
    MAX_POLL_DURATION_SECONDS,
    MIN_POLL_DURATION_SECONDS,
    CastVoteResult,
    DeleteLogVoteTarget,
    EmojiPreset,
    PollScope,
    RoleWeight,
    VoteChange,
    VoteChoice,
    VoteKind,
    VoteOption,
    VoteRefreshResult,
    VoteRejection,
    VoteSelection,
    VoteSessionSnapshot,
    VoteStatus,
    VoteVisibility,
    normalize_vote_options,
)
from squid.core.voting.domain.models import VoteConfiguration
from squid.core.voting.errors import InvalidVoteConfigurationError
from squid.core.worker_context import worker_scope

logger = logging.getLogger(__name__)


type FinalRecount = Callable[[int, int], Awaitable[tuple[bool, tuple[int, ...]]]]


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
        self._permissions = PermissionService(uow_factory=uow_factory)
        self._policy = policy or RoleVoteWeightPolicy(
            lambda guild, kind: self.get_role_weights(guild, kind, context=ActingContext.current())
        )
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
            case VoteKind.BUILD | VoteKind.BUILD_EDIT:
                return self._build_owner_guild_id
            case VoteKind.DELETE_LOG:
                return snapshot.target.server_id if isinstance(snapshot.target, DeleteLogVoteTarget) else None
            case VoteKind.GENERIC:
                return snapshot.poll.guild_id if snapshot.poll is not None else None

    async def _weight_actor(
        self, actor: UserActor, snapshot: VoteSessionSnapshot, owner_guild_id: int | None
    ) -> UserActor | None:
        """Resolve the voter in the guild whose role weights govern the session.

        With no owner guild, retain the supplied actor for eligibility checks. With no
        resolver, use an actor without roles or capabilities for a different owner
        guild. A failed lookup returns None; casting and refresh handle that separately.
        """
        if owner_guild_id is None or actor.guild_id == owner_guild_id:
            return actor
        if self._actor_resolver is None:
            return UserActor(
                account_id=actor.require_account_id(), discord_user_id=actor.discord_user_id, guild_id=owner_guild_id
            )
        return await self._actor_resolver.resolve(actor.require_account_id(), owner_guild_id, snapshot.kind)

    @application_operation
    async def start_build_vote(
        self,
        *,
        author_account_id: int,
        pass_threshold: int,
        fail_threshold: int,
        build_id: int,
        changes: Sequence[VoteChange],
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
        context: ActingContext,
    ) -> int:
        """Create a build vote and its target atomically."""
        context.require_worker()
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

    @application_operation
    async def ensure_build_submission_vote(
        self,
        *,
        author_account_id: int,
        pass_threshold: int,
        fail_threshold: int,
        build_id: int,
        changes: Sequence[VoteChange],
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
        context: ActingContext,
    ) -> int:
        """Return the earliest review session for a build, creating one if none exists.

        The repository serializes creation per build so event retries and concurrent
        submission paths reuse the same session, including after it has closed.
        """
        context.require_worker()
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

    @application_operation
    async def start_delete_log_vote(
        self,
        *,
        pass_threshold: int,
        fail_threshold: int,
        message_id: int,
        channel_id: int,
        server_id: int,
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
        context: ActingContext,
    ) -> int:
        """Create a message-deletion vote and its target atomically."""
        author_account_id = context.effective.require_account_id()
        if context.effective.require_user().guild_id != server_id:
            raise AuthorizationError
        async with self._uow_factory() as uow:
            await self._permissions.require(context.effective, VOTE_POLL_CREATE, uow=uow)
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
        context: ActingContext,
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
        actor = context.effective.require_user()
        author_account_id = actor.require_account_id()
        if actor.guild_id != guild_id:
            raise AuthorizationError
        with context.scope():
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
                    await self._permissions.require(actor, VOTE_POLL_NETWORK_CREATE, uow=uow)
                await self._permissions.require(actor, VOTE_POLL_CREATE, uow=uow)
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

    @application_operation
    async def get_session(self, vote_session_id: int, *, context: ActingContext) -> VoteSessionSnapshot | None:
        async with self._uow_factory() as uow:
            return await uow.votes.get_by_id(vote_session_id)

    @application_operation
    async def get_session_for_message(self, message_id: int, *, context: ActingContext) -> VoteSessionSnapshot | None:
        """Return the session a Discord card displays, so the bot can turn a message into a session."""
        async with self._uow_factory() as uow:
            return await uow.votes.get_by_message(message_id)

    @application_operation
    async def attach_message(self, vote_session_id: int, message_id: int, *, context: ActingContext) -> None:
        """Attach an observed presentation message to a session idempotently."""
        async with self._uow_factory() as uow:
            await uow.votes.attach_message(vote_session_id, message_id)
            await uow.commit()

    @application_operation
    async def list_open(self, kind: VoteKind, *, context: ActingContext) -> Sequence[VoteSessionSnapshot]:
        async with self._uow_factory() as uow:
            return await uow.votes.list_open(kind)

    @application_operation
    async def cast_vote(
        self,
        vote_session_id: int,
        option_id: str,
        *,
        emoji: str | None = None,
        toggle: bool = True,
        count_revision: int | None = None,
        context: ActingContext,
    ) -> CastVoteResult:
        """Validate eligibility, refresh weights, and toggle or replace the actor's ballot.

        The ballot is cast in `actor.guild_id`, which must be a guild the session is
        published in; that guild's aliases decide which emoji the option goes by.
        `emoji` picks one alias when the caller knows it, as a reaction does;
        otherwise the guild's first alias for the option is used.

        Selecting the same option again removes the ballot, even with another alias.
        Pass `toggle=False` to select instead: the ballot then stays on that option, which
        makes repeating the call safe when the caller only knows the desired selection.
        The repository serializes the mutation and any threshold closure. An unresolved
        owner-guild lookup uses base weight 1 for the new ballot, before the option
        multiplier. Unresolved existing ballots retain their cached weights.
        """
        return await self._apply_ballot(
            context.effective.require_user(),
            vote_session_id,
            option_id,
            emoji=emoji,
            toggle=toggle,
            count_revision=count_revision,
            context=context,
        )

    @application_operation
    async def _record_observed_reaction(
        self,
        voter: UserActor,
        vote_session_id: int,
        option_id: str,
        *,
        emoji: str,
        count_revision: int | None,
        context: ActingContext,
    ) -> CastVoteResult:
        """Reconcile a verified card reaction while retaining the recount's executor."""
        return await self._apply_ballot(
            voter,
            vote_session_id,
            option_id,
            emoji=emoji,
            toggle=False,
            count_revision=count_revision,
            context=context,
        )

    async def _apply_ballot(
        self,
        actor: UserActor,
        vote_session_id: int,
        option_id: str,
        *,
        emoji: str | None,
        toggle: bool,
        count_revision: int | None,
        context: ActingContext,
    ) -> CastVoteResult:
        account_id = actor.require_account_id()
        snapshot = await self.get_session(vote_session_id, context=context)
        if snapshot is None:
            return CastVoteResult(session=None, rejection=VoteRejection.NOT_FOUND)
        if not snapshot.is_open and not (
            count_revision is not None
            and snapshot.poll
            and snapshot.poll.count_revision == count_revision
            and snapshot.status is VoteStatus.COUNTING
        ):
            return CastVoteResult(session=snapshot, rejection=VoteRejection.CLOSED)
        if actor.guild_id is None or actor.guild_id not in {message.guild_id for message in snapshot.messages}:
            return CastVoteResult(session=snapshot, rejection=VoteRejection.WRONG_GUILD)
        option = snapshot.option_by_id(option_id, actor.guild_id, emoji=emoji)
        if option is None:
            return CastVoteResult(session=snapshot, rejection=VoteRejection.INVALID_OPTION)

        # The option's alias comes from where the ballot was cast; role weights come from the owner guild.
        owner_guild_id = self._owner_guild_id(snapshot)
        weight_actor = await self._weight_actor(actor, snapshot, owner_guild_id)
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
            if not (await self._permissions.check(actor, VOTE_POLL_CAST, uow=uow)).allowed:
                return CastVoteResult(snapshot, VoteRejection.NOT_ELIGIBLE)
            if weight_actor is None:
                if snapshot.kind is VoteKind.DELETE_LOG:
                    return CastVoteResult(snapshot, VoteRejection.NOT_ELIGIBLE)
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
                weight_actor = replace(weight_actor, credential_scopes=actor.credential_scopes)
                privileges = await self._permissions.check(
                    weight_actor, VOTE_LOG_DELETE_CAST, VOTE_WEIGHT_STAFF, uow=uow
                )
                weight_actor = replace(weight_actor, capabilities=privileges.allowed_nodes)
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

            mutation = await uow.votes.cast_vote(
                vote_session_id,
                account_id,
                actor.guild_id,
                option,
                weight,
                refreshed,
                toggle=toggle,
                count_revision=count_revision,
            )
            await uow.commit()
        if mutation is None:
            # The session closed or vanished after it was read above.
            latest = await self.get_session(vote_session_id, context=context)
            rejection = VoteRejection.CLOSED if latest is not None else VoteRejection.NOT_FOUND
            return CastVoteResult(session=latest, rejection=rejection)
        return CastVoteResult(
            session=mutation.session,
            previous_weight=mutation.previous_weight,
            current_weight=mutation.current_weight,
            just_closed=mutation.just_closed,
        )

    @application_operation
    async def clear_vote(self, vote_session_id: int, *, context: ActingContext) -> CastVoteResult:
        """Remove the actor's ballot while the session is open, without toggling it back on."""
        actor = context.effective.require_user()
        account_id = actor.require_account_id()
        snapshot = await self.get_session(vote_session_id, context=context)
        if snapshot is None:
            return CastVoteResult(session=None, rejection=VoteRejection.NOT_FOUND)
        if not snapshot.is_open:
            return CastVoteResult(session=snapshot, rejection=VoteRejection.CLOSED)
        if actor.guild_id is None or actor.guild_id not in {message.guild_id for message in snapshot.messages}:
            return CastVoteResult(session=snapshot, rejection=VoteRejection.WRONG_GUILD)
        async with self._uow_factory() as uow:
            if not (await self._permissions.check(actor, VOTE_POLL_CAST, uow=uow)).allowed:
                return CastVoteResult(snapshot, VoteRejection.NOT_ELIGIBLE)
            mutation = await uow.votes.clear_vote(vote_session_id, account_id)
            await uow.commit()
        if mutation is None:
            latest = await self.get_session(vote_session_id, context=context)
            rejection = VoteRejection.CLOSED if latest is not None else VoteRejection.NOT_FOUND
            return CastVoteResult(session=latest, rejection=rejection)
        return CastVoteResult(
            session=mutation.session,
            previous_weight=mutation.previous_weight,
            current_weight=mutation.current_weight,
            just_closed=mutation.just_closed,
        )

    @application_operation
    async def refresh(self, vote_session_id: int, *, context: ActingContext) -> VoteRefreshResult:
        """Recalculate resolvable ballot weights and check the session's existing thresholds."""
        snapshot = await self.get_session(vote_session_id, context=context)
        if snapshot is None:
            return VoteRefreshResult(None)
        return await self._refresh_snapshot(
            snapshot, actor=context.effective if not isinstance(context.effective, WorkerActor) else None
        )

    async def _refresh_snapshot(
        self, snapshot: VoteSessionSnapshot, *, actor: Actor | None = None
    ) -> VoteRefreshResult:
        weights, unresolved = await self._calculate_refresh(snapshot)
        async with self._uow_factory() as uow:
            if actor is not None and await self._poll_authority(uow, snapshot, actor) is not None:
                raise AuthorizationError
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

    @application_operation
    async def close(
        self, vote_session_id: int, *, recount: FinalRecount | None = None, context: ActingContext
    ) -> CastVoteResult:
        """Refresh and close a generic poll if its author or an authorized actor requests it."""
        actor = context.effective.require_user()
        actor.require_account_id()
        snapshot = await self.get_session(vote_session_id, context=context)
        if snapshot is None:
            return CastVoteResult(None, VoteRejection.NOT_FOUND)
        if not snapshot.is_open:
            return CastVoteResult(snapshot, VoteRejection.CLOSED)
        return await self._count_and_close(snapshot, recount, actor_id=actor.account_id)

    async def _poll_authority(
        self,
        uow: VoteUnitOfWork,
        snapshot: VoteSessionSnapshot,
        actor: Actor,
        *,
        review_action: str | None = None,
    ) -> VoteRejection | None:
        current = await uow.votes.get_by_id(snapshot.id)
        if (
            current is None
            or current.poll is None
            or current.poll != snapshot.poll
            or current.status is not snapshot.status
        ):
            return VoteRejection.CLOSED
        permissions = await self._permissions.check(actor, VOTE_POLL_CLOSE_ANY, VOTE_POLL_RECOUNT, uow=uow)
        if not isinstance(actor, UserActor):
            return VoteRejection.NOT_AUTHORIZED
        actor = replace(actor, capabilities=permissions.allowed_nodes)
        if review_action is None:
            if (
                actor.account_id == current.author_account_id
                and actor.credential_scopes is not None
                and VOTE_POLL_CLOSE_ANY.name not in permissions.allowed_nodes
                and not (await self._permissions.check(actor, VOTE_POLL_CREATE, uow=uow)).allowed
            ):
                return VoteRejection.NOT_AUTHORIZED
            return current.can_close(actor)
        return (
            None
            if current.can_review_poll(actor, recount_only=review_action == "retry")
            else VoteRejection.NOT_AUTHORIZED
        )

    async def _count_and_close(
        self, snapshot: VoteSessionSnapshot, recount: FinalRecount | None, *, actor_id: int | None = None
    ) -> CastVoteResult:
        # A worker can reclaim an interrupted count after the lease. The revision fences
        # every ballot write and the final transition against a superseded attempt.
        async with self._uow_factory() as uow:
            if actor_id is not None:
                rejection = await self._poll_authority(
                    uow,
                    snapshot,
                    ActingContext.current().effective,
                    review_action="retry" if snapshot.status is VoteStatus.REVIEW else None,
                )
                if rejection is not None:
                    return CastVoteResult(snapshot, rejection)
            counting = await uow.votes.transition_poll(
                snapshot, VoteStatus.COUNTING, claim=True, retry_at=Instant.now().add(minutes=10), actor_id=actor_id
            )
            await uow.commit()
        if counting is None or counting.poll is None:
            return CastVoteResult(snapshot, VoteRejection.CLOSED)
        unreadable = tuple(sorted({*(card.id for card in counting.messages), *counting.poll.unreadable_cards}))
        weights: dict[int, float] = {}
        failed = False
        try:
            with anyio.fail_after(8 * 60):
                if recount is not None:
                    _, unreadable = await recount(counting.id, counting.poll.count_revision)
                    # A card removed from the post registry between attempts is still missing.
                    detached = set(counting.poll.unreadable_cards).difference(card.id for card in counting.messages)
                    unreadable = tuple(sorted(detached.union(unreadable)))
                latest = await self.get_session(counting.id, context=ActingContext.current())
                if latest is None or latest.poll is None or latest.poll.count_revision != counting.poll.count_revision:
                    return CastVoteResult(latest, VoteRejection.CLOSED)
                weights, _ = await self._calculate_refresh(latest)
        except Exception:
            logger.exception("Final count interrupted for poll %s", counting.id)
            failed = True
        status = VoteStatus.CLOSED
        retry_at = None
        if failed or unreadable:
            status = VoteStatus.REVIEW
            if counting.poll.count_attempts < 3 and snapshot.status is not VoteStatus.REVIEW:
                status = VoteStatus.COUNTING
                retry_at = Instant.now().add(minutes=1)
        async with self._uow_factory() as uow:
            result = await uow.votes.transition_poll(
                counting, status, unreadable=unreadable, retry_at=retry_at, weights=weights
            )
            await uow.commit()
        if result is None:
            return CastVoteResult(snapshot, VoteRejection.CLOSED)
        return CastVoteResult(result, just_closed=status is VoteStatus.CLOSED)

    async def close_due(
        self, now: Instant | None = None, *, context: ActingContext, recount: FinalRecount | None = None
    ) -> Sequence[VoteSessionSnapshot]:
        """Count due polls and resume interrupted counts after their retry time."""
        with worker_scope(context):
            async with self._uow_factory() as uow:
                due = await uow.votes.list_due(now or Instant.now())
            closed: list[VoteSessionSnapshot] = []
            for snapshot in due:
                result = await self._count_and_close(snapshot, recount)
                if result.just_closed and result.session is not None:
                    closed.append(result.session)
            return closed

    @application_operation
    async def pending_reviews(self, guild_id: int, *, context: ActingContext) -> Sequence[int]:
        async with self._uow_factory() as uow:
            if not isinstance(context.effective, UserActor) or context.effective.guild_id != guild_id:
                raise AuthorizationError
            await self._permissions.require(
                context.effective, VOTE_POLL_CLOSE_ANY, VOTE_POLL_RECOUNT, mode="any", uow=uow
            )
            return await uow.votes.pending_reviews(guild_id)

    @application_operation
    async def review_poll(
        self,
        snapshot: VoteSessionSnapshot,
        action: str,
        *,
        reason: str,
        deadline: Instant | None = None,
        recount: FinalRecount | None = None,
        context: ActingContext,
    ) -> CastVoteResult:
        """Apply a staff decision to the exact review revision the actor inspected."""
        actor = context.effective.require_user()
        if snapshot.poll is None or snapshot.status is not VoteStatus.REVIEW:
            return CastVoteResult(snapshot, VoteRejection.CLOSED)
        account = actor.require_account_id()
        if action == "retry":
            return await self._count_and_close(snapshot, recount, actor_id=account)
        if not reason.strip():
            msg = "Give a reason for this review decision."
            raise InvalidVoteConfigurationError(msg)
        status = {"finalize": VoteStatus.CLOSED, "reopen": VoteStatus.OPEN, "void": VoteStatus.VOID}[action]
        if status is VoteStatus.OPEN and (deadline is None or deadline <= Instant.now()):
            msg = "Choose a future voting deadline."
            raise InvalidVoteConfigurationError(msg)
        async with self._uow_factory() as uow:
            rejection = await self._poll_authority(uow, snapshot, actor, review_action=action)
            if rejection is not None:
                return CastVoteResult(snapshot, rejection)
            updated = await uow.votes.transition_poll(
                snapshot,
                status,
                actor_id=account,
                deadline=deadline,
                reason=reason,
                unreadable=snapshot.poll.unreadable_cards if status is not VoteStatus.OPEN else (),
            )
            await uow.commit()
        if updated is None:
            return CastVoteResult(snapshot, VoteRejection.CLOSED)
        return CastVoteResult(updated, just_closed=status is VoteStatus.CLOSED)

    @application_operation
    async def configuration(self, guild_id: int, kind: VoteKind, *, context: ActingContext) -> VoteConfiguration:
        """Read a consistent snapshot of stored configuration, without applying defaults."""
        async with self._uow_factory() as uow:
            await uow.votes.lock_configuration(guild_id)
            result = VoteConfiguration(
                await uow.votes.get_emoji_preset(guild_id, kind),
                tuple(sorted(await uow.votes.get_role_weights(guild_id, kind), key=lambda weight: weight.role_id)),
            )
            await uow.commit()
            return result

    @application_operation
    async def emoji_preset(self, guild_id: int, kind: VoteKind, *, context: ActingContext) -> EmojiPreset:
        return (await self.emoji_presets((guild_id,), kind, context=context))[guild_id]

    @application_operation
    async def emoji_presets(
        self, guild_ids: Sequence[int], kind: VoteKind, *, context: ActingContext
    ) -> dict[int, EmojiPreset]:
        """Read one preset snapshot for distinct guilds, applying defaults where unset."""
        guilds = tuple(sorted(set(guild_ids)))
        if not guilds:
            return {}
        async with self._uow_factory() as uow:
            presets = await uow.votes.get_emoji_presets(guilds, kind)
        return {guild_id: presets.get(guild_id) or self._default_emoji_preset(guild_id, kind) for guild_id in guilds}

    @staticmethod
    def _default_emoji_preset(guild_id: int, kind: VoteKind) -> EmojiPreset:
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

    @application_operation
    async def get_role_weights(self, guild_id: int, kind: VoteKind, *, context: ActingContext) -> Sequence[RoleWeight]:
        async with self._uow_factory() as uow:
            return await uow.votes.get_role_weights(guild_id, kind)

    @application_operation
    async def refresh_configuration(self, guild_id: int, *, context: ActingContext) -> None:
        """Refresh ballots after a durable guild-configuration event, including retries."""
        for kind in VoteKind:
            await self._refresh_kind(guild_id, kind)

    async def _refresh_kind(self, guild_id: int, kind: VoteKind) -> None:
        """Refresh open sessions whose owner guild supplies the changed role weights."""
        for snapshot in await self.list_open(kind, context=ActingContext.current()):
            if self._owner_guild_id(snapshot) == guild_id:
                await self._refresh_snapshot(snapshot)

    async def _calculate_refresh(
        self, snapshot: VoteSessionSnapshot, *, replacing: UserActor | None = None
    ) -> tuple[dict[int, float], list[int]]:
        """Calculate replacement weights without changing stored ballots.

        A zero removes an ineligible voter. Unresolved accounts are returned separately
        and omitted from the weight map, preserving their stored weights. Without a
        resolver, return no updates.
        """
        if self._actor_resolver is None:
            return {}, []
        owner_guild_id = self._owner_guild_id(snapshot)
        by_guild: dict[int, set[int]] = {}
        for selection in snapshot.selections:
            if replacing is not None and replacing.account_id == selection.account_id:
                continue
            guild_id = owner_guild_id if owner_guild_id is not None else selection.guild_id or 0
            by_guild.setdefault(guild_id, set()).add(selection.account_id)
        actors: dict[tuple[int, int], UserActor | None] = {}
        for guild_id, account_ids in by_guild.items():
            resolved = await self._actor_resolver.resolve_many(sorted(account_ids), guild_id, snapshot.kind)
            actors.update(((guild_id, account_id), actor) for account_id, actor in resolved.items())
        weights: dict[int, float] = {}
        unresolved: list[int] = []
        for selection in snapshot.selections:
            actor = replacing if replacing is not None and replacing.account_id == selection.account_id else None
            guild_id = owner_guild_id if owner_guild_id is not None else selection.guild_id or 0
            actor = actor or actors.get((guild_id, selection.account_id))
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
