# squid-ui-discord

The discord.py adapter: rendering, delivery, sessions, and routing for portable components.
Most applications need only the first three sections.

```python
import squid_ui_discord as sd
```

## Installing a runtime

One runtime per Discord client. `install()` starts no background work; supervise
`runtime.run()` yourself if you use scheduled refreshes or challenge flows.

::: squid_ui_discord.install

::: squid_ui_discord.ClientRuntime

::: squid_ui_discord.ClientRuntimeMissing

## Entry points

`Screen` declares reusable opening policy; `Invocation` handles one event directly.
`current_invocation` reaches the live invocation from inside a handler.

::: squid_ui_discord.Screen

::: squid_ui_discord.Invocation

::: squid_ui_discord.current_invocation

::: squid_ui_discord.invocation_scope

::: squid_ui_discord.OpenContext

::: squid_ui_discord.Visibility

::: squid_ui_discord.InvocationSource

::: squid_ui_discord.StackNavigator

## Access policies

Every message root names who may use it.

::: squid_ui_discord.AccessPolicy

::: squid_ui_discord.Everyone

::: squid_ui_discord.Owner

::: squid_ui_discord.Private

::: squid_ui_discord.Users

## Message roots

A `MessageRoot` owns one live Discord message for its whole life. Most hosts construct them
through `ClientRuntime.mount` or a session spec rather than directly.

::: squid_ui_discord.MessageRoot

::: squid_ui_discord.MessageRootConfig

::: squid_ui_discord.DEFAULT_MESSAGE_ROOT_CONFIG

::: squid_ui_discord.MessageRootDefaults

::: squid_ui_discord.MessageRootOptions

::: squid_ui_discord.SessionOptions

::: squid_ui_discord.SessionOptionsResolver

::: squid_ui_discord.MessageRootFactory

::: squid_ui_discord.owner_message_root

::: squid_ui_discord.message_roots

::: squid_ui_discord.PauseUpdates

::: squid_ui_discord.RenewEphemeral

::: squid_ui_discord.MessageRootScheduler

::: squid_ui_discord.MessageRootSchedulerSnapshot

::: squid_ui_discord.adopt

::: squid_ui_discord.AdoptionError

## Sessions

Session specs are reusable recipes for opening logical sessions; the manager enforces keyed
cardinality (one settings panel per user, one vote per build).

::: squid_ui_discord.SessionSpec

::: squid_ui_discord.SessionManager

::: squid_ui_discord.SessionKey

::: squid_ui_discord.ScopeKind

::: squid_ui_discord.Cardinality

::: squid_ui_discord.ANY

::: squid_ui_discord.EXACTLY_ONE

::: squid_ui_discord.AT_LEAST_ONE

::: squid_ui_discord.AT_MOST_ONE

## Rendering and delivery

Static rendering without a mount, and the payload/destination vocabulary for delivering it.
`DISCORD_V2_DPY27` is Components V2; classic messages live under `sd.classic` with
`DISCORD_V1_DPY27`, and there is no implicit target switch.

::: squid_ui_discord.render_static

::: squid_ui_discord.render_message

::: squid_ui_discord.render_item

::: squid_ui_discord.DISCORD_V2_DPY27

::: squid_ui_discord.DISCORD_V1_DPY27

::: squid_ui_discord.V2_LIMITS

::: squid_ui_discord.RenderedMessage

::: squid_ui_discord.MessagePayload

::: squid_ui_discord.MessageMode

::: squid_ui_discord.MessageDestination

::: squid_ui_discord.MessageModeError

::: squid_ui_discord.message_mode

::: squid_ui_discord.send_to

::: squid_ui_discord.edit_to

::: squid_ui_discord.reply_to

::: squid_ui_discord.respond_to

::: squid_ui_discord.deliver_to

::: squid_ui_discord.responder

::: squid_ui_discord.native

## Managed results

Deliver an operation's outcome with consistent success and error presentation.

::: squid_ui_discord.run_managed_result

::: squid_ui_discord.ManagedError

::: squid_ui_discord.ManagedDelivery

::: squid_ui_discord.SuccessRenderer

::: squid_ui_discord.ErrorRenderer

::: squid_ui_discord.ErrorObserver

::: squid_ui_discord.Work

::: squid_ui_discord.ResourceCost

::: squid_ui_discord.LocalizationResolver

## Challenges

A guard's refusal can become a question shown to a privileged actor.

::: squid_ui_discord.ChallengePresenter

::: squid_ui_discord.ChallengeRequest

::: squid_ui_discord.ChallengeRunner

::: squid_ui_discord.ChallengeSupervisor

::: squid_ui_discord.DialogPresenter

## Role panels

::: squid_ui_discord.RolePanel

::: squid_ui_discord.RoleCategory

::: squid_ui_discord.RoleOption

::: squid_ui_discord.RoleNoticeHandler

::: squid_ui_discord.RoleTransitionResult

::: squid_ui_discord.RolesUpdated

::: squid_ui_discord.RolesUnchanged

::: squid_ui_discord.RoleSelectionInvalid

::: squid_ui_discord.RoleMutationFailed

::: squid_ui_discord.RoleMutationForbidden

::: squid_ui_discord.RoleConfigurationUnavailable

## Host-owned views

Contribute Squid regions to a view somebody else owns, or validate one.

::: squid_ui_discord.contribute

::: squid_ui_discord.conform

::: squid_ui_discord.EMPTY_RESERVATION

::: squid_ui_discord.button_grid

::: squid_ui_discord.ExistingLayoutError

::: squid_ui_discord.LimitViolationError

## Advanced modules

| Module | Purpose |
|---|---|
| `squid_ui_discord.access` | Explicit authorization policies for Discord message roots. |
| `squid_ui_discord.actions` | Discord adapter for portable component action events. |
| `squid_ui_discord.adapter` | The verified discord.py adapter profile and boundary checks. |
| `squid_ui_discord.challenges` | Showing a guard's challenge, and running the approved press. |
| `squid_ui_discord.classic` | Composing classic Discord messages. |
| `squid_ui_discord.v2` | Exact Components V2 layout factories. |
| `squid_ui_discord.classic_renderer` | Mechanical drawing of resolved classic messages. |
| `squid_ui_discord.conformance` | Boundary gate that keeps built views inside Discord's limits. |
| `squid_ui_discord.delivery` | Send/edit mechanics for rendered Discord messages. |
| `squid_ui_discord.devtools` | Owner-only operational diagnostics for live runtimes. |
| `squid_ui_discord.devtools_runtime` | Operational inspection and controls for live runtimes. |
| `squid_ui_discord.durability` | Opt-in durable component snapshots and host-owned mount management. |
| `squid_ui_discord.fragments` | Squid regions contributed to a view somebody else owns. |
| `squid_ui_discord.grids` | Exact Discord grid construction. |
| `squid_ui_discord.guards` | Discord-layer admission sugar over the portable guard vocabulary. |
| `squid_ui_discord.inspection` | Read-only measurement and validation of a host-owned layout. |
| `squid_ui_discord.invocation` | One localized Discord invocation and its delivery policy. |
| `squid_ui_discord.limits` | Discord presentation limits as data, for both message modes. |
| `squid_ui_discord.live` | Which message roots are live right now. |
| `squid_ui_discord.message_payload` | The whole outgoing Discord message surface, as one value. |
| `squid_ui_discord.message_root` | The mount: one component bound to one Discord message. |
| `squid_ui_discord.modals` | Declarative modals: specs in, clamped discord.py modals out. |
| `squid_ui_discord.navigation` | Stack navigation by composition. |
| `squid_ui_discord.navigation_controls` | One navigation factory for materialized and loaded cursors. |
| `squid_ui_discord.renderer` | Mechanical drawing of resolved Components V2 scenes. |
| `squid_ui_discord.rendering` | Plan documents and render complete message payloads. |
| `squid_ui_discord.roles` | Persistent, router-owned self-role panels. |
| `squid_ui_discord.routing` | Dispatch for stateless routed controls. |
| `squid_ui_discord.runtime` | One installed Discord runtime, reachable from its client. |
| `squid_ui_discord.screen` | Declarative component opening policy built on `Invocation`. |
| `squid_ui_discord.session_specs` | Reusable recipes for opening logical Discord sessions. |
| `squid_ui_discord.sessions` | Live sessions, their attachment trees, and cardinality policy. |
| `squid_ui_discord.target` | Discord target conveniences bound to the shipped adapter. |
| `squid_ui_discord.targets` | The registry that resolves a durable snapshot's target. |
| `squid_ui_discord.testing` | Doubles and payload assertions with no Discord attached. |
