"""Voting application services."""

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from math import isfinite

import anyio
from whenever import Instant

from squid.core.actors import ActingContext, Actor, UserActor, application_operation
from squid.core.errors import AuthorizationError
from squid.core.pagination import FIRST_PAGE, Page, PageSelector
from squid.core.permissions.application import PermissionService
from squid.core.permissions.domain.catalogue import (
    BUILD_SUBMISSION_VIEW_PENDING,
    VOTE_LOG_DELETE_CAST,
    VOTE_NETWORK_CLOSE_ANY,
    VOTE_NETWORK_CREATE,
    VOTE_NETWORK_RECOUNT,
    VOTE_POLL_CAST,
    VOTE_POLL_CLOSE_ANY,
    VOTE_POLL_CREATE,
    VOTE_POLL_RECOUNT,
    VOTE_WEIGHT_STAFF,
)
from squid.core.unit_of_work import UnitOfWorkFactory
from squid.core.voting.application.policies import RoleVoteWeightPolicy
from squid.core.voting.application.ports import VoteUnitOfWork, VoteWeightPolicy
from squid.core.voting.domain import (
    DEFAULT_GENERIC_EMOJIS,
    DEFAULT_VOTE_MENU,
    MAX_POLL_DURATION_SECONDS,
    MIN_POLL_DURATION_SECONDS,
    BallotChange,
    CastVoteResult,
    EmojiPreset,
    GuildMember,
    PollScope,
    ReviewFilters,
    ReviewSort,
    RoleWeight,
    VoteAlias,
    VoteChange,
    VoteChoice,
    VoteKind,
    VoteMenu,
    VoteOption,
    Voter,
    VoteRejection,
    VoteSessionSnapshot,
    VoteStatus,
    VoteVisibility,
)
from squid.core.voting.domain.models import VoteConfiguration
from squid.core.voting.errors import InvalidVoteConfigurationError
from squid.core.worker_context import worker_scope

logger = logging.getLogger(__name__)

_POLL_STAFF_NODES = (VOTE_POLL_CLOSE_ANY, VOTE_POLL_RECOUNT, VOTE_NETWORK_CLOSE_ANY, VOTE_NETWORK_RECOUNT)
"""Nodes that let someone other than the author close, recount, or review a poll."""


type FinalRecount = Callable[[int, int], Awaitable[tuple[bool, tuple[int, ...]]]]


class VoteService:
    """Check voter eligibility and manage voting sessions through the repository.

    A ballot is weighed once, when it is cast. Discord-only sessions use the roles
    the voter reacts with; build reviews use stored roles in the network's owner
    guild, which adapters keep current with `record_members`, so every frontend
    weighs them alike. Role changes afterwards do not move a ballot, so a result
    can be explained from the ballots alone. The repository stores a ballot and any threshold closure
    together; this service commits the transaction.

    Results describe the resulting voting session. Publishing its Discord messages
    and acting on an approved build or deletion vote happen outside this service.
    """

    def __init__(
        self,
        policy: VoteWeightPolicy | None = None,
        *,
        uow_factory: UnitOfWorkFactory[VoteUnitOfWork],
        build_owner_guild_id: int | None = None,
    ):
        self._uow_factory = uow_factory
        self._permissions = PermissionService(uow_factory=uow_factory)
        self._policy = policy or RoleVoteWeightPolicy(
            lambda guild, kind: self.get_role_weights(guild, kind, context=ActingContext.current())
        )
        self._build_owner_guild_id = build_owner_guild_id

    def _owner_guild_id(self, snapshot: VoteSessionSnapshot) -> int | None:
        """Return the guild whose role weights govern this session.

        Role ids only mean anything inside one guild, so a session shared across
        guilds has to pick one authority; otherwise any guild hosting a card could
        set its own multipliers and decide the shared outcome. Build reviews use the
        network's owner guild, and network polls have no authority at all.
        """
        if snapshot.kind in (VoteKind.BUILD, VoteKind.BUILD_EDIT):
            return self._build_owner_guild_id
        return snapshot.home_guild_id

    async def _weigh(
        self, uow: VoteUnitOfWork, actor: UserActor, snapshot: VoteSessionSnapshot
    ) -> float | VoteRejection:
        """Return the actor's base weight in a session, before the option multiplier.

        A Discord-only session is weighed by the roles the voter reacts with in its
        guild, so it refuses votes from anywhere else. A build review is weighed by
        the voter's stored roles in the network's owner guild; a voter without an
        account has none stored, so only the roles they react with there count. With
        no owner guild, as for network polls, every eligible voter weighs 1.
        """
        if not (await self._permissions.check(actor, VOTE_POLL_CAST, uow=uow)).allowed:
            return VoteRejection.NOT_ELIGIBLE
        owner_guild_id = self._owner_guild_id(snapshot)
        if owner_guild_id is None:
            return 1.0
        live = (
            GuildMember(actor.discord_user_id, actor.discord_role_ids, actor.discord_guild_admin)
            if actor.guild_id == owner_guild_id and actor.discord_user_id is not None
            else None
        )
        if snapshot.home_guild_id is not None:
            if live is None:
                return VoteRejection.WRONG_GUILD
            member = live
        elif actor.account_id is not None:
            member = await uow.votes.get_member(owner_guild_id, actor.account_id)
        else:
            member = live
        weight_actor = UserActor(
            account_id=actor.account_id,
            discord_user_id=None if member is None else member.discord_user_id,
            guild_id=owner_guild_id,
            discord_role_ids=frozenset() if member is None else member.role_ids,
            discord_guild_admin=member is not None and member.manage_server,
            credential_scopes=actor.credential_scopes,
        )
        privileges = await self._permissions.check(weight_actor, VOTE_LOG_DELETE_CAST, VOTE_WEIGHT_STAFF, uow=uow)
        calculated = await self._policy.calculate(
            replace(weight_actor, capabilities=privileges.allowed_nodes), snapshot
        )
        if calculated is None:
            return VoteRejection.NOT_ELIGIBLE
        if not isfinite(calculated) or calculated <= 0:
            msg = "Vote policies must return a positive finite weight."
            raise InvalidVoteConfigurationError(msg)
        return calculated

    @application_operation
    async def base_weight(self, vote_session_id: int, *, context: ActingContext) -> float | VoteRejection:
        """Return the weight a ballot from the actor would get now, or why they cannot vote."""
        snapshot = await self.get_session(vote_session_id, context=context)
        if snapshot is None:
            return VoteRejection.NOT_FOUND
        if not snapshot.is_open:
            return VoteRejection.CLOSED
        async with self._uow_factory() as uow:
            return await self._weigh(uow, context.effective.require_user(), snapshot)

    @application_operation
    async def record_members(
        self, guild_id: int, members: Sequence[GuildMember], *, complete: bool = False, context: ActingContext
    ) -> None:
        """Store what an adapter saw of guild members' roles, for weighing their ballots.

        Only members with an account are kept. With `complete`, `members`
        is the whole guild and stored members missing from it are forgotten.
        """
        async with self._uow_factory() as uow:
            await uow.votes.record_members(guild_id, members, complete=complete)
            await uow.commit()

    @application_operation
    async def forget_member(self, guild_id: int, discord_user_id: int, *, context: ActingContext) -> None:
        """Forget a member who left a guild; their later ballots weigh as a non-member's."""
        async with self._uow_factory() as uow:
            await uow.votes.forget_member(guild_id, discord_user_id)
            await uow.commit()

    @application_operation
    async def start_build_vote(
        self,
        *,
        author_account_id: int,
        pass_threshold: int,
        fail_threshold: int,
        build_id: int,
        changes: Sequence[VoteChange],
        menu: VoteMenu = DEFAULT_VOTE_MENU,
        context: ActingContext,
    ) -> int:
        """Create a build vote and its target atomically."""
        context.require_worker()
        async with self._uow_factory() as uow:
            result = await uow.votes.create_build_session(
                author_account_id=author_account_id,
                pass_threshold=pass_threshold,
                fail_threshold=fail_threshold,
                build_id=build_id,
                changes=changes,
                menu=menu.validated(VoteKind.BUILD),
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
        menu: VoteMenu = DEFAULT_VOTE_MENU,
        context: ActingContext,
    ) -> int:
        """Return the earliest review session for a build, creating one if none exists.

        The repository serializes creation per build so event retries and concurrent
        submission paths reuse the same session, including after it has closed.
        """
        context.require_worker()
        async with self._uow_factory() as uow:
            result = await uow.votes.get_or_create_build_submission_session(
                author_account_id=author_account_id,
                pass_threshold=pass_threshold,
                fail_threshold=fail_threshold,
                build_id=build_id,
                changes=changes,
                menu=menu.validated(VoteKind.BUILD),
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
        menu: VoteMenu = DEFAULT_VOTE_MENU,
        context: ActingContext,
    ) -> int:
        """Create a message-deletion vote and its target atomically."""
        author_account_id = context.effective.require_account_id()
        if context.effective.require_user().guild_id != server_id:
            raise AuthorizationError
        async with self._uow_factory() as uow:
            await self._permissions.require(context.effective, VOTE_POLL_CREATE, uow=uow)
            result = await uow.votes.create_delete_log_session(
                author_account_id=author_account_id,
                pass_threshold=pass_threshold,
                fail_threshold=fail_threshold,
                message_id=message_id,
                channel_id=channel_id,
                server_id=server_id,
                menu=menu.validated(VoteKind.DELETE_LOG),
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
        menu: VoteMenu,
        guild_id: int | None = None,
        scope: PollScope = PollScope.GUILD,
        now: Instant | None = None,
    ) -> int:
        """Validate and persist a poll with a deadline derived from its duration.

        Publication is separate, so creating a poll does not require a Discord message.
        A server poll belongs to the actor's guild. A network poll belongs to no guild
        and needs aliases usable in every guild.
        """
        actor = context.effective.require_user()
        author_account_id = actor.require_account_id()
        if scope is PollScope.GUILD and actor.guild_id != guild_id:
            raise AuthorizationError
        with context.scope():
            async with self._uow_factory() as uow:
                if not question.strip():
                    msg = "Poll question cannot be empty."
                    raise InvalidVoteConfigurationError(msg)
                if not MIN_POLL_DURATION_SECONDS <= duration_seconds <= MAX_POLL_DURATION_SECONDS:
                    msg = "Poll duration must be between 1 minute and 30 days."
                    raise InvalidVoteConfigurationError(msg, context={"duration_seconds": duration_seconds})
                menu = menu.validated(VoteKind.GENERIC)
                if guild_id is not None and any(alias.guild_id not in (None, guild_id) for alias in menu.aliases):
                    msg = "Poll options must belong to the poll guild."
                    raise InvalidVoteConfigurationError(msg)
                if scope is PollScope.NETWORK:
                    if guild_id is not None:
                        # No guild may weigh or manage a poll every guild votes in.
                        msg = "A network poll belongs to no guild."
                        raise InvalidVoteConfigurationError(msg)
                    if any(alias.guild_id is not None for alias in menu.aliases):
                        # Guild-scoped aliases would leave the other guilds' cards unvotable.
                        msg = "Network poll options must not be scoped to one guild."
                        raise InvalidVoteConfigurationError(msg)
                    await self._permissions.require(actor, VOTE_NETWORK_CREATE, uow=uow)
                await self._permissions.require(actor, VOTE_POLL_CREATE, uow=uow)
                result = await uow.votes.create_generic_session(
                    author_account_id=author_account_id,
                    question=question.strip(),
                    visibility=visibility,
                    deadline=(now or Instant.now()).add(seconds=duration_seconds),
                    menu=menu,
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
    async def list_reviews(
        self,
        filters: ReviewFilters,
        *,
        sort: ReviewSort = ReviewSort.NEWEST,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
        context: ActingContext,
    ) -> Page[VoteSessionSnapshot]:
        """Page build submission and edit votes for reviewers who can see unreviewed builds."""
        async with self._uow_factory() as uow:
            await self._permissions.require(context.effective, BUILD_SUBMISSION_VIEW_PENDING, uow=uow)
            return await uow.votes.list_reviews(filters, sort=sort, selector=selector, page_size=page_size)

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
        """Validate eligibility, weigh the ballot, and toggle or replace the actor's ballot.

        A ballot cast from Discord comes with `actor.guild_id`, which must be a guild the
        session is published in; that guild's aliases decide which emoji the option
        goes by. `emoji` picks one alias when the caller knows it, as a reaction does;
        otherwise the guild's first alias for the option is used. A ballot cast from
        the web has no guild and no alias.

        Selecting the same option again removes the ballot, even with another alias.
        Pass `toggle=False` to select instead: the ballot then stays on that option, which
        makes repeating the call safe when the caller only knows the desired selection.
        The repository serializes the mutation and any threshold closure.
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
        voter = Voter.of(actor)
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
        # A Discord card's guild decides the emoji alias. A cast without a guild comes
        # from a frontend without cards and selects the option directly.
        if actor.guild_id is not None and actor.guild_id not in {message.guild_id for message in snapshot.messages}:
            return CastVoteResult(session=snapshot, rejection=VoteRejection.WRONG_GUILD)
        option = snapshot.menu.option(option_id)
        alias = None if actor.guild_id is None else snapshot.menu.alias_for(option_id, actor.guild_id, emoji=emoji)
        if option is None or (actor.guild_id is not None and alias is None):
            return CastVoteResult(session=snapshot, rejection=VoteRejection.INVALID_OPTION)

        async with self._uow_factory() as uow:
            weight = await self._weigh(uow, actor, snapshot)
            if isinstance(weight, VoteRejection):
                return CastVoteResult(snapshot, weight)
            weight *= option.multiplier
            mutation = await uow.votes.cast_vote(
                vote_session_id,
                voter,
                option,
                weight,
                guild_id=actor.guild_id,
                emoji=None if alias is None else alias.emoji,
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
        voter = Voter.of(actor)
        snapshot = await self.get_session(vote_session_id, context=context)
        if snapshot is None:
            return CastVoteResult(session=None, rejection=VoteRejection.NOT_FOUND)
        if not snapshot.is_open:
            return CastVoteResult(session=snapshot, rejection=VoteRejection.CLOSED)
        if actor.guild_id is not None and actor.guild_id not in {message.guild_id for message in snapshot.messages}:
            return CastVoteResult(session=snapshot, rejection=VoteRejection.WRONG_GUILD)
        async with self._uow_factory() as uow:
            if not (await self._permissions.check(actor, VOTE_POLL_CAST, uow=uow)).allowed:
                return CastVoteResult(snapshot, VoteRejection.NOT_ELIGIBLE)
            mutation = await uow.votes.clear_vote(vote_session_id, voter)
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
    async def void_ballots(
        self, voter: Voter, guild_id: int, *, context: ActingContext
    ) -> tuple[VoteSessionSnapshot, ...]:
        """Void a voter's ballots in the open sessions `guild_id` weighs, after it bans them.

        Ballots keep their weight when roles change, so a ban is the one membership
        change that removes them. Removing a ballot can still close a threshold vote.
        Returns the sessions that changed.
        """
        changed: list[VoteSessionSnapshot] = []
        for kind in VoteKind:
            for snapshot in await self.list_open(kind, context=context):
                if self._owner_guild_id(snapshot) != guild_id or snapshot.selection_for(voter) is None:
                    continue
                async with self._uow_factory() as uow:
                    mutation = await uow.votes.clear_vote(snapshot.id, voter, change=BallotChange.VOID)
                    await uow.commit()
                if mutation is not None:
                    changed.append(mutation.session)
        return tuple(changed)

    @application_operation
    async def close(
        self, vote_session_id: int, *, recount: FinalRecount | None = None, context: ActingContext
    ) -> CastVoteResult:
        """Count and close a generic poll if its author or an authorized actor requests it."""
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
        permissions = await self._permissions.check(actor, *_POLL_STAFF_NODES, uow=uow)
        if not isinstance(actor, UserActor):
            return VoteRejection.NOT_AUTHORIZED
        actor = replace(actor, capabilities=permissions.allowed_nodes)
        if review_action is None:
            if (
                actor.account_id == current.author_account_id
                and actor.credential_scopes is not None
                and not permissions.allowed_nodes & {VOTE_POLL_CLOSE_ANY.name, VOTE_NETWORK_CLOSE_ANY.name}
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
        failed = False
        try:
            with anyio.fail_after(8 * 60):
                if recount is not None:
                    _, unreadable = await recount(counting.id, counting.poll.count_revision)
                    # A card removed from the post registry between attempts is still missing.
                    detached = set(counting.poll.unreadable_cards).difference(card.id for card in counting.messages)
                    unreadable = tuple(sorted(detached.union(unreadable)))
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
            result = await uow.votes.transition_poll(counting, status, unreadable=unreadable, retry_at=retry_at)
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
            await self._permissions.require(context.effective, *_POLL_STAFF_NODES, mode="any", uow=uow)
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
            menu = VoteMenu(
                tuple(
                    VoteOption(str(index), VoteChoice.GENERIC, label=f"Option {index}", position=index - 1)
                    for index in range(1, len(DEFAULT_GENERIC_EMOJIS) + 1)
                ),
                tuple(
                    VoteAlias(str(index), emoji, guild_id, index - 1)
                    for index, emoji in enumerate(DEFAULT_GENERIC_EMOJIS, 1)
                ),
            )
        else:
            menu = replace(
                DEFAULT_VOTE_MENU,
                aliases=tuple(replace(alias, guild_id=guild_id) for alias in DEFAULT_VOTE_MENU.aliases),
            )
        return EmojiPreset(guild_id, kind, menu)

    @application_operation
    async def get_role_weights(self, guild_id: int, kind: VoteKind, *, context: ActingContext) -> Sequence[RoleWeight]:
        async with self._uow_factory() as uow:
            return await uow.votes.get_role_weights(guild_id, kind)
