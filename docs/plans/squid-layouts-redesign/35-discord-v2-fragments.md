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

An already-invalid host raises `ExistingLayoutError` before Squid plans anything. Fragment
composition cannot repair arbitrary host content or decide which legacy item should be lost.
The error reports all discovered violations, including duplicate host custom IDs, overlong
strings, local row/section limits where inspectable, total component count, and total text.

### 2. Expose the planner's whole reservation boundary

Replace Discord `compose(..., reserved_text=...)` with:

```python
compose(document, reservation=reservation.cost)
```

The portable planner already accepts `ResourceCost`; the Discord convenience wrapper should
not narrow it to one axis. `reserved_text` is deleted and all in-tree consumers migrate.
Callers with non-view resources may construct or add a reservation explicitly, but ordinary
hybrid composition should use `measure` rather than hand-counting.

Unknown resource keys are rejected against the target profile instead of silently ignored.
Reservations at or beyond a hard cap either yield a legal zero-cost/empty document or raise
`UnsolvableLayoutError` under the existing planning rules; they never clamp the host.

## B. Fragment API

### 1. Compose detached top-level items

```python
fragment = sl.discord.fragment(
    document,
    alongside=existing_view,
    attachments=existing_attachment_count,
    localization=localization,
)

fragment.attach(existing_view)
await interaction.response.edit_message(
    view=existing_view,
    attachments=[*host_files, *fragment.files()],
)
```

`fragment()` is the sessionless Discord planning path with an automatically measured
reservation. It returns `Fragment`, containing:

- the ordered tuple of detached top-level `discord.ui.Item` objects;
- the `PlanResult` and normal degradation report;
- declarative assets and a `files()` method that materializes fresh `discord.File` objects;
- the reservation fingerprint against which it was planned;
- one-shot attachment state.

The renderer may produce any number of top-level items. A fragment is not forced into an
extra `Container`, because that changes semantics, consumes another component, and can make a
previously legal document illegal. `Fragment.attach` appends all items in order using public
discord.py APIs.

For authors constructing a new host view in a particular order, `fragment.items` may be
attached manually through a lower-level `detach()` operation, after which the fragment no
longer promises atomic insertion. The recommended `attach` path stays append-only because
`LayoutView` has no public positional-insert API.

### 2. Preflight before mutation

`attach(view)` remeasures the target immediately before insertion. If the host changed since
planning, it either replans through an author-supplied document factory or raises
`StaleReservationError`; it never applies a plan calculated for a different budget.

Before moving one item it validates the complete prospective view:

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

### 3. Interaction boundary

The normal sessionless composition rule remains: semantic `Button` and `Select` nodes with
component-local callbacks raise because no mount can wire them.

Allowed interactive items are:

- link buttons, which have no callback;
- `RoutedButton` and `RoutedSelect`, whose stable IDs dispatch through a separately registered
  `Router` and deliberately stay out of the host view's stored callback table.

`NativeItem` is checked after preparation. A native item whose subtree contains a dispatchable
callback is rejected in fragment mode, closing the escape hatch by which an arbitrary callback
could bypass the stated ownership boundary. Native display items and links remain valid.

The existing host's own callbacks are untouched. Its view-level access, error, and timeout
policy continues to cover them; routed controls use router middleware. A fragment never claims
that host policy protects its routed controls.

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

## D. In-tree migration and public guidance

1. Replace `squid.bot.ui.render_item` and `display_text_length` with the package fragment and
   measurement APIs.
2. Migrate build-card insertion and the remaining hybrid build-editor fallback. Compose the
   complete Squid document where all regions are already semantic; use a fragment only where
   the surrounding owner is genuinely still a legacy `LayoutView`.
3. Replace all `reserved_text=` tests and call sites with full `DiscordReservation` or
   `ResourceCost` values.
4. Document three adoption paths:
   - use Squid for one new command/screen while the rest of the bot remains unchanged;
   - insert static/routed Squid fragments into existing V2 layouts;
   - hand the complete message to `Mount` when component-local state or callbacks move.
5. Amend plan 90: arbitrary live-view adoption remains rejected, while measured sessionless
   V2 fragments are the supported incremental boundary.

The guide must say that a fragment is not a miniature mount. If two independently stateful
regions need to edit one message, the application supplies a single parent component or keeps
the legacy view as the sole owner and makes the Squid region stateless.

## Verification

- Property tests generate valid host views and Squid documents near every text/component
  boundary, then prove the attached result stays within the same limits as whole-document
  planning.
- Measurement covers nested containers, sections, action rows, selects, galleries, routed
  controls, native display items, custom IDs, and external attachment reservations.
- An invalid host, changed host fingerprint, duplicate custom ID, over-cap attachment set,
  dispatchable native item, or unsolvable remaining budget fails before host mutation.
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

Proposed 2026-08-22.
