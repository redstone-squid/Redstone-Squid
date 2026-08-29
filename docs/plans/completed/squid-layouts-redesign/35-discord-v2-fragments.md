# 35 — Discord V2 fragments

## Problem

The redesign audit rejected `compose(document, into=view)` because adopting an arbitrary
live `discord.py` view would surrender renderer ownership and let unknown controls undermine
measurement. That conclusion bundled two different operations:

1. **Adoption** — Squid and an existing view both claim lifecycle or edit ownership of one
   message. This remains unsafe.
2. **Fragment composition** — an existing `LayoutView` remains the sole owner, while Squid
   measures its fixed contents and contributes a sessionless, fully planned set of items to
   the remaining budget. This is safe and already exists as a hand-written host pattern.

`squid.bot.ui.render_item` renders a one-node Squid document, detaches its first item, and
inserts it into a hand-assembled `LayoutView`. Build editing uses it to place an engine-solved
build card between legacy header and control rows. The caller manually computes
`reserved_text`, then runs `conform` after assembly. This proves the migration seam, but it
accounts for only the 4,000-character budget—not the 40-component budget, attachments,
duplicate custom IDs, or future resource axes. It also assumes a Squid document resolves to
exactly one top-level item.

Hand-counting also fails silently. `views.py:1330` reserves nothing at all: the build card is
planned against the whole 4,000-character budget while `_edit_row` and `_navigation_row` are
already destined for the same message. The trailing `conform` hides it by trimming solved
content, which is exactly the outcome the planner exists to avoid.

A library user improving one region of an existing Components V2 screen should not have to
move the whole screen into `Mount`. Squid should make the existing safe pattern first-class
without pretending to own the surrounding view.

## Ownership rule

> One Discord message has one lifecycle and edit owner. A fragment is a value contributed
> to that owner's next payload; it is never an independently mounted UI.

The host `LayoutView` owns:

- sending and editing the message;
- discord.py callback registration, timeout, `interaction_check`, and `on_error`;
- rebuilding its legacy regions;
- merging the fragment's attachments with its own;
- deciding when to discard or replace the fragment.

Squid owns:

- measuring the host's fixed reservation;
- adapting, planning, and drawing the contributed document;
- proving the combined hard-resource budget before mutation;
- reporting degradation inside the Squid fragment;
- routing any stateless routed controls through `Router`.

`Mount`, component-local state, component callbacks, `Reactor`, history, and session policy
are not available inside a fragment. A stateful Squid region needs ownership of the complete
message, with legacy logic called from Squid actions while it is migrated.

## Landing order

Three independently landable units, in this order:

1. **Measurement and audit** (§A.1, §C). Pure read-only inspection with consumers today:
   `conform` re-implements half of it, `discord/testing.py` the other half, and the build
   editor hand-counts what `measure` returns. No planner change.
2. **Reservation axes** (§A.2). The planner learns to reserve every resource, not just text.
   Depends on 1 for the value it consumes; plan 36 depends on this, not on §B.
3. **Fragments** (§B, §D, §E). The public fragment API and the in-tree migration.

## A. General Discord reservation

### 1. Measure an existing layout without mutating it

Add a public immutable reservation value and inspector:

```python
reservation = sl.discord.measure(existing_view, attachments=2)
```

`DiscordReservation` contains at least:

- `ResourceCost` for total nested components and display text;
- attachment slots already occupied outside the view;
- every non-null custom ID with its location;
- whether the existing view is Components V2;
- structural violations already present in the host view.

Measurement walks public item structure and never calls `conform`: the inspector must not
truncate content or mutate callbacks merely to learn the budget. The existing view must be a
`discord.ui.LayoutView`; classic `discord.ui.View` support belongs to plan 36.

Both axes have exact public primitives, so measurement is not a re-derivation:

- text is `LayoutView.content_length()`, which discord.py 2.7 already provides and
  `squid.bot.ui.display_text_length` duplicates;
- components are `len(list(view.walk_children()))`, which equals the count discord.py enforces
  at 40 — `Section._total_count` is `2 + len(children)` and `Section.walk_children` yields the
  accessory, so the public walk and the private counter agree.

The same walk costs a not-yet-attached item, which the fragment API needs for host regions the
view does not contain yet:

```python
sl.discord.cost(controls, footer_row)  # -> ResourceCost
```

`cost` is one implementation shared with `_DiscordItemExtension.prepare`
(`discord/target.py`), so the package has exactly one definition of what a component costs.

An already-invalid host raises `ExistingLayoutError` before Squid plans anything. Fragment
composition cannot repair arbitrary host content or decide which legacy item should be lost.
The error reports all discovered violations, including duplicate host custom IDs, overlong
strings, local row/section limits where inspectable, total component count, and total text.

### 2. Teach the planner the rest of the reservation

`reserved_text` is not a narrowing of a general mechanism; it is the whole mechanism.
`ResourceCost` reaches `plan()`, but only `reservation.get("display_text")` is ever read
(`planning/planner.py`), and it is spent on the text budget alone (`planning/measure.py`).
There is no component, attachment, or custom-ID reservation to expose yet. `attachments` in
`planning/limits.py` is likewise a declared limit that nothing enforces, so honouring it is
new enforcement rather than a hook-up.

The cost vocabulary is already right: `_DiscordItemExtension.prepare` emits
`{"components": ..., "display_text": ...}` and the planner consumes `"components"` as
`RawItem.component_cost`. The axis exists as extension cost and is missing only as reservation.

Implement it by deriving a reduced target from the reservation instead of threading a second
per-axis parameter. `reserved_text` is already exactly `limits.total_text - reserved`, so
`replace(limits, total_text=..., total_components=..., attachments=...)` states every axis the
same way and adds no plumbing to `_measure_once`. Two constraints on that form:

- pagination's footer reservation is subtracted from the same text budget, so it must survive
  the rewrite;
- only message-wide budgets are reducible. Local caps (`row_buttons`, `section_texts`,
  `select_options`) describe Discord's shape rather than the remaining budget, and reducing
  them would silently change what a legal document is.

The Discord wrapper then exposes the whole boundary:

```python
compose(document, reservation=reservation.cost)
```

`reserved_text` is deleted and all in-tree consumers migrate. Callers with non-view resources
may construct or add a reservation explicitly, but ordinary hybrid composition should use
`measure` rather than hand-counting.

Unknown resource keys are rejected against the target profile instead of silently ignored.
Reservations at or beyond a hard cap either yield a legal zero-cost/empty document or raise
`UnsolvableLayoutError` under the existing planning rules; they never clamp the host.

## B. Fragment API

### 1. Contribute a planned fragment

The whole ritual is one call:

```python
attached = sl.discord.contribute(
    document,
    to=existing_view,           # measured, then appended to — named once
    followed_by=(controls,),    # host items appended after the fragment, costed into the plan
    localization=localization,
)
await interaction.response.edit_message(
    view=existing_view,
    attachments=attached.attachments(host_files),
)
```

`contribute` plans against `measure(to) + cost(*followed_by)`, attaches the fragment, then
appends `followed_by` in order. It returns `AttachedFragment` and raises everything `fragment`
and `attach` raise, mutating nothing on failure.

Naming the host view once is the point. The two-step form below takes it twice — once to
measure, once to attach — and measuring against one view while attaching to another voids
every guarantee in this plan.

`contribute` does not send. Swallowing the edit would remove one more line and break the
ownership rule in the same stroke; delivery stays with the host, and `Destination` remains a
`Mount` seam.

`followed_by` is a correctness feature rather than sugar; §B.3 says why.

### 2. The two-step form

```python
fragment = sl.discord.fragment(
    document,
    alongside=existing_view,
    reserve=sl.discord.cost(controls),
    attachments=existing_attachment_count,
    localization=localization,
)

attached = fragment.attach(existing_view)
```

`fragment()` is the sessionless Discord planning path with an automatically measured
reservation. It returns `Fragment`, containing:

- the ordered tuple of detached top-level `discord.ui.Item` objects;
- the `PlanResult` and normal degradation report;
- declarative assets and a `files()` method that materializes fresh `discord.File` objects;
- the reservation fingerprint against which it was planned;
- one-shot attachment state.

`AttachedFragment` carries `plan`, `report`, `files()` and `fingerprint` through from the
`Fragment`, so the one-call form never silently drops the degradation signal. Its
`attachments(host_files)` returns the merged file list, because `[*host_files, *files()]` is
the one line a host can forget — and forgetting it breaks `attachment://` links rather than
raising.

The renderer may produce any number of top-level items. A fragment is not forced into an
extra `Container`, because that changes semantics, consumes another component, and can make a
previously legal document illegal. `Fragment.attach` appends all items in order using public
discord.py APIs.

For authors constructing a new host view in a particular order, `Fragment.release()` hands
over `fragment.items` for manual placement, after which the fragment no longer promises atomic
insertion. It is deliberately not called `detach`: the inverse of `attach` is
`AttachedFragment.remove()`, and a pair of names that look like inverses but are not is a
trap in a public API. The recommended path stays append-only because `LayoutView` has no
public positional-insert API.

### 3. Preflight and the prospective-view invariant

> The validated prospective view is the final view.

`attach(view)` remeasures the target immediately before insertion. If the host changed since
planning, it either replans through an author-supplied document factory or raises
`StaleReservationError`; it never applies a plan calculated for a different budget.

Before moving one item it validates the complete prospective view, `followed_by` included:

- all message-wide resource limits;
- local container, section, gallery, and action-row constraints;
- custom-ID lengths and uniqueness across host and fragment;
- attachment capacity including host and fragment files;
- that no fragment item is already parented elsewhere.

Only then are items detached from the renderer-owned staging view and appended. If a host
subclass nevertheless raises during `add_item`, `attach` removes anything it added and restores
the fragment's staging ownership before propagating. Existing host items are never removed or
reordered.

The returned `AttachedFragment` records exactly which items were inserted and can remove only
those items later. Removal is identity-based, so it cannot delete a host replacement that
happens to carry the same custom ID.

There is no "attach last" rule, because neither in-tree consumer can obey one: `views.py`'s
build editor and build-post view both append host rows *after* the Squid card, and append-only
attachment would move the card below its controls. `followed_by` is what makes the invariant
reachable for them.

Where a host must still mutate after contributing — a region it rebuilds every render, an item
added by a subclass — the documented escape hatch is
`sl.discord.audit(view).raise_if_invalid()` immediately before the edit, and
`AttachedFragment.fingerprint` stays re-checkable at send time. Without one of the two,
fragment composition is weaker than the trailing `conform` it replaces.

### 4. Interaction boundary

The normal sessionless composition rule remains: semantic `Button` and `Select` nodes with
component-local callbacks raise because no mount can wire them.

Allowed interactive items are:

- link buttons, which have no callback;
- `RoutedButton` and `RoutedSelect`, whose stable IDs dispatch through a separately registered
  `Router` and deliberately stay out of the host view's stored callback table.

`NativeItem` is checked after preparation. A native item whose subtree contains a dispatchable
callback is rejected in fragment mode, closing the escape hatch by which an arbitrary callback
could bypass the stated ownership boundary. Native display items and links remain valid.
`Item.is_dispatchable()` is the public predicate for both checks.

The existing host's own callbacks are untouched. Its view-level access, error, and timeout
policy continues to cover them; routed controls use router middleware. A fragment never claims
that host policy protects its routed controls.

The converse also holds, and the guide must say so. `RoutedItem.is_dispatchable()` is `False` —
that is what keeps routed controls out of the host's dispatch table — so a click on a fragment
control never resets the host view's timeout and never runs its `interaction_check`. A host
with the default 180-second timeout therefore expires, possibly running `on_timeout` to disable
its own controls, while the Squid region keeps answering. A host carrying routed fragments
should be `timeout=None`; where it is not, its two regions expire independently.

## C. Pure audit shared with `conform`

Extract the read-only half of `conform` into:

```python
report = sl.discord.audit(view, attachments=0)
report.raise_if_invalid()
```

`audit` returns structured violations and performs no truncation. `conform` becomes an explicit
repair adapter over the same measurements: it may trim values for a wholly host-authored view,
but fragment preflight always uses `audit` because mutating legacy content would violate the
ownership rule.

Not every violation is repairable. Component overflow, duplicate/custom-ID errors, invalid
local structure, and attachment overflow remain hard failures. Existing human-readable
`conform` intervention strings may be projections of the structured report rather than a
second validation implementation.

## D. Errors

`ExistingLayoutError` and `StaleReservationError` subclass `LayoutError` (`squid_layouts.errors`),
so a library user can catch the package's failures in one place.

`LimitViolationError` and `StaleHandleError` predate that rule and inherit from `Exception`.
Rehoming them under `LayoutError` is a widening — every existing `except` clause keeps
matching — so this plan does it rather than leaving the hierarchy half-applied.

## E. In-tree migration and public guidance

1. Replace `squid.bot.ui.render_item` and `display_text_length` with the package fragment and
   measurement APIs; `display_text_length` becomes `LayoutView.content_length()` at the one
   place that still needs a raw number.
2. Migrate build-card insertion and the remaining hybrid build-editor fallback. Compose the
   complete Squid document where all regions are already semantic; use a fragment only where
   the surrounding owner is genuinely still a legacy `LayoutView`. The build-post view's
   missing reservation (`views.py:1330`) is fixed by `followed_by`, not by a hand-written count.
3. Replace all `reserved_text=` tests and call sites with full `DiscordReservation` or
   `ResourceCost` values.
4. Give the sessionless path an asset story. `Fragment.files()` cannot exist while
   `_attachment_files` and `_linked_file_assets` are private to `mount.py` and `Composition`
   is view-plus-plan; extract them, and let `Composition` carry assets too. A `fragment()` that
   can attach a file while `compose()` cannot is incoherent.
5. Document three adoption paths:
   - use Squid for one new command/screen while the rest of the bot remains unchanged;
   - insert static/routed Squid fragments into existing V2 layouts;
   - hand the complete message to `Mount` when component-local state or callbacks move.
6. Amend plan 90: arbitrary live-view adoption remains rejected, while measured sessionless
   V2 fragments are the supported incremental boundary.

The guide must say that a fragment is not a miniature mount. If two independently stateful
regions need to edit one message, the application supplies a single parent component or keeps
the legacy view as the sole owner and makes the Squid region stateless.

## Verification

- Property tests generate valid host views and Squid documents near every text/component
  boundary, then prove the attached result stays within the same limits as whole-document
  planning.
- Measurement covers nested containers, sections, action rows, selects, galleries, routed
  controls, native display items, custom IDs, and external attachment reservations, and agrees
  with `LayoutView.content_length()` and discord.py's own 40-component counter.
- A reservation on every axis is honoured: text, components, and attachments each force the
  planner to degrade or fail rather than overrun, and local caps are unaffected by it.
- An invalid host, changed host fingerprint, duplicate custom ID, over-cap attachment set,
  dispatchable native item, or unsolvable remaining budget fails before host mutation.
- `contribute` plans against host plus `followed_by`, and the view it validated is byte-for-byte
  the view that is sent; a synthetic `LayoutView.add_item` failure while appending `followed_by`
  rolls the whole contribution back.
- A synthetic `LayoutView.add_item` failure proves partial attachment rolls back and the
  fragment remains attachable.
- Routed fragment controls dispatch exactly once through `Router`; component-local controls
  remain rejected.
- `files()` is repeatable and returns fresh file wrappers; attaching items is one-shot and
  identity-based removal cannot touch host replacements.
- The migrated build editor no longer hand-counts text and its complete assembled view passes
  strict `audit` without a repair intervention.
- Run the focused compositor, planner, conform, routing, build-handler, and submission UI tests
  with `--no-cov`, then `just typecheck`, changed-file formatting/linting, architecture tests,
  and `git diff --check`.

## Status

Shipped 2026-08-22 — `contribute` lives at `discord/fragments.py:235`, with the classic-target
form at `discord/classic.py:189`. Amended 2026-08-22 after verifying every claim against the tree: the
reservation axes, the attachment limit, and the sessionless asset path do not exist yet, and
the original preflight guarantee did not hold for either in-tree consumer.
