# Durable sessions

Use a durable session when a stateful component tree must resume on the same Discord messages after a restart.
The runtime owns the complete lifecycle: distributed admission, delivery promotion, whole-session snapshots,
recovery, claim renewal, checkpoint retries, expiry, and record deletion.

Register a stable recipe for every message-root type the runtime may reconstruct. The recipe builds the complete message root,
including its dependencies and explicit access policy; snapshots never import application classes or infer an owner.

```python
import anyio

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid_ui.ext.discord.durability import (
    ComponentRegistry,
    DiscordFrontend,
    DurableSessionRuntime,
    RestoreContext,
    MessageRootStateError,
    SQLiteSessionStore,
)
from squid_ui.runtime.services import UIExecutionHost


components = ComponentRegistry()
execution = UIExecutionHost()
defaults = sd.MessageRootDefaults(execution_host=execution)


def restore_review(context: RestoreContext) -> sd.MessageRoot:
    if context.actor_id is None:
        raise MessageRootStateError("review sessions require an owner")
    return defaults.mount(
        ReviewPanel(review_service),
        access=sd.Owner(context.actor_id),
        timeout=None,
    )


components.register("review", version=1, restore=restore_review)
sessions = sd.SessionManager(defaults)
runtime = DurableSessionRuntime(
    sessions=sessions,
    components=components,
    store=SQLiteSessionStore("mounts.sqlite3", table_name="review_sessions"),
    frontend=DiscordFrontend(bot),
)
```

Run the coordinator after Discord login and before gateway connection. The task-start handshake does not return
until recovery has completed and lease/checkpoint supervision is active.

```python
async with bot, anyio.create_task_group() as tasks:
    await tasks.start(execution.run)
    try:
        await bot.login(token)
        async with anyio.create_task_group() as durable_tasks:
            report = await durable_tasks.start(runtime.run)
            try:
                await bot.connect()
            finally:
                durable_tasks.cancel_scope.cancel()
    finally:
        with anyio.CancelScope(shield=True):
            try:
                await sessions.close_all(reason=sd.CloseReason.RUNTIME_SHUTDOWN)
            finally:
                await execution.close()
```

Stop the durable coordinator before closing live sessions: it releases claims and detaches
persistence, so closing the messages preserves their records for recovery. Store adapters
should bound their own I/O timeouts; cancellation waits for claim-release cleanup.

`DurableBot` supplies the same recovery ordering for `commands.Bot`. Subclasses implement `build_durable_runtime()`;
the runtime is available as `durable_sessions` during `setup_hook()`. Both inherited `start()` and an explicit
`await bot.login(...); await bot.connect()` recover before gateway dispatch.
It does not start or close the UI execution host; keep that host running around the bot lifecycle.

Open a public, addressable delivery through the runtime rather than opening through `SessionManager` and trying
to persist it afterward:

```python
assert interaction.guild_id is not None
message_root = defaults.mount(
    ReviewPanel(review_service),
    access=sd.Owner(interaction.user.id),
    timeout=None,
)
result = await runtime.open(
    message_root,
    sd.respond_to(interaction, ephemeral=False, wait=True),
    recipe="review",
    key=sd.SessionKey.user_guild("review", interaction.user.id, interaction.guild_id),
    actor_id=interaction.user.id,
)
```

The result is `Opened`, `Rejected`, `Abandoned`, or `NotDurable`. Ephemeral and unaddressable messages are not
recoverable and therefore return `NotDurable` without replacing an incumbent. A successful open atomically
publishes the first whole-session record and retires any collision-policy victims. Attach child messages through
the returned `DurableSession.attach(..., recipe=...)` so parent and actor attribution remain in the same record.

Application runtime commits trigger whole-session checkpoints. This includes a render whose Discord edit was
suppressed because its scene matched the live generation: hidden component state and runtime-only action bindings
still advanced. A failed checkpoint leaves the live UI usable, marks
`session.health` as `CHECKPOINT_PENDING`, and enters the runtime's retry queue. Finishing the root deletes the record;
process shutdown releases its claim without deleting it.

`RecoveryReport` separates restored, missing, expired, unreachable, incompatible, failed, and claimed-elsewhere
records. Missing roots and expired sessions are deleted. Missing child branches are pruned. Temporarily
unreachable and incompatible records remain stored for a later recovery or operator action.

Use `SQLiteSessionStore` only for one host or a shared-filesystem deployment whose hosts agree on wall-clock
time. For multi-host deployments, install the Postgres extra and pass an `asyncpg.Pool` to
`PostgresSessionStore`; its fenced claims and admissions use PostgreSQL time. Both stores implement the same
`DurableSessionStore` contract.

Choose the smallest lifecycle that fits:

- Use routed controls for long-lived authoritative posts whose state already lives in route parameters or an
  application service.
- Use ordinary sessions for transient panels that may disappear on restart.
- Use durable sessions for UI-local drafts and presentation state that must resume on the same messages.

Snapshots are not an application transaction log. Persist consequential domain changes in the authoritative
application service; durable sessions preserve only JSON-safe component and presentation state.
