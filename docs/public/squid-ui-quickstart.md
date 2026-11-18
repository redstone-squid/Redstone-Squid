# Build a Discord screen

Create a slash command called `/counter` that posts a number and an **Add** button.
Each press updates the same message.

You need Python 3.14 or newer and a Discord bot token. Install your bot in a test
server with application commands enabled. Set `DISCORD_TOKEN` to the token and
`DISCORD_GUILD_ID` to the test server's ID.

```console
python -m pip install squid-ui-discord==0.1.0a1
```

## Describe the screen

A `Screen` is a component that the Discord adapter can open as a message. Its
`render()` method describes what the message should show using the current state.
Declare values that can change with `sl.state()`. Each `Counter` gets its own count.

Save the following as `counter.py`.

```python
import os

import anyio
import discord
from discord.ext import commands

import squid_ui as sl
import squid_ui.ext.discord as sd

bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
runtime = sd.install(bot)


class Counter(sd.Screen):
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

    async def add(self, event: sd.PressEvent) -> None:
        self.count += 1


@sd.command()
async def counter(request: sd.Request) -> Counter:
    return Counter()
```

The command opens the screen by returning it. A press calls `add()`. When the
handler changes `count`, the runtime renders the new value and edits the message.
The handler only has to update the state.

## Start the bot

Add this to the same file. The example registers the command in one server.
It starts the Squid UI runtime before connecting to Discord and closes it
while the Discord client is still available.

```python
async def main() -> None:
    guild = discord.Object(id=int(os.environ["DISCORD_GUILD_ID"]))
    bot.tree.add_command(counter, guild=guild)

    async with bot:
        async with anyio.create_task_group() as tasks:
            await tasks.start(runtime.run)
            try:
                await bot.login(os.environ["DISCORD_TOKEN"])
                await bot.tree.sync(guild=guild)
                await bot.connect()
            finally:
                with anyio.CancelScope(shield=True):
                    try:
                        await runtime.close()
                    finally:
                        tasks.cancel_scope.cancel()


if __name__ == "__main__":
    anyio.run(main)
```

Run `python counter.py`, then use `/counter` in the test server. The screen is
visible to the person who opened it by default, and only that person can press
**Add**. Run the command again to start a new count.

Continue with [How a screen updates](squid-ui-architecture.md) to see how the
runtime turns your component into a message and when it runs your code.
