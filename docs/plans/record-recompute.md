# Record recomputation

Status: implementation in progress. Concrete computation now accepts a versioned,
JSON-replayable snapshot with an injected evaluation time and runs in a bounded
worker thread. Concrete publication compares semantic fingerprints, preserves
unchanged runs, and stores last-checked time. The migration and record integration
tests cover that behavior.

The worker cutover is not implemented yet: snapshot persistence, explicit settings
and scope identities, input generations, claims and heartbeats, short publication
transactions, independent range scheduling, and public freshness metadata remain.
Until that cutover, computation still runs within the existing rebuild transaction
and concrete and range refresh still share the existing queue lifecycle.

Records are derived from confirmed builds, taxonomy, comparison rules, and requested
categories. Concrete door records already compare fixed builds with expandable
configurations. Range records currently compute smallest-record outcomes across
widths at a fixed height and depth. They should agree at a particular opening when
computed from identical inputs, but they need not publish together.

## Agreed behavior

- One shared current Minecraft version applies to all build kinds.
- Version scopes support all versions, a fixed version, and following current.
- Publish a consistent snapshot even if builds changed during computation, then
  catch up. Never replace a publication with an older input snapshot.
- Keep the existing publication when its meaningful contents are unchanged;
  separately record when those contents were last checked.
- Concrete records and registered ranges refresh and retry independently.
- Keep superseded runs. Notifications read previous runs to calculate holder gains.

## What changes from the earlier proposal

Current candidate loading already uses repeatable-read snapshots. Updating a dirty
state row in that same transaction after another writer changes it can cause a
serialization failure. A transaction advisory lock also does not refresh a snapshot
established before waiting for the lock. The earlier proposal's transaction and
ordering argument therefore does not apply to the current implementation.

Use three phases: capture and save immutable inputs, compute without a database
transaction, then publish in a short transaction. Keep expiring worker claims for
crash recovery. Permanent generation counters represent changed inputs; claim
ownership represents permission to finish an attempt. Neither is the publication ID.

This deliberately replaces the earlier proposal to remove leases entirely. Avoiding
both leases and long-lived database connections would require another durable
ownership mechanism with essentially the same responsibilities.

## State and identities

Introduce a singleton record-settings row with the nullable current-version ID and
an increasing settings revision. Null means no current version has been configured;
current-scope work waits until one exists. Setting the pin is an explicit staff
operation, not a side effect of rebuilding an arbitrary fixed version.

Keep permanent input-state rows for doors and extenders. Each contains an increasing
source generation and its change time. Build changes increment the appropriate
row in the same transaction as the source edit. Audit taxonomy edits, tag thresholds,
version membership, deletions, and category changes as producers too. Moving a build
between kinds invalidates both kinds. Rules activation is explicit deployment or
maintenance work, never a side effect of an ordinary candidate read. Workers must
refuse revisions their deployed calculator cannot implement; an older worker must
not reactivate its own revision during a rolling deployment.

Define durable work targets:

- A concrete catalogue for one kind and one version scope.
- One registered range query, including its version scope.

All-time and current catalogues are permanent targets. Explicit fixed-version
materializations create persistent targets so future build edits refresh them too.
Requested categories remain durable inputs to the relevant catalogues; adding one
invalidates every target whose category set changes.

Use an explicit scope discriminator: `all_time`, `fixed`, or `current`. Only fixed
scope takes a version ID. At capture, current scope resolves to a concrete version;
store both the requested scope and resolved version in attempt provenance. A query's
identity includes the scope, so fixed-version and current queries never alias.

Each target stores its local request generation, last successful input stamp,
last-checked time, retry state, and expiring claim token. An input stamp contains:

- Source generation for its build kind.
- Target request generation, incremented by explicit refresh or relevant category changes.
- Active rules revision.
- Settings revision for current scopes only.

A target needs work when its successful stamp differs from its desired stamp.
Workers find this by joining targets to input state and settings. Build triggers
update one kind row, not every registered range. This preserves inexpensive source
writes as registrations grow. Initial targets have no successful stamp and need work.

## Capture, computation, and publication

### 1. Claim and capture

Claim one due target in a short transaction using a random ownership token and an
expiry. Concurrent workers skip claimed targets. A claim records an attempt ID but
does not decide its input stamp: capture does that.

In a fresh repeatable-read transaction, read the rules, settings, input generations,
requested categories, and all candidate facts. Serialize a versioned immutable input
document and persist it with the captured stamp before computation. Concrete inputs
need the same explicit serialization contract that ranges already have. Copy values
into data objects; never send ORM entities or sessions into computation.

Check claim ownership when attaching the snapshot. A lost claim aborts the attempt.
Retry serialization failures from the beginning of capture, with bounded retries;
otherwise release for normal backoff. Snapshot capture must not activate rules or
mutate source generations.

### 2. Compute

Compute exclusively from the saved document, without holding a database connection.
Use the existing range analyzer's bounded execution for ranges. Offload concrete
computation from the event loop using a bounded thread worker initially; measure
CPU time before choosing process isolation. Cancellation must not release capacity
while abandoned arithmetic continues running. Use structured task lifetimes and
explicit limits on candidates, categories, runtime, and concurrent computations.

Renew claims with a supervised heartbeat. Failure to renew prevents publication.
A lease is an efficiency and recovery mechanism; the ownership check at publication
is the correctness boundary. Expiry can cause duplicate computation but cannot let
an obsolete owner publish.

### 3. Publish and acknowledge

Use a short read-committed transaction. Lock in this order everywhere:

1. Settings/rules coordination row, shared for publication and exclusive for changes.
2. Target state row, exclusively.
3. Attempt and publication rows belonging to that target.

Settings/rules mutations never acquire these locks in reverse order. Source changes
need only their input-state row; publishers do not lock that row during arithmetic.

Verify the attempt still owns an unexpired claim. Verify its rules revision and,
for current scopes, settings revision still match. A changed pin or rules revision
supersedes the attempt: retain its diagnostics, leave work pending, and publish
nothing under the obsolete meaning of current or obsolete rules.

Ordinary source edits and refresh requests do not supersede a valid attempt. Publish
its coherent snapshot and acknowledge only its captured stamp. The newer desired
stamp still differs, so another computation follows. A newer successful stamp must
never be replaced by an older one; check this independently of ownership.

Compare the semantic fingerprint with the active publication. If different, activate
a replacement and emit its event atomically. Otherwise retain the existing run and
emit no holder-change event. In both cases update the successful stamp and
last-checked time, clear owned failure state, and release the claim in this transaction.

No acknowledgement transaction follows publication. A crash commits both or neither.

## Independent range work

Remove `refresh_registered` from concrete rebuild completion. New range registration
and explicit refresh dirty that range target alone. Door input changes make both
concrete and range targets eligible through their shared source generation.

Each range has independent backoff and claim ownership. An unsupported or
limit-exceeded analysis is an unsuccessful refresh even when the analyzer returns a
structured result rather than raising. Keep its diagnostics and previous exact
publication; do not mark the desired stamp successfully processed. Retry with capped
backoff, and expose the reason for staff action. One failing range does not delay other
targets. Schedule concrete and range targets fairly with separate capacity limits.

Range attempt generations currently count reservations and reject an attempt as soon
as a newer reservation exists. Replace that policy with the common ownership and
input-stamp protocol. Keep attempt IDs for inspection, not as source freshness.

## Unchanged results and freshness

Define a versioned canonical fingerprint of publication contents. Include rules,
resolved version, category identities and labels, outcomes, gaps, holder metrics,
history and completeness, evaluated configurations, and substantive source evidence.
For ranges include regions, configurations, and diagnostics. Sort semantically
unordered collections; preserve order where it controls comparison priority.

Exclude generated database IDs, attempt IDs, capture/evaluation timestamps, claim
metadata, and request generations. Inject the evaluation time into computations
rather than reading the clock inside arithmetic. Timestamp-only differences must
not force publication. New rules or a changed resolved version must force publication.

Expose publication time, source capture time, last successful check time, pending
work, and latest failure separately. Last checked does not mean caught up if the
successful stamp still differs from desired inputs. Public reads load publication
and freshness metadata consistently. Preserve the existing publication's original
evidence timestamps when skipping an unchanged result.

Failure updates require the same ownership token. An old worker cannot overwrite a
newer's success or retry state. A source change makes the new input stamp immediately
eligible without erasing consecutive-failure history. Failure backoff belongs to its
captured stamp, so recording an old failure cannot postpone newer inputs. Successful
processing resets failures. Report pending age, consecutive failures, capture/compute/
publish durations, expired claims, unchanged publications, and serialization retries.

## Migration and rollout

1. Add explicit settings and scope identity. Seed the shared pin using the existing
   newest-active-version rule once; record the chosen version. Preserve existing
   concrete definitions and public title identities. Existing versioned range queries
   remain fixed; null-version queries remain all-time. New current queries are opt-in.
2. Add target state, immutable concrete snapshots, input stamps, and fingerprint
   versions. Seed targets dirty, including outstanding queue work and registered
   ranges. Backfill current versus fixed catalogue mappings without deleting runs.
3. Route every producer to input state or target requests. Coordinate worker cutover:
   old workers must stop before generation-based consumers become authoritative.
   Migrations and application rollout must not leave a gap in invalidation recording.
4. Implement the common capture/publish protocol, then independent concrete and range
   consumers. Change staff rebuild/materialization to request the same target work;
   return pending status and publication links instead of doing unbounded arithmetic
   in the interaction. Explicit refresh still recomputes even with unchanged sources.
5. Enable semantic deduplication after comparison tests pass. Keep the old queue until
   producer and consumer audits show it is unused, then remove it separately.

Do not delete superseded runs or snapshots in this project. Retention requires a
separate design covering notification processing, event replay, and public links.

## Required verification

Use real PostgreSQL transactions for concurrency tests, not only repository fakes.

- Edit inputs during capture: the saved document contains one consistent snapshot.
- Edit inputs during arithmetic: the old snapshot publishes and newer work remains due.
- Expire/reclaim a lease: the old owner cannot publish, acknowledge, or record failure.
- Crash before and after publication commit: publication and successful stamp agree.
- Change current pin or rules during arithmetic: reject obsolete publication; fixed
  scopes ignore pin changes and current scopes eventually use the new version.
- Race a failed attempt with newer inputs or success: neither gets delayed or overwritten.
- Run door and extender work concurrently; one failure does not roll back the other.
- Fail one range, including structured unsupported results: concrete work and other
  ranges succeed while the previous range publication stays readable.
- Repeat equal computation: preserve publication IDs and events, advance last checked.
- Change evidence, diagnostics, rules, history, or metrics without changing holder IDs:
  publish when meaningful contents change. Timestamp-only changes do not publish.
- Compare concrete smallest outcomes with corresponding range points from identical
  input fixtures, including fixed builds, families, missing dates, and ties.
- Audit every invalidation producer and exercise migration with pending old work.
- Exercise bounded cancellation, heartbeat loss, serialization retries, and fair scheduling.

Production measurements guide capacity and performance tuning, not correctness:
record input counts, computation duration distributions, unchanged-result rates,
and pending age before and after rollout.
