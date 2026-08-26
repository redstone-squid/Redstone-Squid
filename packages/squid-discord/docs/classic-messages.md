# Classic Discord messages

A Components V2 message has **no `content` field**. Everything that reads `content` therefore
sees nothing: reply previews, search results, push notifications, forwarded-message previews,
and any automation keyed on message content. A bot whose message must ping someone *and* be
readable in the notification cannot use V2 at all. Embeds also carry author, footer,
timestamp, and field structure with no V2 equivalent, and they are the only Discord surface
that renders a titled, coloured, field-structured card beside plain message text.

So the classic target is a permanent capability, not a migration ramp. Pick a target by
saying which one you mean:

```python
import squid_layouts as sl
from squid_discord import CLASSIC_TARGET, V2_TARGET, classic

document = [
    sl.heading("Piston door"),
    sl.paragraph("A 2x2 flush door, seamless from both sides."),
    sl.fields(sl.field("Width", "2", key="width"), sl.field("Height", "2", key="height")),
    sl.note("submitted by squid"),
]

presentation = classic.render_static(document)
await destination(presentation)
```

That one document becomes one embed: the heading is its title, the paragraph its description,
the fields real embed fields, and the trailing note its footer. The same document under
`sd.render_static` becomes a `LayoutView` instead. Nothing in the document changed.

## What the targets differ in

| | `V2_TARGET` | `CLASSIC_TARGET` |
|---|---|---|
| Target id | `discord.components-v2` | `discord.components-v1` |
| Message `content` | not available | 2,000 characters, via `Content` |
| Text budget | 4,000 display characters | 2,000 content **and** 6,000 embed, separately |
| Structure budget | 40 components | 10 embeds, 5 rows, 25 controls |
| Containers, sections, galleries | yes | no |
| Embeds and embed fields | no | yes |
| View type | `discord.ui.LayoutView` | `discord.ui.View` |

The two text budgets are genuinely independent pools. Filling all 6,000 characters of embed
text leaves a 2,000-character `content` untouched, and vice versa: they are separate fields on
the outgoing message and Discord charges them separately.

## Migrating one screen, one step at a time

Each step below changes one thing. The service functions never change.

### 1. Where you start

A hand-built embed and a decorated view:

```python
class DoorView(discord.ui.View):
    @discord.ui.button(label="Approve")
    async def approve(self, interaction, button):
        await approve_build(self.build_id)          # your service function
        await interaction.response.send_message("Approved", ephemeral=True)

embed = discord.Embed(title="Piston door", description="A 2x2 flush door.")
embed.add_field(name="Width", value="2")
await ctx.send(embed=embed, view=DoorView())
```

### 2. Hand the content over, keep the message

The host still owns the message, its lifecycle, and its controls. Squid contributes a region
into what the host leaves unspent:

```python
host = sd.DiscordPresentation.classic(view=DoorView())
contribution = classic.contribute(document, to=host)

await ctx.send(
    **contribution.presentation._send_fields(),
    files=contribution.build_files(),
)
```

`contribute` measures the host, plans against what is left, preflights the complete
prospective payload, and only then moves anything. If the merge would break a limit, nothing
has moved. Contributed controls stay limited to links and routed actions: the host view's
callbacks remain under its owner, and a contributed region is never reactive independently of
the host. Note that a routed control does not run the host view's checks and does not refresh
its timeout.

Squid replaces whole embeds and whole control regions, never anything finer. Splicing fields
into a host-authored embed would mean owning that embed's internal layout and overflow policy
without owning the embed.

### 3. Let a mount own the screen

Now the message is Squid's, and the same service function is called from an action:

```python
class Door(sl.Component):
    approved: bool = sl.state(False)

    def render(self):
        async def approve(event: sl.ActionEvent) -> None:
            await approve_build(self.build_id)      # unchanged
            self.approved = True

        return [
            sl.heading("Piston door"),
            sl.paragraph("A 2x2 flush door."),
            sl.fields(sl.field("Width", "2", key="width")),
            sl.actions(sl.action("Approve", approve, key="approve"), key="controls"),
        ]

mount = sd.Mount(Door(), target=CLASSIC_TARGET, access=sd.Owner(user_id))
await mount.send(sd.reply_to(ctx))
```

The mount's access policy, generation-qualified custom IDs, transaction funnel, timeout, error
hook, forms, navigation, and history are the same code the V2 target uses. Only the dialect,
the renderer, the view factory, and the message mode differ.

A legacy callback body is reusable; a decorated item or a whole live view is not. When a
handler genuinely needs the interaction, reach for it explicitly with `sd.native(event)`
rather than transferring the item that carried it.

### 4. Open a V2 mount instead

Change the target, and nothing else:

```python
mount = sd.Mount(Door(), target=V2_TARGET, access=sd.Owner(user_id))
```

This has to be a *new* message. Discord cannot take the Components V2 flag back off a sent
message, so a V2-to-classic edit is refused with `DiscordModeError`; classic-to-V2 works by
replacing the whole presentation.

## Exact classic structure

Where the semantic layer is not enough, the classic primitives are exact:

```python
from squid_layouts.primitives import Card, CardField, CardFooter, Content, Text, Truncate

document = [
    Content("@here the build is ready"),
    Card(
        title="Piston door",
        url="https://example.invalid/door",
        children=(Text("A 2x2 flush door."),),
        fields=(CardField("Width", "2", inline=True),),
        footer=CardFooter("submitted by squid"),
        accent=0x00FF00,
    ),
]
```

A card slot takes either a bare string, which means `Never`, or a `Text` carrying an overflow
policy. A title written to be read whole should be refused rather than silently clipped, so
`Card(title="x" * 300)` is a planning error and `Card(title=Text("x" * 300, overflow=Truncate()))`
is not.

`Content` is legal only under a classic target, and at most one may appear in a document,
because a message has exactly one content field.

## What is checked, and by whom

Almost nothing about a classic message is validated before it reaches Discord. discord.py
enforces `len(embeds) > 10` and the 25-child cap on a view; the 6,000-character aggregate and
every per-value embed cap are server-only, and a violation comes back as an HTTP 400 naming
nothing useful.

`ClassicRenderer` therefore runs a strict payload audit over what will actually be sent —
`Embed.to_dict()` for the wire shape, `Embed.__len__` for the aggregate — covering per-value
caps, URL schemes, empty field names and values, duplicate embed URLs, custom-ID uniqueness and
length, row and control structure, and attachment capacity. `sd.audit_classic_payload`
is the same check, callable directly.

Duplicate non-null embed URLs are refused rather than sent: Discord renders only the first such
embed, so the second would be silently invisible, which is worse than an error.

## Durability

A durable snapshot records its target's id, version, and a fingerprint of the profile. Recovery
resolves the exact target before rebuilding the mount, so a target that has since changed its
capabilities or limits is refused rather than used to rebuild a render it was never fitted to.
The two built-in targets are registered by default; a custom target needs explicit
registration:

```python
from squid_discord import Target, TargetRegistry
from squid_layouts.planning.limits import ClassicLimits

compact = Target.classic(limits=ClassicLimits(embeds=2))
targets = TargetRegistry(compact)
mount = components.restore(snapshot, targets=targets, access=sd.Everyone())
```

## Limits and where they come from

The values in `ClassicLimits` follow Discord's
[Message Resource](https://docs.discord.com/developers/resources/message) and
[Component Reference](https://docs.discord.com/developers/components/reference); the
irreversible Components V2 transition is described in the
[Components Overview](https://docs.discord.com/developers/components/overview). The attachment
cap of 10 is a conservative library cap: Discord's message documentation does not currently
state a number.

## Non-goals

- Preserving an arbitrary `View`'s `interaction_check`, `on_error`, timeout, or navigation
  stack. Legacy application functions are reusable; legacy view ownership is not.
- Editing a Components V2 message back into classic mode. Discord does not offer it.
- Pixel-identical embed reproduction from semantic input.
- Making V2-native extensions work without a classic or portable fallback. A `Panel`,
  `Section`, or `Gallery` is refused by name under the classic target rather than quietly
  reinterpreted as an embed.
- Two independently mounted regions in one classic message.
- Choosing a message mode automatically. The author picks a target.
- Treating embeds as an application state store.

## Not yet verified live

Everything above is covered by synthetic tests and discord.py cross-checks. A live
classic-to-classic edit and a live classic-to-V2 replacement against a real message have not
been run, and remain the one piece of end-to-end evidence this target does not have.
