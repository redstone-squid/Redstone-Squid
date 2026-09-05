# PR #183 Review: Runtime and Observability

## Findings

Sixteen threads land here: the fifteen runtime, configuration, error-presentation and telemetry
comments `Glinte` left on [PR #183](https://github.com/redstone-squid/Redstone-Squid/pull/183) at or
before the `5edfd3e` cutoff, plus the bootstrap thread that
[13-test-tooling-dispositions.md](13-test-tooling-dispositions.md) deferred here. The cluster's
other test threads were dispositioned in plan 13 and are not reopened.

Several concerns are already overtaken. `24980f0` split API, bot, and worker into three process
entry points, each owning its own `main()`; `compose.yml` runs them as three containers, and the
schematic worker is `asyncio.create_subprocess_exec` (`squid/schematics/infrastructure/worker.py:146`),
not a fork. `d1e06e6` moved the FastAPI import in `squid/observability.py` behind `TYPE_CHECKING`.
`75a8dc3` gave the OTLP signal URLs one `_signal_endpoint` helper. `f55aaab` and `fb8c136` removed
Discord account identifiers from error and command logs. `1da4ceb` gave the welcome relay its own
`CommunityConfig.welcome_relay_channel_id` (`squid/config.py:526`).

What remains is real:

- **The collector's log input is undecided in the deployment, not in the code.** Production emits
  the same JSON records twice - `console` to stdout and a `RotatingFileHandler` at 32 MB × 5
  (`squid/logging_config.py:120-141`) - and `deploy/otel-collector.yaml:6-18` scrapes only the
  files, which `compose.yml` bind-mounts read-only into the collector. `docs/plans/observability.md`
  Decision 2 chose file scraping deliberately, so logs survive a stopped collector, an unchosen
  backend, and a `just deploy` with no container at all. That reasoning is sound and is nowhere the
  operator can see it; the duplicate stream, its rotation policy, and the volume's owner are
  undocumented. That plan has since moved to `docs/plans/completed/observability.md` (`f9eca04a`),
  which is the path any write-up here should cite, not the old one.
  `8713c9df` (`docker: fix log files permission error`) swapped the bind-mounted `./logs` directory
  for a named `squid-logs` volume, read-write into the app containers and read-only into the
  collector (`compose.yml:17,172,177`) - that incidentally answers the "volume's owner" question
  this subplan was meant to write down, but it is still a `compose.yml` diff with no comment, and
  the duplicate-stream rationale and rotation policy remain undocumented anywhere an operator would
  look.

- **The full 128-bit trace ID leaks into a user-facing string.** `build_error_presentation`
  (`squid/bot/errors.py:190`) puts `correlation_id()` straight into
  `"An unexpected error occurred. Reference: {error_id}"`, so a Discord user is asked to retype 32
  hex characters. The same value is the log field and the `X-Error-ID` header, which is exactly
  where the full width is worth having.

- **`squid/observability.py` repeats one optional-dependency dance seven times.** `trace_span`,
  `inject_trace_context`, `extracted_trace_span`, `record_current_exception`,
  `_current_trace_context`, `instrument_api_app` and `configure_observability` each re-import
  OpenTelemetry inside the function and each re-implement the same `ModuleNotFoundError` filter.
  The metric helpers do not: they gate on `_meter is None` and skip the pid check entirely, and
  `_meter` is never cleared by `ObservabilityHandle.shutdown`, so metrics recorded after shutdown
  go to a stopped provider. `_meter: Any` and `_counters: dict[str, Any]` type-check as nothing.

- **`inject_trace_context` splices W3C headers into a wire protocol header.** `worker.py:113-117`
  builds `{"id", "op", "params", "job_id"}` and then lets the function `carrier.update(headers)`
  arbitrary keys into it; `extracted_trace_span` compensates on the far side by filtering the whole
  header for `str` values (`squid/observability.py:198`). The frame format documented in
  `wire.py:1-14` says nothing about this.

- **The command tree reads Discord's raw payload to name a span.** `_interaction_command_name`
  (`squid/bot/errors.py:348`) walks `interaction.data`, matching option types `1` and `2` by
  integer literal, and feeds the result into a span *name*. Names taken from the payload rather
  than from the registered tree are unbounded cardinality. Meanwhile `interaction.command` is a
  public `cached_slot_property` that resolves the same command through the tree and fills the same
  `_cs_command` slot `_call` sets moments later. The related suggestion, contrib PR
  [#4842](https://github.com/open-telemetry/opentelemetry-python-contrib/pull/4842), is an open
  draft that wraps `Bot.invoke` and `CommandTree._call` - the same private target - and records
  `discord.user.id`, which our privacy filter exists to keep out.

- **`squid.surface` and `surface=` are free-text and mean two things at once.** Values in use are
  `command`, `application_command`, `running_message`, `modal`, `background_loop`, and
  `view:{item class}` - the last packing a component name into what is otherwise an enum.

- **`squid/api/errors.py` avoids FastAPI for no enforced reason.** The `ExceptionRegistrar`
  Protocol, the Starlette-only imports, and the local `correlation_id` wrapper all exist to keep
  FastAPI out of the module. Nothing outside `squid/api/` imports it (`rate_limit.py`,
  `request_body.py`, `app.py`, and the eighteen `v1/` route modules), the import-surface case
  guards only `sqlalchemy` and `discord` (`tests/architecture/test_import_surfaces.py:19`), and the
  boundary test forbids `fastapi*` only in domain and application layers
  (`tests/architecture/test_boundaries.py:12-51`). `responses()` is the one addition worth keeping.

- **The welcome relay sleeps 30 seconds inside a listener.** `squid/bot/welcome_relay.py:44` waits
  for `on_member_join` to land and for the member cache to fill, in a task discord.py owns and the
  `BackgroundTaskSupervisor` does not, which the concurrency rules in `CLAUDE.md` forbid. The delay
  has already been tuned once (`welcome: extend relay delay to 30 seconds`), which is what a race
  looks like when it is being papered over. `service.resolve` consumes the pending member before
  the guild and member-cache checks that follow, so a cache miss loses the record permanently - and
  the cache lookup exists only to build `member.mention`, which is `<@{id}>` and needs no cache.

- **The bootstrap thread is answered by the process split, but its test is not.**
  `tests/unit/bot/test_app_main.py` no longer describes a fork; it mocks five collaborators to
  assert that `main` shuts observability down and stops the log queue listener. The reviewer's
  suggested replacement - separate processes with a queue between them - is what `24980f0` plus the
  durable `squid/sync` queue now do. The assertion is worth keeping and worth extending: nothing
  covers the failure path, and the API and worker entry points have the same contract with no
  equivalent coverage.

---

## Subplans

1. **Decide and document the log transport** — *not started*. `8713c9df` moved the log volume from
   a bind mount to a named volume for an unrelated permissions fix, which happens to settle the
   ownership question below, but no documentation landed and the source plan is now at
   `docs/plans/completed/observability.md`.
   - Keep `filelog` as the collector's authoritative input and record why in
     `deploy/otel-collector.yaml` and the deployment docs: log delivery must not depend on the
     collector being up, on the observability extra being installed, or on a container runtime's
     log driver.
   - Keep stdout as the operator/`docker logs` stream and state that the duplication is deliberate,
     naming the one stream the backend reads.
   - Document rotation (32 MB × 5 per file, `squid/logging_config.py:25-28`) and volume ownership:
     the app writes `./logs` as `appuser`, the collector mounts it read-only, and nothing prunes it
     but rotation.
   - If a deployment platform later exposes container stdout safely, switching is a collector-side
     change plus dropping the file handlers; leave that trigger written down rather than half-built.
   - Any change to this transport updates `compose.yml`, `deploy/otel-collector.yaml`,
     `squid/logging_config.py` defaults, `.env.example`, and the deployment documentation together.

2. **Separate the correlation ID from its display reference** — *done*, alongside error-report
   storage. Two corrections found while implementing:
   - `ProblemDetail` never had an `error_id` field and `X-Error-ID` no longer exists; the full value
     travels in the `Request-Id` response header, which is what the detail route accepts as the long
     form of a reference. Nothing needed un-truncating.
   - The reference alone was not enough to be useful. The bot never called `bind_correlation_id`, so
     the ID was minted inside `build_error_presentation` and nothing the command had already logged
     carried it. `SquidCommandTree._call` and `Bot.invoke` now open a re-entrant `correlation_scope`
     around the whole invocation, which is what makes a stored report's log tail non-empty.
   - Add `correlation_reference(correlation_id) -> str` to `squid/observability.py`: the first 12
     hex characters of a trace ID, and the identity function for the untraced fallback, which is
     already `uuid4().hex[:12]`. Both paths then look identical to a user and to support.
   - 12 hex is 48 bits. The requirement is that one reported reference resolves to one incident
     within the retention window, so the bound is `N / 2^48`: at 10 000 unexpected errors per
     window that is ~4 × 10⁻¹¹, and even the stricter all-pairs-distinct bound, `N²/2^49`, is
     ~2 × 10⁻⁷. Eight hex characters would put the all-pairs bound at ~1% for the same volume,
     which is why the prefix is not shortened further.
   - `ErrorPresentation` keeps `error_id` (full) for the log line and gains `reference` (short) for
     the user-facing string. Do not truncate `ProblemDetail.error_id` or `X-Error-ID`.
   - Log both: the full `error_id` and the short `error_ref`, so a backend that does not index
     prefix queries can still resolve a reported reference by exact match.

3. **Collapse the optional-dependency dance into one resolved record**
   - Resolve every OpenTelemetry entry point once, inside `_configure_otel`, into a frozen
     `_Telemetry` record carrying the pid, the two tracers, the meter, the propagator, and the
     error-status factory. Store it in one module global.
   - Replace all seven guards with `telemetry = _telemetry()`, which returns `None` when telemetry
     is unconfigured, when the extra is missing, or when the pid does not match. The
     `ModuleNotFoundError` filter then exists exactly once, in `configure_observability`.
   - Route the metric helpers through the same accessor, which gives them the pid check they lack
     today, and clear the record in `ObservabilityHandle.shutdown` so post-shutdown metrics are
     dropped rather than handed to a stopped provider.
   - Type the record and the instrument caches with local Protocols (`_Tracer`, `_Span`, `_Meter`,
     `_Counter`, `_Histogram`, `_Gauge`). Protocols, not `TYPE_CHECKING` imports: the extra is
     optional, so an import that resolves to `Unknown` when it is absent buys nothing.
   - Delete `_trace_endpoint`; call `_signal_endpoint(endpoint, "traces")` and
     `_signal_endpoint(endpoint, "metrics")` symmetrically at both call sites.

4. **Make trace propagation an explicit field of the worker protocol**
   - Replace `inject_trace_context(carrier)` with `trace_context_headers() -> dict[str, str]`,
     returning `{}` when telemetry is inactive.
   - Give the frame header a named `trace` field: `worker.py` sets it only when non-empty, and
     `worker_main.py` passes `header.get("trace", {})` to `extracted_trace_span`. Document the field
     in the `wire.py` module docstring alongside `parts`.
   - `extracted_trace_span` then takes a `Mapping[str, str]` and drops the `isinstance(value, str)`
     filter over the whole header.
   - No compatibility window is needed: the worker child is spawned by the supervisor from the same
     image, so both sides always ship together.

5. **Instrument commands through public API, with typed surfaces**
   - Name the span from `interaction.command.qualified_name`, read before `super()._call()`. It is
     public, it resolves through the registered tree so span names stay bounded, it handles context
     menus, and it pre-fills the same `_cs_command` slot `_call` assigns, so nothing is resolved
     twice. Unresolvable commands get `unknown`. Delete `_interaction_command_name` and its magic
     option-type literals.
   - Add `TraceSurface(StrEnum)` in `squid/observability.py` - `APPLICATION_COMMAND`,
     `PREFIX_COMMAND`, `RUNNING_MESSAGE`, `VIEW`, `MODAL`, `BACKGROUND_LOOP` - so the bot, worker,
     and API share one vocabulary without importing Discord. Split the packed `view:{item}` value:
     the surface becomes `VIEW` and the component class name becomes its own attribute and log
     field.
   - Instrument prefix commands by overriding the public `Bot.invoke`, closing the gap where
     `handle_context_error` reports failures for a surface that emits no span. One enum, both
     command paths.
   - Record the decision on contrib #4842: keep our own override while the package is an unmerged
     draft, keep `squid.*` attribute names, and re-evaluate if it is released - adopting the
     instrumentor wholesale would need a processor to strip `discord.user.id`, which `f55aaab` and
     `fb8c136` deliberately removed.

6. **Revert the transport-neutrality changes in `squid/api/errors.py`**
   - Import `FastAPI`, `Request`, `Response`, and `RequestValidationError` at module scope again;
     delete `ExceptionRegistrar`, the deferred import inside `register_exception_handlers`, and the
     local `correlation_id` wrapper in favour of importing it from `squid.observability`.
   - Keep `responses()`, and keep the import-surface case: `sqlalchemy` and `discord` are the
     imports that actually matter for a module every route imports, and `squid.observability` pulls
     in neither.

7. **Correlate welcome joins and messages instead of sleeping**
   - Move the pairing into `WelcomeRelayService`, which already holds pending joins: a welcome
     message that finds no matching join is parked under the same TTL and bound as joins, and
     `record_join` resolves it when the member arrives. Whichever event completes the pair returns
     the decision; neither waits.
   - Roll `forward_chance` once, when the message arrives, and park only a message that won the
     roll, so a parked message cannot change its mind later.
   - Build the mention as `<@{member_id}>` and delete the `guild.get_member` lookup. The member
     cache was the only reason to wait, and it was never needed.
   - Do not consume the pending member until the send has a decision to act on.
   - If a timer survives review at all, it belongs to `bot.background_tasks` via
     `BackgroundTaskSupervisor` so shutdown cancels it. The correlation design needs none: pruning
     stays lazy, as it is today.

8. **Rewrite the entry-point lifecycle test around the contract**
   - One table-driven test over the three entry points asserting the shared contract: observability
     is configured with that process's `service_name`, and on the way out telemetry is shut down and
     the log queue listener - where the process has one - is stopped.
   - Add the failure path: an exception from the process body still runs both shutdowns, in order.
   - Extract the collaborator patching into one helper per process so the test body states the
     invariant rather than the mock graph. That is the answer to "ugliest test I've ever seen" now
     that no fork remains to blame.

---

## Interfaces and Tests

### Resolved telemetry record

```python
@dataclass(frozen=True, slots=True)
class _Telemetry:
    pid: int
    tracer: _Tracer
    worker_tracer: _Tracer
    meter: _Meter
    propagator: _Propagator
    error_status: Callable[[], object]


def _telemetry() -> _Telemetry | None:
    """Return this process's telemetry, or None when it is absent, unconfigured, or inherited."""
    active = _ACTIVE
    return active if active is not None and active.pid == os.getpid() else None
```

### Correlation reference

```python
CORRELATION_REFERENCE_LENGTH = 12
"""Hex characters shown to users: 48 bits, and the width of the untraced fallback."""


def correlation_reference(correlation_id: str) -> str:
    """Shorten a correlation ID for display without changing what is stored or sent."""
```

### Worker frame header

```python
header: dict[str, Any] = {"id": self._next_id, "op": operation, "params": params}
if (trace := trace_context_headers()):
    header["trace"] = trace
```

### Trace surfaces

```python
class TraceSurface(StrEnum):
    APPLICATION_COMMAND = "application_command"
    PREFIX_COMMAND = "prefix_command"
    RUNNING_MESSAGE = "running_message"
    VIEW = "view"
    MODAL = "modal"
    BACKGROUND_LOOP = "background_loop"
```

### Tests

- **Observability facade** (`tests/unit/test_observability.py`): disabled and missing-extra
  configurations stay inert; `shutdown()` clears the record so a later `add_counter` is a no-op; a
  simulated foreign pid makes every public helper degrade to its no-op branch.
- **Propagation** (`tests/integration/observability/test_traces.py`): keep the live-SDK module,
  updated for the `trace` field - parent context injected by the supervisor and extracted by the
  child produces one trace, and a header without `trace` still starts a root span.
- **Worker protocol** (`tests/unit/schematics/infrastructure/test_worker_logging.py`): the encoded
  frame carries `traceparent` under `trace`, and no propagation keys appear at the header's top
  level.
- **Command spans** (`tests/unit/bot/test_errors.py`): span name comes from the registered command's
  `qualified_name`, an unresolvable interaction yields `unknown`, autocomplete is not traced, a
  failed command sets the error status, and no span or log field carries a Discord user ID.
- **Presentation** (`tests/unit/bot/test_errors.py`): the Discord string shows the 12-character
  reference, the log line carries the full ID and the reference, and the untraced fallback renders
  identically.
- **API errors** (`tests/unit/api/test_app.py`): `X-Error-ID` and `ProblemDetail.error_id` stay full
  width; the import-surface case still passes with FastAPI imported at module scope.
- **Welcome relay** (`tests/unit/community/application/test_services.py`, plus a new bot-side
  module - the cog has no test today): message-before-join and
  join-before-message both relay exactly once, the roll happens once per message, a member absent
  from the cache is still mentioned, TTL expiry drops a parked message, and no listener sleeps.
- **Entry points** (`tests/unit/bot/test_app_main.py`, `tests/unit/api/test_app.py`,
  `tests/unit/worker/`): the shared lifecycle contract on success and on failure.

---

## Disposition

| # | Thread | Comment | Disposition |
|---|---|---|---|
| 3783513656 | `deploy/otel-collector.yaml` | "why are we ingesting log files instead of stdout or app->collector" | **Retained, documented.** File scraping keeps logs working with the collector down, the extra uninstalled, and no container at all. The answer moves out of `docs/plans/observability.md` Decision 2 and into the collector config and deployment docs, together with rotation and volume ownership. |
| 3783528986 | `squid/bot/errors.py:191` | "with a longer ID we should add special handling in the UI to only show a prefix" | **Fix.** `correlation_reference()` shows 12 hex characters in Discord; logs, `ProblemDetail.error_id`, and `X-Error-ID` keep all 32. |
| 3783966831 | `squid/observability.py:197` | "this crap has been repeated so many times" | **Fix.** Seven copies of the import-and-pid guard collapse into `_telemetry()`; the `ModuleNotFoundError` filter survives once. |
| 3782936386 | `squid/observability.py` | "i get it but seems annoying" (lazy SDK imports) | **Fix, same change.** Lazy imports were the price of an optional extra; paying it once at configure time removes the annoyance without making the extra required. |
| 3783944392 | `squid/observability.py:81` | "use a protocol or a forward reference with TYPE_CHECKING" | **Fix.** Local Protocols for tracer, span, meter, and instruments. Protocols over `TYPE_CHECKING` imports because the extra may be absent, and an import that resolves to `Unknown` checks nothing. |
| 3784041813 | `squid/observability.py:319` | "why no `_metric_endpoint` helper" | **Fix by symmetry.** `_trace_endpoint` is deleted; both signals call `_signal_endpoint` directly. |
| 3783950048 | `squid/observability.py:178` | "urmmmmmm" (`inject_trace_context`) | **Fix.** `trace_context_headers()` returns the headers and the worker protocol gains a documented `trace` field, so nothing splices unnamed keys into a frame header. |
| 3783589159 | `squid/bot/errors.py:349` | "seems like a very bad idea" (`_interaction_command_name`) | **Fix.** Deleted in favour of the public `interaction.command`, which resolves through the registered tree, bounds span-name cardinality, and handles context menus. |
| 3783633285 | `squid/bot/errors.py:333` | contrib #4842 as inspiration | **Retained with rationale.** The PR is an open draft wrapping the same private `_call`, and it records `discord.user.id`. Keep our override and `squid.*` names; re-evaluate if the package is released, and strip the user ID if we adopt it. |
| 3783561014 | `squid/bot/errors.py:327` | "we may want a StrEnum for this" | **Fix.** `TraceSurface` in `squid/observability.py`, shared by bot, worker, and API, with the component name split out of the packed `view:{item}` value. |
| 3784405259 | `squid/api/errors.py` | "lets think about reverting most of the changes here" | **Fix by reverting.** FastAPI imports return to module scope. |
| 3784392069 | `squid/api/errors.py:11` | "i really dont think we have to avoid importing fastapi here" | **Fix.** Nothing outside `squid/api/` imports the module, and no test forbids FastAPI there. |
| 3784376308 | `squid/api/errors.py:47` | "useless" (local `correlation_id`) | **Fix by deletion.** Imported from `squid.observability`, which pulls in neither `sqlalchemy` nor `discord`, so the import-surface case stays green. |
| 3784410445 | `squid/api/errors.py:66` | "this can be kept" (`responses()`) | **Retained**, as the reviewer asked. |
| 3780241482 | `squid/bot/welcome_relay.py:26` | "bad config reuse" | **Already fixed** in `1da4ceb`: `welcome_relay_channel_id` is its own `CommunityConfig` field, distinct from `welcome_channel_id`. The listener's 30-second sleep is fixed separately by subplan 7. |
| 3783323740 | `tests/unit/bot/test_app_main.py:8` | "refactor the bootstrap to not have a forked process… ugliest test ive ever seen" | **Already fixed, then improved.** `24980f0` split the three processes and `squid/sync` is the durable queue between them; no fork remains. The test is rewritten as one table-driven lifecycle contract across the three entry points, with the failure path added. |

## Sequencing

Subplans 3 and 4 touch the same module and should land together, ahead of 5, which consumes
`TraceSurface` from it. Subplans 1, 2, 6, 7, and 8 are independent of the rest and of each other.

## Validation

- Focused modules while developing, with `--no-cov`; changed-file Ruff and `just typecheck`
  (pyrefly) afterwards, plus `git diff --check`.
- `tests/architecture/test_import_surfaces.py` and `test_boundaries.py` after the
  `squid/api/errors.py` revert.
- `tests/integration/observability/test_traces.py` needs the observability extra, and the worker
  frame tests need the schematic extra; both belong to the CI run rather than a local pass.
- No persistence is touched, so `alembic heads` is not implicated by this plan.

Replying on GitHub and resolving these threads still requires separate explicit authorization, per
[README.md](README.md).

## Completion update (2026-08-30)

**Done.** Filelog is documented as the authoritative backend stream and stdout as the deliberate
operator duplicate. Correlation display, optional telemetry resolution, PID/shutdown behavior,
worker trace propagation, typed trace surfaces, public command naming, FastAPI exception
registration, welcome join/message correlation, and the three-process lifecycle contract are all
implemented. Live optional-SDK tests cover parent propagation, metrics/privacy, and an empty worker
trace mapping starting a root span.
