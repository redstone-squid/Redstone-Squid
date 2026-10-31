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
app_ui = runtime.scope(bot)
```

Then define a `Screen`. Its class variables are opening policy; its fields and `render()` are the
portable component:

```python
class Counter(sd.Screen):
    audience = "personal"
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
    request = await app_ui.request(interaction)
    await request.respond(Counter())
```

The request carries localization, user, destination, acknowledgement state, and response policy.
Its scope owns the screens it opens.
For a common logical session policy, choose a named screen preset:

```python
class Lobby(sd.SharedGuildSessionScreen, session_name="lobby"):
    pass
```

`UserSessionScreen` opens one owner-only session per user; `UserGuildSessionScreen` opens one
per user and guild. Both replace a previous session. `SharedGuildSessionScreen` is public,
admits everyone, and rejects duplicate guild opens. All three preserve `Screen[OwnerT]` typing.
For custom admission, capacity, quota, or scope, declare a complete `session = sd.SessionSpec(...)`
instead of the `session_name` class keyword.

Use `on_load()` for request-dependent loading before the first render; `opening` is available inside
that hook. For one-off output, resolve a request once and use it for deferral and replies:

```python
request = await app_ui.request(interaction)
await request.defer("private")
await request.respond("Saved.")
```

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
