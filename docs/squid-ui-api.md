# Squid UI API map

The package roots are intentionally curated public APIs. Start there; submodules expose advanced
composition and adapter contracts.

| Task | Public API |
|---|---|
| Reuse reactive state and behavior | `squid_ui.Reactive` (also `squid_reactivity.Reactive`) |
| Observe external changes | `squid_reactivity.Topic`, `watch`, optional `TopicBus` |
| Retain one model per key | Application-owned dictionary |
| Declare stateful UI | `squid_ui.Component`, `state`, `computed`, `resource` |
| Author layout | lowercase `squid_ui` factories such as `section`, `heading`, `choices` |
| Evaluate explicit composition choices | `squid_ui.planning.CompositionOptions`, `plan(..., composition=...)` |
| Plan portable output | `squid_ui.planning.plan`, an explicit target |
| Select native HTML | `squid_ui.html.target` |
| Encode a scene | `squid_ui.scene.Codec` |
| Draw a native HTML scene | `squid_ui.html.Renderer` |
| Preview Components V2 as HTML | `squid_ui.html.DiscordPreviewRenderer` |
| Compile a Slack message | `squid_ui_slack.SLACK_MESSAGE_SDK343`, `MessageRenderer` |
| Compile a Slack modal | `squid_ui_slack.SLACK_MODAL_SDK343`, `ModalRenderer` |
| Compile an App Home view | `squid_ui_slack.SLACK_HOME_SDK343`, `HomeRenderer` |
| Install a Discord host | `squid_ui_discord.install`, `DiscordUIRuntime` |
| Bind UI authority to an owner | `DiscordUIRuntime.scope`, `Scope` |
| Resolve a native command or component event | `squid_ui_discord.request`, `Request` |
| Resolve a listener in its cog scope | `Scope.request` |
| Declare opening policy and typed screen results | `squid_ui_discord.Screen`, `ResponseSpec` |
| Open an owned child screen | `Screen.open_child`, `Screen.complete`, `Screen.cancel` |
| Declare session identity and admission | `SessionSpec`, `SessionManager` |
| Own one live message | `MessageRoot` |
| Render Components V2 | `render_static`, `render_message`, `DISCORD_V2_DPY27` |
| Render classic messages | `classic.render_static`, `classic.render_message`, `DISCORD_V1_DPY27` |
| Test roots and payloads without a gateway | `squid_ui_discord.testing` |
| Stage the complete Discord facade | `squid_ui_discord.testing.stage`, `invoke` |
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

## Explicit shared composition

The shared composition path supports fixed choices on Components V2, Slack messages, and HTML:

```python
from squid_ui.planning import CompositionOptions

planned = sl.planning.plan(
    sl.paragraph("Ready for review."),
    target=sl.html.target(),
    composition=CompositionOptions(),
)
```

Use `sl.alternatives(..., content=..., key=...)` for peer presentations with explicit fidelity.
Multiple offers need a selection keyed by `OccurrenceId`; automatic joint search is not enabled
on this path yet. `CompositionRejectedError` identifies a failed candidate, not proven
unsolvability. Planning retains selected live bindings but performs no state updates.

Named resource limits use `squid_ui.presentations.budget(node, limits={Axis.COMPONENTS: 20})`.
They use target protocol counts and preserve structural boundaries across alternatives. This is
separate from the legacy `sl.budget(min=..., prefer=...)` API. Platform limits remain automatic.

`AtomicNode`, `AtomicContract`, and `FixedComposition` provide the typed custom-widget path.
See the [composition API](plans/generic-region-pagination/api-sketch.md) for occurrence paths,
source references, and custom rule responsibilities, and the
[migration inventory](plans/generic-region-pagination/02-composition.md#implemented-boundary-and-migration-inventory)
for supported vocabulary and remaining legacy routes.
