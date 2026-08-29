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

Install one runtime on each Discord client, then enter application code through an invocation or
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


@bot.tree.command()
async def counter(interaction) -> None:
    await Counter().show(interaction)
```

`sd.install()` starts no tasks. The host owns `runtime.run()` when it enables scheduled refreshes
or challenge flows, and `await runtime.close()` ends the installed runtime. Components V2 uses
`sd.DISCORD_V2_DPY27`; classic messages use `sd.DISCORD_V1_DPY27` through `sd.classic`.

Named sessions can use `sd.UserSessionScreen`, `sd.UserGuildSessionScreen`, or
`sd.SharedGuildSessionScreen` for the common owner/scope/admission policies. Exact Discord layouts
have concise factories under `sd.v2` and `sd.classic`; both still return the public primitive IR.

Install the optional operational dashboard as a Cog for the default hybrid `/dev ui` command, or
call `await sd.open_devtools(...)` from a host-owned command. `DevToolsPolicy()` is read-only;
mutations require `DevToolsPolicy.full_access()` or an explicit `DevToolsPolicy.allow(...)` list.

Storage stays optional. Importing `squid_ui_discord` without a durability extra does not import or
require `squid-storage`.

- [Quickstart](https://redstone-squid.github.io/Redstone-Squid/squid-ui-quickstart/)
- [API map](https://redstone-squid.github.io/Redstone-Squid/squid-ui-api/)
- [Architecture and ownership](https://redstone-squid.github.io/Redstone-Squid/squid-ui-architecture/)
- [Classic-message guide](https://github.com/redstone-squid/Redstone-Squid/blob/master/packages/squid-ui-discord/docs/classic-messages.md)
- [Migration guide](https://github.com/redstone-squid/Redstone-Squid/blob/master/packages/squid-ui-discord/docs/migrating.md)
