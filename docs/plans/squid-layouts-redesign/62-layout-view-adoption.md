# 62 — LayoutView adoption: exact Components V2 under Squid ownership

## Problem

Plan [53](53-view-adoption.md) lets Squid adopt an unsent classic `discord.ui.View`: Discord
controls become exact primitives, the original object remains the callback model, and the mount
becomes the sole message writer. It deliberately refuses `discord.ui.LayoutView` on the premise
that layout items are content and should be rewritten semantically or kept host-owned through
[`contribute()`](35-discord-v2-fragments.md).

That refusal leaves a migration gap. A bot can have a substantial unsent `LayoutView` whose
Discord representation is intentional and whose callbacks should be retained, while still
wanting Squid to own session lifetime, access, rendering, measurement, and surrounding flexible
content. `contribute()` expresses the opposite ownership arrangement: the native view owns the
message and Squid contributes one stateless region. Rewriting the whole view is unnecessary work
before ownership can move.

The classic invariant applies unchanged: an unsent view owns no message, so Squid may translate
it and become the only writer. A live view still dispatches and may edit its own message, so it
remains forbidden.

## Decision

Overload `sl.discord.adopt()` for an **unsent `discord.ui.LayoutView`**. Treat its complete tree as
target-exact Components V2, not as semantic Squid content:

```python
legacy = ExistingLayoutView(...)

component = sl.discord.adopt(legacy, assets=(...))

await SETTINGS.open(
    sessions,
    component,
    sl.discord.respond_to(interaction, ephemeral=True),
    opener=sl.discord.Opener.of(interaction),
)
```

```text
discord.ui.LayoutView model
        ↓ translate on every render
exact V2 primitives + declared assets
        ↓ planner measures but does not reinterpret or degrade
SceneComponentsV2
        ↓ renderer
new renderer-owned LayoutView
```

The original `LayoutView` is never sent. It remains a mutable model and callback collection. The
renderer constructs a fresh native tree from Squid's scene after every successful or failed
callback mutation, exactly as classic adoption reconstructs a `View`.

“Exact” means component kind, authored attributes, nesting, document order, row structure, and
callback behaviour are preserved. Discord-assigned numeric component ids are transport metadata
and are not preserved; custom ids remain callback identity. Text is exact Discord text, not
reclassified as `sl.heading`, `sl.paragraph`, or another semantic node.

## Public API

The existing function becomes overloaded without changing classic call sites:

```python
@overload
def adopt(
    view: discord.ui.View,
    *,
    keys: KeyFactory | None = None,
    discard_timeout: bool = False,
) -> Component: ...

@overload
def adopt(
    view: discord.ui.LayoutView,
    *,
    keys: KeyFactory | None = None,
    assets: Sequence[Asset] = (),
    discard_timeout: bool = False,
) -> Component[ComponentsV2Target]: ...
```

`KeyFactory` continues to receive a `discord.ui.Item[Any]`. For a layout tree it may be called for
every dispatchable item, including a section accessory. The classic overload does not accept
`assets`; adding files to a classic adopted control-only view remains outside its payload.

The V2 component's `render()` returns a `Document` containing exact nodes and the supplied assets.
Consequently it composes through `boundary`, `Screen`, `MountDefaults`, `Navigator`, session
durability rules, and flexible sibling components like any other component. Its target type and
exact primitives make planning for the classic target fail by capability rather than silently
converting the authored V2 structure.

The existing live/finished/message/timeout validation applies to both view families. Overridden
`on_timeout` is refused unless `discard_timeout=True`; mount lifetime remains authoritative.

## Exact translation

The adapter recursively translates the public child tree:

| discord.py Components V2 item | Squid exact representation |
|---|---|
| `Container` | `primitives.Panel`, retaining accent colour, spoiler, and child order |
| `Section` | `primitives.Section`, retaining 1–3 text displays and its accessory |
| `TextDisplay` | `primitives.Text` with `Never()` overflow |
| `Separator` | `primitives.Sep`, retaining divider visibility and spacing |
| `MediaGallery` | `primitives.Gallery` with ordered media, descriptions, and spoilers |
| `Thumbnail` accessory | `primitives.Thumbnail` with media, description, and spoiler |
| `File` | `primitives.File` linked to a supplied `Asset` |
| button `ActionRow` | `primitives.Row` in authored order |
| select `ActionRow` | the exact select primitive; its renderer recreates the required row |
| callback button | `primitives.Button` with the classic adoption proxy binding |
| link button | `primitives.LinkButton` |
| premium button | `primitives.PremiumButton` |
| string select | `primitives.SelectMenu` and exact options |
| user/role/channel/mentionable select | `primitives.EntitySelect` |

Every text-bearing exact primitive uses a non-degrading overflow policy where that primitive has
one. If the authored tree exceeds a V2 budget, planning fails with its measured violation; Squid
does not truncate a `TextDisplay`, remove gallery media, flatten a container, or choose a classic
fallback.

All supported public attributes are copied: disabled state, styles, labels, emoji, URLs, SKU ids,
placeholders, min/max values, option descriptions/defaults/emoji, entity defaults, channel type
filters, separator spacing, colours, spoiler flags, and media descriptions. Translation uses the
discord.py adapter capability profile where names or shapes vary by supported version rather than
scattering version checks through adoption.

Unsupported message components fail during `adopt()`, before mounting, with `AdoptionError`
naming their structural path and concrete type. `DynamicItem` remains refused because stable
route identity belongs to `Router`. Modal-only items (`Label`, text input, file upload), custom
third-party `Item` subclasses, and future native kinds without a pinned adapter translation are
also refused rather than passed through as `RawItem`.

## Assets and media

A `LayoutView` does not contain upload bytes, so attachment-backed media cannot be reconstructed
from the view alone. `assets=` supplies ordinary Squid `Asset` values and keeps delivery through
the existing document resource path.

- Every `attachment://name` referenced by a `File`, gallery item, or thumbnail must match exactly
  one supplied asset by `Asset.name`.
- A native `File` is matched to that asset and uses its key, name, media type, and source.
- An `http`/`https` file reference must match a supplied `StoredAsset.reference`; its declared
  metadata supplies the primitive's name and media type.
- Gallery and thumbnail HTTP URLs need no asset and are preserved directly.
- Duplicate asset keys or ambiguous duplicate names are refused by the normal document/adapter
  validation.
- Supplied assets not referenced by the adopted subtree remain legal document attachments. This
  permits an authored view to offer a download elsewhere in a flexible Squid sibling without
  splitting asset ownership between components.

The adapter never attempts to recover a previously sent Discord attachment. An already-live view
is rejected before asset resolution.

## Identity and reconstruction

The original view is traversed on every render; translated nodes are never cached across a
callback. This preserves mutations to text, media, containers, rows, options, disabled state, and
the tree itself.

Dispatchable items use the same identity hierarchy as classic adoption:

1. `keys(item)` when supplied;
2. an explicitly authored `custom_id`; or
3. a deterministic structural path such as `adopted-0.2.1`.

`.` remains the Squid boundary separator, so authored/factory keys are escaped by the existing
rule. Duplicate effective keys anywhere in the nested dispatch tree fail at `adopt()` or the first
render that introduces them. Structural keys are documented as positional: clearing, reordering,
or reparenting controls without explicit ids moves identity. Non-dispatchable content needs no
handler key and is addressed only by structural path for diagnostics.

A callback lookup traverses the **current** original tree and resolves its effective key, then
sets select values through the same discord.py adapter seam used by classic adoption. Removing a
control makes its old rendered interaction fail with the existing stale-control diagnostic rather
than dispatching to whichever item moved into its position.

## Callback ownership

Generalize the existing interaction proxy from `discord.ui.View` to discord.py's common base-view
behaviour. The contract remains:

- `interaction_check` runs before the item callback.
- Item callbacks and `on_error` run on the original item/view objects.
- `interaction.response.edit_message(view=original_view)` means “flush my mutations”; it performs
  no HTTP and marks the proxy response done.
- Editing with a different view, content, embeds, attachments, or other message fields raises
  `AdoptionError` because the mount owns the complete payload.
- Direct message edits and other second-writer paths already guarded by the classic proxy remain
  guarded.
- A modal sent by a callback receives the same proxied submit/refresh bridge as classic adoption.
- `view.stop()` finishes the mount through the responder path.
- The adapter calls `self.mutated(view)` in `finally`, so mutations made before an exception are
  reflected even when `on_error` handles the failure.

The renderer-owned `LayoutView` has the mount's timeout and dispatch machinery. The original view
never becomes dispatching, never receives a Discord message, and can never perform its own HTTP
edit.

## Composition boundary

An adopted layout is one exact V2 component and may sit beside flexible Squid siblings:

```python
class Screen(sl.Component):
    def __init__(self, legacy: discord.ui.LayoutView) -> None:
        self.legacy = sl.discord.adopt(legacy)

    def render(self):
        return (
            sl.paragraph("Flexible introduction"),
            self.boundary(self.legacy, key="legacy"),
        )
```

V1 does not place a Squid `Component` *inside* a native `LayoutView`; discord.py accepts only
native items there and no marker-item contract exists. “Explicitly flexible region” therefore
means a sibling or parent Squid boundary, not a hole hidden in the native tree. `contribute()`
continues to cover the reverse ownership arrangement.

The migration matrix becomes:

| Existing UI | Message owner | API |
|---|---|---|
| unsent classic `View` | Squid | `adopt(view)` |
| unsent V2 `LayoutView` | Squid | `adopt(view, assets=...)` |
| V2 `LayoutView` | host | `contribute(document, to=view)` |
| Squid `Component` | Squid | `Mount` / `Screen` |

No already-live `View` or `LayoutView` is adoptable.

## Not included

- No semantic inference from native content.
- No degradation or classic fallback for the adopted subtree.
- No pass-through native items or renderer reuse of the original view.
- No live-view takeover, dispatch migration, or message-handle stealing.
- No embedded Squid marker item inside a native layout.
- No `DynamicItem` conversion; use `Router`.
- No recovery of upload bytes or sent attachments from Discord.
- No promise to support a new discord.py component until its adapter translation and measurement
  are explicitly added.

## Verification

- Every supported V2 item and nested combination translates with the same authored attributes,
  ordering, nesting, and exact measured cost; discord.py cross-checks compare serialized component
  payloads while excluding server-assigned numeric ids.
- Text and media never degrade; an over-budget adopted tree fails with the exact V2 violation and
  never plans for the classic target.
- Section button accessories, row buttons, string selects, and every entity-select family dispatch
  to the original callbacks with current values.
- Callback mutations to content, nesting, controls, options, and disabled state appear in the next
  renderer-owned `LayoutView`, including mutations made before a handled exception.
- Duplicate keys, structural-key reordering, removed controls, unsupported/custom items,
  `DynamicItem`, finished/live/message-owning views, and undiscarded timeout overrides produce
  specific adoption diagnostics.
- The proxy accepts only the original-view flush, refuses every second-writer payload, bridges
  modals, runs checks/errors, and finishes when the original view stops.
- Attachment files, gallery media, thumbnails, inline assets, and stored assets resolve through
  `Document.assets`; missing and ambiguous attachment references fail at `adopt()`.
- Flexible semantic siblings can absorb pressure without changing the exact adopted subtree.
- Existing classic adoption tests and API typing remain unchanged.
- Focused adoption, assets, measurement, planner, renderer, mount, public API, adapter-profile, and
  typing tests pass; property-based supported-tree tests agree with discord.py; then run
  `just typecheck` and `git diff --check`.

## Status

Designed. Supersedes only plan 53's refusal of `LayoutView`; all classic-adoption and live-view
ownership decisions in that plan remain in force. Independent of plans 59–61.
