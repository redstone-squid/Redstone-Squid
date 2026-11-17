# Record recomputation

Status: implemented with the simpler design below. Database concurrency tests cover
snapshot publication ordering and changes arriving during computation.

## Behavior

Concrete door records compare fixed builds and expandable configurations at an
opening. Registered ranges compare smallest-record outcomes across widths at a
fixed height and depth. They use the same candidate rules, but publish independently.

- One explicit current Minecraft version is shared by doors and extenders.
- Registered ranges can use a fixed version, follow current, or include all versions.
- Publish consistent captured inputs even when builds change during arithmetic, then
  catch up. An older concrete input generation cannot replace a newer publication.
- Keep unchanged publications, track successful check time separately, and expose
  pending refreshes. Meaningful evidence changes count even when holders stay the same.
- Concrete kinds and individual registered ranges retry independently.
- Keep superseded runs and immutable snapshots. Notifications still read previous runs.

## Simplifications made during implementation

Keep the existing queue abstraction instead of introducing a generic work-target
framework. There are two queues: concrete work keyed by kind, and range work keyed
by registration. Both use the existing claim tokens, expiry, and capped retry backoff.

Only concrete input ordering needs a new counter: one permanent generation per kind.
Ranges retain their existing attempt generations and immutable snapshot format.
There is no composite input stamp, heartbeat service, or shared attempt hierarchy.
Expiry can cause duplicate computation; publication ordering and unchanged-result
comparison make that safe. Queue tokens protect pending work, not the correctness of
computed output.

Door invalidation queues registered ranges in one database operation. This is simpler
than joining every registration to a separate dirty-generation model. It costs one
upsert per registered range, so measure transaction time as registrations grow.
Explicit range refresh queues only that range and does not rebuild concrete records.

Concrete maintenance still publishes all-time records and the shared current version.
Explicit concrete lookups can materialize another version on demand; they do not
change the pin. We have not introduced a generic registry of every historical
concrete version as an automatically maintained target. Fixed registered ranges
remain automatically refreshed.

## Concrete capture and publication

`RecordComputationService` performs three phases:

1. In a repeatable-read transaction, read the rules revision, kind generation,
   confirmed candidates, and requested categories. Save a versioned input snapshot
   and commit. Numeric taxonomy values survive JSON round trips as decimals; text
   that happens to look numeric stays text.
2. Compute from the saved representation in a bounded worker thread with no database
   transaction or connection held. Capture time supplies evaluation time, so replay
   does not read the clock. Cancellation retains the thread's capacity token until
   arithmetic finishes.
3. In a short read-committed transaction, verify the rules revision remains active,
   lock the kind/version publication, and compare input generations. If a newer
   generation was already checked, retain its result. Otherwise publish changed
   results or update last-checked metadata on the existing publication. A worker
   acknowledges its owned queue token in this same transaction.

A source edit during arithmetic increments the kind generation and replaces the
queue token. The captured snapshot can publish, but its acknowledgement cannot
remove the newer request. A late computation cannot replace a publication checked
against a higher input generation. A crash commits publication and acknowledgement
together or neither.

The original publication snapshot remains attached to its run. A separate pointer
records the inputs of the latest successful check, including checks that changed
nothing. Snapshot rows reject updates and deletes.

Worker claims contain one kind, so failure of doors does not roll back extender
publication. Staff rebuilding both kinds still publishes their concrete scopes in
one transaction. Requested categories are saved with invalidation before rebuilding;
a computation failure leaves the request durable and available for worker retry.

## Invalidation and locks

Source changes enqueue their affected kind, which increments its permanent input
generation in the same transaction. This covers build facts, timing variants, version
membership, and tag assignments. Moving a build between kinds invalidates both.
Tag-definition and unit changes invalidate both kinds because they affect category
membership or displayed measurements. Explicit category requests, rules activation,
and pin changes also enqueue affected work.

Concrete publication takes the shared rules-activation advisory lock, then the
kind/version publication lock. Rules activation takes the exclusive rules lock.
No publication lock is held during arithmetic. Range completion retains its existing
rules lock and registration lock; current-version completion additionally reads the
settings row under a shared row lock before locking the registration.

Do not mix old record writers with the new publication path during rollout. Older
code does not write input generations on publications and cannot enforce the new
ordering guarantee. Stop old worker, bot, and API record writes, migrate, and enable
the new writers. The migration queues both kinds so existing publications receive
snapshot provenance on their next check.

## Version selection and ranges

`record_settings` stores the shared pin and its revision. Migration seeds it once
using the previous newest-active-version rule. Later fixed-version publications
cannot change configuration. The staff Current version ID control changes the pin
explicitly and queues both kinds and registered ranges. Reapplying the same pin
neither advances the revision nor invalidates claims.

Range requests retain `version_id` for a fixed version. `follow_current_version`
selects the shared pin instead; specifying both is invalid. Existing fixed and
all-time registration keys are preserved. Each attempt records the concrete version
it used. With no pin configured, a current query remains pending with an inspectable
failure instead of silently considering all versions.

If the pin changes during a current-range computation, its result cannot become
active. The new pin's queued request remains available. Fixed queries keep their
version. Old range publications remain readable while a replacement is pending;
API responses include resolved-version provenance and pending state.

Each registered range retries separately. Exceptions and structured unsupported,
timeout, or limit-exceeded results retain the prior publication and back off. They
are not successful acknowledgements. New source changes make the query eligible
again. Existing generation fencing prevents superseded attempts from activating.

Range publication and queue acknowledgement remain separate transactions. A crash
between them can repeat an analysis, but semantic comparison retains the same
publication. This avoids coupling the existing range publication interface to a
new generic worker protocol.

## Equality and freshness

Concrete fingerprints include the rules revision, resolved version, categories,
titles, outcomes, missing facts, holder metrics, history, configuration evidence,
and substantive source documents. They exclude evaluation timestamps and losing
fixed candidates that contribute no published evidence. Preserve meaningful ordering;
canonicalize unordered inputs where needed.

Range equality compares both the computed regions and semantic snapshot contents,
including source evidence and resolved version, without the capture clock. Keep
attempts inspectable even when their result leaves the active publication unchanged.

Public records expose publication time, last successful check time, and pending
refresh state. Discord and web views distinguish checking from publication. A recent
check does not imply caught up when newer inputs remain pending. Range responses
also retain the latest attempt and its diagnostics independently of the active result.

## Validation and operational limits

Focused tests cover immutable snapshots, decimal/text round trips, out-of-order
concrete completion, edits during arithmetic, independent failures, unchanged
publication IDs, source-evidence changes, current-pin races, unset pins, queue token
replacement, and taxonomy invalidation. PostgreSQL tests exercise the real migration
chain and transaction boundaries. API schemas and generated clients include freshness
and resolved-version metadata.

No retention policy is introduced. Storage growth and range-invalidation fanout
should be measured in production. Queue health reports range work separately;
computation profiling should guide process isolation or further incremental work.
Rules revision selection still follows the deployed calculator's existing activation
behavior, so mixed calculator versions require coordinated rollout rather than
assuming they can safely reactivate one another's rules.
