# Observability for Redstone-Squid

> **Status.** Phases 0-3 implemented on 2026-08-05. Phase 4 is the next unit of work.
> The findings below are verified in-tree, not hypothetical: items 1, 2, and 5 are active
> defects today, independent of whether any of this ships. Amend this document in place as
> phases land, calling out where building it proved part of it wrong rather than silently
> rewriting.

## Context

Redstone-Squid runs three processes and can observe none of them. `app.py:16` forks the HTTP
API into a `multiprocessing.Process`, runs the Discord bot in the parent under uvloop, and the
schematic supervisor (`squid/schematics/infrastructure/worker.py`) spawns a third as a
respawnable subprocess. The only signal any of them emits is human-readable text into
`logs/discord.log` and stdout, rotated at 32 MB with five backups
(`squid/logging_config.py:25-28`).

That was proportionate while the bot was the only transport. It stops being proportionate for
three reasons: `docs/plans/rest-api.md` adds a public HTTP surface with three classes of
consumer; the schematic worker is a native-code process whose failure modes are OOM, rlimit
kills, and Rust panics — none of which produce a Python traceback; and voting, ingestion, and
record maintenance are background loops whose failures are currently invisible until someone
notices a stale build card.

The outcome: one trace per unit of work spanning all three processes, structured logs
correlated to those traces, and enough metric coverage to alert on the four things that
actually break — worker crashes, embedding/inference provider failures, vote-close lag, and
API error rate.

**Backend is deliberately undecided.** Everything below emits OTLP and nothing imports a
vendor SDK. The choice between a self-hosted Grafana stack and a SaaS backend is a Collector
configuration change, not an application change, and this document is written so that decision
can be deferred past Phase 2.

## What already exists and must be reused

- **`squid/logging_config.py::build_logging_config`** — a single dictConfig builder shared by
  both processes, with the formatters isolated in one block (`:184-195`). This is the entire
  leverage point for structured output; the call sites do not need to change.
- **The `QueueHandler`/`QueueListener` path** (`:126-131`, `:216-232`) — the bot already moves
  formatting and I/O off the event loop. JSON serialization inherits that for free. The API
  process deliberately does not use it (`use_queue` defaults to `False`), which is correct:
  uvicorn's access logger has its own formatter and the queue would reorder against it.
- **`squid/config.py::LogConfig` (`:351`) and `LoggingConfig` (`:363`)** — the established
  split between an env-facing shared block and a per-process resolved projection. An
  `ObservabilityConfig` follows the same shape rather than inventing a new pattern.
- **`squid/api/errors.py`** — RFC 9457 problem details with `X-Error-ID` (`:53-54`) and
  redaction already in place. The correlation identifier changes; the response shape does not.
- **`squid/bot/errors.py::SquidCommandTree.on_error` (`:288-298`)** — every application-command
  failure already funnels through one place. That is the span-error hook; nothing new is needed
  to catch bot errors.
- **`squid/schematics/infrastructure/wire.py::Frame`** — the header is an open
  `Mapping[str, Any]` serialized to JSON (`:50-64`), so a `traceparent` key costs nothing and
  old workers ignore it.
- **`tests/architecture/test_boundaries.py`** — the archrules that keep frameworks out of
  domain and application layers. Instrumentation must be placed so these keep passing; see
  Decision 3.

## Findings — verified in-tree

### 1. Two independent error-ID schemes, neither of which correlates anything

`squid/api/errors.py:79` and `squid/bot/errors.py:162` each define the same
`uuid4().hex[:12]` generator, separately. They share no code and no namespace. A build
submitted through the API and then voted on in Discord produces two unrelated IDs, and neither
survives past the single log line that mentions it — nothing else in the request carries it,
because there is no `ContextVar` and no middleware anywhere in `squid/` (verified: zero matches
for `ContextVar`, `add_middleware`, or `Middleware`).

The practical consequence: a user reports `error_id=a3f9c21b0e44`, you find exactly one line,
and you cannot see what happened immediately before it in the same request.

### 2. The worker stderr pump destroys the child's log structure

`squid/schematics/infrastructure/worker.py:130`:

```python
async for line in process.stderr:
    worker_logger.warning("[pid %s] %s", process.pid, line.decode("utf-8", "replace").rstrip())
```

Every record the child emits — `logger.debug("Could not lower worker priority.", exc_info=True)`
at `worker_main.py:70`, the `RLIMIT` warning at `:81`, and any faulthandler traceback — arrives
in the parent as a **WARNING**, one log record per physical line, with the child's level,
logger name, timestamp, and exception structure discarded. A multi-line Rust panic becomes N
unrelated WARNING records.

This is a defect on its own terms. It is also the single clearest argument for structured
output: if the child writes JSON lines, the parent parses and re-emits them faithfully.

### 3. Zero structured fields, but unusually good call-site discipline

Across 85 log call sites in `squid/`: **43 use lazy `%s` interpolation, 0 use f-strings, and 0
use `extra=`**. The absence of f-strings is what makes a formatter swap safe — the message
template is a stable event identity that an aggregator can group on, and no call site has
already baked its variables into the string. This discipline is worth preserving explicitly;
see Phase 0.

### 4. The config projection has a silent-drop trap

`ApplicationConfig.bot_process()` and `api_process()` (`squid/config.py:494-530`) project
settings by an explicit `include={...}` set. A new `observability` block added to
`RuntimeConfig` but not to **both** include sets is silently absent in that process — no
validation error, just an unconfigured exporter. `log` appears in both sets today (`:504`,
`:527`); `observability` must too.

### 5. Fork-safety is currently accidental, and must become deliberate

`app.py` calls `load_application_config()`, then `multiprocessing.Process(target=api_main).start()`,
and each process configures its own logging inside its own `main()`
(`squid/api/app.py:87`, `squid/bot/app.py:194`). Fork is the default start method on Linux for
Python 3.12, so anything holding a background thread at fork time is broken in the child.

Today nothing does, by luck. The OTel `BatchSpanProcessor` and `BatchLogRecordProcessor` both
own exporter threads, so **initializing the SDK before the fork silently produces a child that
buffers spans forever and exports none.** The invariant — no telemetry setup above the fork in
`app.py` — must be written down and tested, not left to chance.

### 6. Two deployment paths, only one of which can host a sidecar

`compose.yml` runs a single container with `./logs:/var/log/app` bind-mounted, but `justfile`'s
`deploy` recipe runs `nohup python app.py &` directly on the host. A design that assumes a
Collector sidecar container breaks the second path. The Collector must be reachable by URL and
**optional** — telemetry off by default, and a down Collector must never affect the bot.

## Decisions

### Decision 1 — stdlib `logging` stays the call-site API

Do not migrate the 85 call sites to structlog's kwargs API.

discord.py, SQLAlchemy, uvicorn, openai, aiohttp, and alembic all emit through stdlib
`logging` and always will. They are a large share of log volume, so a stdlib→structured bridge
is required no matter what. Once that bridge exists, routing first-party code through the same
path gives one pipeline instead of two, and keeps `extra=` as the way to attach fields.

Implementation is a formatter swap in `build_logging_config`'s `"formatters"` block
(`squid/logging_config.py:184`): JSON in production, the existing human format
(`DEFAULT_LOG_FORMAT`, `:34`) in development. Either `python-json-logger` or structlog's
`ProcessorFormatter` with a `foreign_pre_chain` works; prefer `python-json-logger` unless
structlog's processor pipeline earns its keep later, because it is a strictly smaller surface
and this design needs no processors that a `logging.Filter` cannot express.

### Decision 2 — logs reach the backend via stdout and a filelog receiver, not the OTLP log exporter

The OTel Python **logs** signal is materially less mature than traces; the SDK handler still
lives at `opentelemetry.sdk._logs` behind a private module name. More importantly, the OTLP log
exporter makes log delivery depend on the Collector being up, which is exactly backwards for
the signal you need most when infrastructure is failing.

So: application writes JSON lines to stdout and the rotating file; the Collector scrapes them
with a `filelog` receiver and attaches resource attributes. Logs keep working with the
Collector stopped, with the backend unchosen, and under `just deploy` with no container at all.
Revisit only if log-to-trace linking proves inadequate through trace IDs alone.

### Decision 3 — instrumentation lives in transports and infrastructure, never in domain or application

`tests/architecture/test_boundaries.py:30-55` forbids `sqlalchemy`, `discord`, `fastapi`, and
`nucleation` imports in `squid.*.domain*` and `squid.*.application*`. `opentelemetry*` is not
on those lists yet, and it should be added to them.

The reasoning is not purism. Application services are the layer both transports share; if they
create spans directly they acquire a global tracer provider as a hidden dependency, and every
unit test either initializes the SDK or tolerates a no-op provider that silently masks
instrumentation bugs. Spans belong at the edges — FastAPI middleware, the command tree, the
repository adapters, the worker supervisor — where the framework is already present.

Where an application service genuinely needs to mark a sub-operation, it takes the same shape
as every other outward dependency here: a port on `ApplicationServices`, injected, with a no-op
default. Do not reach for this until a phase actually needs it.

### Decision 4 — trace context replaces both error-ID schemes

Delete `_new_error_id()` (`squid/api/errors.py:79`) and the inline `uuid4().hex[:12]` in
`squid/bot/errors.py:162`. Surface the 128-bit trace ID instead, in the same places: the
`X-Error-ID` header, `ProblemDetail.error_id`, and the Discord
`"An unexpected error occurred. Reference: {error_id}"` string.

Same user experience, strictly more power — the reported ID now resolves to a full trace across
all three processes rather than one log line. When tracing is disabled or the span context is
invalid, fall back to a locally generated ID so the field is never empty.

### Decision 5 — OTel is an optional extra, off by default

Follow the `schematics` extra precedent in `pyproject.toml:38-45`: an `observability` extra
carrying `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, and the instrumentors.
Import them behind a feature check the way `squid/bootstrap.py:75` already degrades when the
schematic engine is absent. A deployment that sets no OTLP endpoint pays nothing and imports
nothing.

## Field and span naming

Use OTel semantic conventions where they exist (`http.*`, `db.*`, `server.*`) and a `squid.*`
namespace where they do not. Fixing this in Phase 0 matters more than getting it perfect —
renaming an attribute after dashboards depend on it is the expensive mistake.

| Attribute | Applies to | Notes |
|---|---|---|
| `squid.build.id` | submission, voting, schematic, ingestion | The single highest-value join key |
| `squid.command.name` | bot spans | Qualified name from the command tree |
| `squid.surface` | bot spans | Mirrors the existing `surface=` in `bot/errors.py:186` |
| `squid.guild.id`, `squid.channel.id` | bot spans | Never the user ID; see below |
| `squid.vote.session_id` | voting | Matches `snapshot.id` in existing log calls |
| `squid.schematic.operation` | worker | One of the seven `wire.Operation` literals |
| `squid.schematic.format` | worker | `SchematicFormat` value |
| `squid.error.code` | all | The existing 40-value `ErrorCode` enum, unchanged |

**Do not attach Discord user IDs as span or log attributes.** They are stable pseudonymous
identifiers for real people, and a tracing backend has a different retention and access model
than the application database. Where a user must be identifiable for debugging, attach the
build or vote-session ID and join through the database.

## Phases

Each phase is independently valuable and independently shippable. Phases 0-1 are worth doing
even if the rest is never built.

### Phase 0 — structured logs, no OTel (implemented)

No new runtime dependency beyond a JSON formatter. No backend required.

1. Add a `json` formatter to `build_logging_config`'s formatters block and select it by config,
   defaulting to the human format when `development_mode` is set.
2. Fix Finding 2: have `worker_main.py` configure a JSON stderr formatter, and have
   `worker.py::_pump_stderr` parse each line and re-emit it through `worker_logger.handle()`
   with the child's original level and logger name. Non-JSON lines (faulthandler output, Rust
   panics, anything written before logging is configured) fall back to the current
   `WARNING` behaviour — that path must keep working, because it is the one that matters during
   a native crash.
3. Add a lint or test asserting no f-strings in logging calls, locking in Finding 3's
   discipline before it erodes.
4. Add `extra={...}` at the ~15 call sites where a field is genuinely useful — the `build_id`
   sites in `bot/submission/submit.py:296-355` and `bot/submission/ingestion.py:56-92`, the vote-session
   sites in `voting/application/services.py:163-229`, and the schematic service sites in
   `schematics/application/services.py:261-357`.

**Exit criterion:** `logs/discord.log` is JSON, a worker DEBUG record arrives in the parent as
DEBUG, and `grep`-based debugging still works in development.

**Implementation notes (2026-08-05):** Production bot, API, uvicorn access, and worker records
use `python-json-logger`; bot development mode retains the human formatter. The API has no
development-mode setting, so it remains structured in every current deployment rather than
adding an unrelated configuration field. Worker exception tracebacks cross the stderr pipe as
formatted text because a traceback object cannot be reconstructed across processes, while the
level, logger, timestamp, source location, message, and structured fields remain first-class
`LogRecord` data. Non-JSON native crash output still takes the warning fallback. Architecture
tests now preserve lazy log templates and forbid future OTel imports in domain/application
layers. Stable build, vote-session, schematic-format, and operation fields were added without
adding Discord user identifiers.

### Phase 1 — configuration and the process-init contract (implemented)

1. `ObservabilityConfig` next to `LogConfig` in `squid/config.py`: OTLP endpoint, headers,
   sample ratio, service name, enable flag. Add `observability` to **both** include sets
   (Finding 4).
2. A `squid/observability.py` module with `configure_observability(config, *, service_name)`,
   idempotent, returning a shutdown handle the way `configure_bot_logging` returns its
   `QueueListener`.
3. Call it inside each process's own entry point — `api/app.py::main`, `bot/app.py::main`,
   `worker_main.py::main` — and **never** in `app.py` above the fork.
4. A test that asserts `app.py` performs no telemetry or logging setup before
   `multiprocessing.Process.start()` (Finding 5).
5. `compose.yml` gains an optional Collector service; the endpoint stays a URL so `just deploy`
   still works without it.

**Exit criterion:** telemetry configurable and fully inert when unset; both processes export a
service-name resource attribute; test suite green with the extra uninstalled.

**Implementation notes (2026-08-05):** `ObservabilityConfig.service_name` is the deployment
base name; process entry points append `-api`, `-bot`, or `-worker`, resolving the ambiguity
between the configured name and `configure_observability(..., service_name=...)`. The endpoint
is a generic OTLP/HTTP base URL and the application appends `/v1/traces` (without duplicating an
already-present signal path), matching the HTTP exporter's exact-endpoint behavior. Sampling is
parent-based so Phase 4's propagated sampling decisions will be honored.

The worker uses a narrow settings loader in `config.py` rather than receiving observability
headers through argv. Workers inherit the deployment environment already, this keeps secrets
out of process listings, and it avoids putting telemetry configuration into the application
service graph. Programmatically constructed parent config does not override worker telemetry;
worker settings remain an environment-owned process boundary, like a standalone worker launch.
Guardrails are applied before the worker imports the SDK or starts its batch-export thread.

The optional Compose profile pins the contrib Collector and uses its debug exporter as a
backend-neutral validation sink. It accepts OTLP/HTTP traces and tails JSON log files from the
shared log volume; replacing the exporter is the remaining backend choice. The bare-metal path
can use the same published port or any external Collector URL. No `depends_on` was added, so the
application remains independent of Collector availability.

### Phase 2 — traces in the API process (implemented)

Mostly free. `opentelemetry-instrumentation-fastapi` in `create_api_app`, plus the SQLAlchemy
and aiohttp instrumentors. Then Decision 4: replace both error-ID generators with the trace ID.

The required `docs/plans/rest-api.md` Phase 0 routes, including `/verify`, are now present, so
Phase 2 no longer has that sequencing blocker.

**Exit criterion:** a `/verify` request produces one trace with an HTTP span and its SQL child
spans; a forced 500 returns an `X-Error-ID` that resolves to that trace.

**Implementation notes (2026-08-05):** The process-owned API app is instrumented after its
tracer provider is configured; SQLAlchemy and aiohttp client instrumentation is installed once
with that provider before the runtime creates engines or clients. Disabled deployments still
return before importing any instrumentor. An optional-extra integration test composes a real
FastAPI server span with a SQLAlchemy child span and proves that the surfaced correlation ID is
the same 32-character trace ID.

The shared `correlation_id()` helper now owns both transport fallbacks. API problem details and
`X-Error-ID`, as well as Discord's unexpected-error reference, use the active trace ID when a
valid span exists and retain the prior 12-character local identifier when tracing is disabled
or no span is active.

### Phase 3 — traces in the bot process (implemented)

The real work; no auto-instrumentation exists for discord.py.

1. A span around application-command invocation, started in a `CommandTree` wrapper and ended
   in `SquidCommandTree.on_error` (`bot/errors.py:288`) or on success.
2. Spans for the background loops — `bot/submission/records.py:242`, `bot/voting/vote.py:316-318`,
   `bot/starboard/debounce.py:41` — each currently a bare `logger.exception` with no duration or
   outcome recorded.
3. A trace-ID-injecting `logging.Filter`, so Phase 0's JSON records carry `trace_id`/`span_id`.

Note that discord.py's gateway is long-lived and event-driven: do **not** create a root span
per gateway event, or every heartbeat becomes a trace. Root spans start at user-initiated
work — a command, an interaction, a scheduled loop iteration.

**Exit criterion:** a `/submit` invocation produces one trace covering command handling,
inference, and database writes.

**Implementation notes (2026-08-05):** `SquidCommandTree` wraps discord.py's dispatch boundary
while deliberately excluding autocomplete traffic. The span name and attributes are derived
from Discord's nested command payload before dispatch; `on_error` records the actual command
exception while the root span remains current. Command completion marks failures that
discord.py handles internally, and uncaught dispatch failures are recorded by the shared span
context manager. No user ID is attached.

Record/search maintenance, due-vote closure, per-session Discord refreshes, and debounced
starboard work now have bounded spans with error outcomes. A trace-context filter is installed
only when observability is enabled. It runs on the queue handler while bot context is still
active, then preserves those captured IDs when the listener thread formats the record; the
same preservation rule is ready for worker child records in Phase 4. Optional-extra integration
coverage proves command spans and their structured log records share trace/span identifiers.

### Phase 4 — the worker, and cross-process propagation

1. Inject `traceparent` into `wire.Frame.header` in the supervisor; extract it in
   `worker_main.py::handle` and start the worker span as a child of the caller's span. The
   header is an open dict, so old workers ignore the key and new workers tolerate its absence —
   no version negotiation needed.
2. Emit the metrics that justify the whole exercise: worker crash and respawn counts by exit
   code (the `worker.py:150` warning becomes a counter), operation duration by
   `squid.schematic.operation`, and rlimit-kill counts.
3. Correlate Phase 0's re-emitted child log records into the worker span.

**Exit criterion:** one trace shows a Discord command, the render request, and the native
operation inside the worker; a worker OOM is visible as a metric, not just a log line.

## Non-goals and scope honesty

- **This does not add profiling or continuous profiling.** Out of scope.
- **Metrics are deliberately thin** — four things worth alerting on, added in Phase 4. Broad
  metric coverage before anyone reads a dashboard is wasted work.
- **No log-volume estimate is offered here, and one is needed before choosing a SaaS backend.**
  Per-seat-free, per-GB-priced backends make DEBUG-level schematic logging expensive in a way
  self-hosted Loki does not. Measure after Phase 0, when logs are machine-countable.
- **Sampling is set to always-on through Phase 3.** This bot's traffic does not warrant tail
  sampling, and adding it early hides exactly the rare failures being chased.
- **`docs/plans/rest-api.md` Finding 5** (per-process `CursorCodec` seed) is a related
  symptom of the same two-process split but is not fixed here; it needs a shared secret, not
  telemetry.

## Open questions

1. **Does the Collector run as a container under `just deploy`?** If the bare-metal path is the
   real production path, the Collector needs a systemd unit and this document's Phase 1 should
   say so.
2. **Retention for trace data containing build content.** Schematic filenames and build titles
   are user-submitted and will appear in span attributes. Confirm the eventual backend's
   retention is acceptable, or add a redaction processor in the Collector.
3. **Whether Phase 3 should wait for `rest-api.md`.** Both touch `squid/bot/errors.py` and
   `squid/api/errors.py`; sequencing them concurrently guarantees conflicts in Decision 4's
   changes.
