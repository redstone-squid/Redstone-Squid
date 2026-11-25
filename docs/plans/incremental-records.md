# Incremental record computation

Recompute only the records a change can affect. A full recompute is still
available, but only as a backstop that finds and repairs drift.

## Today

`RecordComputationService` publishes one `ComputationRun` per build kind and
version scope. A run holds a `RecordResult` for every record of that kind, and
readers see whichever run is active. Every build change goes into
`record_recompute_queue` keyed by kind (`scope_key = kind.value`), so the
queue's `build_id` and `reasons` columns never narrow anything: editing one
door recomputes and republishes every door record.

Publishing a whole run per kind has one real benefit. When a build moves
between categories, for example from 3x3 to 4x4, it leaves one record and joins
the other in the same commit. This plan keeps that atomicity per change instead
of per kind.

## Terms

- A **record** (`record_titles`) is the stable public identity: kind,
  criterion, version scope and category key. Its definitions name it under
  each rules revision.
- A record's **members** are the builds in its category within its version
  scope. **Competitors** are the members that the criterion can compare. For
  example, `first` ignores builds without a completion date, and `smallest`
  ignores builds without a volume.
- A record's **affected set** for a change is every record whose members or
  competitors that change can alter.

## Affected sets

A record's result depends on its members, the rules revision and the current
version pin. For a change to build *b*, the affected set is the union of:

- **Old memberships.** Records *b* belonged to before the change. These are
  read from a membership index (record ↔ build for every member, not only
  holders), which is written whenever results are published.
- **New memberships.** Categories the new candidate maps to, from `_base_key`,
  `_restriction_choice_groups` and `_restriction_combinations`, plus any
  requested categories it satisfies.
- **Threshold changes.** `_observed_thresholds` builds threshold categories
  from every build in the kind. If *b* adds or removes the only (tag, value)
  pair of its kind, every category at that threshold appears or disappears,
  along with its other members.
- **Families.** A variable-size door projects onto every fixed size it can
  reach, so a family change affects all of those categories. The first door of
  a new fixed size likewise needs every family projected onto it.

Each criterion of an affected category is recomputed in each version scope the
old or new candidate belongs to.

Some changes legitimately affect the whole kind: a new rules revision, a
change of the current version pin, and a change to a tag's record operator or
restriction flag. These queue a full recompute of the kind.

## Publication

The current result becomes per record instead of per run. Each published
result stores the input generation it was computed from, and a result from an
older generation never replaces a newer one. A change publishes its whole
affected set in one transaction, so a build moving between categories stays
atomic without republishing the kind. Runs remain as a log of which records
each batch touched.

Queue items are keyed by build change and claimed one batch per kind at a
time. A batch computes the union of the affected sets of every pending item,
so overlapping work in one kind never races. The generation check is a second
safeguard.

Publishing only changed records also removes most search projection churn.
The November 22 findings in [Discord record search](discord-record-search.md)
counted 126,734 queued jobs for inactive results.

## Retirement

A record with no competitors has no current result. This covers both an empty
category and a category whose members the criterion can't compare. It replaces
`ResolutionStatus.NO_CANDIDATE`, which accounted for 33,841 of the 46,447 live
results on November 22 and carries no holders or gaps.

- When an affected record ends up with no competitors, the same transaction
  deletes its result and membership rows and closes the open holder period.
- The record row and its holder history stay. Former holders keep linking to
  it, and subscriptions stay attached. A later competitor revives the same
  record, with the same public ID.
- `records_get` shows a retired record with its history and a retired state.
  It doesn't return 404.
- Retirement and revival are per-record change events, so subscribers hear
  about both.
- Remove `ResolutionStatus` entirely. That covers the enum, the
  `title_results_status_check` constraint, the vocabulary entry and the
  generated web type. `PublicRecordQueryService` checks that a result has at
  least one holder.

Members that can't compete are still worth showing to staff. For example,
builds without a completion date could claim `first` if they were dated. If X7
needs that, report them as gaps on the category rather than as a status.

## Full recompute as a backstop

The existing kind-wide path runs on a new rules revision, a version pin change,
a schedule, and on demand. It computes every record and compares the result
with what is published, then publishes the differences. In steady state the
comparison should find none, so each difference is recorded as drift, with the
record and the inputs that differed. Drift means the affected-set calculation
missed a case.

The test that keeps this honest: for random sequences of build edits,
publishing incrementally must give the same results as a full recompute.

## Admin operations

This replaces item 46 in [the web API gaps](web-components/api-gaps.md).

- `POST /v1/records/{id}/recompute` queues an affected set of one record and
  returns 202. It replaces `RecordService.materialize_definition`, which
  rebuilds the whole kind inside the request.
- `GET /v1/records/{id}/inspection` computes the record fresh without
  publishing it and compares that with the published result: holders, gaps,
  and members gained or lost.
- `POST /v1/records/recomputes {kinds}` queues the full backstop recompute.
- `GET /v1/records/recompute-queue` lists pending and failed items with their
  last error, which `fail_recompute` already stores.
- `GET /v1/records/drift` lists differences found by the backstop.

## Open questions

- **Candidate loading.** Thresholds and families need the kind-wide view, so
  the first version can still load every candidate and only compute the
  affected categories. Load targeted rows only if profiling shows the read is
  the expensive part. Neither has been measured.
- **Door range publications** have their own queue and runs. The same approach
  applies, but they can follow once records work.
