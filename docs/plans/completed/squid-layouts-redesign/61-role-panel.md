# 61 — RolePanel: persistent, router-owned self roles

## Problem

Squid already has portable menus, tabs, wizards, choices, editors, browsers, and ranked lists.
Adding Discord-named copies of those patterns would create a second framework. The missing domain
primitive is a long-lived self-role panel: a message whose controls survive process restarts and
whose state is Discord's member-role set.

A mounted component is the wrong owner. Its selected UI state can expire, become stale when an
administrator changes roles, and disappear on restart while the public role message remains.
The existing [`Router`](14-routed-actions.md) already owns stable interaction identity without a
mount; what is missing is the role transition policy and a semantic layout that can fit buttons or
a select to Discord's budgets.

## Decision

Add `sl.discord.RolePanel`, backed by a caller-owned `RouteGroup`. The panel registers stable
button and select routes, renders only stateless routed controls, and treats a freshly fetched
Discord member as the source of truth on every interaction.

```python
self_roles = routes.group("self-roles").group("server")

panel = sl.discord.RolePanel(
    self_roles,
    title="Server roles",
    categories=(
        sl.discord.RoleCategory(
            key="colour",
            label="Colour",
            roles=(
                sl.discord.RoleOption(RED_ID, "Red", emoji="🔴"),
                sl.discord.RoleOption(BLUE_ID, "Blue", emoji="🔵"),
            ),
            cardinality=sl.discord.EXACTLY_ONE,
        ),
        sl.discord.RoleCategory(
            key="notifications",
            label="Notifications",
            roles=(...),
            cardinality=sl.discord.ANY,
        ),
    ),
)

view = sl.discord.render_static(panel)
await channel.send(view=view)
```

The `RolePanel` object is constructed during route registration and retained by the registered
handler closures. Recreating the same configuration after a restart makes an old message work
again; no session, mount snapshot, or local selection state is recovered.

## Public model

The Discord-specific role module exports:

```python
@dataclass(frozen=True, slots=True)
class Cardinality:
    minimum: int = 0
    maximum: int | None = None

ANY = Cardinality()
AT_MOST_ONE = Cardinality(maximum=1)
AT_LEAST_ONE = Cardinality(minimum=1)
EXACTLY_ONE = Cardinality(minimum=1, maximum=1)

@dataclass(frozen=True, slots=True)
class RoleOption:
    role_id: int
    label: TextLike
    emoji: EmojiLike | None = None
    description: TextLike | None = None

@dataclass(frozen=True, slots=True)
class RoleCategory:
    key: str
    label: TextLike
    roles: tuple[RoleOption, ...]
    cardinality: Cardinality = ANY
    description: TextLike | None = None

class RolePanel(Component):
    def __init__(
        self,
        routes: RouteGroup,
        *,
        title: TextLike,
        categories: Sequence[RoleCategory],
        feedback: RoleFeedback | None = None,
        audit_reason: str = "Self-role panel",
    ) -> None: ...
```

`TextLike` and emoji values follow the normal Squid localization and normalization path while
rendering. `RolePanel` is re-exported from `sl.discord`; the supporting values live there too,
with the implementation in `squid_layouts.discord.roles`.

Category keys are stable route data, not display labels. They must be non-empty route-safe strings
and unique within a panel. Role ids must be positive, non-boolean integers and unique across the
whole panel: two categories controlling the same Discord role would make their cardinalities
contradict one another. A category contains 1–25 options. Its cardinality must satisfy
`0 <= minimum <= maximum <= len(roles)`, treating `None` as `len(roles)` for validation.

The 25-role ceiling is deliberate in v1. It guarantees a select fallback and avoids inventing
persistent pagination whose page would be shared by every reader of one public message. Hosts
with more roles split them into multiple categories or panels.

## Routes and rendering

`RolePanel` requires a dedicated, unfrozen `RouteGroup` and defines two identities under it:

```text
toggle:{category}:{role_id:int}  # button representation
set:{category}                   # select representation
```

It registers both handlers immediately. The caller may attach group middleware before the root
router is installed; guild restrictions, feature flags, and application authorization remain
ordinary router policy. A group already holding either overlapping definitions or registrations
is refused during construction rather than partially installing the panel.

Each category renders as its heading/description followed by one variant ladder:

1. A preferred `ActionGroup` of `RoutedButton`s, one stable toggle id per role.
2. A fallback `RoutedSelect`, whose values are decimal role ids and whose route is the category's
   stable `set` id.

The normal planner chooses the preferred buttons while they fit and falls back to the one-control
select under component or row pressure. Both variants are exact Discord controls and work on the
classic and Components V2 targets. Option descriptions appear in the select; button rendering
keeps the label and emoji. No control is marked selected because a public message cannot display a
different default for each reader.

Select limits derive from cardinality: `minimum` is the category minimum and `maximum` is the
finite maximum or the option count. `minimum=0` permits clearing. The renderer continues to audit
all custom-id and component budgets; a whole panel that cannot fit even with one select per
category fails normally instead of dropping a role.

This plan adds a small reusable variant builder for “preferred routed buttons, fallback routed
select” if the planner cannot express that ladder without RolePanel reaching into adaptation
internals. It does **not** change `sl.routed_choices`, whose current contract is select-only.

## Interaction transition

Router dispatch has no mount action lock, so all `RolePanel` instances share an internal lock
table keyed by `(guild_id, member_id)`. It retains no idle locks, following `SessionRegistry`'s
waiter-counted pattern. This serializes panels in one process when they touch the same member;
Discord remains authoritative across processes and administrator actions.

Both routes:

1. Require a guild interaction and a `discord.Member` actor. Otherwise send the normal ephemeral
   unavailable result.
2. Defer ephemerally before REST work so fetching and editing cannot miss the acknowledgement
   deadline.
3. Enter the per-member lock, then fetch the member again from Discord. The interaction object's
   cached `roles` are never the transition input.
4. Resolve every configured role from the interaction guild and reject the operation without a
   write if a configured id is missing.
5. Compute the current category set, the requested candidate, and a cardinality result.
6. Verify every role being added or removed is editable by the bot: not managed, below the bot's
   top role, and permitted by `manage_roles`. A role merely retained by the candidate need not be
   editable.
7. Preserve every role outside the category and issue one `Member.edit(roles=..., reason=...)`
   request for the complete candidate set. The default role is handled according to discord.py's
   `Member.edit` contract rather than being sent as an editable role.
8. Send feedback. The persistent panel message is never edited.

One complete edit avoids a half-applied remove-then-add transition for `EXACTLY_ONE`. An
administrator can still change roles between the fetch and edit because Discord offers no
compare-and-swap. Keeping the critical window to one request is the strongest available
guarantee; the next interaction re-reads whatever actually won.

### Button rules

- Clicking an absent role adds it when the maximum permits.
- For `maximum == 1`, clicking an absent role replaces every currently held category role with
  that role. This is the intuitive radio-button transition and also repairs a manually invalid
  multi-role state.
- Clicking a held role removes it only when the resulting count remains at least the minimum.
- For a maximum greater than one, clicking an absent role at capacity is refused; the panel does
  not guess which existing role to remove.
- Any candidate that still violates the cardinality because Discord started in an invalid manual
  state is refused, except the `maximum == 1` replacement repair above.

### Select rules

The submitted values are the desired complete role set for that category. Unknown, duplicate, or
out-of-category values are refused. A candidate within cardinality replaces the category set;
this lets a user repair any manually invalid state in one interaction. Empty selection is legal
only when the minimum is zero.

An unchanged candidate performs no Discord write and reports the idempotent result.

## Feedback and failures

The module exposes typed outcomes to one optional feedback hook:

```python
type RoleTransitionResult = (
    RolesUpdated
    | RolesUnchanged
    | RoleSelectionInvalid
    | RoleConfigurationUnavailable
    | RoleMutationForbidden
    | RoleMutationFailed
)

type RoleFeedback = Callable[
    [discord.Interaction[Any], RoleTransitionResult],
    Awaitable[None],
]
```

Results contain category key and the relevant immutable role-id sets; failure results contain a
safe diagnostic reason but never a raw Discord response body. The default hook sends concise
English ephemeral feedback with mentions disabled. Applications needing localization, audit
logging, or domain-specific wording provide a hook. The hook runs for every normal outcome,
including unchanged and invalid cardinality.

`discord.Forbidden`, a missing role, and expected role-hierarchy failures become typed feedback.
Unexpected exceptions still reach the Router's configured error hook after the interaction has
been deferred. A feedback-hook exception also reaches that hook; it never retries a role mutation.

## Ownership boundaries

- Discord owns the member-role set.
- The caller-owned `RouteGroup` owns stable interaction identity and middleware.
- `RolePanel` owns category transition and rendering policy.
- The planner owns button-versus-select fitting.
- The host owns where and how the static message is posted.
- No `sl.state`, `Shared`, `Mount`, `Session`, database table, or message edit is introduced.

## Not included

- No administrator role editor or permission-role integration.
- No role creation, deletion, ordering, or hierarchy repair.
- No reaction-role migration.
- No paging, per-user selected appearance, or live message refresh.
- No cross-guild panel; one interaction always resolves roles in its own guild.
- No scheduled reconciliation when administrators change roles.
- No automatic audit database beyond Discord's audit-log reason.
- No game-board, settings, leaderboard, or other Discord pattern family.

## Verification

- Construction rejects invalid category keys, duplicate categories/roles, invalid snowflakes,
  impossible cardinalities, empty/oversized categories, and a group that cannot be installed
  atomically.
- Rendering prefers button groups when they fit, falls back to a routed select under pressure,
  preserves labels/emoji/descriptions, and never carries selected state.
- Route ids round-trip category and role parameters, remain stable across reconstruction, and fit
  Discord's custom-id limit.
- `ANY`, `AT_MOST_ONE`, `AT_LEAST_ONE`, `EXACTLY_ONE`, and a general bounded cardinality cover add,
  remove, replacement, capacity refusal, and manual-invalid-state repair.
- Select submissions reject unknown, duplicate, malformed, and out-of-category role ids.
- The handler uses a freshly fetched member, preserves unrelated roles, skips an unchanged edit,
  and sends one complete edit for a change.
- Missing, managed, hierarchy-inaccessible, and forbidden roles produce feedback without a partial
  mutation.
- Concurrent interactions for one member serialize; different members can proceed independently;
  idle locks are removed.
- Reconstructing the panel with the same route group shape handles controls rendered before a
  simulated restart.
- The default feedback is ephemeral with mentions disabled, and a custom feedback hook receives
  every typed outcome.
- Focused role, route, planner, classic/V2 renderer, public API, and typing tests pass; then run
  `just typecheck` and `git diff --check`.

## Status

Designed. Independent of plans 59, 60, and 62.
