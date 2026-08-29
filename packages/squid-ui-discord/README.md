# squid-ui-discord

The discord.py runtime for [`squid-ui`](../squid-ui/README.md): message roots, sessions,
routing, delivery, adoption, roles, devtools and durable panels.

`squid-ui` plans a layout. This package puts it on Discord and keeps it there.

```python
import squid_ui_discord as sd
import squid_ui as sl


class Panel(sl.Component):
    count = sl.state(0)

    def render(self) -> sl.LayoutNode:
        return sl.section(
            sl.heading("Clicks"),
            sl.paragraph(str(self.count)),
            sl.action_controls(sl.action_control("Add", self.add)),
        )

    async def add(self, event: sl.PressEvent) -> None:
        self.count += 1


message_root = sd.MessageRoot(Panel(), access=sd.Everyone())
await message_root.send(sd.respond_to(interaction))
```

## What lives here, and what does not

The dividing line is **discord.py**, not the word "Discord".

Discord *protocol* knowledge stays in `squid-ui`, because the planner plans against it and
the HTML renderer reads the same target ids: Components V2 and classic limits, the two dialects,
the scene shapes, the `discord.*` capability tags, and the `DiscordTarget` marker types. None of
that imports discord.py.

What lives here is everything that needs a gateway: the renderers that turn a scene into
`discord.ui` objects, `MessageRoot` and its lifecycle, `Session`, `Router`,
`MessageRootScheduler`, `SessionSpec`,
delivery and receipts, adoption of views this library did not create, challenges, `RolePanel`,
devtools, and durability.

## Installing

```
pip install squid-ui-discord              # message roots, sessions, routing
pip install squid-ui-discord[durable]     # adds squid-storage for panels that survive a restart
pip install squid-ui-discord[postgres]    # durable panels on PostgreSQL
```

`durability` is imported lazily, so the base install genuinely never imports `squid_storage` --
`tests/test_public_api.py::test_base_install_needs_no_store_backend` is what keeps that true.

## Host integration

`sd.install(client)` assembles the session manager, challenge runner, and dialog presenter,
then records the `ClientRuntime` against the discord.py client. Anything carrying that client can
reach it through `ClientRuntime.of(source)`. Install once per client; a second install is refused.

```python
runtime = sd.install(bot, defaults=sd.MessageRootDefaults(chrome=CHROME), bus=topic_bus)
```

`install` starts nothing. Supervise `runtime.run()`, or supervise `runtime.scheduler.run()` and
`runtime.challenges.run()` separately when the host needs per-job health reporting. `runtime.close()`
ends every session and removes the client registration.

## Testing without Discord

`squid_ui_discord.testing` ships the doubles this package's own suite uses:

```python
from squid_ui_discord.testing import assert_within_limits, commit_render, fake_interaction

message_root = sd.MessageRoot(Panel(), access=sd.Everyone(), timeout=None)
commit_render(message_root)
await message_root.dispatch("add", fake_interaction())
```

A panel that renders portable nodes needs no double at all -- plan it against a target and
inspect the scene, which is what `squid-ui` is for.

## Docs

- [Adapter profiles](docs/discord-adapter-profiles.md)
- [Durable message roots](docs/durable-message-roots.md)
- [Classic messages](docs/classic-messages.md)
- [Migrating an existing discord.py bot](docs/migrating.md)
