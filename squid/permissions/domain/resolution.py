"""Deciding whether a subject holds a permission node.

The resolver is pure: it takes a subject, a flat sequence of rules already loaded
for that subject, and one node, and returns a decision plus the trace that
produced it. `/perm can` renders that trace, so there is exactly one
implementation of the precedence rules.

Precedence, highest first:

0. The bot owner is allowed everything, before any rule is examined.
1. A matching `FORBID` rule denies, before any ranking. This is the emergency
   stop, and it is the only effect that does not have to win a specificity
   argument.
2. Otherwise the matching rules are ranked and the maximum decides:
   `(specificity, scope, subject, origin, deny-first)`.
3. With no matching rule, the node's catalogue default applies.

Specificity outranks deny on purpose, which is LuckPerms' rule and what Minecraft
admins already expect: `settings.**` allowed with `settings.server.edit` denied
reads as "the namespace minus one hole". Deny only breaks a complete tie.

That tie-break inverts Discord's. Discord unions every role's denies, then every
role's allows, and applies denies first, so allow wins and role position never
enters into it. Here a deny is always something a person deliberately typed —
absence already denies, so nobody writes a deny just to structure things — and
honouring it is the safer reading. Role rank deliberately plays no part at all;
it governs who may edit which role, and nothing else.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum

from whenever import Instant

from squid.permissions.domain.catalogue import CATALOGUE, Catalogue
from squid.permissions.domain.matching import Pattern
from squid.permissions.domain.models import Default, Effect, NodeScope, PermissionNode

MAX_ROLE_DEPTH = 8
"""How far role composition is followed. A cycle already terminates because a
role is never expanded twice on one path; this only bounds pathological fan-out."""


class Origin(IntEnum):
    """Where a rule came from, ranked. Higher wins a tie.

    Mirrors Discord's overwrite tiers: what is set directly on you outranks what
    you inherit, and an explicit rule outranks the Manage Server bridge.
    """

    GUILD_ADMIN_BRIDGE = 0
    ROLE = 1
    DISCORD_ROLE_GRANT = 2
    ACCOUNT_GRANT = 3


class SubjectKind(IntEnum):
    """Who a rule is attached to, ranked. Higher wins a tie."""

    DISCORD_ROLE = 0
    ACCOUNT = 1


class Reason(StrEnum):
    """Why a decision came out the way it did."""

    OWNER = "owner"
    FORBIDDEN = "forbidden"
    RULE = "rule"
    DEFAULT = "default"


RANK_COMPONENTS = ("specificity", "scope", "subject", "origin", "effect")
"""Names of the rank tuple's components, in comparison order, for trace rendering."""

TIE = "tie"
"""`TraceStep.lost_on` when a rule tied the winner on every rank component, and so
would have produced the same verdict had it won."""

type Rank = tuple[tuple[int, int, int, int], int, int, int, int]


@dataclass(frozen=True, slots=True)
class Subject:
    """Who is being checked, and in what context.

    `account_id` is optional so an unauthenticated caller can still be resolved
    from Discord role grants alone — a permission check must never be the thing
    that creates an account row.
    """

    account_id: int | None = None
    discord_role_ids: frozenset[int] = field(default_factory=frozenset)
    guild_id: int | None = None
    is_bot_owner: bool = False
    discord_guild_admin: bool = False


@dataclass(frozen=True, slots=True)
class Rule:
    """One allow, deny or forbid statement applying to a subject.

    `excluded` carries a role's subtractions as resolved node names. Keeping them
    separate from the pattern is what makes Azure-style subtraction work without
    turning into a deny: a role contributing `build.**` minus `@destructive`
    still ranks with `build.**`'s specificity, and a node it subtracts stays
    available from any other role that includes it.
    """

    pattern: Pattern
    effect: Effect
    subject_kind: SubjectKind = SubjectKind.ACCOUNT
    origin: Origin = Origin.ACCOUNT_GRANT
    scope_guild_id: int | None = None
    expires_at: Instant | None = None
    excluded: frozenset[str] = field(default_factory=frozenset)
    source: str = ""
    via: str | None = None

    def matches(self, node: PermissionNode) -> bool:
        """Whether this rule speaks about `node` at all."""
        return self.pattern.matches(node) and node.name not in self.excluded

    def rank(self) -> Rank:
        """This rule's precedence, compared descending against sibling rules."""
        return (
            self.pattern.specificity,
            1 if self.scope_guild_id is not None else 0,
            int(self.subject_kind),
            int(self.origin),
            1 if self.effect is Effect.DENY else 0,
        )


@dataclass(frozen=True, slots=True)
class TraceStep:
    """One rule that spoke about the node, and how it fared."""

    rule: Rule
    rank: Rank
    decisive: bool = False
    lost_on: str | None = None
    """Which rank component this rule lost on, `TIE` if it lost only the stable
    tiebreaker, or None on the decisive step itself."""


@dataclass(frozen=True, slots=True)
class Decision:
    """The verdict for one node, and everything that went into it."""

    node: str
    allowed: bool
    reason: Reason
    trace: tuple[TraceStep, ...] = ()

    @property
    def decisive_rule(self) -> Rule | None:
        """The rule that decided this, if a rule did."""
        return next((step.rule for step in self.trace if step.decisive), None)


@dataclass(frozen=True, slots=True)
class RoleSpec:
    """A role's pattern lists and its composition edges.

    Applies to both built-in roles, whose lists live in code, and user-defined
    roles loaded from the database.
    """

    key: str
    includes: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()
    includes_roles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RoleExpansion:
    """What a role contributes once composition and subtraction are applied."""

    includes: frozenset[str]
    excluded: frozenset[str]


def expand_role(
    key: str,
    roles: Mapping[str, RoleSpec],
    *,
    catalogue: Catalogue = CATALOGUE,
    max_depth: int = MAX_ROLE_DEPTH,
) -> RoleExpansion:
    """Resolve a role to its include patterns and its subtracted node names.

    Exclusions accumulate down the composition chain and are applied at match
    time, so a parent can subtract from what it inherits and a child's own
    subtraction survives into the parent. A role is never expanded twice on one
    path, so a cycle terminates rather than raising: a broken composition graph
    should not take every permission check down with it.
    """
    return _expand(key, roles, catalogue, frozenset(), max_depth)


def _expand(
    key: str,
    roles: Mapping[str, RoleSpec],
    catalogue: Catalogue,
    visited: frozenset[str],
    depth: int,
) -> RoleExpansion:
    role = roles.get(key)
    if role is None or key in visited or depth <= 0:
        return RoleExpansion(frozenset(), frozenset())

    includes = set(role.includes)
    excluded: set[str] = set()
    for pattern in role.excludes:
        excluded |= catalogue.expand(pattern)

    for child_key in role.includes_roles:
        child = _expand(child_key, roles, catalogue, visited | {key}, depth - 1)
        includes |= child.includes
        excluded |= child.excluded

    return RoleExpansion(frozenset(includes), frozenset(excluded))


def rules_from_role(
    expansion: RoleExpansion,
    *,
    subject_kind: SubjectKind,
    origin: Origin = Origin.ROLE,
    scope_guild_id: int | None = None,
    expires_at: Instant | None = None,
    source: str = "",
    via: str | None = None,
) -> tuple[Rule, ...]:
    """Turn a role expansion into the allow rules it contributes."""
    return tuple(
        Rule(
            pattern=Pattern.parse(pattern),
            effect=Effect.ALLOW,
            subject_kind=subject_kind,
            origin=origin,
            scope_guild_id=scope_guild_id,
            expires_at=expires_at,
            excluded=expansion.excluded,
            source=source,
            via=via,
        )
        for pattern in sorted(expansion.includes)
    )


def _applicable(
    rule: Rule,
    node: PermissionNode,
    subject: Subject,
    now: Instant,
) -> bool:
    if rule.expires_at is not None and rule.expires_at <= now:
        return False
    if rule.scope_guild_id is not None:
        if rule.scope_guild_id != subject.guild_id:
            return False
        # A guild-scoped rule can never satisfy a global node. The test is on the
        # node's declared scope at evaluation time rather than on anything stored
        # with the grant, so a global node added years after a guild wildcard was
        # granted is still out of that guild's reach.
        if node.scope is NodeScope.GLOBAL:
            return False
    return rule.matches(node)


def _ordering(rule: Rule) -> tuple[object, ...]:
    """Sort key placing the winner first, and breaking remaining ties stably.

    Rank alone leaves genuine ties, and Python's sort would then hand the verdict
    to whichever rule the database happened to return first. Two rules that tie on
    rank always agree on the verdict — deny-first is part of the rank, so an allow
    and a deny can never tie — but the *trace* would still shuffle between runs,
    and `/perm can` is a user-facing explanation that ought to read the same
    way twice.
    """
    negated_rank = tuple(-component for component in rule.rank()[1:])
    negated_specificity = tuple(-component for component in rule.rank()[0])
    return (negated_specificity, *negated_rank, rule.source, rule.pattern.raw, int(rule.effect))


def _lost_on(winner: Rank, loser: Rank) -> str:
    for name, won, lost in zip(RANK_COMPONENTS, winner, loser, strict=True):
        if won != lost:
            return name
    # Ranks are equal, so the stable tiebreaker in `_ordering` picked between
    # them. Deny-first is part of the rank, so equally-ranked rules always agree
    # on the verdict: this rule lost nothing that would have changed the answer,
    # and `/perm can` should say so rather than leave a blank.
    return TIE


def resolve(
    node: PermissionNode | str,
    subject: Subject,
    rules: Sequence[Rule] = (),
    *,
    catalogue: Catalogue = CATALOGUE,
    now: Instant | None = None,
) -> Decision:
    """Decide whether `subject` holds `node`, and record why."""
    resolved = catalogue[node] if isinstance(node, str) else node
    instant = now if now is not None else Instant.now()

    if subject.is_bot_owner:
        # Before the forbid check and before any rule is read, so no stored rule
        # can lock the owner out of their own bot.
        return Decision(resolved.name, allowed=True, reason=Reason.OWNER)

    candidates = [rule for rule in rules if _applicable(rule, resolved, subject, instant)]

    forbidding = sorted((rule for rule in candidates if rule.effect is Effect.FORBID), key=_ordering)
    if forbidding:
        trace = tuple(TraceStep(rule, rule.rank(), decisive=index == 0) for index, rule in enumerate(forbidding))
        return Decision(resolved.name, allowed=False, reason=Reason.FORBIDDEN, trace=trace)

    if not candidates:
        return Decision(
            resolved.name,
            allowed=resolved.default is Default.ALLOW,
            reason=Reason.DEFAULT,
        )

    ranked = [(rule.rank(), rule) for rule in sorted(candidates, key=_ordering)]
    winning_rank, winner = ranked[0]
    trace = tuple(
        TraceStep(
            rule,
            rank,
            decisive=rule is winner,
            lost_on=None if rule is winner else _lost_on(winning_rank, rank),
        )
        for rank, rule in ranked
    )
    return Decision(resolved.name, allowed=winner.effect is Effect.ALLOW, reason=Reason.RULE, trace=trace)


def resolve_many(
    nodes: Iterable[PermissionNode | str],
    subject: Subject,
    rules: Sequence[Rule] = (),
    *,
    catalogue: Catalogue = CATALOGUE,
    now: Instant | None = None,
) -> frozenset[str]:
    """The names of the nodes in `nodes` that `subject` holds.

    One call answers a whole command's checks, or a vote actor's capability set,
    from a single already-loaded rule sequence.
    """
    instant = now if now is not None else Instant.now()
    return frozenset(
        decision.node
        for decision in (resolve(node, subject, rules, catalogue=catalogue, now=instant) for node in nodes)
        if decision.allowed
    )
