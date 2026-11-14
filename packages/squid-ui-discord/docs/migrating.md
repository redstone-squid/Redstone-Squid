# Migrating an existing discord.py bot

You do not need to convert a bot all at once. Choose the smallest boundary that gives Squid
enough ownership to do the job, and leave the rest of the message lifecycle where it is.

| Existing screen | First Squid boundary | Who owns the message? |
|---|---|---|
| `LayoutView` with working callbacks | V2 fragment | Your view |
| `View`, embeds, or message content | Classic contribution | Your code |
| Persistent fixed `custom_id` controls | Router | Your post; Squid dispatches controls |
| One interactive message | MessageRoot and session | Squid |
| `Modal` used by a mounted component | Form | Its message root |

Squid does not adopt a live `View` or `LayoutView`. Planning is sound because the renderer owns
what it draws and measures everything the host keeps. Move the ownership boundary only when the
next migration step needs it.

## Keep a LayoutView and contribute one region

Use `sd.contribute` when an existing Components V2 view should retain its callbacks,
timeout, error policy, sending, and editing. Declare controls the host will append afterward with
`followed_by`; Squid measures and preflights them with the contributed region.

```python
import discord
import squid_ui as sl

view = ExistingLayoutView(timeout=180)
host_controls = discord.ui.ActionRow(
    discord.ui.Button(label="Legacy action", custom_id="legacy:action"),
)

attached = sd.contribute(
    [sl.heading("Build details"), sl.paragraph(description)],
    to=view,
    followed_by=(host_controls,),
)
await interaction.response.send_message(view=view, files=attached.build_files())
```

This is the one-call form exercised by `tests/test_fragments.py`. For a plan/apply boundary, use
the same test's two-step form:

```python
planned = sd.fragment(document, alongside=view, followed_by=(host_controls,))
# The host may inspect planned.report here.
attached = planned.attach(view)
```

`attach` remeasures the view. A host changed after planning raises `StaleReservationError`; an
item already owned by another view, a reused fragment, or component-local dispatch without a
message root raises `FragmentOwnershipError`. Attachment is transactional: a failed preflight or
`add_item` leaves the host unchanged.

A fragment is stateless. It may contain links and routed controls, but it has no message root to own
component state, local action callbacks, timeout, history, or refreshes. If the region needs any
of those, make one component own the whole message. There is deliberately no `into=` argument and
no arbitrary-view adoption: appending after preflight would make the measured payload differ from
the sent one.

## Keep a classic message

Use the classic adapter when the message needs `content`, embeds, or a normal `discord.ui.View`.
The host may contribute a region to an existing presentation or render a complete static one.

```python
from squid_ui_discord import MessagePayload, classic

host = MessagePayload.classic(content="@here", embeds=(legacy_embed,), view=legacy_view)
contribution = classic.contribute(document, to=host)
await destination(contribution.payload)

# For a complete, sessionless message:
presentation = classic.render_static(document)
await destination(presentation)
```

Classic contributions are one-step because content and embeds are immutable presentation values;
the returned presentation is the complete prospective message. Contributions replace whole embeds
and control regions—Squid does not splice fields into a host-owned embed. Component-local actions
remain unavailable until a message root owns the message. See
[Classic Discord messages](classic-messages.md) for exact primitives and limits. The examples above
follow `tests/test_classic_contribution.py`.

Message mode is a lifecycle constraint:

| Existing message | New presentation | Result |
|---|---|---|
| Classic | Classic | Allowed |
| Classic | Components V2 | Allowed; content and embeds are cleared |
| Components V2 | Components V2 | Allowed |
| Components V2 | Classic | `MessageModeError` before an API request |

Discord does not let a sent message relinquish its Components V2 flag. Open a replacement message
when migrating back to classic.

## Keep persistent custom IDs with a Router

A persistent post normally stores authoritative state in a database and needs stable custom IDs,
not an in-memory component session. A `Router` can take over those IDs without reposting the
message. Aliases provide a migration path from IDs already in the wild.

```python
routes = sd.RouteGroup[Bot]("r")
polls = routes.group("polls")
close_poll = polls.define("close", aliases=("poll:close",))

router = sd.Router(namespace=routes, on_gone=control_gone)


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
the router reserves whichever namespace it is given. `ctl:` is reserved for message-root-generated IDs.

Router and group middleware form one onion in attachment order. Use middleware for reusable
authorization, tracing, and rate policy. Routed controls do not inherit a legacy view's
`interaction_check`, timeout, or error hook; configure those policies on the router.

## Adopt an unsent classic view

Between a router and a full rewrite sits a view you already have. `sd.adopt` takes a
classic `discord.ui.View` that has **not** been sent and returns a `Component`. Squid reads
`view.children`, builds its own controls from them, owns the message, and dispatches back into
the legacy callbacks. Nothing in the callback body changes.

```python
class Paginator(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.page = 0

    @discord.ui.button(label="Next", custom_id="next")
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page += 1
        self.next.disabled = self.page == LAST
        await interaction.response.edit_message(view=self)  # no HTTP; the message root flushes


message_root = defaults.mount(sd.adopt(Paginator()), access=sd.Owner(user.id))
await message_root.send(sd.respond_to(interaction))
```

`await interaction.response.edit_message(view=self)` is the line that makes this worth doing: it
means "I am done mutating; redraw", performs no request of its own, and lets the message root answer the
interaction. The view is the model, and the adapter reports its in-place mutations to the runtime
after every callback returns.

Adoption is for an *unsent* view only. A live view — already sent, and editing its own message —
stays unsupported, because Squid and the view would both be writing one message and neither could
prove the result fits. `adopt()` refuses on `view.is_dispatching()`, and every call that would
make the legacy object a second writer raises `AdoptionError` rather than being swallowed:
`edit_original_response`, `interaction.message.edit`, and `edit_message` with a different view or
with a payload the message root owns. A `LayoutView` is refused too — that is content, so either move it
to the semantic layer or keep it and use `contribute()` on a region.

Three differences to plan for, all of them loud:

- **The message root owns the timeout.** `view.timeout` is ignored, because a message root's timeout interacts
  with its expiry policy, session lifetime, and disable-on-finish behaviour. An overridden
  `on_timeout` is refused at `adopt()` unless you pass `discard_timeout=True`; move real cleanup
  to a message root finish hook.
- **In-place mutations do not roll back.** A callback that raises reaches the message root's `on_error`
  with component state restored, but the view keeps whatever it wrote before raising.
- **Generated custom IDs are positional.** discord.py invents a random `custom_id` when you give
  none, so an item without an explicit one is identified by position. A view that rebuilds itself
  with `clear_items()` should pass `keys=` rather than let a reorder move state between controls.

`view.stop()` finishes the message root, an overridden `interaction_check` still guards the press
alongside the message root's `access`, and an overridden `on_error` still sees its own errors first. A
modal opened from a callback keeps working: its submit is wrapped in the same proxy and refreshes
the message root, though it runs outside the dispatch funnel, so it takes no author lock and no
generation check.

An adopted component composes like any other, which is how a migration finishes — embed it under
`self.boundary(child, key=...)` and move controls out of the legacy view one at a time.

## Hand one whole message to a MessageRoot

Use a message root when Squid component state, local actions, forms, history, or reactive refreshes should
own the whole message. Access is always explicit and visibility remains a destination decision.

```python
defaults = sd.MessageRootDefaults(chrome=BOT_CHROME, on_error=component_error)
sessions = sd.SessionManager(defaults=defaults)
message_root = defaults.mount(
    SettingsPanel(settings),
    access=sd.Owner(interaction.user.id),
    timeout=300,
)

result = await sessions.open(
    message_root,
    sd.respond_to(interaction, ephemeral=True, wait=True),
    key=sd.SessionKey.user_guild(
        "settings",
        interaction.user.id,
        interaction.guild_id,
    ),
    admission=sd.AdmissionSpec(collision=sd.Reject()),
    actor_id=interaction.user.id,
)

if isinstance(result, sd.Rejected):
    await interaction.followup.send("You already have settings open.", ephemeral=True)
```

`MessageRootDefaults` holds host-wide construction policy. Per-message-root overrides win, while `access=` stays
required because it identifies the actor allowed to use this particular message root. `SessionManager.open`
accepts a constructed message root, admits, delivers, and registers it atomically, and returns `Opened`,
`Rejected`, or `Abandoned`; do not race an open with a separate `manager.get()` preflight.
`AdmissionSpec` owns cardinality, collision choice, and replacement protection. Attach an additional
mount beneath an existing session with `Session.attach`.

## Replace mounted modals with forms

Forms keep schema, parsing, validation, and submission portable while the Discord adapter builds
the modal. A mounted form action automatically handles opening and submission.

```python
class DurationPanel(sl.Component):
    seconds: int = sl.state(0)

    def __init__(self) -> None:
        self.spec = sl.forms.FormSpec(
            "Duration",
            (sl.forms.DurationField(key="duration", label="Duration"),),
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
subclass `sl.forms.Form` when descriptors are more convenient. Under the default
`FormValidationMode.RETRY`, invalid input reopens a fresh modal with the submitted values and
errors. `ACCEPT_AND_MARK` instead delivers a `SubmitEvent` carrying errors to the handler. A legacy
`discord.ui.Modal` instance does not carry over: move its fields and validation into the schema and
its callback body into the submit handler.

## Lifecycle mapping

| discord.py surface | Squid surface | Migration note |
|---|---|---|
| `View.on_timeout` | `MessageRoot(timeout=...)`, `MessageRoot.on_finish(...)` | The message root disables and tears down its generated view; use a finish hook for host cleanup. |
| `View.interaction_check` | `AccessPolicy`; action guards | `Owner`, `Users`, `Everyone`, or async `Check` gates the message root. Guards apply finer action policy. |
| `View.on_error` | `MessageRoot(on_error=...)` / `MessageRootDefaults(on_error=...)` | One host hook receives the interaction, exception, and action source. Routers configure their own hook. |
| `DynamicItem` | `Route`, `RouteGroup`, `Router` | Keep stable IDs and aliases; register the router at startup. |
| Decorated button callback | `sl.action_control(..., on_click=...)` | Reuse the service call, not the live item. Annotate the callback with `sd.PressEvent` and use `event.context` for Discord facts. |
| `Modal.on_submit` | `FormSpec` or `sl.forms.Form` plus `SubmitEvent` | Parsing and retry policy move out of the Discord modal class. |
| Ephemeral edit token | `RenewEphemeral` with an expiry-supervising `MessageRootScheduler` | Interactive edits can renew credentials; unattended authority still expires. |

`RenewEphemeral` presents a replacement control before known edit authority expires. It requires a
scheduler implementing expiry supervision; a plain message root without one raises rather than promising
a renewal it cannot schedule.

## When to make a session durable

Keep an ordinary session for transient panels that may disappear on restart. Prefer routed
controls for long-lived authoritative posts whose state already lives in route parameters or an
application service. Use a durable session only when UI-local state must resume on the same Discord
messages after a process restart.

See [Durable sessions](durable-message-roots.md) for recipes, recovery, storage, leases, and the durable
open path. Durability preserves component and presentation state; consequential domain writes still
belong in the application's authoritative service.

## Alpha policy and exact-layout conveniences

Replace individual class policy fields and preset bases with one `ResponseSpec` value:

```python
class Settings(sd.Screen):
    policy = sd.ResponseSpec(
        session=sd.SessionSpec("settings", scope=sd.ScopeKind.USER_GUILD),
        timeout=300,
    )
```

Screens default to personal visibility and opener-only access. Preserve shared guild behavior with
`audience="public"`, `access=sd.Everyone()`, and a guild-scoped session with
`admission=sd.AdmissionSpec(collision=sd.Reject())`. Session scope does not imply audience or access.
Omit a session name to derive identity from the concrete screen class; retain explicit names when
identity must survive a rename or be shared across classes. Replacing an inherited policy replaces
the whole value; use `Base.policy.overlay(...)` when extending it.

Use `sd.v2.panel(*children)` and `sd.classic.card(*children)` when exact Discord structure is needed.
These factories promote bare text and omit `None`/`False`; primitive dataclass constructors are not
deprecated.

DevTools mutations are no longer enabled by default. Preserve the previous operational capability
set with an explicit allowlist, or opt into every operation:

```python
runtime = sd.devtools_runtime.DevToolsRuntime(
    policy=sd.devtools_runtime.DevToolsPolicy.full_access(),
)
```

The bundled Cog exposes one hybrid `/dev ui [tab]` command (prefix: `dev ui [tab]`).
The optional `tab` defaults to `overview`; choose `roots`, `sessions`, `routes`, `activity`,
`queues`, `profile`, or `persistence` to start elsewhere. Former subcommands are replaced by
the dashboard controls: select roots and sessions inside their tabs, find action results
and dispatches under Activity, and download the snapshot from Overview. A custom command
can call `sd.open_devtools(ctx, runtime=runtime)` instead of installing that Cog.

`Screen[OwnerT]` becomes `Screen[None, OwnerT]`: the first type argument now describes the screen's
result. Presentation still goes through `Request.respond(ScreenInstance())`. `opening` is that
typed request, or `None` for `Scope.send` and `Scope.edit`; `owner` is available before loading in
all three cases. `screen.session` is the live session; the recipe belongs in `Screen.policy.session`.
The host must authorize callers of `open_devtools`; the opener restricts the resulting panel to
that caller, while the bundled Cog defaults to bot-owner authorization.

Use `self.open_child(event, child, on_result=...)` for dialogs opened inside actions, and
`self.complete(event, value)` or `self.cancel(event)` inside the child. The result callback receives
a typed `ScreenResultEvent` with fresh parent authority. Child sessions keep their own admission
policy while their lifetime follows the parent. `on_cancel` receives a cancellation reason without
an interaction; handle rejected opening separately. Replace bespoke answer events and unmount
callbacks with `wait()` and `on_close(outcome)`. Only callers outside an active UI transaction may
await a screen's result.

## Respond through a request

`Scope.respond` has been removed. Commands declared with `@sd.command`,
`@sd.hybrid_command`, or `@sd.prefix_command` receive a `Request`; return the
screen and let the command wrapper present it:

```python
class Builds(sd.Cog):
    @sd.command()
    async def browse(self, request: sd.Request) -> BuildScreen:
        return BuildScreen(user_id=request.user.id)
```

For listeners and callbacks without a command binding, resolve the request through the cog
scope. Pass it to helpers that need to defer, prompt, or reply:

```python
request = await self.ui.request(interaction)
await request.defer("private")
await request.respond(BuildScreen(user_id=request.user.id))
```

Use `Scope.send` and `Scope.edit` for work without an incoming request. Closing
the scope still finishes the screens opened by its requests.


## Finish the request-centric migration

Use one request API for commands, native callbacks, and component events:

| Previous API | Replacement |
|---|---|
| `DiscordUIConfig` or installation keyword arguments | `sd.install(client, sd.Config(...))` |
| `sd.request(source, owner=self)` | `await self.ui.request(source)` for listeners and callbacks without command bindings |
| `bot.app_ui.request(source)` | `await sd.request(source)` for native commands, component events, and application-owned callbacks |
| Scope response defaults | `Config.responses`, screen class policy, or call keywords |
| `sd.responder(event).message_root` | `event.context.root` |
| `sd.native(event)` | `event.context.interaction` |
| `responder(event).send_modal(modal)` | `await event.context.form(modal)` |
| `squid_ui_discord.ext` | `squid_ui_discord` |
| `ext.testing.stage` / `invoke` | `sd.testing.stage` / `invoke` |
| `run_managed_result` | A command's `pending=` card and returned document |
| Application `MessageRootDefaults.on_error` | `Config.errors=sd.ErrorPolicy(render=..., observe=...)` |
| `Screen.root_options` | Typed screen fields such as `chrome` and `retain_routed_on_timeout` |

`sd.request` is asynchronous for every source. The root and session fields are optional because
commands and native callbacks need not originate on a live panel. A component event supplies its
root and inherits that root's owner scope. Native commands infer ownership from their Discord
binding. `Scope.request` supplies an owner when no command identifies one, while sharing the same
memoized request and ledger. `Scope` also owns lifetime and out-of-band sends/edits; it carries no
response defaults.

A missing or closed command-owner scope raises rather than falling back to application lifetime.
Use `sd.Cog` to register and close native commands' scopes automatically; a plain discord.py cog
must manage its own `runtime.scope(cog)` lifetime.
Bound resolution rejects another client's source or another owner's request. An application-scoped
request can be claimed before its first message or form; after that, attempting to change its owner
raises. Truly unbound sources and free commands remain application-owned.

`ErrorPolicy.render(request, error)` returns a document. `observe(request, error, delivery)` records
the failure; delivery is absent when no notice was sent. Pending commands replace their card and
re-raise the failure for discord.py's command error handler. Component failures roll back first,
then use the same policy to send a notice and report the failure. Standalone low-level roots can
still use their transport error hook; installing a runtime requires application errors in `Config`.

`RouteGroup.define` returns the existing route for an identical format and aliases, including after
registration. Reloading a handler module needs no application route cache; changed aliases and
new overlapping definitions still fail.

## Typed event contexts

Use Discord event aliases when a handler needs Discord capabilities:

```python
async def clicked(event: sd.PressEvent) -> None:
    await event.context.respond("Hello!", audience="personal")
```

The same applies to `sd.ChoiceEvent`, `sd.SubmitEvent`, and the other semantic event aliases.
Keep `sl.PressEvent` for callbacks that only change state or use portable operations such
as `event.notice`. The old `event.context` metadata dictionary is now `event.metadata`.
Constructing a portable event explicitly requires `context=`; pass `None` when there is
no frontend invocation context.

A screen containing Discord callbacks returns
`sl.LayoutNode[sl.ComponentsV2Target, sd.Request]` (or a sequence/document of that type).
The second parameter also carries through child components and widget content slots.
Bare layout annotations describe portable invocation capabilities.

For multiple implementations, use `sl.handlers(sd.on_press(callback), fallback=portable)`.
The `on_selection`, `on_choice`, `on_submit`, and other event registration functions provide
the same selection for their respective shapes. Registrations are selected using the
adapter profile's explicit `frontend`, with no callback annotation inspection.

## Updating helper-based calls

Entity references now own their stable presentation encoding: use `ref.encode()` and
`EntityRef.decode(value)`. Picker compatibility is `entity_type.supports(kind)`. The encoding itself
is unchanged, including the distinction between integer and string IDs.

Apply a delivered plan's updates with `presentation.apply_updates(plan.session_updates)`. This still
advances the presentation revision once when the batch changes state. Apply it only after successful
delivery; failed delivery must leave the remembered presentation unchanged.

Use `str(address)` for diagnostic state/topic addresses, `inspection.measure_classic(payload, ...)`
for an existing classic payload's cost, and `SolveNote(...)` to construct solver diagnostics. The
`describe`, `classic.measure_host`, and diagnostic `note` forwarding functions have been removed.
