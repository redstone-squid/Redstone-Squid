# Incremental record computation

Status: implemented. A build change recomputes and publishes only the records
it can affect. A full recompute still runs after rules, version pin and tag
changes, and as a scheduled backstop that reports drift. A property test
checks that publishing incrementally gives the same result as a full
recompute, for random sequences of edits.

## Terms

- A **record** (`record_titles`) is the stable public identity: kind,
  criterion, version scope and category key. Its definitions name it under
  each rules revision.
- A record's **members** are the builds in its category within its version
  scope. **Competitors** are the members that the criterion can compare. For
  example, `first` ignores builds without a completion date, and `smallest`
  ignores builds without a volume.
- A record is **live** while it has competitors, and **retired** otherwise.

## Affected sets

`ScopeCategories.affected` works out, for each version scope, which category
keys a batch of changed builds can affect:

- **Old memberships.** Live categories the build was in, read from
  `record_members`. That table indexes every member of every live record and
  has no foreign key to builds, so a deleted build's rows survive until a
  worker processes the deletion.
- **New memberships.** Categories the build is in now, including requested
  categories.
- **Thresholds and sizes.** Thresholds come from values that builds actually
  have, and families project onto the sizes that fixed doors make concrete.
  When the set of observed thresholds or concrete sizes changes, every
  category at that threshold or size is affected, at any size and with any
  other restrictions. "Before" is the unchanged builds plus every threshold
  and size in a live key. *b*'s own old memberships are not enough: a threshold
  that only *b* had can live on in a category at another size that *b* was
  never in.

A batch still loads every candidate of the kind and generates every category,
but resolves and publishes only the affected ones. Only all-time and the
pinned version are maintained. A full recompute retires records in any other
version, so a pin change retires the old pin's records.

## Publication

`title_results` holds one current result per record, updated in place, so a
result ID is stable while its record lives. A batch publishes its whole
affected set in one transaction under a per-kind advisory lock, so a build
moving between categories is still atomic. Each record row stores the input
generation it was last published or retired from, and an older batch leaves
it alone. Unchanged results, compared by a per-record fingerprint, only record
the check. `record_computation_runs` is a log of published batches. Results
and drift rows name the run that wrote them.

A record with no competitors has no result. Retirement deletes the result and
membership rows in the same transaction, closes the open holder period, and
sets `retired_at`. The record row and its holder history remain, so former
holders and subscriptions keep linking to it, and a later competitor revives
it with the same public ID. `records_get` shows a retired record with its
history. `ResolutionStatus` is gone. An empty `RecordResolution` means no
competitor, which also covers live lookups and range regions.

Each batch publishes one `record_run.published` event listing records that
appeared, revived, retired, or changed holders. Notifications treat revivals as
gains, and send `record_retired` to followers of retired records.

## Queue

Work items are keyed by target: `door` for a whole kind, `door:backstop`,
`door:build:12` and `door:record:<uuid>`. A worker claims a batch of one kind
at a time, and no other worker claims that kind while the claim lives. A full
item in the batch recomputes every category. A backstop also reads every build
with pending work, and reports as drift each changed record outside those
builds' affected sets. Rules, pin and tag changes queue plain full items,
because their differences are expected.

## Admin operations

- `POST /v1/records/{id}/recompute` queues every criterion of the record's
  category and returns 202. It replaces `RecordService.materialize_definition`.
- `GET /v1/records/{id}/inspection` computes the record fresh without
  publishing it, and compares holders, gaps and members with what is stored.
- `POST /v1/records/recomputes {kinds}` queues the backstop. The worker also
  queues it daily.
- `GET /v1/records/recompute-queue` lists pending and failed items with their
  last error.
- `GET /v1/records/drift` lists differences the backstop found.

The bot's staff rebuild still recomputes synchronously, without reporting drift.

## Open questions

- **Candidate loading.** Every batch reads the whole kind. Load targeted rows
  only if profiling shows the read is the expensive part.
- **Door range publications** have their own queue and runs. The same approach
  applies, but has not been done.
