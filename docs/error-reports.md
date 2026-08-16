# Stored error reports

When something unexpected fails, the user is shown a reference. That reference resolves to a stored
report holding the exception, its traceback, the redacted diagnostic context, and the log lines the
process emitted just before the failure. Anyone holding `diagnostics.error.read` can look it up from
Discord, the HTTP API, or the CLI.

Before this existed the reference was only useful for grepping `logs/*.log` on the host, which the
moderator reading a complaint on Discord cannot do.

## The two widths of a reference

One correlation ID, shown at two lengths:

| Width | Where it appears | Example |
| --- | --- | --- |
| 12 characters | The Discord error card, because the user has to retype it | `0a1b2c3d4e5f` |
| Full | Log lines, the `Request-Id` response header, the stored row | `0a1b2c3d4e5f60718293a4b5c6d7e8f9` |

The short form is `correlation_reference()`: the first 12 characters, which is already the width of
the fallback used when OpenTelemetry is not installed. A reference quoted from a traced deployment
and one quoted from an untraced deployment therefore look alike.

**Lookup accepts either width**, by exact match on an indexed column. Neither column is unique. Twelve
hex characters is 48 bits: at 10 000 unexpected errors per retention window the all-pairs-distinct
collision bound is around 2 × 10⁻⁷, which is small but not zero. A unique constraint would turn that
into a refusal to record the second failure, so instead a lookup reports how many reports share the
reference and returns the newest.

## What is captured, and from where

Two mechanisms, because one is not enough.

**Transports capture their own failures**, with the command or route that produced them:

| Surface | Captured in | `surface` value |
| --- | --- | --- |
| Discord commands, views, modals, progress messages | `squid/bot/errors.py` | `application_command`, `command`, `modal`, `view:…`, `running_message` |
| HTTP API | `squid/api/errors.py` | `http` |
| Background jobs that crash | `BackgroundTaskSupervisor` in `squid/runtime.py` | `background_job` |

**The store also follows the logs**, and this is the only thing that reaches the worker. Its queue
consumers are built to absorb a failure: catch it, dead-letter the job, `logger.exception`, carry
on. Nothing ever reaches the supervisor, so before log-driven capture the worker's failures were
precisely the ones visible in `docker logs` and impossible to look up. `ErrorReportLogHandler`
stores anything logged at ERROR or above with an exception attached, under `surface = log` with the
logger name as `origin` — which makes the store mirror the container output by construction, and
covers code written later without it having to know this exists.

The two do not double up: a handler that captured a failure itself passes `extra=captured()` on the
log line that follows, and the log handler skips it.

`SQUID_DIAGNOSTICS_CAPTURE_LOGGED_ERRORS=false` turns the log-driven half off.

### Work lost

Following the logs means most reports are failures something recovered from. A dead-lettered job is
not — nothing will retry it, and a build's search document or a schematic's render simply never
appears. Those sites add `**work_lost()` to the `extra` of the line they already log, which is the
whole of what they do; there is no service to inject and no dependency pointing at a context they
should not know about.

`work_lost` is a column, a response field, a `--work-lost` CLI flag, and a `/error recent work_lost:
True` option, so the handful that cost something cannot be buried by the hundred that did not.

### Delivery

Capture is best effort by construction. `ErrorReportService.record` never raises, and each call site
is guarded again on top of that: every one of them is a handler that has already failed once and
still owes someone a response. A lost report is a lost diagnostic; a report that raises is a command
that silently does nothing.

Transport capture runs *before* the failure is logged, because the log buffer is drained at capture
time and logging first would fill the tail with an echo of the traceback the report already carries.

The log handler cannot write to the database from `emit`: that is synchronous and may run on any
thread — the bot logs through a `QueueListener`, and anything inside `to_thread` logs from a worker
thread. So `emit` appends to a bounded deque and nudges the loop, and a supervised task
(`error-log-capture`) does the writing. Overflow drops the oldest and is counted and logged, never
silently absorbed. The log handler reads the correlation tail with `snapshot` rather than `drain`,
since one run can log several failures and the first must not take the context from the rest.

## Correlation and the log tail

`CorrelatedLogBuffer` keeps the last N formatted records per correlation ID and is installed as a
named logging handler. It is a sibling of the bot's queue handler rather than one of its targets:
queued records reach their handlers on the listener thread, so a tail drained moments after the lines
that explain a failure would silently lose them.

For the tail to be non-empty, the correlation ID has to exist before the failure. The API binds one
in `RequestContextMiddleware`; the bot binds one in `SquidCommandTree._call` and `Bot.invoke`;
background jobs bind one per run. The scope is re-entrant, so a hybrid command passing through both
Discord paths keeps a single ID.

Component and modal callbacks are the exception. Discord dispatches them outside both bot paths, so
they still mint an ID at error time. Those reports are stored and resolvable, just without a tail.

## Retention

`expires_at` is stamped on write and the worker's `error-report-retention` job sweeps past it, the
same shape as `idempotency_requests`. An expired reference and an unknown one are deliberately
indistinguishable: telling a caller that a reference *used* to exist reveals that an error happened.

Storage is PostgreSQL rather than the Redis the API already uses for rate limiting. That instance runs
with `--save "" --appendonly no --maxmemory 128mb --maxmemory-policy noeviction`, so error blobs there
would eventually make the rate limiter start erroring, and the bot process has no Redis client at all.

## Configuration

| Variable | Default | Meaning |
| --- | ---: | --- |
| `SQUID_DIAGNOSTICS_RETENTION_HOURS` | 168 | How long a report stays queryable |
| `SQUID_DIAGNOSTICS_LOG_TAIL_RECORDS` | 50 | Records buffered per correlation ID; `0` uninstalls the buffer |
| `SQUID_DIAGNOSTICS_MAX_TRACEBACK_CHARS` | 20000 | Cap on stored traceback text |
| `SQUID_DIAGNOSTICS_CAPTURE_LOGGED_ERRORS` | true | Store logged exceptions, not only what a transport captures |
| `SQUID_DIAGNOSTICS_LOG_CAPTURE_QUEUE` | 256 | Failures held between the log call and the write before the oldest drop |

An over-long traceback is truncated **from the front**. A runaway recursion makes the head thousands
of identical frames, and the frames nearest the failure are the ones worth reading.

## Reading a report

```
/error 0a1b2c3d4e5f          # Discord, always ephemeral, full report attached
/error recent                # the last ten, for looking around without a reference
/error recent work_lost:True # only the ones that abandoned work

GET /v1/diagnostics/errors/{reference}
GET /v1/diagnostics/errors?work_lost=true

squid errors show 0a1b2c3d4e5f
squid errors list --work-lost
```

Discord answers ephemerally and the HTTP listing omits the message and traceback entirely: both carry
internal paths and the unredacted exception text that every other surface withholds from the user who
triggered it. The Discord card previews the tail of the traceback and attaches the whole report,
because a real traceback does not fit in a Components V2 card's 4000-character budget.
