# Squid UI API map

The package roots are intentionally curated public APIs. Start there; submodules expose advanced
composition and adapter contracts.

| Task | Public API |
|---|---|
| Declare stateful UI | `squid_ui.Component`, `state`, `computed`, `resource` |
| Author layout | lowercase `squid_ui` factories such as `section`, `heading`, `choices` |
| Plan portable output | `squid_ui.planning.plan`, an explicit target |
| Select native HTML | `squid_ui.html.target` |
| Encode a scene | `squid_ui.scene.Codec` |
| Draw a native HTML scene | `squid_ui.html.Renderer` |
| Preview Components V2 as HTML | `squid_ui.html.DiscordPreviewRenderer` |
| Install a Discord host | `squid_ui_discord.install`, `ClientRuntime` |
| Handle one Discord event | `squid_ui_discord.Invocation` |
| Declare reusable opening policy | `squid_ui_discord.Screen` |
| Compose dynamic session policy | `SessionSpec`, `SessionManager` |
| Own one live message | `MessageRoot` |
| Render Components V2 | `render_static`, `render_message`, `DISCORD_V2_DPY27` |
| Render classic messages | `classic.render_static`, `classic.render_message`, `DISCORD_V1_DPY27` |
| Test without a gateway | `squid_ui_discord.testing` |
| Persist scoped bytes | `squid_storage.ScopedStore`, `MemoryScopedStore`, `PostgresScopedStore` |
| Persist reactive state | `squid_storage.PersistentStatePool` |
| Replicate state | `squid_replication.Replica` and a selected backend |

## Stability boundary

Names in each package's `__all__` are the supported alpha surface and are snapshot-tested. A module
that is importable but absent from `__all__` is internal unless its documentation explicitly says
otherwise. Scene protocol 1 has a published [JSON Schema](schema/scene-v1.schema.json); callbacks,
native Discord objects, and expiring runtime authority never enter that protocol.

## Native HTML

```python
import squid_ui as sl

document = sl.article(
    sl.heading("Submission"),
    sl.paragraph("Ready for review."),
)
planned = sl.planning.plan(document, target=sl.html.target())
html = sl.html.Renderer().draw(planned.scene, plan=planned)
```

HTML planning retains semantic content, emits native controls and forms, and exposes action,
route, and pager metadata as data attributes. Callbacks remain in `planned.bindings`: the host owns
browser dispatch, authentication, and HTTP or WebSocket transport. Exact Discord primitives are
not portable and cannot be planned to this target.

The complete behavioral model—including transaction boundaries, action middleware, session scopes,
durability, and ownership—is in [architecture and API interactions](squid-ui-architecture.md).
