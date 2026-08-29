# Squid UI Discord quickstart

Squid UI requires Python 3.14 or newer and discord.py 2.7.

```console
python -m pip install squid-ui-discord==0.1.0a1
```

Install one runtime after constructing the Discord client:

```python
import discord
from discord.ext import commands

import squid_ui as sl
import squid_ui_discord as sd


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
runtime = sd.install(bot)
```

Then define a `Screen`. Its class variables are opening policy; its fields and `render()` are the
portable component:

```python
class Counter(sd.Screen):
    visibility = "personal"
    count: int = sl.state(0)

    def render(self) -> sl.LayoutNode:
        return sl.section(
            sl.heading("Counter"),
            sl.paragraph(f"Count: {self.count}"),
            sl.action_controls(
                sl.action_control("Add", self.add, key="add"),
                key="counter-actions",
            ),
        )

    async def add(self, event: sl.PressEvent) -> None:
        self.count += 1


@bot.tree.command()
async def counter(interaction: discord.Interaction) -> None:
    await Counter().show(interaction)
```

`screen.show()` resolves the installed runtime, localization, user, destination, and access policy.
Omit `access` for an owner-only screen. For a logical session, declare `session_name` and its policy
alongside the common root policy:

```python
class Lobby(sd.SharedGuildSessionScreen):
    session_name = "lobby"
    capacity = 8
```

Use `UserSessionScreen` for one owner-only session per user and `UserGuildSessionScreen` when the
same user may open one in each guild. `SharedGuildSessionScreen` is public, admits everyone, and
rejects a duplicate guild session. Every preset subclass still declares its stable `session_name`;
all policy fields remain overridable.

Override `resolve_access(invocation)` when access depends on constructor state. Use `on_load()` for
invocation-dependent loading before the first render; `opening` is available inside that hook.
For one-off output, resolve an invocation directly:

```python
invocation = await sd.Invocation.of(interaction)
await invocation.reply(sl.heading("Saved"), sl.paragraph("Your changes are live."))
```

`sd.install()` starts no background work. If the application uses scheduled topic refreshes or
challenge flows, the host must supervise `runtime.run()` with its other process jobs. Call
`await runtime.close()` during shutdown; cancellation remains the responsibility of the supervisor
that started the task.

Use `sd.DISCORD_V2_DPY27` for Components V2. Classic-message rendering is explicitly separate under
`sd.classic` and uses `sd.DISCORD_V1_DPY27`; there is no implicit target switch.

For exact Discord structure without tuple-heavy primitive constructors, use mode-specific factories:

```python
v2_card = sd.v2.panel(
    sd.v2.heading("Build"),
    "Exact text is promoted automatically.",
    sd.v2.row(sd.v2.link_button("Open", build_url)),
    accent=0xE74C3C,
)

classic_card = sd.classic.card(
    "Classic embed description",
    title="Build",
    fields=(sd.classic.card_field("Status", "Ready"),),
)
```

The factories produce the existing primitive dataclasses; direct constructors remain available.

Next, use the [API map](squid-ui-api.md) or read the complete
[architecture and ownership guide](squid-ui-architecture.md).
