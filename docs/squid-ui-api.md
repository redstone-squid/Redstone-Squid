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
| Compile a Slack message | `squid_ui_slack.SLACK_MESSAGE_SDK343`, `MessageRenderer` |
| Compile a Slack modal | `squid_ui_slack.SLACK_MODAL_SDK343`, `ModalRenderer` |
| Compile an App Home view | `squid_ui_slack.SLACK_HOME_SDK343`, `HomeRenderer` |
| Install a Discord host | `squid_ui_discord.install`, `DiscordUIRuntime` |
| Bind UI authority to an owner | `DiscordUIRuntime.scope`, `DiscordUI` |
| Handle one Discord event | `DiscordUI.resolve`, `DiscordRequest` |
| Declare reusable opening policy | `squid_ui_discord.Screen` |
| Compose dynamic session policy | `SessionSpec`, `SessionManager` |
| Own one live message | `MessageRoot` |
| Render Components V2 | `render_static`, `render_message`, `DISCORD_V2_DPY27` |
| Render classic messages | `classic.render_static`, `classic.render_message`, `DISCORD_V1_DPY27` |
| Test without a gateway | `squid_ui_discord.testing` |
| Persist scoped bytes | `squid_storage.ScopedStore`, `MemoryScopedStore`, `PostgresScopedStore` |
| Persist reactive state | `squid_storage.PersistentStatePool` |
| Replicate state | `squid_replication.Replica` and a selected backend |

## Reference

Each package has a curated reference page rendering every supported name:
[squid-ui](reference/squid-ui.md), [squid-ui-widgets](reference/squid-ui-widgets.md),
[squid-ui-discord](reference/squid-ui-discord.md),
[squid-ui-slack](reference/squid-ui-slack.md),
[squid-reactivity](reference/squid-reactivity.md),
[squid-storage](reference/squid-storage.md), and
[squid-replication](reference/squid-replication.md).

## Stability boundary

Names in each package's `__all__` are the supported alpha surface and are snapshot-tested. A module
that is importable but absent from `__all__` is internal unless its documentation explicitly says
otherwise. Scene protocol 1 has a published [JSON Schema](schema/scene-v1.schema.json); callbacks,
native Discord objects, and expiring runtime authority never enter that protocol.

## Slack Block Kit

```python
import squid_ui as sl
import squid_ui_slack as ss

planned = sl.planning.plan(sl.paragraph("Ready for review."), target=ss.SLACK_MESSAGE_SDK343)
payload = ss.MessageRenderer().draw(planned.scene, plan=planned)
await client.chat_postMessage(channel=channel_id, **payload.to_kwargs())
```

The Slack package ends at SDK models. The host owns the Slack client, acknowledgement deadlines,
action and view-submission listeners, routing, retries, and delivery. Action IDs match keys in
`planned.bindings`; modal callbacks and fields match `planned.form_bindings`.

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
