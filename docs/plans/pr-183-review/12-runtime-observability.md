# Runtime and observability review follow-up

## Review findings

This plan covers the runtime, logging, configuration, error-presentation, and telemetry comments
made through `5edfd3e`. Several concerns have already been overtaken by later work: API, bot, and
worker startup are now separate process entry points; optional FastAPI imports use `TYPE_CHECKING`;
OTLP signal URLs share `_signal_endpoint`; command logs no longer retain user identifiers; and the
current lifecycle tests verify resource ownership rather than the old fork bootstrap.

The remaining questions are deployment choices rather than obvious local refactors:

- production emits JSON to stdout and rotating files while the collector reads only the files;
- Discord shows the full 32-character trace ID even though a shorter reference would be easier to
  communicate;
- observability tests mix Squid-owned propagation guarantees with lower-value SDK wiring checks;
- the welcome relay still sleeps in a listener and relies on member-cache timing, although its
  channel setting is now in the correct community configuration.

## Planned work

1. Document the production log transport and choose one authoritative collector input. Prefer
   stdout/container collection when the deployment platform exposes it safely; otherwise retain
   `filelog`, remove duplicate production output, and document rotation/volume ownership.
2. Separate the full correlation ID used in logs and headers from a stable short display reference
   used in Discord. Preserve enough prefix entropy to find one incident unambiguously within the
   configured retention window.
3. Move the welcome relay delay into owned, cancellable runtime work or make the service correlate
   join and system-message events without sleeping in the Discord listener.
4. Keep tests that prove Squid's span composition, error correlation, privacy filtering, and
   shutdown ownership. Replace tests that merely restate OpenTelemetry or logging-library behavior
   with assertions at Squid's facade boundaries.
5. Close comments already satisfied by the process split, endpoint helper, import boundary, and
   privacy changes with links to their commits instead of rewriting the current implementation.

## Interfaces and validation

- If display shortening is needed in more than Discord, add one correlation-reference formatter;
  do not truncate the stored `error_id` or HTTP `X-Error-ID` value.
- If log transport changes, update `compose.yml`, `deploy/otel-collector.yaml`, logging defaults,
  and the deployment documentation together.
- Cover disabled and enabled telemetry, parent/child propagation, shutdown idempotence, redaction,
  collector ingestion, and cancellation during the welcome delay.
