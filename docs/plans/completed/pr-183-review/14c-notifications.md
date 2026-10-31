# PR #183 Review 14C: Notifications

## Scope

This plan covers thirteen threads across durable event wakeups, notification models/materialization, authenticated
inbox routes, and Discord notification management. It owns notification visibility and delivery semantics. Generic
event-worker lifetime remains in plan 12, while UI framework mechanics remain in the Squid UI plans.

## Findings

### The Discord command UX has already been replaced

The reviewed cog exposed internal subscription IDs through several commands. Current production code exposes one
slash-only `/notifications` workspace backed by `NotificationScreen`; selection and mutation happen through UI state,
not user-entered IDs. Prefix commands were removed rather than hidden.

The remaining Discord gap is localization: `render_delivery` and the command description still author raw English.
Move delivery text to deferred messages and keep payload facts separate from rendered transport text. No recipient
locale is persisted today and background DMs have no interaction locale, so this plan deliberately renders DMs with
the configured deterministic deployment fallback. Capturing a durable user preference would require a separate
profile/API/UI/privacy migration and is not implied here.

### Inbox filtering moved to SQL, but policy is still repeated at the route

Pagination and count now share `_inbox_filter`, so staff items are not filtered from a page in memory. Staff access is
also credential-bounded through `BUILD_SUBMISSION_VIEW_PENDING`, replacing the reviewed snowflake allowlist.

Both `list_inbox` and `mark_read` still calculate the same permission and pass `include_staff`. Introduce an
application `InboxVisibility` value resolved once by a dependency/service boundary; repository list, count, mark read,
and mark unread must accept the same value so their predicates cannot drift.

### Mark-unread is still missing

The repository and API support only marking read. Add a symmetric owner-visible operation. It must enforce the same
staff visibility predicate as reads, be idempotent, update only the requested account's row, and return not-found for
hidden or foreign items without revealing which condition applied.

### Materialization is durable, but recipient expansion needs set-based proof

`source_key` is now the idempotency key; the `event_id` foreign key preserves causality and traceability rather than
serving as the only deduplication mechanism. Retention is protected by cleanup queries' `NOT EXISTS` predicates—the
current `ON DELETE CASCADE` foreign key does not protect it. Keep both roles explicit and test cleanup ordering.

Some materializers still loop over recipient IDs. A loop is acceptable only when inserts are emitted in one batch and
recipient discovery is set-based. Refactor staff, creator-follow, and record-filter paths to produce immutable
`NotificationCandidate` rows and perform one PostgreSQL upsert plus one delivery insert-from-select. Add query-count
tests for 0, 1, and 100 recipients.

### Persistence should expose the domain enums it already has

Notification kinds and subscription kinds are checked strings in SQLAlchemy models and converted later. Map them as
`NotificationKind` and `NotificationSubscriptionKind` while retaining text columns/check constraints. Correct the
profile docstring to describe channel preferences only (current wording already does), and add model/domain round-trip
coverage so new enum members require an explicit migration.

### The wake listener now has a concrete purpose

`DomainEventWakeListener` is a PostgreSQL `LISTEN` hint that wakes a durable poller; correctness still comes from the
queue. Its current module and method docstrings state that. Retain this thin adapter and add a lost-notification test
showing periodic polling still drains work.

## Planned work

1. **Freeze the already-correct UX and SQL behavior.** Add command-tree/UI tests for the single workspace and query
   tests proving page/count filtering occurs in SQL.
2. **Unify inbox visibility.** Add `InboxVisibility`, resolve it once, and use the identical predicate for list, count,
   read, and unread transitions.
3. **Add mark-unread.** Implement repository, application, REST, OpenAPI, and workspace actions with indistinguishable
   hidden/foreign absence.
4. **Batch materialization.** Introduce typed candidates, set-based recipient queries, batched idempotent inserts, and
   constant query-count tests. Preserve event causality and source-key uniqueness.
5. **Type persistence.** Map subscription/notification enum values through SQLAlchemy and cover migration drift.
6. **Localize delivery presentation.** Store no translated text in event payloads; localize web responses from the
   request locale and background Discord DMs with the deterministic deployment fallback.
7. **Prove wake hints are optional.** Cover disconnect/reconnect and polling recovery without making `LISTEN` an owner
   of correctness.

## Interface sketch

```python
@dataclass(frozen=True, slots=True)
class InboxVisibility:
    include_staff: bool = False


@dataclass(frozen=True, slots=True)
class NotificationCandidate:
    account_id: int
    source_key: str
    kind: NotificationKind
    payload: Mapping[str, JSONValue]
    web_visible: bool
    enqueue_dm: bool
```

The materializer consumes candidates in deterministic `(account_id, source_key)` order. Database uniqueness remains
the final concurrency boundary; Python deduplication is not sufficient.

## Test matrix

- Repository integration: list/count symmetry, read/unread idempotency, hidden staff rows, foreign rows, forward and
  backward pagination, and uniqueness under replay/concurrency.
- Query counts: fixed upper bound for 0/1/100 staff recipients, creator followers, exact-record subscribers, and
  filter subscribers.
- Application: event-type to candidate mapping, current-outcome suppression, first-confirmation behavior, baseline
  record-run suppression, and invalid stored filters as data-integrity failures.
- API: permission allowed/denied, read/unread not-found privacy, idempotency replay, and OpenAPI response contracts.
- Bot: one `/notifications` surface, no raw-ID inputs, localized DM variants, safe mentions, forbidden-user
  suspension, retry, dead-letter, and recipient-locale fallback.
- Listener: wakeup, dropped notification, reconnect, and periodic-poll recovery.

## Thread dispositions

| Thread | Disposition |
|---|---|
| [`squid/events/infrastructure/listener.py`: “whats the point”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790849505) | **Already addressed.** It is explicitly an optional wake hint. Milestone 7 proves correctness without it. |
| [`squid/api/v1/notifications.py`: “we are NOT filtering in memory”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790811050) | **Already addressed.** `_inbox_filter` applies visibility before pagination/count; milestone 1 pins it. |
| [`squid/api/v1/notifications.py`: “this staff decision is wack”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790811493) | **Already addressed.** A permission node replaced the snowflake allowlist; milestone 2 additionally removes route duplication. |
| [`squid/notifications/infrastructure/repository.py`: “can we mark unread too”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790858055) | **Fix in milestone 3.** Add symmetric, visibility-safe unread transitions through every surface. |
| [`squid/notifications/infrastructure/repository.py`: “N+1?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790860328) | **Fix in milestone 4.** Recipient discovery and writes become set-based with query-count tests. |
| [`squid/notifications/infrastructure/repository.py`: “not sure if I like deduplicating by the DomainEventRecord table”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790864864) | **Retain.** `source_key` owns idempotency; `event_id` preserves causality/traceability, while cleanup predicates protect retention. |
| [`squid/notifications/infrastructure/models.py`: “too specific of a docstring vs the class name”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790855154) | **Already addressed.** `NotificationProfile` now documents independent channel switches and the consent boundary. |
| [`squid/notifications/infrastructure/models.py`: “enum”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790851725) | **Fix in milestone 5.** Map checked text columns through domain enums. |
| [`squid/bot/notifications.py`: “don't return an ID...”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790813978) | **Already addressed.** The workspace does not expose mutation IDs. |
| [`squid/bot/notifications.py`: “What an useless user-unfriendly description. This command shouldn't take in an ID.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790814832) | **Already addressed.** Users choose described items inside `NotificationScreen`. |
| [`squid/bot/notifications.py`: “in general, don't take in IDs in user facing commands.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790815751) | **Already addressed.** `/notifications` has no ID argument. |
| [`squid/bot/notifications.py`: “Add a UI and most commands can be gone. (or be hidden, i think prefix commands are useful for testing)”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790817804) | **Already addressed.** One slash-only workspace replaced the command set; behavior is tested through public controls. |
| [`squid/bot/notifications.py`: “translation?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790819450) | **Fix in milestone 6.** Author deferred text and localize at delivery. |

## Sequencing and delivery

Land visibility/read-unread work before batching materialization so failures stay attributable. Enum mapping may land
with no physical type change, but any check-constraint change requires an Alembic revision and upgrade/downgrade test.
The bot localization commit follows the application payload contract so no translated string becomes durable data.
