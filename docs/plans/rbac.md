# RBAC, replacing the four permission tiers

> **Status.** Phase 0 (this document) landed 2026-08-13. Phases 1–8 outstanding. Amend this
> document in place as building it proves parts of it wrong, calling out the amendments where
> they occur rather than silently applying them.

## Context

Authorization today is four hardcoded tiers in `squid/bot/utils/permissions.py`
(`check_is_global_admin`, `check_is_server_admin`, `check_is_trusted_or_global_admin`,
`check_is_home_server_trusted_or_global_admin`), backed by one boolean table
(`global_administrators`) and one array column (`server_settings.trusted_roles_ids`). The REST
API runs a *second*, unrelated vocabulary: the `Scope` StrEnum in `squid/api/security.py`.

Three problems follow from that.

- **Nothing is grantable individually.** A user who should be able to run `build measure-timing`
  has to be handed the whole Trusted tier, which also carries `build edit` and delete-log votes.
- **Every check pays for itself.** `is_global_admin` does an accounts lookup then an
  authorization lookup, uncached; `is_trusted_or_global_admin` adds an uncached settings read.
  `squid/bot/give_redstoner.py:104-123` stacks two checks on one command, so a single invocation
  can cost six round trips.
- **The tiers are not a model, they are four points on an ad-hoc lattice.** Adding a fifth
  distinction means a fifth `CheckFailure` subclass, a fifth branch in
  `squid/bot/errors.py:112-193`, and a fifth entry in the taxonomy test.

The goal is a real RBAC engine: every permission separately grantable, hierarchical nodes whose
wildcards cover a namespace *and* leaves that do not exist yet, a way to grant most of a namespace
but not all of it, roles as live composable presets with no independent authority, and the
permission system evaluated ahead of anything Discord-native.

Two existing bugs get fixed on the way. `squid/voting/infrastructure/discord_rest.py:127`
hardcodes `is_staff=False, is_trusted=False`, so `delete_log` votes cast over REST are always
rejected as `not_eligible`. And the legacy bootstrap secret at `squid/api/security.py:93-98`
receives `frozenset(Scope)` — every capability, forever.

### Settled decisions

| | |
|---|---|
| Reach | **Unified bot + API.** One catalogue; API key scopes become nodes, bounded by the owner's own authority. |
| Roles | **Live bundles, composable.** A role may include other roles; edits propagate immediately. |
| Cut-over | **Hard.** The four tiers are deleted; the old tables are backfilled then dropped. |
| Delegation | **Guild-scoped nodes only.** Guild admins can never grant a global node. |
| Wildcards | **`*` = one segment, `**` = subtree.** Gives `build.*.view` as a cross-cutting subset. |
| Subsets | **Tags + subtractive roles.** `build.**` except `@destructive`, Azure-style — subtraction, not deny. |
| Hard deny | **A third `forbid` effect**, absolute and owner-only, for banning. |
| Role priority | **None.** Roles do not outrank each other for resolution; specificity, `excludes` and composition decide. Ties resolve to deny. |
| Role management | **Both** an authority boundary (you cannot edit a role into a state you don't hold) **and** an explicit rank (you cannot manage a role at or above your own). |

## Prior art, and what each one contributes

| System | Idea borrowed | Idea deliberately rejected |
|---|---|---|
| **GCP IAM** | `service.resource.verb` — every one of ~10,000 permissions has the same shape. Predictable shape is what makes wildcards meaningful. | No wildcard support at all; custom roles enumerate permissions by hand. |
| **AWS IAM** | Permission *boundaries* = intersection — exactly the API-key ∩ owner rule, and the delegation rule ("you cannot grant what you do not hold"). Explicit deny as an absolute, short-circuiting layer → our `forbid`. | Mid-string globs (`s3:Get*`). They match on human name spelling and silently capture future nodes sharing a prefix — AWS's best-known IAM footgun. |
| **Azure RBAC** | `Actions` **minus** `NotActions`: set subtraction at role-definition time, explicitly *not* a deny, so another role granting the same action still wins. This is the answer to "grant a subset of a namespace" without deny's blast radius. | Deny *assignments* as a separate parallel system; we fold that into one `forbid` effect. |
| **Kubernetes RBAC** | Role aggregation — composing roles rather than duplicating node lists. | Purely additive, no deny. Their own docs concede a wildcard grants secrets access "and there is no way to subtract it afterwards." That cost is exactly what tags + subtraction avoid. |
| **LuckPerms** (closest analogue; Minecraft-native, so the community already knows it) | *More specific wildcards override less specific ones* — `luckperms.*` true with `luckperms.user.*` false denies the user perms. Confirms specificity-before-deny. Also: temporary nodes, contexts (≈ our guild scope), and own-permissions-beat-inherited (≈ our provenance rank). | Weighted parent groups — weight-vs-specificity interaction is the hardest thing in any permission system to explain. |
| **Discord itself** | The **tier** structure — `@everyone` → role overwrites → member overwrites, where a later tier wins outright — which is already our provenance and subject ranks. Tri-state allow / deny / neutral as the vocabulary `/perm` should use. Role *position* as a **management** hierarchy (who may edit whom). | The within-tier tie-break. Discord unions all role denies, then all role allows, and applies denies before allows, so **allow beats deny and role position is irrelevant**: *"permissions do not obey the role hierarchy … the user would ultimately be able to view the #coolstuff channel, regardless of the role positions."* We invert this — see "Why roles do not outrank each other". |

References: [AWS IAM policy evaluation][aws-eval], [AWS permissions boundaries][aws-bound],
[Azure role definitions][az-roles], [Azure deny assignments][az-deny],
[GCP IAM overview][gcp-iam], [Kubernetes RBAC][k8s-rbac],
[LuckPerms advanced setup][lp-adv], [LuckPerms wildcard specificity][lp-508],
[Discord permissions][dis-perms].

[aws-eval]: https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_evaluation-logic.html
[aws-bound]: https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_boundaries.html
[az-roles]: https://learn.microsoft.com/en-us/azure/role-based-access-control/role-definitions
[az-deny]: https://docs.azure.cn/en-us/role-based-access-control/deny-assignments
[gcp-iam]: https://docs.cloud.google.com/iam/docs/overview
[k8s-rbac]: https://kubernetes.io/docs/reference/access-authn-authz/rbac/
[lp-adv]: https://github.com/LuckPerms/LuckPerms/wiki/Advanced-Setup
[lp-508]: https://github.com/LuckPerms/LuckPerms/issues/508
[dis-perms]: https://docs.discord.com/developers/topics/permissions

## 1. Node catalogue

`squid/permissions/domain/catalogue.py`. The current `domain.py` becomes a package
(`models.py`, `catalogue.py`, `matching.py`, `resolution.py`) with `domain/__init__.py`
re-exporting `GlobalAdministrator` so the four existing importers keep working.

Domain layer: imports only stdlib and `squid.core.i18n._`, which `squid/idempotency/domain.py`
already precedents, so `tests/architecture/test_boundaries.py:30-42` stays green and bot, API and
worker all share one import.

### Naming convention

Every node is **`<domain>.<resource>.<verb>`**, GCP-style, with deeper nesting allowed when a
resource genuinely nests (`build.schematic.render.queue`). Depth 2–5, validated at catalogue
build. Consistency is not cosmetic: it is what makes `build.*.view` mean something.

### Declaration

Registration and constant are the same expression, so duplicates raise at import time:

```python
_b = CatalogueBuilder()

BUILD_APPROVE = _b.node("build.submission.approve", Scope.GLOBAL,
                        _("Approve pending build submissions."), tags=(Tag.MODERATION,))
BUILD_READ    = _b.node("build.submission.read", Scope.GLOBAL,
                        _("View published builds."), default=Default.ALLOW, tags=(Tag.READONLY,))
VOTE_CAST     = _b.node("vote.poll.cast", Scope.GUILD,
                        _("Vote in polls."), default=Default.ALLOW)

CATALOGUE = _b.build()   # freezes; validates grammar, depth, uniqueness, tag membership
```

`PermissionNode` is `@dataclass(frozen=True, slots=True)`: `name`, `scope`, `description`,
`default`, `tags: frozenset[Tag]`. `__str__` returns `name`.

**Tags** are a closed enum, not free strings: `@destructive`, `@moderation`, `@diagnostic`,
`@readonly`. They are semantic classifications, and they are what keep subtraction correct as the
catalogue grows.

### Patterns

A stored *pattern* is one of:

| Form | Matches |
|---|---|
| `build.submission.approve` | that leaf exactly |
| `build.*` | exactly one more segment — `build.foo`, **not** `build.schematic.convert` |
| `build.**` | one or more further segments, any depth. `**` is trailing-only |
| `build.*.view` | the `view` verb across every build resource — a cross-cutting subset |
| `**` | root; everything |
| `@destructive` | every catalogue leaf carrying that tag |

Matching is structural against the catalogue; **no grant ever stores an expansion**. That is what
buys structural immediacy — a leaf added tomorrow is already covered by an existing `build.**`
grant, with no re-grant and no migration. The choice between `*` and `**` is an explicit choice of
blast radius, which a single-wildcard design cannot offer.

Both directions of "automatic" exist and are documented: `build.**` auto-*includes* future
descendants, while a role excluding `@destructive` auto-*excludes* future destructive nodes. The
second is the safe direction, which is why destructive capability is classified by tag rather than
by where it happens to sit in the tree.

Convention: genuinely destructive future capabilities get a **fresh top-level namespace** *and*
the `@destructive` tag, never a quiet leaf under an existing namespace.

### Specificity

A total order, compared lexicographically descending — property **P1** proves it is a strict weak
ordering:

```python
specificity(pattern) = (
    0 if pattern.is_tag else 1,          # concrete patterns beat tag selectors
    count_of_literal_segments,           # more literals = more specific
    0 if "**" in pattern else 1,         # ** is broader than * at equal literal count
    len(pattern.segments),
)
```

So `build.submission.approve` > `build.*.approve` > `build.*` > `build.**` > `@moderation` > `**`.

### The catalogue itself

Every current check site maps to a node. Global — touches the shared cross-guild build database or
bot-wide state, never delegable by a guild admin. `*` marks default-ALLOW:

```
build.submission.{read*, create*, edit, approve, reject, view_pending, recalc, debug}
build.schematic.{measure_timing, detect_lattice}
record.entry.{inspect, rebuild@destructive}
tag.proposal.{list, approve, reject, archive}
restriction.alias.create
version.entry.create
account.claim.{list, approve, reject}
account.verify.relay
account.self.read*
perm.grant.global          role.definition.manage
bot.{sync, debug}@destructive
```

Guild:

```
settings.server.{view, edit}      settings.voting.edit
starboard.board.{view, create, edit, delete@destructive, recount}
starboard.{emoji, weight}.edit
message.archive.create            redstoner.{panel.manage, role.resync}
vote.poll.{cast*, create, close_any}
vote.log_delete.cast              vote.weight.staff
perm.node.view*   perm.subject.inspect   perm.audit.view   perm.grant.guild
role.definition.manage_guild
```

Splitting `tag.proposal` into four leaves rather than one `tag.moderate` is the design paying for
itself: each is separately grantable, and `tag.proposal.*` remains the one-word bundle.

`check_is_home_server` does **not** become a node. It is feature availability driven by
`BotIdentityConfig.owner_server_id`, not authorization — conflating the two is what produced the
four-way tier explosion. It stays as `in_home_server()` in `squid/bot/utils/checks.py`, applied
alongside `requires(REDSTONER_PANEL_MANAGE)`.

A snapshot test pins the sorted `(name, scope, default, tags)` list, so every new leaf is a visible
diff in the pull request that adds it.

## 2. Data model

Eight tables in `squid/permissions/infrastructure/models.py` — the module is already registered in
`squid/persistence/model_registry.py:14`, so no registry change is needed.

- **`permission_roles`** — `id`, `slug`, `name`, `description`, `guild_id` (NULL = global),
  `builtin_key`, `rank int NOT NULL DEFAULT 0`, `protected bool NOT NULL DEFAULT false`,
  `created_by_account_id`, `created_at`.
  `UNIQUE (guild_id, slug) NULLS NOT DISTINCT` (tests run pg17), `UNIQUE (builtin_key)`,
  `CHECK (builtin_key IS NULL OR guild_id IS NULL)`.
  **`rank` is management-only and never enters resolution** — reordering roles must not silently
  change who can do what. `protected` marks the built-ins.
- **`permission_role_patterns`** — `(role_id, pattern) PK`, `mode smallint` (`1` include /
  `-1` exclude), audit columns. One mode per pattern per role, so a role cannot contradict itself.
- **`permission_role_includes`** — `(role_id, included_role_id) PK`,
  `CHECK (role_id <> included_role_id)`, plus audit columns. Composition edges.
- **`permission_grants`** — `subject_account_id` XOR `subject_role_id` (+ `subject_guild_id`),
  `pattern`, `effect smallint`, `scope_guild_id` (NULL = global), `expires_at`,
  `granted_by_account_id`, `granted_at`, `reason`. Partial uniques per subject kind on
  `(subject, pattern, scope_guild_id) NULLS NOT DISTINCT`; covering indexes per subject column.
- **`permission_role_assignments`** — same subject shape plus `role_id`, `scope_guild_id`.
- **`permission_audit_log`** — append-only, written in the mutation's transaction; no
  update/delete path in the repository. `reason` is NOT NULL for `forbid`.
- **`permission_epoch`** — singleton `(id = 1, version, updated_at)`.

`effect` is `CHECK (effect IN (1, -1, -2))` — allow / deny / forbid.

The anti-escalation constraint lives in storage, not only in code:
`CHECK (subject_role_id IS NULL OR scope_guild_id IS NULL OR scope_guild_id = subject_guild_id)`
— a Discord role from guild A can never carry authority scoped into guild B.

**Built-in roles carry their pattern lists in code** (`catalogue.py: BUILTIN_ROLES`), not in the
database; the row exists only so assignments have a foreign key. This is what stops a
migration-seeded list from rotting as the catalogue grows. Database rows for a built-in are purely
*additive overrides*, so an operator can still extend `global-admin` without a deploy.

`api_keys.scopes` keeps its `ARRAY(Text)` shape; only its values become node patterns. Making a
key a fourth grant subject was considered and rejected — a key's node set is an attribute of the
key, with the key's lifetime.

**Epoch trigger** — `bump_permission_epoch()` goes in `squid/persistence/postgres_entities.sql`
as statement-level `AFTER INSERT OR UPDATE OR DELETE` triggers on the six mutable tables, doing
`UPDATE ... version + 1 RETURNING version` then `PERFORM pg_notify('squid_permissions', ...)` —
the same shape as `publish_domain_event` at `postgres_entities.sql:352`. **Bump the
`15 functions / 34 triggers` assertion at `squid/persistence/alembic_entities.py:20-22`.**

## 3. Resolution

`Subject(account_id, discord_role_ids, guild_id, is_bot_owner, discord_guild_admin)`.

**Role expansion** happens first, per role, and is pure set algebra on *patterns*:

```
patterns(R) = (own_includes ∪ ⋃ patterns(child) for child in R.includes) − own_excludes
```

Exclusion applies after the union at each level, so a parent can subtract from what it inherits
("moderator = helper minus the destructive bits"). Cycles are rejected at write time and capped at
depth 8 at read time as belt-and-braces.

**Subtraction is not a deny.** A pattern excluded from role R is simply absent from R's
contribution; if role S includes it, the subject still gets it. This is Azure `NotActions`
semantics, and it is the whole reason subtraction exists alongside `forbid`.

Then, per node:

0. **Owner short-circuit** — `is_bot_owner` ⇒ ALLOW, zero rows read.
1. **Forbid short-circuit** — any candidate rule with `effect = forbid` ⇒ DENY, reason
   `FORBIDDEN`. Absolute, AWS explicit-deny semantics, no specificity ranking.
2. **Candidates** — keep a rule when: it has not expired; `scope_guild_id` is NULL or equals the
   subject's guild; the pattern matches; and — the load-bearing one — a guild-scoped rule is
   discarded when `CATALOGUE[node].scope is GLOBAL`. That check is on the *node's* declared scope
   at evaluation time, so leaves added years after a grant are safe.
   Provenance rank: `3` account grant, `2` Discord-role grant, `1` via an assigned role (at any
   composition depth — subtraction already resolved inside the role), `0` the `guild-admin` bridge
   (present only when `discord_guild_admin is True`).
3. **Rank**, descending, first difference wins:
   `(specificity, scope_rank, subject_rank, provenance_rank, deny_first)`.
4. **Verdict** = the maximal element's effect. `deny_first` sits last: deny breaks *complete* ties
   only.
5. No candidate ⇒ the catalogue default. Unknown node string ⇒ `UnknownPermissionNodeError`
   (a programming error, never a silent deny).

Specificity above deny is LuckPerms' rule and the one Minecraft admins already expect:
`settings.**` allow plus `settings.server.edit` deny is "the namespace minus one hole". `forbid`
exists so the emergency stop does not have to fight specificity.

The resolver returns `Decision(allowed, reason, node, trace)` with every candidate and its
lose-reason. `/perm explain` formats exactly this, so there is no second implementation of the
rules.

### Why roles do not outrank each other

`rank` is deliberately absent from the precedence tuple. Two reasons.

First, the case that makes role weights feel necessary in LuckPerms does not arise here. Bukkit
permissions default to unset-means-nothing, so admins write blanket denies into a `default` group
and then need weights to punch through them. Here an absent rule already falls through to the
catalogue default, which is deny for every privileged node — so a blanket deny in a base role is
never needed, and what remains is covered by specificity, `excludes`, and composition. Adding
priority would be a fourth mechanism aimed at a problem the first three already solve. Neither
AWS, GCP, Azure nor Kubernetes has role ordering.

Second, **Discord does not do this either**, which is the common misreading and is worth stating
plainly in the user-facing docs. Discord unions every role's denies, then unions every role's
allows, then applies denies followed by allows — so within the role tier, allow beats deny and
position is never consulted. Their own documentation uses this exact example: role A denies
`VIEW_CHANNEL`, role B allows it, *"the user would ultimately be able to view the #coolstuff
channel, regardless of the role positions."* Role position in Discord governs management
capability only, which is what §8 uses it for.

**We invert Discord's within-tier tie-break on purpose.** Discord resolves a same-tier conflict to
allow; we resolve it to deny. A deny means something different in the two systems: in Discord a
deny is often just how you structure a channel, whereas here — because absence already denies — a
deny rule is only ever something a person deliberately typed. Honouring it is the safer reading,
and it keeps `deny` useful rather than advisory. The tradeoff is real and belongs in the
user-facing docs: admins fluent in Discord will expect the opposite, so `/perm explain` always
names the losing rule and why it lost.

### Worked case: guild admin escalation, blocked twice

A `perm.grant.guild` holder runs `/perm grant @Helpers build.**` in guild G. Grant-time validation
rejects it, because the pattern reaches global-scoped leaves. And if a row somehow existed with
`scope_guild_id = G`, resolving `build.submission.approve` discards it at step 2 and falls to
default DENY.

### Owner escape hatch

Step 0 precedes both the forbid check and any row read, so no rule can lock the owner out. Plus:
the `owner` built-in is immutable (patterns are the code constant `("**",)`); `perm revoke` refuses
to remove the last `perm.grant.global` holder; and
`uv run python -m squid.permissions.cli grant --account <id> --pattern '**'` writes through the
repository for the case where `owner_id` itself is misconfigured.

## 4. Caching and immediacy

**Epoch row + NOTIFY hint + wall-clock backstop** — the belt-and-braces pattern the repository
already chose for domain events (`squid/events/infrastructure/listener.py`; the comment at
`postgres_entities.sql:350-351` says *"NOTIFY … is a latency hint; durable consumers still poll"*).

Rejected alternatives: TTL alone (staleness lands on *revocation*, the wrong direction); Redis
pub/sub (the bot process has no Redis today — `squid/api/rate_limit.py` is the only user); the
`domain_events` machinery (three processes are not three durable consumers, and cache invalidation
needs neither durability nor ordering).

`PermissionEpochWatcher` runs as a `BackgroundTaskSupervisor` job in the bot and API processes:
`LISTEN squid_permissions` on one asyncpg connection (generalise `DomainEventWakeListener` rather
than duplicating it) plus a 5 s poll. On change, `cache.clear()` — O(1), and correct for role
edits, where per-subject invalidation would have to know every holder, and for role *composition*
edits, where it would have to know every transitive holder.

**Cache the expanded rule set, not the decision.** Key
`(account_id, frozenset(role_ids), guild_id)` → `SubjectRuleSet`, role expansion already applied.
One query then serves every node on a command, `resolve_many()` for the vote actor's nodes, *and*
any future leaf covered by a cached wildcard — zero further queries. Entries stamp `epoch` and
`fetched_at`; a read discards a mismatched epoch or an entry older than 30 s, so a dead watcher
degrades to a 30-second TTL rather than to unbounded staleness. Bounded LRU (4096 entries).
Counters via `squid.observability.add_counter`:
`permissions.cache.{hit,miss,invalidation,stale_epoch_discard}`.

Resolve the subject **once per command** in a `bot.before_invoke` hook, and keep a small TTL map
for `discord_id → account_id`. Never call `get_or_create_account` inside a check — that writes a
row for every unauthenticated caller; the resolver accepts `account_id=None` and answers from
Discord-role grants. Discord role membership is not cached by us on the bot path
(`ctx.author.roles` is already gateway-fresh); the REST path gets its own documented 60 s cache.

## 5. Transports

### discord.py

`squid/bot/utils/permissions.py` collapses to
`requires(*nodes, mode="all"|"any", guild_only=False)`. The predicate builds a `Subject`, calls
`ctx.bot.services.permissions.check(...)`, and raises `PermissionNodeRequired(node)` — one
`CheckFailure` subclass replacing all four. It stamps `predicate.__squid_nodes__` so the taxonomy
test can introspect the real contract. Drop the `@cache` on the factories: it exists only to keep
`predicate.__qualname__` stable for the current test.

`squid/bot/errors.py:132-156` — four branches become one, rendering the node name (identifiers stay
untranslated) plus its `_()`-wrapped catalogue description, and a distinct message for `FORBIDDEN`.
Four `.po` entries out, two in; run the i18n extract in that phase.

### FastAPI

`require(scope)` at `squid/api/security.py:51-61` becomes `requires(node)`. Five call sites:
`api/app.py:75`, `v1/me.py:20`, `v1/notifications.py:22`, `v1/votes.py:18`, `v1/builds.py:29`.
`Principal.scopes` becomes `Principal.nodes: frozenset[str]`; `Scope` is deleted, with the
scope→node alias table living only inside the alembic revision.

Subject derivation:

- **anonymous** — defaults only, so `build.submission.read` keeps public reads working.
- **account / cli / minecraft_player** — `guild_id=None`, therefore **only global-scoped rules ever
  apply**; a guild-scoped grant can never authorize an HTTP call. Deliberate asymmetry; document it
  in the API reference.
- **service key** — `resolve(owner, node).allowed AND any(matches(p, node) for p in key.nodes)`.
  This is AWS's permission-boundary rule: pattern-aware on both sides, enforced once inside
  `PermissionService.check`, so revoking the owner's node instantly defangs every key they issued.
  An ownerless key falls back to its own nodes. The legacy bootstrap secret gets demoted to an
  explicit config node list, or deleted.

`ApiKeyService.issue` validates that requested patterns are a subset of the issuer's authority.
`tests/fuzz/api/database.py:335` seeds literal scope strings and needs updating in the same commit.
Regenerate `contracts/openapi.json` via `scripts/export_openapi.py`.

## 6. The imperative checks

`VoteActor` (`squid/voting/domain/models.py:78-86`) and `ReactionActor`
(`squid/reactions/domain/models.py:8-15`) are duck-typed against each other —
`RoleVoteWeightPolicy` passes a `VoteActor` into `RoleWeightPolicy.calculate(actor: ReactionActor,
...)` — so both drop `is_staff`/`is_trusted` together and gain `capabilities: frozenset[str]`,
holding already-resolved node names. The domain stays framework-free; the booleans are computed at
the edge.

- `squid/voting/application/policies.py:24` → `VOTE_LOG_DELETE_CAST.name in actor.capabilities`.
- `squid/reactions/application/policies.py:38` → a `staff_capability: str` constructor parameter,
  so the reactions context stays generic and the voting context supplies the concrete name.
- `squid/bot/voting/vote.py:272-290` `_actor()` builds the subject from the `discord.Member` and
  calls `resolve_many` (one fetch); `vote.py:218-221` uses `VOTE_POLL_CLOSE_ANY`.
- **`squid/voting/infrastructure/discord_rest.py:127`** — the design fixes the bug rather than
  working around it: `vote.log_delete.cast` is now grantable to a Discord role, so the REST
  payload's role ids answer it with no guild-permission fetch. Only the Manage-Server bridge stays
  unavailable there, and it is the lowest-priority source anyway.
- `squid/bot/submission/ui/views.py:461-477` `can_edit` → owner-of-pending unchanged, else
  `allows(subject, BUILD_SUBMISSION_EDIT)`. Because that node is global-scoped, "home server only"
  now comes from *where the grant was made*, not from a hardcoded guild comparison — another
  community can be given build-edit authority without a code change.
- `squid/api/v1/builds.py:105-109` → `allows(subject, BUILD_SUBMISSION_EDIT)`; `:183-194` →
  `requires(BUILD_SUBMISSION_VIEW_PENDING)`. Keep the "service keys never read unreviewed
  submissions" property by granting that node to no key by default — it is now an expressible
  policy rather than a hardcoded branch.

New port `squid/permissions/application/ports.py: ActorCapabilityResolver`, so the voting
infrastructure depends on a protocol rather than on the permissions application package, keeping
`test_application_layers_are_framework_and_infrastructure_independent` green.

## 7. Migration

Create each revision with `just db-revision "..."` so the filename template applies.

1. **`add_permission_rbac_tables`** — eight tables, constraints, indexes, the epoch row, the
   trigger function, and four built-in role rows. All `protected`, ranks `owner` 1000,
   `global-admin` 800, `guild-admin` 500, `trusted` 200, and **no pattern rows** (patterns live in
   code):

   | Role | Includes | Excludes |
   |---|---|---|
   | `owner` | `**` | — (immutable) |
   | `global-admin` | `build.**`, `record.**`, `tag.**`, `restriction.**`, `version.**`, `account.**`, `settings.**`, `starboard.**`, `message.**`, `vote.**`, `perm.subject.inspect`, `perm.audit.view`, `perm.grant.guild`, `role.definition.manage_guild` | `@destructive`, `bot.**`, `perm.grant.global`, `role.definition.manage` |
   | `guild-admin` (Manage-Server bridge) | `settings.**`, `starboard.**`, `message.**`, `redstoner.**`, `vote.**`, `perm.grant.guild`, `perm.subject.inspect`, `role.definition.manage_guild` | `@destructive` |
   | `trusted` | `build.schematic.measure_timing`, `build.schematic.detect_lattice`, `vote.log_delete.cast`, `vote.weight.staff` | — |

   `global-admin` expressed as subtraction rather than a hand-enumerated list is the design paying
   for itself twice: it is shorter, and a future `@destructive` node is excluded automatically.
   Invariant test: every leaf `guild-admin` resolves to is `Scope.GUILD`.

2. **`backfill_permission_grants_from_legacy_tiers`** — each `global_administrators` row becomes a
   `global-admin` role assignment preserving the original `granted_by`/`granted_at`; each
   `trusted_roles_ids` entry becomes one `trusted` role assignment per Discord role. Assigning the
   built-in role rather than writing raw patterns keeps the migration honest: it is one row per
   role, self-documenting in `/perm explain`, and stays correct if the tier's meaning is later
   refined in code.

   Also rewrites `api_keys.scopes` (`builds:read→build.submission.read`,
   `builds:write→build.submission.create`, `verify→account.verify.relay`,
   `votes:cast→vote.poll.cast`, `users:read→account.self.read`) with a matching downgrade.

   The home-server extras (`build.submission.edit`, `build.submission.recalc`, from
   `check_is_home_server_trusted_or_global_admin`) are **not** backfilled. The migration cannot
   read `BotIdentityConfig.owner_server_id`, and silently granting cross-guild build-edit to every
   guild's Trusted roles would be a real privilege escalation. Two manual commands go in the
   revision docstring and the cut-over runbook:

   ```
   /perm grant @Trusted build.submission.edit   --scope global   (run in the home guild)
   /perm grant @Trusted build.submission.recalc --scope global
   ```

3. **`drop_legacy_permission_tiers`** (Phase 8, after a soak) — drops `global_administrators` and
   `server_settings.trusted_roles_ids`, once `AuthorizationService`,
   `GlobalAdministratorRepository`, the `"Trusted"` key at
   `squid/settings/infrastructure/repository.py:30`, and the `Trusted` entries in
   `squid/settings/domain/models.py:14,18,19` are all gone.

## 8. Admin surface

`/perm`: `grant`, `deny`, `forbid`, `revoke` (removes a rule — distinct from `deny`, and the help
text says so), `list`, `nodes` (paginated catalogue with tags; autocomplete over leaves, their
wildcard ancestors, and tag selectors), `explain`, `audit`, `whoami`, `test <user> <node>`.

`/role`: `create`, `delete`, `show` (rendering includes, excludes, composed roles, rank, **and**
the resolved leaf set), `list` (ordered by rank), `include`, `exclude`, `add-role`/`remove-role`
(composition), `rank`, `assign`, `unassign`.

The three effects are surfaced in Discord's own vocabulary — allow / deny / neutral (no rule) —
with `forbid` presented as a distinct, loud, reason-mandatory action.

`/perm explain` renders `Decision.trace`, winner first:

```
build.submission.edit for @alice in Redstone Squid → DENIED

  ✗ deny  build.submission.edit  account:@alice      global  (lit3, global, account, direct)  ← decisive
  · allow build.submission.edit  role:@Trusted       global  (lit3, global, role, direct)     lost: subject
  · allow build.*.edit           role:moderator      global  (lit2+*, …)                      lost: specificity
  · allow build.**               role:moderator      global  (lit1+**, …)                     lost: specificity
      via: moderator → includes helper, excludes @destructive
  default for build.submission.edit: deny (global)
```

### Delegation guards

In `PermissionService`, not in the cog. `perm.grant.global`, `role.definition.manage` and `forbid`
are owner-only. `perm.grant.guild` and `role.definition.manage_guild` are grantable only by a
`perm.grant.global` holder, so there is no re-delegation of the granting permission. A
`perm.grant.guild` holder may only write rules scoped to their guild where **every catalogue leaf
the pattern reaches** is guild-scoped.

### Two independent management gates

Both must pass before any role edit or assignment. They block different attacks, so the failure
messages must say which one refused — a single "insufficient permissions" here is what makes
permission systems infuriating.

1. **Authority boundary** (AWS permissions-boundary semantics). You may only grant a pattern, or
   edit a role into a state, whose every reachable leaf you hold yourself. Self-maintaining: it
   tracks real authority rather than a hand-kept integer that drifts as the catalogue grows. This
   stops *escalation* — you cannot mint yourself something you lack.
   > `build.submission.approve is outside your authority; you cannot grant what you do not hold.`

2. **Rank** (Discord's model, and the reason server admins will find this familiar). You may only
   manage a role whose `rank` is strictly below your own highest held rank, and you may not set a
   role's rank at or above your own. This stops *lateral sabotage* — two equally-privileged guild
   admins editing or deleting each other's roles, which the boundary rule alone permits because
   neither party gains anything by it.
   > `moderator (rank 60) is at or above your highest role (rank 60); you cannot manage it.`

`protected` roles (the four built-ins) refuse structural edits from anyone but the owner regardless
of both gates, so `global-admin` cannot be hollowed out by someone who happens to hold everything
in it. The owner bypasses rank entirely, mirroring Discord's guild-owner exemption.

Keeping `rank` out of resolution is what makes having both gates safe: reordering roles for
management reasons can never silently change an authorization outcome.

`/role show` should surface "you can manage this role's rank but not these 3 patterns" rather than
making someone discover the boundary one rejected edit at a time — the two gates disagreeing is
the case most likely to generate confused bug reports.

The `admin global-admin` group (`squid/bot/submission/records.py:34-110`) is deleted.

## 9. Phasing

Commits are Hashimoto-style and component-scoped, e.g.
`permissions: add the node catalogue and pattern matcher`.

| Phase | Content | Leaves working |
|---|---|---|
| 0 | this document | docs only |
| 1 | catalogue, `matching.py` (`*`/`**`/tags), resolver, role expansion; unit + Hypothesis tests | all; no callers yet |
| 2 | models, revision 1, repository, `PermissionService`, ports; wire into `runtime.py` + `bootstrap.py:537,564` | all; tiers untouched |
| 3 | rule-set cache, epoch watcher job, observability counters | all |
| 4 | revision 2, then `requires()` + error branch + call sites, **one commit per cog family**, deleting each old predicate as its last user goes; rewrite the taxonomy test | per commit: that cog on nodes, the rest on tiers |
| 5 | actor `capabilities`, vote + reaction policies, `can_edit`, `discord_rest.py:127` fix | REST `delete_log` votes work |
| 6 | API `requires(node)`, `Principal.nodes`, key ∩ owner, `api_keys.scopes` rewrite, `Scope` deleted, OpenAPI regenerated | API on nodes |
| 7 | `/perm` + `/role` + audit + explain | full self-service |
| 8 | delete the tier predicates, `AuthorizationService`, `GlobalAdministrator*`, `Setting["Trusted"]`; revision 3 | single engine |

Phase 4 carries the only live-behaviour risk, so it is split per cog family: a bad node mapping
affects one command family and reverts cleanly. Revision 2 lands *before* the first flipped site,
so nobody loses access mid-deploy.

## 10. Testing

**Catalogue** (`tests/unit/permissions/test_catalogue.py`): pinned `(name, scope, default, tags)`
snapshot; naming convention and depth; every leaf `guild-admin` resolves to is `Scope.GUILD`;
`owner` is exactly `("**",)`; `global-admin` reaches no `@destructive` node, no `bot.**`, no
`perm.grant.global`.

**Matcher and resolver, property-based.** Hypothesis is already a dependency
(`pyproject.toml:71`); follow `tests/unit/versions/domain/test_version_properties.py`. Strategies
generate rule sets, roles and composition graphs over the real catalogue.

- **P1 order invariance** — shuffling rules never changes the decision. Proves the specificity
  tuple is a strict weak ordering; the single most valuable property here.
- **P2 wildcard equivalence** — a lone ancestor-pattern grant decides a leaf exactly as a lone leaf
  grant does.
- **P3 depth discipline** — `a.*` matches a leaf iff the leaf has exactly one more segment; `a.**`
  matches iff it has one or more. No leaf is matched by `a.*` and not by `a.**`.
- **P4 scope containment** — no guild-scoped rule can ever ALLOW a global node.
- **P5 delegation safety** — any grant the `perm.grant.guild` validator *accepts*, added to any
  rule set, leaves every global node's decision unchanged for every subject. Tests validator and
  resolver together; the strongest escalation guarantee in the suite.
- **P6 subtraction is not deny** — for any roles R (excluding pattern P) and S (including P), a
  subject holding both is ALLOWed P. The Azure semantic, and the one most likely to be broken by a
  well-meaning refactor.
- **P7 composition termination** — role graphs with cycles are rejected at write time; expansion of
  any accepted graph terminates and is order-independent.
- **P8 forbid supremacy** — a forbid rule matching a node denies it regardless of any other rules,
  for every non-owner subject.
- **P9 owner supremacy** — for every rule set, including forbid rules, the owner is ALLOWed.
- **P10 rank does not affect resolution** — permuting every role's `rank` leaves every decision for
  every subject unchanged. This keeps the two management gates from leaking into authorization, and
  will fail loudly if someone later adds rank to the precedence tuple.
- **P11 management gates are non-escalating** — for any role edit the boundary gate accepts, the
  editor's own effective permissions are unchanged by the edit, and no sequence of accepted edits
  lets a subject reach a node they could not already reach.
- **P12 specificity dominance**, **P13 trace soundness** (re-running with only the winning trace
  step reproduces the verdict, which is what keeps `/perm explain` honest), **P14 default
  fallthrough**.

**Cache**: epoch bump clears; stale-epoch entries are discarded on read; the wall-clock backstop
refetches; and **one repository call for an N-node resolution**, the N+1 regression guard.

**Integration** (`tests/integration/permissions/`): the CHECK constraints actually reject a
cross-guild role scope, a duplicate rule, and a self-including role; `test_epoch_notify.py` mirrors
`tests/integration/events/test_emit_trigger.py`; and `test_migration_backfill.py` seeds
`global_administrators` + `trusted_roles_ids` at the pre-revision head, upgrades, then runs a
**parametrized old-vs-new equivalence table** over ~20 `(user, guild, tier)` cases asserting the new
resolver matches the old predicate — using the `migration_database_url` pattern from
`tests/integration/test_alembic_migrations.py`.

**Rewritten `tests/unit/bot/test_command_taxonomy.py`**: replace `_check_names` and the count
assertions (lines 148-211) with a node contract read from `predicate.__squid_nodes__`, plus a
companion assertion the old test could not make — **every non-hidden command in the
admin/settings/starboard/records/verify cogs declares at least one node**, so a privileged command
shipped with no gate fails CI. `is_owner`-only commands go in an explicit allowlist.

**Architecture** (`tests/architecture/test_boundaries.py`): `squid.permissions.domain*` imports
nothing from `squid.*` except `squid.core*`; and an AST scan asserting no `requires(...)` call
passes a bare string literal, making a node typo a CI failure rather than a runtime deny.

## Traps this design closes

1. **Wildcard scope creep** — node scope is checked against the *rule's* scope at evaluation time,
   so future leaves are safe under old grants (P4).
2. **Namespace wildcards that cannot be trimmed** — the Kubernetes "no way to subtract it
   afterwards" problem, solved by tags + subtractive roles without deny's blast radius (P6).
3. **Prefix globs capturing future nodes** — AWS's footgun, avoided by rejecting mid-string globs;
   `*` and `**` match segment boundaries only (P3).
4. **Guild-admin escalation** — blocked three times: validator, DB CHECK, resolver filter (P5).
5. **Owner lockout** — step 0 precedes the forbid check and any row read; the `owner` role is
   immutable; last-admin revoke is refused; a CLI escape hatch exists (P9).
6. **Deny-rule surprise** — specificity is the top-level rule, deny breaks complete ties only,
   `forbid` carries the absolute case, and `/perm explain` names the decisive rule. The deliberate
   inversion of Discord's same-tier tie-break is documented user-facing, not only in code.
7. **Lateral sabotage between equal admins** — the authority boundary alone permits it, so `rank`
   covers it and `protected` covers the built-ins.
8. **Management hierarchy leaking into authorization** — `rank` is deliberately absent from the
   precedence tuple, enforced by P10 rather than by convention.
9. **Role composition cycles and non-determinism** — rejected at write time, depth-capped at read
   time, order-independent expansion (P7).
10. **Stale revocation** — epoch invalidation + NOTIFY + poll + 30 s backstop; the worst case is a
    30-second stale grant, never an indefinite one.
11. **N+1 per check** — cache the expanded rule *set*; one query per command, zero for
    wildcard-covered future leaves; enforced by a call-count test.
12. **Account rows created by unauthenticated checks** — the resolver accepts `account_id=None`.
13. **Migration over-granting** — the home-server extras are an explicit manual step, not a silent
    cross-guild grant.
14. **Built-in role lists rotting inside a migration** — they live in code; database rows are
    additive only.
15. **The bootstrap secret holding every node forever** — demoted or deleted in Phase 6.
