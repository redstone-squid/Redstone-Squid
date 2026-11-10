# squid-ui-discord

The discord.py adapter and application runtime for [`squid-ui`](https://pypi.org/project/squid-ui/).
It renders planned scenes, delivers them, and owns live message roots, sessions, routing,
challenges, and optional durability.

This is an alpha release. The Python API may change before 1.0.

```console
pip install squid-ui-discord==0.1.0a1
pip install 'squid-ui-discord[durable]==0.1.0a1'
pip install 'squid-ui-discord[postgres]==0.1.0a1'
```

Install one runtime on each Discord client, then enter application code through an owner scope and
a declarative screen:

```python
import squid_ui as sl
import squid_ui_discord as sd


runtime = sd.install(bot)


class Counter(sd.Screen):
    count: int = sl.state(0)

    def render(self) -> sl.LayoutNode:
        return sl.section(
            sl.heading("Counter"),
            sl.paragraph(str(self.count)),
            sl.action_controls(
                sl.action_control("Add", self.add, key="add"),
                key="counter-actions",
            ),
        )

    async def add(self, event: sl.PressEvent) -> None:
        self.count += 1


@sd.command()
async def counter(request: sd.Request) -> Counter:
    return Counter()


bot.tree.add_command(counter)
```

Command decorators supply an owned request. Native commands can use `await sd.request(source)`
to infer the command's cog; component callbacks use typed events such as `sd.PressEvent` and obtain the root-owned request from `event.context`. Cog listeners without a
command binding use `await self.ui.request(source)`. Both resolvers share one request and ledger;
a missing or closed command-owner scope raises instead of extending the panel's lifetime.

`sd.install()` starts no tasks. Run the installed runtime before opening panels, even when
using neither scheduled refreshes nor challenges: ordinary panel shutdown also needs its execution
host. Await startup readiness, then finish the runtime before cancelling its runner:

```python
import anyio


async def main(token: str) -> None:
    async with bot:
        async with anyio.create_task_group() as tasks:
            await tasks.start(runtime.run)
            try:
                await bot.start(token)
            finally:
                with anyio.CancelScope(shield=True):
                    try:
                        await runtime.close()
                    finally:
                        tasks.cancel_scope.cancel()
```

The application owns its shutdown deadline. Shielding lets accepted cleanup finish when the
caller is cancelled; resource-specific timeouts belong in that cleanup.

Components V2 uses
`sd.DISCORD_V2_DPY27`; classic messages use `sd.DISCORD_V1_DPY27` through `sd.classic`.

Screens declare an immutable `policy = sd.ResponseSpec(...)`, including an optional
`session=sd.SessionSpec(...)`. Omitted session names derive from the concrete screen class;
explicit names preserve shared or durable identity. `Screen[ResultT, OwnerT]` exposes typed
results and optional owner typing. Exact Discord layouts have factories under `sd.v2` and
`sd.classic`, returning the existing primitive nodes.

Use `self.open_child(event, child, on_result=...)` to open a child with its own admission
policy and a lifetime bounded by the parent. Children call `self.complete(event, value)` or
`self.cancel(event)`; completion reaches a fresh parent action after the child's action commits.
A failed result callback leaves the child available for retry. `on_cancel` receives a `Cancelled`
reason without interaction authority. Commands can await `screen.wait()` for `Completed(value)`
or `Cancelled`; waiting inside an active UI action transaction is rejected.

Migration: replace preset screen bases and class policy fields with `Screen` and a
`ResponseSpec`. `Screen[Owner]` becomes `Screen[None, Owner]`. Access the live session through
`screen.session`, and the declaration through `Screen.policy.session`. Inherited policy is an
ordinary immutable value; use `Base.policy.overlay(...)` to extend it explicitly.

Install `sd.devtools.DevTools` as a Cog for prefix `dev ui` and the hybrid `/dev ui` command tree,
or call `await sd.open_devtools(ctx, runtime=devtools_runtime)` from an authorized host command.
`sd.devtools_runtime.DevToolsPolicy()` is read-only; mutations require `full_access()` or an
explicit `allow(...)` list. Persistence records load only when the dashboard visits that section.

Storage stays optional. Importing `squid_ui_discord` without a durability extra does not import or
require `squid-storage`.

- [Quickstart](https://redstone-squid.github.io/Redstone-Squid/squid-ui-quickstart/)
- [API map](https://redstone-squid.github.io/Redstone-Squid/squid-ui-api/)
- [Architecture and ownership](https://redstone-squid.github.io/Redstone-Squid/squid-ui-architecture/)
- [Classic-message guide](https://github.com/redstone-squid/Redstone-Squid/blob/master/packages/squid-ui-discord/docs/classic-messages.md)
- [Migration guide](https://github.com/redstone-squid/Redstone-Squid/blob/master/packages/squid-ui-discord/docs/migrating.md)
