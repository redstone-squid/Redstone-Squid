# Tiny-helper audit

Audit baseline: `fa7d274c5`, 2026-11-10. Cleanup completed on 2026-11-10.

All 24 finding groups below have been implemented. Source locations in the findings describe the audit
baseline; the implementation and follow-up decisions are recorded here so those locations remain useful
as review evidence.

This audit follows [the small-helper guidance](../code-documentation-style.md#small-helpers).
The question is whether an abstraction helps its callers, not whether it is short.
The findings were recorded before implementation; cleanup was committed in separate reviewable pieces.

## Findings at a glance

The audit identified 24 finding groups and eight follow-ups. All are resolved, including explicit retention
decisions for filesystem helpers and CLI workflow policy.

| Priority | Findings | Reason |
| --- | --- | --- |
| Correctness first | H19-H21 | Ambiguous content hashing, rounded pagination identifiers, and inaccurate error-envelope type guards. |
| Ownership and dependency direction | H01-H03, H12-H13, H17 | Behavior detached from its type or a shared service depending on a transport renderer. |
| Straightforward cleanup | H04-H11, H14-H16, H22-H24 | Redundant wrappers, dead helpers, same-domain duplication, and misleading names. |
| Clarify effects | H18 | Mutation and account creation hidden behind value/accessor-like names. |

H01 includes the original `require_consented_account` example and three related operations. H19 and H20
were reproduced against the actual functions. H21 was reproduced by evaluating the existing guard bodies.
The remaining findings come from definitions and call-site inspection, not observed production failures.

## Implementation

| Findings | Result | Commits |
| --- | --- | --- |
| H01 | Consent, credential matching, permission subjects, and effective permissions are Caller methods. | `3eaee3203` |
| H02 | Page and PageAnchor own their `from_domain` constructors. | `669d0483f` |
| H03, H08 | Candidate.create owns construction; empty-query limiting uses lazy islice. | `8312093fa` |
| H04-H07, H09-H10, H14-H15 | Removed dead application helpers and forwarding layers; reused domain tag normalization. | `7b827c46a` |
| H11-H14 | Entity/presentation behavior moved onto its types; redundant library wrappers and unused clocks removed. | `07461210f`, `6b686c97f` |
| H16 | Developer-tool status summaries share one presentation module. | `c3eddd8e7` |
| H17-H18 | Plain claimant labeling moved to accounts; hydration and account creation have explicit names. | `1cbb6dbb4`, `492603125` |
| H19 | Cursor fingerprints and option revisions use unambiguous length/array encodings. | `d7e3cc5b8`, `3dd69cd48` |
| H20-H22 | Exact pagination integers, checked error envelopes, and direct idempotency UUID creation. | `1ff5cc223` |
| H23 | Protocol-owned player-token validation is shared with stored credentials. | `00a087c4b` |
| H24 | Terminal label formatting is shared; generic transport predicates belong to error types. | `163812241` |

H17 also exposed a pre-existing bug in plain message interpolation: it still escaped Markdown punctuation.
`2b368768e` makes plain interpolation preserve literal values, with regression tests. Discord Markdown
interpolation still escapes as before. Shared claimant labels no longer include formatting backticks or
Discord autocomplete truncation; the Discord adapter retains mention formatting and its display limit.

Package API replacements are documented in
[the migration guide](../../packages/squid-ui-discord/docs/migrating.md#updating-helper-based-calls).
No forwarding compatibility aliases were left behind. Existing cursor fingerprints and option revisions
will change once with the new encoding; callers should treat them as opaque content identities.

## Scope and method

Inventory tracked source, then inspect definitions, callers, imports, registrations, exports, and test patch targets.
Python screening includes functions, methods, and nested functions; a second pass considers larger operations
whose ownership is wrong even though they exceed the small-function threshold. Historical migrations and
completed design spikes are not cleanup targets. Generated clients must be changed through their generator.

The initial inventory contains 1,678 Python files and 9,965 functions with at most five executable AST statements
(excluding test functions and empty bodies, but including methods, overloads, and protocol declarations).
Those are screening candidates, not findings. Identifier occurrence counts only prioritize inspection;
they are not resolved call graphs and cannot establish that a public API is unused.

Coverage is repository-wide screening followed by definition/call-site review of candidates, rather than
an exhaustive manual proof that each of the 9,965 functions should exist. Python screening used both a
five-statement inventory and a wider twelve-statement top-level pass, plus same-module type ownership,
identical-body, low-reference, and nested-wrapper searches. Shortness is a discovery aid, never a verdict.

| Area | Review emphasis |
| --- | --- |
| `squid/accounts`, auth, API, permissions | Credential ownership, consent boundaries, alternate constructors, hydration, dependency callbacks. |
| `squid/bot` | Accessors, callbacks, presentation policy, stale helper definitions, transport adapters. |
| Builds, records, search, suggestions, tags | Normalization ownership, duplicate rules, ranking, conversions, transport coupling. |
| Submissions, schematics, media | Queue/transaction boundaries, validation, hashing, resource lifetime, forwarding methods. |
| Persistence, runtime, workers, observability | Session ownership, cancellation, task callbacks, clock accessors, retry/fencing rules. |
| All seven Python workspace packages | Type-owned behavior, public factories/exports, callable accessors, nested helpers, duplicate rendering rules. |
| Tests, scripts, benchmarks | Stale helpers, one-off setup, fixtures, callback roles, type-check-only probes. |
| `web` | Handwritten TS/TSX helpers, Astro helper declarations, numeric parsing, error guards, retry-key lifetime. |
| `cli` | Rust helper declarations and relevant consumers, terminal formatting, error policy, filesystem helper differences. |
| `minecraft` | Kotlin function/extension declarations and relevant consumers, token grammar, protocol adapters, model methods. |

The tracked non-Python inventory includes 70 TS/TSX, 50 Astro, 41 Kotlin, and 21 Rust files (including tests
and generated files). Generated SDK implementations, SQL schema exports, historical migration bodies,
vendored/dependency files, and completed design spikes are excluded from cleanup recommendations.
The initial audit did not execute Rust or Kotlin tests; cleanup validation is recorded below. Public package functions may have consumers
outside this repository; absence of local references alone is not permission to break those APIs.

## Issue classes

| Class | Decision |
| --- | --- |
| Behavior detached from its owner | Move to the existing type when its state and invariants determine the operation. |
| Detached alternate constructor | Put conversion on the destination type, provided this preserves dependency direction. |
| Forwarding layer | Inline when it supplies no policy, lifecycle, type, or callable boundary. |
| One-use accessor or formatting fragment | Prefer the expression or a named local when the caller becomes clearer. |
| Dead helper | Remove only after checking registrations, exports, string lookups, and patch targets. |
| Duplicate policy | Share within the owning subsystem; do not merge superficially similar rules from unrelated domains. |
| Hidden mutation or resource acquisition | Make the effect and ownership visible; preserve transactional and cleanup behavior. |
| Misleading validation or fallback | Verify the contract against invalid inputs and distinguish coercion from validation. |
| Excessive parameter plumbing | Move behavior with the state it repeatedly receives rather than creating more wrappers. |
| Over-generalized abstraction | Narrow the interface to actual callers; retain meaningful extension points. |
| Test indirection | Keep scenario setup and semantic assertions; inline trivial one-off setup and remove stale helpers. |
| Required callable or type boundary | Keep callbacks, dependency providers, decorators, factories, overloads, and type guards. |

## Confirmed findings

### H01: HTTP caller behavior belongs on Caller

`squid/api/security.py`: `require_consented_account` (210), `credential_allows` (94),
`subject_for` (99), and `caller_allows` (75) operate on `Caller` but live beside it.
Routes repeatedly import the type and its operations separately. Move these to
`Caller.require_consented_account()`, `Caller.credential_allows(node)`,
`Caller.permission_subject()`, and `Caller.allows(permissions, node)`.

Keep `current_caller`, `requires`, `current_browser_account_id`, and `consented_account_id` as FastAPI
integration functions. Preserve account-less service credential behavior, anonymous checks, guild-less
permission subjects, consent error context, and authentication-before-consent precedence.

### H02: Pagination conversion is detached from its response types

`squid/api/pagination.py`: `anchor` (99) and `render_page` (106) construct the adjacent `PageAnchor` and `Page`
types. Use response-owned `from_domain` constructors, consistent with the other API schemas. Keep optional
anchor handling explicit and preserve generic item conversion. Do not move HTTP policy in `resolve_selector`
into the shared pagination model.

### H03: Candidate construction is detached from Candidate

`squid/suggestions/application/matching.py`: `candidate` (48) builds `Suggestion` and then `Candidate`.
The conversion, label fallback, and matching terms belong together on a `Candidate` alternate constructor.
Preserve the current treatment of an empty label when moving it. Keep the ranking and matching algorithms.

### H04: Schematic bootstrap has a one-use constructor wrapper

`squid/bootstrap.py`: `create_schematic_analyzer` (126) only returns `QueuedSchematicAnalyzer(jobs, artifacts, config)`.
Its sole caller is `create_schematic_service` (149). Construct it there and retain the explanation that native
execution belongs to the worker. The factory does not choose an implementation or manage a resource.

### H05: Build persistence has an extra forwarding method

`squid/builds/infrastructure/repository.py`: `_update_existing` (510) obtains `self._session()` and calls
`_update(session, build)`. The existing-build branch at 474 can do that directly. Keep `_update`, which owns
substantial persistence behavior, and preserve session evaluation order.

### H06: Vote reactions wrap an already named consent operation

`squid/bot/voting/reactions.py`: `_consented_account_id` (272) only awaits
`self._voters.consented_account_id(discord_id)`. Its caller at 132 can call the resolver directly.
The wrapper does not translate errors, cache, or enforce another rule.

### H07: Unreferenced application helpers remain

`collect_lookback` in `squid/bot/submission/message_context.py:137` and `utcnow` in `squid/core/time.py:6`
have no references in executable tracked source after checking imports and all tracked-text matches.
No dynamic registration or patch target was found; `collect_lookback` appears only in a historical plan.
The latter also returns a formatted string despite its clock-like name; do not promote it into a replacement
time API.

### H08: A custom take loop duplicates itertools.islice

`squid/suggestions/application/matching.py:116`: `_take` is called only by the empty-query branch of `rank`.
That branch already returns early for a nonpositive limit. Replace the intermediate list and custom loop
with `tuple(item.suggestion for item in islice(candidates, limit))`. Preserve lazy consumption: do not
convert the entire iterable to a list and slice it.

### H09: Singular NBT budget wrapper adds a second name for one operation

`squid/schematics/infrastructure/java_structure.py:262`: `_claim_value()` only calls `_claim_values(1)`.
Its sole caller at 164 can use the latter. Keep `_claim_values`, which enforces the decoding budget, and
keep `_payload_without_claim`, which deliberately compensates the budget before recursive parsing.

### H10: Preview lookup wraps session.get without adding behavior

`squid/schematics/infrastructure/preview_publisher.py:392`: `_preview_object` obtains a session and calls
`session.get(SchematicPreviewObject, object_key)`. Inline that lookup at its sole caller at 73, using the
existing transaction session. Keep methods that express publication state changes or fencing predicates.

### H11: MessageRoot returns a callable only to call it immediately

`packages/squid-ui-discord/src/squid_ui_discord/message_root.py:1071`: `_render_message` returns
`self._binding.render_message`. The sole call at 1017 spells this as `self._render_message()(...)`.
Call `self._binding.render_message(...)` directly. This is an accessor, not the renderer-selection policy;
that policy already lives in the binding. Check private patch targets when implementing.

### H12: Entity behavior is detached from the entity vocabulary

`packages/squid-ui/src/squid_ui/entity.py`: `encode_entity_ref` (63), `decode_entity_ref` (71), and
`supports_entity` (92) belong on `EntityRef` and `EntityType`. Prefer `EntityRef.encode()`,
`EntityRef.decode(value)`, and `EntityType.supports(kind)`. Preserve the distinction between integer and
string identifiers and the existing decoding errors. These functions are exported, so change package
exports, users, tests, and documentation together; do not leave permanent forwarding aliases by accident.

### H13: PresentationState has an external mutator that reaches into its internals

`packages/squid-ui/src/squid_ui/presentation.py:189`: `apply_updates(session, updates)` mutates three of
`PresentationState`'s dictionaries and invokes its private `_touch()`. Move the operation onto
`PresentationState`. Preserve ordered application, stale-cursor removal, and exactly one revision increment
when anything changes. This operation is larger than a tiny helper, but it is the same ownership defect.
It is also re-exported through the runtime modules; account for that public API change.

### H14: More obsolete helpers survived the previous pass

The following have no executable references or registrations in tracked source:

- `squid/bot/help.py:177`, `HelpCog._root_commands`: the help implementation builds its command collections elsewhere.
- `squid/schematics/infrastructure/wire.py:173`, `validate_request_payloads`: request decoding and durable request
  validation use their own live entry points. Remove this obsolete entry point, not the shared arity validation.
- `packages/squid-reactivity/src/squid_reactivity/actions.py:719`, `elapsed`, and 723, `monotonic_started`:
  neither is called or re-exported. Removing the latter also removes this module's only use of `time`.
- `tests/unit/bot/test_account_panel.py:275`, `text_of`: no test uses it.

Together with H07 these are seven obsolete definitions. The only extra `collect_lookback` reference is an
old completed plan, not live code. Public-looking service methods without in-repository consumers are
listed separately below rather than declared dead solely from reference counts.

### H15: Restriction normalization duplicates the owning domain rule

`squid/builds/infrastructure/restrictions.py:89`, `_normalize`, duplicates
`squid/builds/domain/taxonomy.py:36`, `normalize_tag_name`. Both casefold and collapse whitespace for the
same tag names and aliases. Use the domain function in `add_alias` and `get_restriction_id`.
Do not merge creator-name, title-token, or search-text normalization merely because parts look similar:
they have different contracts, including Unicode compatibility normalization and hyphen treatment.

### H16: Developer tools maintain duplicate display rules

`packages/squid-ui-discord/src/squid_ui_discord/devtools.py` has `_scheduler_text` (542), `_topics_text` (548),
and `_recovery_text` (554). The inspector repeats their exact bodies as `_scheduler_summary` (940),
`_topic_summary` (946), and `_recovery_summary` (963) in `devtools_view.py`.
Use one small presentation module for these shared developer-tool summaries. Keep command delivery and
interactive layout construction separate. These functions are useful formatting operations; duplication,
not their size, is the problem.

### H17: A tiny suggestion helper hides a dependency on the Discord presentation layer

`squid/suggestions/infrastructure/providers/records.py:93`, `_claimant_description`, forwards to
`present_claimant(claim, mention=False)[:100]` imported from `squid.bot.profile_render`.
The provider serves shared suggestions, yet its formatter lives in the bot and contains Discord mentions,
Markdown, and localization. Move transport-neutral claimant labeling into the account presentation/application
boundary, and keep Discord mentions in the bot. Apply Discord-specific limits at the Discord adapter unless
100 characters is deliberately part of the shared suggestion contract. Simply inlining this wrapper would
leave the actual dependency problem intact.

### H18: Account helpers obscure mutation behind value-like names

`squid/accounts/infrastructure/repository.py:67-83`: `_with_identities`, `_with_creator`, and `_with_claimant`
mutate and return the same ORM object. `_with_identities` also resets and reconstructs `consent`, which is
not suggested by its name. Keep repeated hydration together, but name it as mutation/materialization and
state that the input object is modified. Inline the one-field assignment where that is clearer.
Do not turn these into domain constructors that import the repository or silently copy ORM entities.

`squid/bot/utils/accounts.py:12`, `account_id_for`, also creates an identity when absent. Its docstring is
accurate, but the accessor-like name hides a database write at the two call sites. A name such as
`get_or_create_discord_account_id` makes the effect visible. Keep this transport adapter outside AccountService:
the service should not acquire a Discord dependency.

### H19: Delimiter-based content hashes conflate different inputs

Three small hash helpers encode structured content by joining unrestricted strings:

- `packages/squid-ui/src/squid_ui/planning/cursors.py:47`, `content_fingerprint`, uses NUL between parts.
- `squid/suggestions/application/services.py:164`, `content_revision`, uses NUL between value and label,
  and newline between suggestions.
- `squid/submissions/infrastructure/options.py:80`, `_content_revision`, repeats the latter encoding.

These are deterministic encoding collisions, not probabilistic hash collisions. Verified against the actual
functions: `['a\0b', 'c']` and `['a', 'b\0c']` get the same cursor fingerprint. A single suggestion/choice
`('a', 'b\nc\0d')` and two entries `('a', 'b'), ('c', 'd')` get the same revision. Both `Suggestion` and
`ChoiceOption` accept the example values. An empty sequence and a single empty string also collide in the
cursor helper without needing embedded delimiters.

Use an unambiguous encoding such as length-prefixed fields or JSON arrays before hashing. Preserve ordering,
the nonzero revision convention, and the documented exclusion of description/kind from suggestion revisions.
Keep subsystem-specific public interfaces; a shared encoding primitive is optional. Revisions/fingerprints
will change once after deployment. The consequence is missed content-change detection; this audit did not
establish a remotely exploitable path through every provider or renderer.

### H20: Pagination's numeric helper accepts rounded identifiers

`web/src/lib/pagination.ts:6`, `positiveInteger`, uses `Number.isInteger`, while
`web/src/lib/search.ts:54`, `readPositiveInteger`, checks `Number.isSafeInteger` and decimal syntax.
Verified against the actual pagination module:

```text
readPageParams(new URLSearchParams('after_id=9007199254740993'))
=> { afterId: 9007199254740992 }
```

Reject unsafe integers before forwarding an anchor. All current `minimum` arguments are 1, so the options
object is unnecessary generality. Do not silently unify syntax policies: pagination explicitly allows
JavaScript numeric parsing, while search deliberately requires nonzero decimal text. Decide whether to keep
that distinction and test it. The precision defect can be fixed independently of the syntax decision.

### H21: Duplicate problem-detail type guards promise more than they validate

`web/src/lib/api.ts:49` and `web/src/lib/submission-api.ts:123` each define `isProblemDetail`.
They check only `title` and `status` before asserting the complete generated `ProblemDetail` type.
Evaluating each existing guard with `{title: "Failure", status: 400, detail: 123, context: "not an object"}`
returns true despite violating that type. The error
constructors then consume `detail` under the stronger contract.

Share a decoder for the error envelope that validates the fields actually exposed, or return a genuinely
narrow partial type. Keep catalogue/submission-specific error mapping and network-failure semantics separate.
Add malformed optional-field cases; do not replace a useful type boundary with unchecked casts.

### H22: requestId is a misleading alias for randomUUID

`web/src/lib/submission-api.ts:173`, `requestId`, only calls `crypto.randomUUID()`.
Its eight call sites use the result as an idempotency key, not as the `Request-Id` correlation header.
Use the native expression with a local `idempotencyKey` name, or a correctly named helper if it owns a future
key policy. Preserve key creation outside retry callbacks so retries reuse the same key.

### H23: Minecraft player-token validation is duplicated across modules

`minecraft/protocol/.../MinecraftAuthProtocol.kt:196-219` and
`minecraft/core/.../auth/MinecraftCredentials.kt:177-200` each implement visible-ASCII checking,
`validPlayerToken`, and `parseCompactUuid`. They validate the same token grammar; one spells the secret rule
with a regex, the other with length and character predicates after the ASCII check.
Move the shared grammar into the protocol module and have both response validation and credential storage
call it. Keep both validation entry points: deserialized responses and directly constructed credentials
still need validation. Preserve compact UUID parsing, prefix, length, ASCII, and expected-grant matching.

### H24: CLI terminal formatting duplicates the same sanitization pipeline

`cli/crates/squid-cli-core/src/form.rs:581`, `single_line_terminal_text`, and
`cli/crates/squid-cli-core/src/tui.rs:524`, `single_line`, have identical bodies: sanitize terminal text,
then replace line breaks/tabs with spaces. Put that named operation beside `sanitize_terminal_text` in
`terminal.rs` and use it from both renderers. Keep the distinction between multiline sanitization and
single-line presentation. Error-enum-specific filesystem helpers need a more careful review and are not
an automatic consequence of this straightforward consolidation.

## Follow-up decisions

The cleanup resolved the eight questions raised by the audit:

| Location | Decision |
| --- | --- |
| `StarboardService.get_role_multipliers` | Removed after confirming no tracked consumers, registrations, exports, or supported documentation. |
| `SchematicService.bytes_for` | Removed after the same consumer check; live file-reading operations remain. |
| `DatabaseEngine.validate_database_consistency` | Removed; readiness uses the live migration-head check. |
| `squid-reactivity.shared_state.describe` | Removed; all callers use `str(address)`, with migration guidance. |
| `squid-ui-discord.classic.measure_host` | Removed; callers use `inspection.measure_classic`, with migration guidance. |
| Solver diagnostic `note` | Removed; consumers construct `SolveNote` directly. |
| CLI filesystem helpers | Retained. They perform I/O and validation under different error enums and symlink-check orders. Sharing them would require a new error adapter or change when errors occur. |
| CLI error classifiers | Generic unauthorized/network checks moved to TransportError/CliAuthError. Enrollment, pending authorization, and invalid-session retry policy remain with their workflows. |

## Helpers to retain

| Family | Why it earns an abstraction |
| --- | --- |
| `squid.api.dependencies.get_*`, consent dependency functions, route functions | FastAPI needs callable dependency/endpoint boundaries; annotations and override targets matter. |
| `resource_topic` | Narrows generic Topic construction to `ResourceKind`, catching invalid application resources statically. |
| `squid.bot.message_adapter.to_message_snapshot`, ORM-to-domain mappings | Keep transport/storage dependencies out of shared domain types. These are not misplaced constructors. |
| `squid.api.v1.submissions._submission_actor` | Adapts HTTP credentials to application identity/provenance. Putting Caller dependencies on the domain actor would invert the boundary. |
| `BuildEditScreen.may_edit`, typed UI factories, dynamic target factories | Public/component API or deliberate type boundary; a short forwarding body alone is insufficient evidence. |
| `squid.bot.ui.render_payload`, `render_item`, error-card helpers | Apply shared chrome, localization, palette, and message conventions. |
| Nested authorization callbacks, default factories, sort keys, event handlers | Their callers need functions, often with captured context or a precise callback signature. |
| `squid.core.concurrency` helpers, worker task entry points | Own cancellation, settlement, capacity, tracing, or supervisor registration. Removing them changes lifetime semantics. |
| Consent, permission, retry, fence, and queue predicates | Name meaningful rules and prevent drift between call sites. Review semantics, not line count. |
| JSON/type guards and parser fragments with real validation | Preserve useful narrowing and error context. H21 is about an inaccurate guard, not about removing guards. |
| Test fixtures, harnesses, reusable setup and semantic assertions | Keep scenario meaning and setup reuse. Uncalled public library testing tools and typing probes are not automatically dead. |
| Kotlin transport extension functions, Rust error conversions | Adapt transport/domain errors or attach context; they are not empty pass-through layers. |
| TypeScript URL encoding, CSRF extraction, storage-failure handling, locale conversion | Preserve protocol, security, fallback, and representation rules even when only a few lines long. |

## Original implementation plan

1. Fix H19 and H20 with regression cases showing distinct content identities and exact/rejected numeric anchors.
   Tighten H21 separately with malformed problem responses.
2. Move Caller behavior (H01), covering anonymous and account-less service callers, account permission denial,
   consent-pending callers, and browser-only restrictions. Then handle response/candidate ownership (H02-H03).
3. Remove the forwarding/accessor wrappers and obsolete internal helpers (H04-H11, H14) in small subsystem commits.
   Check patch targets and imports again at implementation time.
4. Fix the cross-layer claimant formatter (H17), then same-domain duplication and naming (H15-H18, H22-H24).
5. Coordinate library API changes (H12-H13) with exports, documentation, typing tests, and downstream callers.

No blanket line-count rule or test asserting a fixed list of private helpers is recommended. Such a rule
would reject useful callbacks and let larger ownership mistakes through. Review should require a concrete
reason for each helper: policy, reuse, an interface/type boundary, or a cohesive operation.


## Verifying content identities after cleanup

Run from the repository root with the installed Python environment:

```python
from squid.suggestions.application.services import content_revision
from squid.suggestions.domain import Suggestion
from squid.submissions.infrastructure.options import _content_revision
from squid.submissions.domain import ChoiceOption
from squid_ui.planning.cursors import content_fingerprint

assert content_fingerprint(["a\0b", "c"]) != content_fingerprint(["a", "b\0c"])
assert content_fingerprint([]) != content_fingerprint([""])
assert content_revision([Suggestion("a", "b\nc\0d")]) != content_revision(
    [Suggestion("a", "b"), Suggestion("c", "d")]
)
assert _content_revision((ChoiceOption("a", "b\nc\0d"),)) != _content_revision(
    (ChoiceOption("a", "b"), ChoiceOption("c", "d"))
)
```

These assertions now pass. At the audit baseline each pair compared equal; regression tests retain these cases.

## Validation

- `just typecheck`: zero errors after the Python changes; no new suppressions or baseline file.
- Integrated API, CLI-auth, and Minecraft-auth transport checks: 301 passed.
- Web: 47 focused tests, ESLint, and Astro checks passed.
- Minecraft protocol/core: 59 tests passed.
- Rust: 35 focused tests, rustfmt, and normal Clippy passed. Strict Clippy reports the same 39 existing
  `result_large_err` diagnostics on an archived pre-change CLI checkout; no suppressions were added.
- Focused package and application tests cover the method moves, content collisions, lazy iteration,
  consent/permission behavior, claimant rendering, and plain interpolation.
- Installed hooks format/lint each Python commit. `git diff --check` passed.

The full configured Python suite was run again after the final code changes. It retains the three
baseline failures listed below; the claimant interpolation failure found during integration is fixed.
Database/container integration tests are outside that configured suite.

Three unrelated failures were independently reproduced using an archived `a01a6c5e3` source tree:

- `test_no_model_stores_a_bare_datetime`: `runtime_setting_changes.changed_at` bypasses InstantUTC.
- `test_localized_messages_are_present_in_the_english_catalog`: pre-existing showcase/workbench msgids
  are missing from the English catalogue. The changed claimant msgids were updated in both catalogues.
- `test_application_and_domain_layers_raise_only_structured_errors`: a builtin raise in
  `squid/settings/domain/runtime.py` violates the existing boundary rule.

These existing failures were not suppressed or folded into the helper cleanup.
