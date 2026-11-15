# squid-ui-discord

The discord.py adapter: rendering, delivery, sessions, and routing for portable components.
Most applications need only the first three sections.

```python
import squid_ui.ext.discord as sd
```

## Installing a runtime

One runtime per Discord client. `install()` starts no background work; supervise
`runtime.run()` before opening panels and close the runtime during shutdown.

::: squid_ui.ext.discord.Config

::: squid_ui.ext.discord.install

::: squid_ui.ext.discord.DiscordUIRuntime

::: squid_ui.ext.discord.DiscordUIRuntimeMissing

## Entry points

`DiscordUIRuntime` owns process resources. Its exact-identity owner scopes provide delivery, while
`Request` normalizes one event and its acknowledgement state. Command decorators inject it;
`sd.request(source)` infers native command and component ownership. Listeners without a command
binding use `self.ui.request(source)`, sharing the same request through the cog's scope. Missing
or closed expected scopes raise. `Screen` declares reusable opening policy.

::: squid_ui.ext.discord.Screen

::: squid_ui.ext.discord.Completed

::: squid_ui.ext.discord.Cancelled

::: squid_ui.ext.discord.ScreenCancelReason

::: squid_ui.ext.discord.ScreenResultEvent

::: squid_ui.ext.discord.Scope

::: squid_ui.ext.discord.Request

::: squid_ui.ext.discord.request

::: squid_ui.ext.discord.Response

::: squid_ui.ext.discord.ResponseSpec

::: squid_ui.ext.discord.OpenContext

::: squid_ui.ext.discord.Visibility

::: squid_ui.ext.discord.ResponseSource

::: squid_ui.ext.discord.StackNavigator

::: squid_ui.ext.discord.open_devtools

::: squid_ui.ext.discord.DevToolsSection

## Commands and testing

`sd.command` and `sd.hybrid_command` return native discord.py commands whose handlers receive
a `Request`. `sd.Group` and `sd.hybrid_group` add inherited command policy; `sd.Cog` owns their
scope and context menus. `sd.testing.stage` exercises presentation, access, sessions, dispatch,
generated forms, and serialized payload limits without connecting a gateway.

::: squid_ui.ext.discord.Cog

::: squid_ui.ext.discord.command

::: squid_ui.ext.discord.hybrid_command

::: squid_ui.ext.discord.prefix_command

::: squid_ui.ext.discord.Group

::: squid_ui.ext.discord.hybrid_group

::: squid_ui.ext.discord.context_menu

::: squid_ui.ext.discord.autocomplete

::: squid_ui.ext.discord.testing.stage

::: squid_ui.ext.discord.testing.invoke

## Access policies

Every message root names who may use it.

::: squid_ui.ext.discord.AccessPolicy

::: squid_ui.ext.discord.Everyone

::: squid_ui.ext.discord.Owner

::: squid_ui.ext.discord.Private

::: squid_ui.ext.discord.Users

## Message roots

A `MessageRoot` owns one live Discord message for its whole life. Most hosts construct them
through `Request.respond` or a session spec rather than directly.

::: squid_ui.ext.discord.MessageRoot

::: squid_ui.ext.discord.MessageRootConfig

::: squid_ui.ext.discord.DEFAULT_MESSAGE_ROOT_CONFIG

::: squid_ui.ext.discord.MessageRootDefaults

::: squid_ui.ext.discord.MessageRootOptions

::: squid_ui.ext.discord.owner_message_root

::: squid_ui.ext.discord.messages.roots

::: squid_ui.ext.discord.PauseUpdates

::: squid_ui.ext.discord.RenewEphemeral

::: squid_ui.ext.discord.MessageRootScheduler

::: squid_ui.ext.discord.MessageRootSchedulerSnapshot

::: squid_ui.ext.discord.adopt

::: squid_ui.ext.discord.AdoptionError

## Sessions

Session specs are reusable recipes for opening logical sessions; the manager enforces keyed
cardinality (one settings panel per user, one vote per build).

::: squid_ui.ext.discord.SessionSpec

::: squid_ui.ext.discord.SessionManager

::: squid_ui.ext.discord.SessionKey

::: squid_ui.ext.discord.ScopeKind

::: squid_ui.ext.discord.Cardinality

::: squid_ui.ext.discord.ANY

::: squid_ui.ext.discord.EXACTLY_ONE

::: squid_ui.ext.discord.AT_LEAST_ONE

::: squid_ui.ext.discord.AT_MOST_ONE

## Rendering and delivery

Static rendering without a mount, and the payload/destination vocabulary for delivering it.
`DISCORD_V2_DPY27` is Components V2; classic messages live under `sd.classic` with
`DISCORD_V1_DPY27`, and there is no implicit target switch.

::: squid_ui.ext.discord.render_static

::: squid_ui.ext.discord.render_message

::: squid_ui.ext.discord.render_item

::: squid_ui.ext.discord.DISCORD_V2_DPY27

::: squid_ui.ext.discord.DISCORD_V1_DPY27

::: squid_ui.ext.discord.V2_LIMITS

::: squid_ui.ext.discord.RenderedMessage

::: squid_ui.ext.discord.MessagePayload

::: squid_ui.ext.discord.MessageMode

::: squid_ui.ext.discord.MessageDestination

::: squid_ui.ext.discord.MessageModeError

::: squid_ui.ext.discord.message_mode

::: squid_ui.ext.discord.send_to

::: squid_ui.ext.discord.edit_to

::: squid_ui.ext.discord.reply_to

::: squid_ui.ext.discord.respond_to

::: squid_ui.ext.discord.deliver_to

## Error policy

Configure command and component failure rendering and observation through `Config.errors`.

::: squid_ui.ext.discord.ErrorPolicy

::: squid_ui.ext.discord.ErrorRenderer

::: squid_ui.ext.discord.ErrorObserver

::: squid_ui.ext.discord.ResourceCost

::: squid_ui.ext.discord.LocalizationResolver

## Challenges

A guard's refusal can become a question shown to a privileged actor.

::: squid_ui.ext.discord.ChallengePresenter

::: squid_ui.ext.discord.ChallengeRequest

::: squid_ui.ext.discord.ChallengeRunner

::: squid_ui.ext.discord.ChallengeSupervisor

::: squid_ui.ext.discord.DialogPresenter

## Role panels

::: squid_ui.ext.discord.RolePanel

::: squid_ui.ext.discord.RoleCategory

::: squid_ui.ext.discord.RoleOption

::: squid_ui.ext.discord.RoleNoticeHandler

::: squid_ui.ext.discord.RoleTransitionResult

::: squid_ui.ext.discord.RolesUpdated

::: squid_ui.ext.discord.RolesUnchanged

::: squid_ui.ext.discord.RoleSelectionInvalid

::: squid_ui.ext.discord.RoleMutationFailed

::: squid_ui.ext.discord.RoleMutationForbidden

::: squid_ui.ext.discord.RoleConfigurationUnavailable

## Host-owned views

Contribute Squid regions to a view somebody else owns, or validate one.

::: squid_ui.ext.discord.contribute

::: squid_ui.ext.discord.conform

::: squid_ui.ext.discord.EMPTY_COST

::: squid_ui.ext.discord.button_grid

::: squid_ui.ext.discord.ExistingLayoutError

::: squid_ui.ext.discord.LimitViolationError

## Advanced modules

| Module | Purpose |
|---|---|
| `squid_ui.ext.discord.access` | Explicit authorization policies for Discord message roots. |
| `squid_ui.ext.discord.actions` | Discord adapter for portable component action events. |
| `squid_ui.ext.discord.adapter` | The verified discord.py adapter profile and boundary checks. |
| `squid_ui.ext.discord.challenges` | Showing a guard's challenge, and running the approved press. |
| `squid_ui.ext.discord.classic` | Composing classic Discord messages. |
| `squid_ui.ext.discord.v2` | Exact Components V2 layout factories. |
| `squid_ui.ext.discord.rendering.classic` | Mechanical drawing of resolved classic messages. |
| `squid_ui.ext.discord.conformance` | Boundary gate that keeps built views inside Discord's limits. |
| `squid_ui.ext.discord.delivery` | Send/edit mechanics for rendered Discord messages. |
| `squid_ui.ext.discord.devtools` | Owner-only operational diagnostics for live runtimes. |
| `squid_ui.ext.discord.devtools_runtime` | Operational inspection and controls for live runtimes. |
| `squid_ui.ext.discord.durability` | Opt-in durable component snapshots and host-owned mount management. |
| `squid_ui.ext.discord.fragments` | Squid regions contributed to a view somebody else owns. |
| `squid_ui.ext.discord.grids` | Exact Discord grid construction. |
| `squid_ui.ext.discord.guards` | Discord-layer admission sugar over the portable guard vocabulary. |
| `squid_ui.ext.discord.inspection` | Read-only measurement and validation of a host-owned layout. |
| `squid_ui.ext.discord.request` | One localized, owner-bound Discord request and its acknowledgement ledger. |
| `squid_ui.ext.discord.limits` | Discord presentation limits as data, for both message modes. |
| `squid_ui.ext.discord.live` | Which message roots are live right now. |
| `squid_ui.ext.discord.messages.payload` | The whole outgoing Discord message surface, as one value. |
| `squid_ui.ext.discord.messages.root` | The mount: one component bound to one Discord message. |
| `squid_ui.ext.discord.modals` | Declarative modals: specs in, clamped discord.py modals out. |
| `squid_ui.ext.discord.navigation` | Stack navigation by composition. |
| `squid_ui.ext.discord.navigation_controls` | One navigation factory for materialized and loaded cursors. |
| `squid_ui.ext.discord.rendering.v2` | Mechanical drawing of resolved Components V2 scenes. |
| `squid_ui.ext.discord.rendering` | Plan documents and render complete message payloads. |
| `squid_ui.ext.discord.roles` | Persistent, router-owned self-role panels. |
| `squid_ui.ext.discord.routing` | Dispatch for stateless routed controls. |
| `squid_ui.ext.discord.runtime` | One installed Discord runtime, reachable from its client. |
| `squid_ui.ext.discord.screen` | Declarative component response policy presented by an owner scope. |
| `squid_ui.ext.discord.session_specs` | Reusable recipes for opening logical Discord sessions. |
| `squid_ui.ext.discord.sessions` | Live sessions, their attachment trees, and cardinality policy. |
| `squid_ui.ext.discord.target` | Discord target conveniences bound to the shipped adapter. |
| `squid_ui.ext.discord.targets` | The registry that resolves a durable snapshot's target. |
| `squid_ui.ext.discord.testing` | Doubles and payload assertions with no Discord attached. |

## Event contexts

`sd.PressEvent`, `sd.SelectionEvent`, `sd.EntitySelectionEvent`, `sd.SubmitEvent`, and
`sd.InvalidSubmitEvent` specialize the corresponding portable event with `sd.Request`.
Semantic aliases include `sd.ChoiceEvent`, `sd.EntityEvent`, `sd.ToggleEvent`,
`sd.NavigateEvent`, `sd.ScaleEvent`, and `sd.OpenEvent[ValueT]`.

Use `event.context` for that invocation's request and `event.metadata` for dispatch metadata.
`sd.on_press` and the matching `sd.on_*` registration functions supply implementations to
`sl.handlers`; plain callbacks remain the normal single-frontend authoring path.
