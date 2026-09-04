# PR #183 Review: Shared Reaction Routing

## Findings

- Current HEAD replaced direct Discord listeners with one `ReactionRouter`, bounded message-keyed queues, and subscriber
  callbacks. This preserves per-message order and isolates subscriber failures, but the abstraction is difficult to
  follow and creates shard workers with bare `asyncio.create_task`, contrary to the project's owned-task rule.
- A shared normalization/cache layer is justified for the existing voting and administrative reaction consumers.
  A generic in-process event bus is not: Discord already supplies events, and subscribers should not need four no-op
  methods or dynamic `getattr` dispatch.
- Backpressure currently blocks Discord event callbacks on a full shard. Subscriber execution uses `asyncio.gather`, so
  ordering between subscribers is unspecified and cancellation/failure ownership is harder to reason about.
- This plan intentionally excludes every starboard policy, persistence, score, recount, and rendering concern.

## Subplans

1. **Make the contract explicit**
   - Keep a small bot-layer dispatcher only if multiple non-starboard consumers need shared message/member lookup or
     strict per-message ordering; otherwise restore ordinary discord.py listeners with a shared resolver helper.
   - If retained, expose typed add/remove/clear subscriptions (or separate protocols) instead of method-name strings,
     `getattr`, and mandatory no-op callbacks. Document ordering, snapshot-of-subscribers behavior, and overload policy.
2. **Own task lifetime**
   - Run dispatcher workers under `BackgroundTaskSupervisor`/anyio task ownership and replace shutdown-time
     `asyncio.timeout`, bare tasks, and broad `gather` orchestration with owned cancellation scopes/task groups.
   - Choose one explicit overload behavior: bounded enqueue with telemetry and a durable/reconciliation fallback for
     state that cannot be dropped. Do not silently discard accepted events during shutdown.
3. **Separate event normalization from Discord I/O**
   - Keep immutable raw identifiers as the event payload, but move memoized message/member fetching into an injected
     resolver scoped to one dispatch. Avoid pretending the event itself is framework-neutral.
   - Give voting its own adapter that translates Discord events to application commands and named rejection outcomes.
4. **Observability and failure handling**
   - Measure enqueue wait, queue depth, handler latency, handler failures, and shutdown drain time by consumer/event kind.
   - Ensure one subscriber failure does not terminate a shard while making persistent failures visible to operators.

## Tests

- Same-message FIFO and different-message concurrency under saturation.
- Subscribe/unsubscribe during dispatch, one failing consumer, cancellation, shutdown drain, and overload fallback.
- At-most-once shared Discord lookups per event and no lookup when no consumer requests it.
- Voting add/remove/clear behavior through the adapter, without any starboard fixtures or assertions.
- Architecture check that the dispatcher contains no bare `asyncio.create_task` and all workers have a runtime owner.

## Completion update (2026-08-30)

**Done.** The retained router exposes typed optional add/remove/clear callbacks, memoizes Discord
lookups per dispatch, preserves same-message FIFO, and runs shard lifetimes under
`BackgroundTaskSupervisor` and anyio. Enqueue wait/depth, handler latency/failure, drain duration,
and outstanding work are observable. Capacity waits are lossless during normal operation; if the
shutdown deadline aborts an unadmitted vote event, the router invokes the voting consumer's typed
recovery path instead of dropping it. Saturation, cancellation, clear dispatch, recovery handoff,
failure isolation, and source-level ownership rules are covered without starboard fixtures.
