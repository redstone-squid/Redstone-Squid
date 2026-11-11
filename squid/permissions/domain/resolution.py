"""Decide whether a caller may use a permission, and explain the decision.

A permission node names one capability, such as `settings.server.edit`. The
catalogue defines the known nodes and their defaults. A subject describes the
caller: their account, Discord roles, and the guild where the check happens.
Rules select nodes with patterns and request an allow, deny, or forbid result.
PermissionService loads the subject's grants and expands permission roles into
these rules before calling this module; the resolver does no database reads.

Resolution follows these steps:

1. The bot owner is allowed every known node without consulting rules.
2. Discard rules that have expired, belong to another guild, or do not match the
   node. Guild-scoped rules also cannot apply to global capabilities.
3. Any remaining `FORBID` denies the node, regardless of other rules.
4. Otherwise choose the highest-ranked matching rule. If none remain, use the
   node's catalogue default.

Allow and deny rules are compared in this order; later factors break ties only:

- Pattern specificity: an exact permission outranks a broad wildcard. The full
  ordering, including tag selectors, is defined by `Pattern.specificity`.
- Scope: a rule for this guild outranks an unscoped rule.
- Subject: a rule attached to an account outranks one attached to a Discord role.
- Origin: a direct account grant outranks a direct Discord-role grant, then a
  permission-role assignment, then the Discord Manage Server bridge.
- Effect: deny wins if all preceding factors tie.

For example, denying `settings.**` and allowing `settings.server.edit` permits
that one action: specificity is compared before effect. A matching forbid would
still reject it, except for the bot owner. Permission-role management rank
controls who can edit a role; it does not influence this decision.

The returned Decision includes a trace of the rules used to reach the answer.
`/perm can` renders that trace, so permission checks and their explanations share
one implementation.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum

from whenever import Instant

from squid.core.actors import Actor
from squid.permissions.domain.catalogue import CATALOGUE, Catalogue
from squid.permissions.domain.matching import Pattern
from squid.permissions.domain.models import Default, Effect, NodeScope, PermissionNode

MAX_ROLE_DEPTH = 8
"""Maximum roles visited along a composition path, including the starting role."""


class Origin(IntEnum):
    """Where a rule came from, ranked after specificity, scope, and subject.

    Higher values win when those earlier components tie.
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
class Rule:
    """An allow, deny, or forbid statement already selected for the subject being checked.

    The rule does not carry an account or Discord role ID. PermissionService must
    select the subject's records before constructing it; subject_kind and origin
    describe how it was assigned and determine its precedence.

    `excluded` removes named nodes from this rule's contribution without changing
    its pattern specificity or denying grants from other rules. For example, a role
    including `build.**` and excluding `@destructive` can coexist with a separate
    grant of a destructive build permission.
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
        """Match the pattern and exclusions; guild scope and expiry are checked by resolve."""
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
    """A matching rule and its position in the decision trace."""

    rule: Rule
    rank: Rank
    decisive: bool = False
    lost_on: str | None = None
    """Which rank component this rule lost on, `TIE` if it lost only the stable
    tiebreaker, or None on the decisive step itself."""


@dataclass(frozen=True, slots=True)
class Decision:
    """Whether the requested node is allowed, with the reason and supporting rule trace.

    Owner and default decisions need no rules and have an empty trace. Forbids
    short-circuit ordinary ranking, so a forbid trace contains only forbidding rules.
    """

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
    """A permission role's own patterns and the other permission roles it includes.

    Role composition means including another role's patterns rather than copying
    them. includes_roles holds the keys used to follow those relationships.
    Built-in and user-defined permission roles use the same representation.
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
    """Collect include patterns and excluded node names through role composition.

    Exclusions from every visited role apply to the whole expansion, including
    patterns inherited through other branches. Missing roles, cycles on the current
    path, and branches beyond `max_depth` contribute nothing further.
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
    subject: Actor,
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
    """Order rules by descending rank, then by stable source and pattern values.

    Allow/deny ties already agree on the verdict; stable ordering makes their
    explanation independent of database row order.
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
    subject: Actor,
    rules: Sequence[Rule] = (),
    *,
    catalogue: Catalogue = CATALOGUE,
    now: Instant | None = None,
) -> Decision:
    """Resolve a known node and record the rules that determine the answer.

    Unknown node names raise `UnknownPermissionNodeError`, including for the owner.
    Owner and default decisions have no trace. A forbid decision lists only matching
    forbids; other rule decisions list all applicable candidates in precedence order.
    """
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
    subject: Actor,
    rules: Sequence[Rule] = (),
    *,
    catalogue: Catalogue = CATALOGUE,
    now: Instant | None = None,
) -> frozenset[str]:
    """Return allowed node names, evaluating every node at the same instant."""
    instant = now if now is not None else Instant.now()
    return frozenset(
        decision.node
        for decision in (resolve(node, subject, rules, catalogue=catalogue, now=instant) for node in nodes)
        if decision.allowed
    )
