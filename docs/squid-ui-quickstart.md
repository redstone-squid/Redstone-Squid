# Squid UI Discord quickstart

Squid UI requires Python 3.14 or newer and discord.py 2.7.

```console
python -m pip install squid-ui-discord==0.1.0a1
```

A screen's `render()` describes content. The framework then plans a layout that fits
Discord, constructs a message payload, and sends it. State changes repeat that cycle.
See [the rendering pipeline](squid-ui-architecture.md#end-to-end-flow) for the stages,
including the difference between component rendering and `Renderer.draw()`.

Install one runtime after constructing the Discord client:

```python
import discord
from discord.ext import commands

import squid_ui as sl
import squid_ui.ext.discord as sd


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
runtime = sd.install(bot)
```

Then define a `Screen`. It owns component state and opening policy; its `render()` describes
the interface:

```python
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

    async def add(self, event: sl.PressEvent) -> None:
        self.count += 1


@sd.command()
async def counter(request: sd.Request) -> Counter:
    return Counter()


bot.tree.add_command(counter)
```

The request carries localization, user, destination, acknowledgement state, and response policy.
Its scope owns the screens it opens. Squid command decorators supply that scope automatically.
Inside native commands, `await sd.request(ctx)` or `await sd.request(interaction)` infers it from
Discord's command binding. In a cog listener without a command binding, use
`await self.ui.request(source)` so the panel ends when that cog unloads. Component handlers use
`sd.PressEvent` (or the matching event alias) and use `event.context`, whose request inherits the root's scope.
Declare opening defaults in one immutable `policy` value:

```python
class Lobby(sd.Screen):
    policy = sd.ResponseSpec(
        audience="public",
        access=sd.Everyone(),
        session=sd.SessionSpec(
            scope=sd.ScopeKind.GUILD,
            admission=sd.AdmissionSpec(collision=sd.Reject()),
        ),
    )
```

An unnamed session uses the concrete screen class's module and qualified name. Supply
`name="lobby"` when different screen classes intentionally share identity or when identity must
survive a class rename. The default session scope is per user; `USER_GUILD` distinguishes guilds.
Audience and access remain separate from session scope. Screens default to personal visibility
and opener-only access, even when the session scope changes.

`Screen[ResultT, OwnerT]` types a result and, optionally, its application owner. Use `on_load()`
for loading before the first render. `opening` is the original request, or `None` for background
delivery; current handlers use their event's request. `session` is the live session, not its recipe.

For a dialog that returns a value, type its result and complete it from an action:

```python
class Confirmation(sd.Screen[bool]):
    def render(self) -> sl.LayoutNode:
        return sl.action_controls(sl.action_control("Confirm", self.confirm))

    async def confirm(self, event: sd.PressEvent) -> None:
        self.complete(event, True)


class Settings(sd.Screen):
    confirmed: bool = sl.state(False)

    def render(self) -> sl.LayoutNode:
        return sl.action_controls(sl.action_control("Review", self.review))

    async def review(self, event: sd.PressEvent) -> None:
        await self.open_child(event, Confirmation(), on_result=self.accept)

    async def accept(self, event: sd.ScreenResultEvent[bool]) -> None:
        self.confirmed = event.value
```

The opening action returns immediately. Acceptance runs in a fresh parent action; a failed callback
leaves the child open for retry. Closing the parent closes its children. Use `on_cancel` for cleanup
without an interaction, and handle a returned `Rejected` or `Abandoned` when opening reserves resources.
Commands outside an action may instead present a screen and await `screen.wait()` for `Completed`
or `Cancelled`; action handlers use `open_child` so they release their transaction and dispatch lock.

For one-off output, resolve a request once and use it for deferral and replies:

```python
request = await sd.request(interaction)
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


## Presentation controls

Use `sl.presentation` when the runtime should handle a control's value and other
UI needs to read it. Values belong to each mounted placement, whereas `sl.state`
belongs to the component instance.

```python
class Filters(sl.Component):
    flagged = sl.presentation.toggle("Flagged only")
    selected = sl.presentation.choices(minimum=0, maximum=2)

    def render(self):
        return (
            sl.paragraph(f"Selected: {len(self.selected.value)}"),
            sl.action_controls(
                self.flagged.bind,
                sl.action_control("Apply", self.apply, key="apply"),
                key="actions",
            ),
            self.selected.bind(
                sl.choice("Pending", key="pending"),
                sl.choice("Reviewed", key="reviewed"),
            ),
        )

    async def apply(self, event: sl.PressEvent):
        await event.acknowledge()
        selected = event.read(self.selected)
        # Use selected in the application operation.
```

A complete declaration can supply options up front and place `.bind` directly.
Call `.bind(...)` for render-time options or control overrides such as
`available=False`. Generic `sl.presentation(initial)` and
`sl.presentation(factory=...)` return value bindings for custom controls.

Reads use `.value` during rendering and `event.read(reference)` in handlers.
Assignment to the declaration or `.value` is forbidden. A removed placement's
references expire; hiding its control alone preserves the value.

Set `record=edits` to opt into undo history, where `edits = sl.runtime.history()`
can be declared earlier in the same class. Choices and generic values also need
`record_label`. Set `persist=True` independently to include values in configured
durable snapshots. Custom values use the restore recipe's paired
`presentation_encode(path, name, value)` and `presentation_decode(...)` callbacks.
Portable hosts can store `runtime.presentation_cells.export()` through a typed
storage slot and call `.restore(values)` before rendering. Recovery starts with
empty history.

The generic constructor accepts `opaque=True` for identity-based collaborators.
Use `factory=` for independent per-placement objects and `self.mutated(object)`
after an in-place change during an interaction. The notification can roll back;
the object's mutation cannot. Opaque values cannot use `persist=True`.

## Media and downloads

Both accept a `Path`, bytes, an open binary stream, or an HTTP(S) URL. Strings are
URLs; use `Path` for local files. Supply `filename=` for bytes and unnamed streams;
the MIME type is inferred from the name, or overridden with `media_type=`.

```python
from io import BytesIO
from pathlib import Path

preview = sl.media(Path("preview.png"), key="preview")
video = sl.media(BytesIO(video_bytes), filename="simulation.mp4", key="video")
export = sl.download(schematic_bytes, filename="build.litematic", label="Schematic", key="export")
gallery = sl.media(
    sl.media_item(Path("front.png"), description="Front view"),
    "https://example.org/side.png",
    key="views",
)
```

`media` previews the content; `download` offers a file control. The same file can
appear in both without a second upload. Use `sl.file(source, filename=...)` when
you want to name a file once and share it across controls or renders. `sl.figure`
also accepts these inputs and adds a caption.

Construction and planning do not read local files. Discord delivery snapshots them
in a worker thread on first use. Reusing a `sl.file` result or a constructed node
reuses that snapshot; create a new one to pick up changed content. Caller-owned
streams stay open and are read from their current position. Seekable streams have
their position restored. Keep a stream open until first delivery, and reuse a
`sl.file` result for non-seekable streams so they are consumed only once.

Synchronous HTML rendering and `build_files()` read synchronously. Custom async
Discord destinations should use `await payload.prepare_files()` to avoid blocking
the event loop. `Asset` and `StoredAsset` remain available for host-managed durable
references; local paths and streams belong to the current process.

## Joint page regions

`sl.region` allocates space together with sibling and nested regions on Discord
Components V2, classic Discord, HTML, Slack messages, and Slack Home. Use `header` and `footer` for content that must remain visible on
every page of that region. `title` supplies descriptive metadata; put a heading in `header` when it should
appear above the content. Navigation labels come from the configured chrome.

```python
return sl.Document(
    (
        sl.region(
            sl.region(
                *(sl.paragraph(item.description, key=str(item.id)) for item in results),
                key="results",
                title="Results",
                header=(sl.heading("Results"),),
            ),
            sl.region(
                render_overview(build),
                render_timing(build),
                key=f"build:{build.id}",
                title="Build details",
            ),
            key="browser",
            title="Build browser",
            header=(workspace_controls(),),
            footer=(close_action(),),
        ),
    ),
    key="build-browser",
)
```

The planner measures persistent content and navigation before certifying every
reachable page combination. Successful delivery commits the allocation and
boundaries; subsequent page turns reuse them. Content, locale, target limits, or control-state changes permit a new
allocation. Hidden regions keep their positions while they remain in the tree.
The planner consumes the supplied data snapshot; it does not fetch more results.

Use existing `key=` arguments for content identity. Paragraphs, groups, stacks,
blocks, and sections accept optional keys. Keys are unique among siblings and
region cursor paths include their ancestors. Keys preserve reading position
across insertion and reordering; unkeyed content has no such guarantee. A key
does not make content indivisible: groups and stacks may break between children.
Wrap a group in `.unbreakable()` to keep it together, or use `.keep_with_next()`
to prohibit the next break. Existing action keys retain their dispatch scopes.

`page=` accepts the existing uncontrolled, controlled, and routed ownership
policies. Planning never invokes change handlers. Explicit application page
requests take precedence over remembered anchors. A removed anchor recovers to
the nearest surviving item in the last displayed order, preferring the following
item on a tie. `sl.paged` retains its character cap in V2 and HTML regions. Other targets
use explicit regions or lists; a character grant does not map unambiguously to
classic's separate text pools or Slack's block budget.

Use `sl.alternatives` with explicit fidelity declarations to offer another
representation. Legacy `sl.fallback` ladders also work in shared regions, but selecting
a replacement reports a fidelity effect because equivalence was not declared.
Strict planning permits equivalent presentations and
lossless pagination, but rejects declared content loss. Section headings repeat
on continuation pages. Exact page counts and historical breaks are not a
compatibility guarantee.

`result.report.regions` describes each allocation, page count, resolved page,
and anchor. `result.cost` bounds the certified page family. `result.report.search`
distinguishes complete search from exhaustion with a valid certificate. An
explicit `search_budget` limits shared operations, including preprocessing and
target evaluation. Exhaustion without a certificate raises
`LayoutSearchExhaustedError`; established infeasibility raises
`UnsolvableLayoutError`. Neither case publishes a partial result or cursor writes.

Unsupported combinations raise `JointUnsupportedError` with capability diagnostics;
they never retry through another region solver. Slack modals retain their separate
form-only contract. Native `Variants` participates with checked source correspondence
and loss contracts; V2 `NativeItem` participates through the Discord adapter's native
validation contract. All documents use shared composition, including ordinary
non-region layouts and Slack modal forms. Platform implementations live in their adapter packages.


## Native alternatives inside regions

A native ladder defaults to using its first rung as canonical content. When the
rungs deliberately change layout, supply semantic `content` and map each rung to
that source. This example declares two equivalent presentations of one paragraph:

```python
from squid_ui.ext.discord import primitives

summary = primitives.Variants(
    (
        primitives.Variant(
            (primitives.Panel((primitives.Text("Build summary", overflow=primitives.Never()),)),),
            correspondence=(sl.corresponds("@content", "summary"),),
        ),
        primitives.Variant(
            (primitives.Text("Build summary", overflow=primitives.Never()),),
            correspondence=(sl.corresponds("@content", "summary"),),
        ),
    ),
    content=sl.paragraph("Build summary", key="summary"),
    key="summary-layout",
)
layout = sl.region(summary, key="details", title="Details")
```

`@content` names the group of nodes in a native rung. Correspondence is an author
declaration about meaning; the planner checks coverage and structural constraints,
not prose equivalence. A rung that drops or replaces content needs a specific
`Variant.contract`, such as `sl.losses(sl.omit("summary"))`. Changed control behavior
needs named interaction effects. Strict planning rejects those effects even when
the rung is marked `EXACT` or `REFORMATTED`.

Primitive `Paginate` also works without a key, using scoped positional ownership.
Supply an explicit key when the pager must retain its position after reordering.
On `Lines`, per-entry `Alt` ladders preserve individual line identity, while
`overflow=Alts(...)` replaces the whole collection and reports collection loss.
See the [native extension contract](squid-ui-architecture.md#semantic-authoring-adaptation-and-exact-primitives)
for integrating opaque widgets and shared attachments.
