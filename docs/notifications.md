# Notification system

Redstone Squid uses its existing PostgreSQL domain-event log as the internal event bus. A separate Kafka, RabbitMQ,
NATS, or Redis Streams deployment is not required at the current scale: event publication, consumer fanout, and the
state change are committed in one database transaction. `LISTEN/NOTIFY` is only a low-latency wake hint; the worker
always polls `domain_event_deliveries`, so a lost notification cannot lose work.

## Delivery guarantees

- Events and Discord DMs are delivered at least once and may be observed out of order across replicas. Consumers
  re-read authoritative state and materialization uses stable source keys, so retries are idempotent and stale events
  are harmless.
- Domain-event claims use database timestamps, UUID fencing tokens, and separate claim counts. Unsupported schema
  versions are rejected directly to the dead-letter state rather than retried as transient failures.
- Discord DMs also use database-clock UUID claims. Ambiguous network failures are retried and can rarely produce a
  duplicate. The persisted Discord nonce is a best-effort short-window deduplication hint, not an exactly-once
  guarantee. Discord `Forbidden` responses suspend only the DM channel until the user re-enables it.

The optional `SQUID_DATABASE_LISTENER_URL` must point directly at PostgreSQL and support long-lived `LISTEN`
connections. `SQUID_DATABASE_URL` may continue to use the normal application pooler. Without a listener URL, polling
alone provides the same correctness with up to the configured worker event interval of additional latency.

## Event contracts

The envelope contains `event_type`, `schema_version`, aggregate identity, occurrence time, and a JSON payload.

| Event | Version | Purpose |
| --- | ---: | --- |
| `build.submitted` | 1 | Staff/owner moderation alert |
| `build.confirmed` | 2 | Submitter outcome and first-confirmation creator subscriptions |
| `build.denied` | 2 | Submitter outcome |
| `record_run.activated` | 1 | Compare stable competition holders with the previous active run |
| `vote_session.closed` | 1 | Existing vote outcome processing |

The first active record run for a scope is a baseline and intentionally sends no record-gain notifications. Later
runs group every competition newly gained by the same build into one notification per recipient.

## Consent, preferences, and subscriptions

Notification consent is independent from Minecraft account-linking consent. Web inbox and Discord DM channels both
default off, and can be changed independently through REST or the slash-only `/notifications` command group.
Disabling DMs cancels pending sends; enabling them later does not release a surprise backlog.

Subscriptions may target:

- a public creator UUID, which intentionally groups every public alias claimed by the same account;
- a stable record competition UUID, which survives record ruleset and computation-run changes; or
- a structured record filter with optional build-kind, record-class, version-scope, tag-presence, and exact tag-value
  predicates. Omitted fields are wildcards.

The REST surface is under `/v1/users/me/notifications`. It exposes notification consent/preferences, subscription
CRUD, a cursor-paginated inbox, and read acknowledgements. Staff inbox entries are authorization-checked on every
read and DM claim, so revoking a global-administrator grant removes access to already materialized staff alerts.
Configured owner IDs remain staff recipients.

Inbox notifications and their source events are retained for 90 days by default. Events with outstanding consumer
deliveries are never removed by notification retention. `SQUID_NOTIFICATION_PUBLIC_SITE_URL` controls links in DMs,
and `SQUID_NOTIFICATION_RETENTION_DAYS` changes the retention window.
