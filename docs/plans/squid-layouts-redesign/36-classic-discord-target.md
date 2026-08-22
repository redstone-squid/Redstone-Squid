# 36 — Classic Discord target

## Problem

Plan 35 lets an existing Components V2 `LayoutView` remain in charge while Squid contributes
a measured sessionless fragment. It cannot help the most common legacy discord.py screen:

```python
await ctx.send(embed=embed, view=discord.ui.View(...))
```

Components V2 is a different message mode. Editing such a message to a `LayoutView` requires
explicitly clearing content, embeds, and attachments, and the message cannot then return to the
classic representation. A V2 fragment therefore cannot migrate one region of a V1 screen.
Transferring arbitrary items out of the existing `View` is not a substitute: view-level access,
error handling, timeout state, decorator bindings, and callback-store registration belong to
the original view and do not transfer with its child list.

CascadeUI's support for both classic `View`/embed screens and V2 `LayoutView` screens is a real
adoption advantage. Squid can match it without weakening planner guarantees, because its
architecture already separates semantic authoring from target planning and rendering. The
correct feature is a second exact Discord target, not `compose(..., into=old_view)`.

> A classic target lets Squid produce and own a legal classic Discord message. It does not
> adopt the lifecycle of an arbitrary existing view.

The payoff is a staged migration:

1. Replace hand-built embed content with a Squid semantic document while keeping the classic
   message mode.
2. Move individual controls onto Squid actions while calling the same application functions.
3. Let a classic `Mount` own the screen's reactive lifecycle.
4. Select the V2 target for a new/replaced message without rewriting the semantic component.

## Boundaries and invariants

- One message still has one lifecycle/edit owner. Plan 35's rule applies unchanged.
- Both targets use the semantic tree, adapter registry, bounded search, exact resource
  accounting, action bindings, presentation state, and degradation report.
- Classic layout decisions happen in planning. The renderer mechanically constructs content,
  embeds, and rows from an exact classic scene; it does not infer field grouping or overflow.
- The classic target may choose lossy semantic strategies when the author permits them. It does
  not pretend every V2 exact primitive has a lossless classic representation.
- Application/database state remains outside the UI package. This plan adds no shared store.
- Arbitrary callback-bearing `View` adoption remains rejected. Legacy application functions are
  reusable; legacy view ownership is not.

## A. A complete Discord presentation value

### 1. Delivery cannot remain view-only

Today `Destination` receives `(LayoutView, files)` and `EditHandle.write` edits a view plus
attachments. A classic renderer must also produce content and embeds, while V2 delivery must
explicitly clear them when converting an older message.

Introduce one complete replacement payload:

```python
@dataclass(frozen=True, slots=True)
class DiscordPresentation:
    mode: DiscordMode
    content: str | None
    embeds: tuple[discord.Embed, ...]
    view: discord.ui.View | discord.ui.LayoutView | None
    assets: tuple[Asset, ...]
```

`DiscordMode` is `CLASSIC` or `COMPONENTS_V2`. A presentation describes the whole outgoing
message surface Squid owns; absent content and embeds are explicit clears, not omitted kwargs.
It exposes `files()` to materialize fresh file wrappers and a package-private conversion to
discord.py send/edit kwargs.

`Composition` carries `presentation` beside its `PlanResult`. V2 convenience APIs may retain a
property exposing the `LayoutView`, but `Mount`, `Destination`, `DeliveryReceipt`, and
`EditHandle` operate on `DiscordPresentation` so delivery atomicity covers every rendered field.
Assets stop travelling as a parallel parameter whose ordering callers must remember.

Every destination continues to own transport policy—ephemerality, waiting, allowed mentions,
DM fallback, and files supplied by the host. It merges host files with presentation files and
rejects attachment overflow before calling Discord. `AllowedMentions.none()` remains the
package default.

### 2. Message-mode transitions are explicit

The delivery adapter knows the previous message mode when a message object is available:

- classic → classic edits content, embeds, view, and attachments as one payload;
- classic → V2 clears legacy content/embeds and installs the `LayoutView`;
- V2 → V2 edits the layout normally;
- V2 → classic raises `DiscordModeError` before HTTP because Discord's Components V2 flag is
  not reversible.

An interaction response with no readable source message still carries its intended mode in the
standing handle after first delivery. Recovery stores the mode with its locator. A `Mount` has
one target for its lifetime; changing targets means opening a replacement mount, not mutating a
live mount's renderer under its action bindings.

## B. Classic target profile and exact scene

### 1. Target capabilities and limits

Add `sl.discord.CLASSIC_TARGET` beside a renamed/explicit `V2_TARGET`. Before implementation,
verify every value against current Discord API documentation and discord.py behavior. The
classic target accounts separately for at least:

- message content characters;
- embeds per message and aggregate embed characters;
- embed title, description, field count/name/value, footer, and author limits;
- action rows, row width, and total interactive controls;
- button/select labels, placeholders, options, values, and custom IDs;
- attachments and image/thumbnail references.

The planning layer currently accepts a generic `ResourceCost` but several solver and renderer
paths take `V2Limits` directly. Extract target-independent cap lookup and keep typed target
limit tables at the adapters. Modal limits remain Discord-wide where V1 and V2 share them.
No classic strategy may borrow the V2 totals of 40 components or 4,000 display characters.

Cross-check limits that discord.py enforces locally and retain strict payload audits for string
or aggregate limits it leaves to the API, following the existing V2 limits test pattern.

### 2. Semantic adaptation

Classic adapters nominate exact candidate representations rather than letting the renderer
guess:

- articles/panels become one or more embeds;
- a primary heading may become an embed title, with paragraphs/lists in its description;
- field-like semantic content becomes embed fields when it fits, then description text or
  pagination under author-granted overflow policy;
- accent becomes embed colour;
- footer/attribution becomes embed footer text;
- lead media becomes thumbnail or image where its meaning survives;
- action groups become classic action rows under the five-row/width constraints;
- choices use string selects and the existing option-pagination state;
- document pagination owns both embed windows and control rows so one page is one exact payload.

Tables, galleries, sections with accessories, downloads, and other V2-rich structures need
explicit ladders. A table may become aligned text or fields; a gallery may become one image plus
links; a section accessory may become a thumbnail, link row, or ordinary text. `strict=True`
rejects any adaptation whose cost tier represents author-visible loss.

Exact V2 primitives are not silently reinterpreted when their shape has no classic contract.
They either have a documented classic adapter or require `Variants`/a semantic fallback.
`NativeItem` remains gated to the V2 target and uses its required portable fallback under
classic planning.

### 3. Classic scene protocol

Add exact scene values such as:

```python
SceneClassicMessage(content, embeds, rows, assets)
SceneEmbed(title, description, fields, footer, colour, image, thumbnail)
SceneEmbedField(name, value, inline)
SceneClassicRow(controls)
```

Names are illustrative; the essential constraint is that embed grouping, field placement,
row allocation, and pagination are already resolved before drawing. The scene carries action
references and routed IDs but no callbacks or discord.py objects, and receives canonical codec
coverage like the V2 scene.

The scene target remains `discord.components-v1` with its own version. V2 and classic scene
nodes may share portable leaf types where that keeps the codec honest, but the renderer must
never branch on semantic intent that planning failed to encode.

## C. Static and mounted rendering

### 1. Static classic composition

The public entry point is target-explicit:

```python
composition = sl.discord.classic.compose(document, localization=localization)
await destination(composition.presentation)
```

`sl.discord.classic.render_static` returns a `DiscordPresentation`, not only a `View`, because
the embeds are inseparable from the rendered result. Sessionless documents may contain links
and routed controls. Component-local actions remain an error without a mount.

A classic renderer only constructs `discord.Embed`, `discord.ui.View`, buttons, and selects
from the exact scene, then runs a strict non-mutating classic audit. Any intervention needed
after planning is a `DrawInvariantError` in tests; production may retain the existing
ugly-but-delivered conform fallback only for repairable upstream drift.

### 2. Classic mounts

`Mount` accepts a Discord target/renderer pair, defaulting to V2 for new code:

```python
mount = sl.discord.Mount(
    component,
    target=sl.discord.CLASSIC_TARGET,
    access=sl.discord.Owner(user_id),
)
```

The classic renderer creates `ClassicMountedView(discord.ui.View)` with the same mount ID,
generation-qualified custom IDs, access policy, stale-event handling, action serialization,
transaction funnel, timeout, error hook, forms, navigation, and stage → deliver → commit
semantics as `MountedView`. Buttons and selects are merely a different mechanical drawing of
the same `ActionBinding`s.

Do not fork the mount lifecycle into `ClassicMount`. The target-specific pieces are candidate
planning, renderer/view factory, payload audit, and delivery serialization. If a lifecycle
branch appears elsewhere, extract the shared operation before adding a mode conditional.

History, resources, topic refresh, sessions, and the plan-34 durable runtime work unchanged.
Durable snapshots record target ID/version and recovery refuses an unavailable or mismatched
target. Discord reconnection redraws and edits the complete classic presentation before
registering the recovered callback view.

## D. Classic fragments for host-owned screens

Once complete classic composition works, add the plan-35 equivalent:

```python
fragment = sl.discord.classic.fragment(
    document,
    alongside=sl.discord.classic.host(
        content=content,
        embeds=existing_embeds,
        view=existing_view,
        attachments=attachment_count,
    ),
)

combined = fragment.attach_to(host)
await ctx.send(**combined.to_kwargs())
```

The host snapshot is immutable data measured from—but not an adoption of—the existing payload.
Fragment planning reserves every classic resource axis. Attachment creates a new complete
presentation or appends through public `Embed`/`View` APIs only after combined preflight. It
does not mutate the host on a failed plan.

As in plan 35, fragment controls are limited to links and routed actions. The existing view's
callbacks stay under its owner. Arbitrary native callback items cannot enter through a Squid
fragment, and a fragment cannot become reactive independently of the host message.

Content and embed placement need explicit operations rather than an ambiguous `append`:

- add embeds before or after the host embeds;
- add action items into newly allocated rows after host rows;
- use message content only when the host leaves that axis available;
- never splice fields into a host-authored embed, because Squid cannot own its internal layout
  or overflow policy.

That narrower boundary still enables replacing one entire embed/card or control region at a
time while preserving host lifecycle.

## E. Migration guide and non-goals

Ship a worked classic-to-V2 example using one semantic component:

1. Hand-built embed + decorated `View`.
2. Host-owned classic message with a static/routed Squid fragment.
3. Classic Squid `Mount` whose action handlers call the unchanged service functions.
4. A newly opened V2 mount selected by changing only the target and any target-specific
   `Variants`.

The guide must distinguish reusable callback logic from callback ownership. A legacy function
can be called from a portable action with `sl.discord.native(event)` when it needs the
interaction; a decorated item or whole live view is not transferred.

Explicit non-goals:

- preserving arbitrary `View.interaction_check`, `on_error`, timeout, or navigation stacks;
- editing a Components V2 message back into classic mode;
- pixel-identical embed reproduction from semantic input;
- making exact V2-native extensions work without a classic/portable fallback;
- two independently mounted regions in one classic message;
- treating embeds as an application state store.

## Implementation sequence

1. `discord: deliver complete presentations` — presentation/mode value, destinations and edit
   handles, V2 migration, assets, and transition tests.
2. `planning: make Discord limits target-specific` — cap lookup, classic limit table, and
   official/discord.py cross-checks.
3. `scene: describe classic Discord messages` — exact scene values and canonical codec.
4. `discord: render static classic messages` — adapters, bounded planning, renderer, audit, and
   degradation tests.
5. `discord: mount classic component views` — classic wired view through the unchanged mount
   lifecycle and target-aware durability metadata.
6. `discord: compose classic fragments` — immutable host measurement, preflight combination,
   and the adoption cookbook.

## Verification

- Property tests generate semantic documents at every classic resource boundary and prove a
  successful plan satisfies per-value, aggregate embed, row, control, and attachment limits.
- Limit cross-checks exercise discord.py construction/serialization and pin every server-only
  constant to the current official documentation consulted during implementation.
- Scene round trips are canonical and contain no callbacks or discord.py objects.
- The renderer is mechanical: malformed/over-budget classic scenes fail strict audit rather
  than invoking a hidden degradation path.
- Delivery tests cover classic → classic, classic → V2 with explicit clears, V2 → V2, and
  preflight rejection of V2 → classic across channel, original-response, and webhook handles.
- Classic mount tests reuse the access, stale-generation, exclusive/rebase, transaction,
  timeout, navigation, form, resource, history, and failed-delivery contracts currently run
  against V2. Target-agnostic cases become parametrized rather than copied.
- Recovery preserves target identity and reinstalls classic callback registrations before a
  session becomes live.
- Classic fragments reserve existing content/embeds/rows exactly, cannot introduce callback
  ownership, and leave the host unchanged on every failure path.
- Run focused planning/scene/Discord/delivery/durability suites with `--no-cov`,
  `just typecheck`, changed-file formatting/linting, architecture tests, `git diff --check`,
  and a live V1 → V1 edit plus V1 → V2 replacement experiment. The mount/delivery blast radius
  warrants the full package suite locally; the full application suite remains CI-owned unless
  failures show wider coupling.

## Status

Proposed 2026-08-22.
