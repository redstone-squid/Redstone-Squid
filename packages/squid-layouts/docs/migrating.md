# Migrating an existing discord.py bot

You do not need to convert a bot all at once. Choose the smallest boundary that gives Squid
enough ownership to do the job, and leave the rest of the message lifecycle where it is.

| Existing screen | First Squid boundary | Who owns the message? |
|---|---|---|
| `LayoutView` with working callbacks | V2 fragment | Your view |
| `View`, embeds, or message content | Classic contribution | Your code |
| Persistent fixed `custom_id` controls | Router | Your post; Squid dispatches controls |
| One interactive message | Mount and session | Squid |
| `Modal` used by a mounted component | Form | Its mount |

Squid does not adopt a live `View` or `LayoutView`. Planning is sound because the renderer owns
what it draws and measures everything the host keeps. Move the ownership boundary only when the
next migration step needs it.

## Keep a LayoutView and contribute one region

Use `sl.discord.contribute` when an existing Components V2 view should retain its callbacks,
timeout, error policy, sending, and editing. Declare controls the host will append afterward with
`followed_by`; Squid measures and preflights them with the contributed region.

```python
import discord
import squid_layouts as sl

view = ExistingLayoutView(timeout=180)
host_controls = discord.ui.ActionRow(
    discord.ui.Button(label="Legacy action", custom_id="legacy:action"),
)

attached = sl.discord.contribute(
    [sl.heading("Build details"), sl.paragraph(description)],
    to=view,
    followed_by=(host_controls,),
)
await interaction.response.send_message(view=view, files=attached.files())
```

This is the one-call form exercised by `tests/test_fragments.py`. For a plan/apply boundary, use
the same test's two-step form:

```python
planned = sl.discord.fragment(document, alongside=view, followed_by=(host_controls,))
# The host may inspect planned.report here.
attached = planned.attach(view)
```

`attach` remeasures the view. A host changed after planning raises `StaleReservationError`; an
item already owned by another view, a reused fragment, or component-local dispatch without a
mount raises `FragmentOwnershipError`. Attachment is transactional: a failed preflight or
`add_item` leaves the host unchanged.

A fragment is stateless. It may contain links and routed controls, but it has no mount to own
component state, local action callbacks, timeout, history, or refreshes. If the region needs any
of those, make one component own the whole message. There is deliberately no `into=` argument and
no arbitrary-view adoption: appending after preflight would make the measured payload differ from
the sent one.

## Keep a classic message

Use the classic adapter when the message needs `content`, embeds, or a normal `discord.ui.View`.
The host may contribute a region to an existing presentation or render a complete static one.

```python
from squid_layouts.discord import DiscordPresentation, classic

host = DiscordPresentation.classic(content="@here", embeds=(legacy_embed,), view=legacy_view)
contribution = classic.contribute(document, to=host)
await destination(contribution.presentation)

# For a complete, sessionless message:
presentation = classic.render_static(document)
await destination(presentation)
```

Classic contributions are one-step because content and embeds are immutable presentation values;
the returned presentation is the complete prospective message. Contributions replace whole embeds
and control regions—Squid does not splice fields into a host-owned embed. Component-local actions
remain unavailable until a mount owns the message. See
[Classic Discord messages](classic-messages.md) for exact primitives and limits. The examples above
follow `tests/test_classic_contribution.py`.

Message mode is a lifecycle constraint:

| Existing message | New presentation | Result |
|---|---|---|
| Classic | Classic | Allowed |
| Classic | Components V2 | Allowed; content and embeds are cleared |
| Components V2 | Components V2 | Allowed |
| Components V2 | Classic | `DiscordModeError` before an API request |

Discord does not let a sent message relinquish its Components V2 flag. Open a replacement message
when migrating back to classic.

## Keep persistent custom IDs with a Router

A persistent post normally stores authoritative state in a database and needs stable custom IDs,
not an in-memory component session. A `Router` can take over those IDs without reposting the
message. Aliases provide a migration path from IDs already in the wild.

```python
routes = sl.discord.RouteGroup[Bot]("r")
polls = routes.group("polls")
close_poll = polls.define("close", aliases=("poll:close",))

router = sl.discord.Router(namespace=routes, on_gone=control_gone)

@polls.route(close_poll)
async def close(interaction: discord.Interaction[Bot]) -> None:
    await polls_service.close(interaction.message.id)

router.add_middleware(RequirePollModerator())
router.register(bot)
```

This is the route-group shape tested in `tests/test_routing.py`. New controls use
`r:polls:close`; the alias continues to dispatch `poll:close` on existing posts. A namespaced
router requires `on_gone`: any unmatched ID under its prefix belongs to a retired version of that
router and receives the host's friendly response. The conventional `r:` prefix is not magic, but
the router reserves whichever namespace it is given. `ctl:` is reserved for mount-generated IDs.

Router and group middleware form one onion in attachment order. Use middleware for reusable
authorization, tracing, and rate policy. Routed controls do not inherit a legacy view's
`interaction_check`, timeout, or error hook; configure those policies on the router.

## Hand one whole message to a Mount

Use a mount when Squid component state, local actions, forms, history, or reactive refreshes should
own the whole message. Access is always explicit and visibility remains a destination decision.

```python
defaults = sl.discord.MountDefaults(chrome=BOT_CHROME, on_error=component_error)
sessions = sl.discord.SessionRegistry(defaults=defaults)

result = await sessions.open(
    SettingsPanel(settings),
    sl.discord.respond_to(interaction, ephemeral=True, wait=True),
    access=sl.discord.Owner(interaction.user.id),
    key=sl.discord.SessionKey.user_guild(
        "settings",
        interaction.user.id,
        interaction.guild_id,
    ),
    policy=sl.discord.SessionPolicy(collision=sl.discord.Reject()),
    actor_id=interaction.user.id,
    timeout=300,
)

if isinstance(result, sl.discord.Rejected):
    await interaction.followup.send("You already have settings open.", ephemeral=True)
```

`MountDefaults` holds host-wide construction policy. Per-open overrides win, while `access=` stays
required because it identifies the actor for this particular mount. `SessionRegistry.open` admits,
delivers, and registers atomically and returns `Opened`, `Rejected`, or `Abandoned`; do not race an
open with a separate `registry.get()` preflight. `SessionPolicy` owns cardinality, collision choice,
and replacement protection. The component-opening path above is covered by
`tests/test_sessions.py`.

If application code needs the mount before delivery—for example, to install a finish hook or attach
it beneath an existing session—construct it with `defaults.mount(component, access=...)` and pass
the resulting mount to `open` or `Session.attach`.

## Replace mounted modals with forms

Forms keep schema, parsing, validation, and submission portable while the Discord adapter builds
the modal. A mounted form action automatically handles opening and submission.

```python
class DurationPanel(sl.Component):
    seconds: int = sl.state(0)

    def __init__(self) -> None:
        self.spec = sl.FormSpec(
            "Duration",
            (sl.DurationField(key="duration", label="Duration"),),
        )

    def render(self) -> sl.LayoutNode:
        return sl.form("Duration", self.spec, key="duration", on_submit=self.submitted)

    async def submitted(self, event: sl.SubmitEvent) -> None:
        if not event.errors:
            duration = event.values["duration"]
            assert isinstance(duration, int)
            self.seconds = duration
```

This example is shortened from `tests/test_form_discord.py`. `FormSpec` is the explicit schema;
subclass `sl.Form` when descriptors are more convenient. Under the default
`FormValidationPolicy.RETRY`, invalid input reopens a fresh modal with the submitted values and
errors. `ACCEPT_AND_MARK` instead delivers a `SubmitEvent` carrying errors to the handler. A legacy
`discord.ui.Modal` instance does not carry over: move its fields and validation into the schema and
its callback body into the submit handler.

## Lifecycle mapping

| discord.py surface | Squid surface | Migration note |
|---|---|---|
| `View.on_timeout` | `Mount(timeout=...)`, `Mount.on_finish(...)` | The mount disables and tears down its generated view; use a finish hook for host cleanup. |
| `View.interaction_check` | `AccessPolicy`; action guards | `Owner`, `Users`, `Everyone`, or async `Check` gates the mount. Guards apply finer action policy. |
| `View.on_error` | `Mount(on_error=...)` / `MountDefaults(on_error=...)` | One host hook receives the interaction, exception, and action source. Routers configure their own hook. |
| `DynamicItem` | `Route`, `RouteGroup`, `Router` | Keep stable IDs and aliases; register the router at startup. |
| Decorated button callback | `sl.action(..., on_click=...)` | Reuse the service call, not the live item. Use `sl.discord.native(event)` only when the interaction itself is required. |
| `Modal.on_submit` | `FormSpec` or `sl.Form` plus `SubmitEvent` | Parsing and retry policy move out of the Discord modal class. |
| Ephemeral edit token | `RenewEphemeral` with an expiry-supervising `Reactor` | Interactive edits can renew credentials; unattended authority still expires. |

`RenewEphemeral` presents a replacement control before known edit authority expires. It requires a
scheduler implementing expiry supervision; a plain mount without one raises rather than promising
a renewal it cannot schedule.

## When to make a session durable

Keep an ordinary session for transient panels that may disappear on restart. Prefer routed
controls for long-lived authoritative posts whose state already lives in route parameters or an
application service. Use a durable session only when UI-local state must resume on the same Discord
messages after a process restart.

See [Durable sessions](durable-mounts.md) for recipes, recovery, storage, leases, and the durable
open path. Durability preserves component and presentation state; consequential domain writes still
belong in the application's authoritative service.
