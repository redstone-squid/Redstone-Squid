# 53 — Adopting an unsent discord.py view

## Problem

The migration ladder in `../../../../packages/squid-layouts/docs/migrating.md` has a missing rung. A host
can contribute a measured region to a view it still owns ([35](35-discord-v2-fragments.md)),
keep persistent custom ids with a `Router` ([14](14-routed-actions.md)), or hand a whole
message to a `Mount` — but the step from the second to the third is a rewrite. Every control,
every piece of view state, and every callback moves at once, and until it does, the screen gets
none of the budget measurement the package exists for.

The audience is a bot that still has classic `discord.ui.View` screens: a paginator, a settings
panel, a confirm dialog. Its callbacks mutate `self`, flip `disabled`, and end in
`await interaction.response.edit_message(view=self)`. That logic is fine as it stands; its
*layout* is what Squid could measure.

**This repository is not that audience, and the plan does not pretend otherwise.** `../../../../squid`
contains zero `discord.ui.View` subclasses — all thirteen hosts are Components V2 `LayoutView`s
built on `ErrorHandledLayoutView` / `ExpiringLayoutView` (`squid/bot/errors.py:469,483`), which
§2 refuses on purpose. The six hand-rolled selects an earlier draft of this plan cited
(`squid/bot/voting/poll_wizard.py:235,258,281`, `squid/bot/claims_view.py:408`,
`squid/bot/submission/ui/components.py:30,60`) are real, but five of their six hosts are
constructed only from `../../../../tests`: production already runs the `sl.Component` twins beside them
(`PollConfirmationComponent`, `ClaimReviewComponent`, `SubmissionFormComponent`,
`BuildEditComponent`, `PagedList`). Adoption is a package capability with no in-repo consumer,
so it ships against synthetic fixtures and the live gate is its only real-world signal.

## Relation to the standing rejection

[90](../../squid-ui-redesign/90-deferred.md) rejects `compose(into=view)` / adopting existing discord.py views, and the
2026-08-22 revisit refined it:

> *Adoption* — Squid and a live view both claiming lifecycle or edit ownership of one
> message — stays rejected. *Fragment composition* — the host stays the sole owner while
> Squid measures it and contributes a sessionless, fully planned region to what is left — is
> [35](35-discord-v2-fragments.md), and is the supported incremental boundary.

That rejection is about a **live** view: one that has been sent, owns a message, and will edit
it. This plan does not touch that case and does not reopen it.

An **unsent** view owns no message and claims no lifecycle. It is a bag of items and callbacks
that has not yet met Discord. Squid can read those items, build its *own* exact primitives from
them, become the sole writer, and leave the legacy object as a model plus a set of handlers.
Renderer ownership — the property 90 says "is what keeps budget measurement sound" — is fully
preserved, because Squid constructs every item it draws.

| | owns the message | Squid measures it | status |
|---|---|---|---|
| `contribute(document, to=view)` | the host view | yes | shipped ([35](35-discord-v2-fragments.md)) |
| adopting a **live** view | contested | unsound | **stays rejected** |
| adopting an **unsent** view | Squid, solely | yes | this plan |

### What "unsent" actually tests

An earlier draft made `view.message is not None` the load-bearing check. **That check does not
work.** discord.py never sets `View.message` — grepping the installed 2.7.1 tree for an
assignment to it returns nothing. It is a convention bots follow by hand
(`self.message = await ctx.send(view=self)`), not a framework fact, so a view that had been sent
by a bot which does not follow the convention would sail straight through.

The framework fact is `View.is_dispatching()`, public since 2.0 and documented as *"Whether the
view has been added for dispatching purposes."* It returns true exactly when
`_start_listening_from_store` has registered the view with the connection's `ViewStore`, which
is what sending a message with a view does. `adopt()` refuses on:

- `view.is_dispatching()` — sent, and Discord will route clicks to it;
- `view.is_finished()` — already stopped, so its handlers are retired;
- `getattr(view, "message", None) is not None` — the convention, kept as a cheap second signal
  rather than as the primary one.

Those three lines are what make this a narrowing of the recorded rejection rather than a
reversal of it, and 90 is amended to say so rather than having the entry deleted.

## Design

### 1. Shape

```python
def adopt(
    view: discord.ui.View,
    *,
    keys: Callable[[discord.ui.Item[Any]], str] | None = None,
    discard_timeout: bool = False,
) -> Component
```

New `discord/adoption.py`. It returns a `Component`, not a `Mount`, so an adopted view composes
with `MountDefaults`, [51](51-screens.md)'s `Screen`, `Navigator`, and
`self.boundary(child, key=…)` exactly like anything else — including being embedded as one
region of a larger Squid screen, which is how a migration finishes.

`discard_timeout` is for §6.

### 2. Translation

`view.children` become exact primitives. Exact primitives are the escape hatch for
target-specific structure (`primitives/__init__.py:1`): the planner validates them rather than
reinterpreting them, and a node with no `Variants` ladder and no `Overflow` policy has nothing
to give up (`primitives/nodes.py:435,479`). That is the right semantics here — the author
already chose this layout, and the engine's job is to measure it.

| discord.py item | primitive |
|---|---|
| `Button` with a `callback` | `Button` |
| `Button` with a `url` | `LinkButton` |
| `Select` (string) | `SelectMenu` + `Option` |
| `UserSelect` / `RoleSelect` / `ChannelSelect` / `MentionableSelect` | `EntitySelect` ([52](52-entity-selects.md)) |
| `Button` with a `sku_id` | refused — no portable premium-button node |
| `DynamicItem` | refused — that is a `Router` ([14](14-routed-actions.md)) |
| a `LayoutView`, or any layout item | refused |

Layout items are refused on purpose: they are *content*, and content is what the semantic layer
is for. A `LayoutView` full of `TextDisplay` has nothing to gain from adoption and everything to
gain from `sl.section` — or, if it must stay a `LayoutView`, from `contribute()`. The error says
that. Refusals raise `AdoptionError` naming the item and its attribute.

**Rows.** An earlier draft said `item.row` is "preserved as `primitives.Row` so the legacy layout
is reproduced verbatim." Half of that is impossible: `Row.items` is typed
`tuple[LinkButton | Button | RoutedButton | RawItem, ...]` (`primitives/nodes.py:369`) and
`planning/v2.py:106` raises `LayoutInvariantError` for anything else, so neither select
primitive can go inside a `Row` — both say so themselves ("occupies its own row",
`nodes.py:178,193`), and [52](52-entity-selects.md):50 states it outright.

What is actually reproduced is discord.py's own packing. `_ViewWeights` sorts children with
`row=None` last, places explicitly-rowed items at their row, and packs the rest into the first
row with space, at width 1 per button and 5 per select. `adopt()` replicates that arithmetic
from public attributes (`item.row`, and width by item type) and emits, in ascending row order:
a `Row` per row of buttons, and a bare `SelectMenu`/`EntitySelect` per row holding a select.
The result is what discord.py would have drawn; the mechanism is a re-derivation, not a
pass-through of the private `_rendered_row`.

### 3. Keys

`item.custom_id` when the author set one — it is stable and intentional — otherwise
`f"adopted-{index}"`. discord.py generates a random `custom_id` per instance when none is given,
so it cannot be trusted as identity; `item._provided_custom_id` is the flag that says which
happened, and it is exactly the distinction this needs. `keys=` overrides both. Author ids are
stripped of `.` (the boundary-path separator) and any duplicate key, however derived, raises
`AdoptionError` rather than silently letting two controls share a handler.

**The positional fallback is a hazard, and the docstring says so.** Real callbacks call
`clear_items()` + `add_item()` and rebuild the item tree on every interaction. §4's re-read of
`view.children` handles that correctly, but `adopted-{index}` is identity-by-position: a rebuild
that *reorders* controls silently migrates mount-held per-control state onto a different
control. A view that rebuilds and whose items have no author-set `custom_id` should pass `keys=`.

### 4. Re-render, and the mutation seam

The view is held as `sl.state(view, opaque=True)` — a collaborator the component holds and never
persists, which is exactly what `opaque=True` is for ([41](41-reactivity-cells.md)). `render()`
re-reads `view.children` every time, so a callback that rebuilds the tree is seen.

Legacy callbacks mutate the view in place (`self.next.disabled = True`), and no tracked read can
see that. `Component.mutated(collaborator)` (`runtime/component.py:306`) already exists for
precisely this situation, so the adapter calls `self.mutated(self._view)` after every callback
returns. No new reactivity machinery.

**Rollback.** `mutated`'s own docstring is explicit: *"it cannot roll the change back."* When an
adopted callback raises, the mount rolls back component state and reaches `on_error` as usual,
but the legacy view keeps whatever the callback wrote to it before raising. This is the one
place adoption is genuinely weaker than a native component, it is inherent to holding a mutable
collaborator, and it is stated in the docstring and the migration guide rather than papered over.

### 5. The interaction proxy

This is the engineering, and where the design can rot if it is not pinned. Callbacks receive a
proxy over the Discord interaction:

| call | behaviour |
|---|---|
| `response.edit_message(view=<the adopted view>)` | records intent, performs no HTTP; the mount flushes the re-render |
| `response.edit_message(view=<anything else>)`, or with any other keyword | `AdoptionError` — that is a different screen, or a payload the mount owns |
| `response.defer(...)` | `ActionResponder.acknowledge()` |
| `response.send_message(...)` | `ActionResponder.notice(...)`, `ephemeral` mapped to `Visibility.PRIVATE`/`PUBLIC`; a `view=` or `embed=` keyword raises |
| `response.send_modal(modal)` | wraps `modal.on_submit`, then `ActionResponder.send_modal(modal)` — which already accepts a bare `discord.ui.Modal` (`discord/actions.py:39`) |
| `response.is_done()` | the proxy's own flag, so a swallowed `edit_message` still reads as answered |
| `followup.send(...)` | acknowledges the real interaction if a swallowed edit left it unanswered, then passes through: a different message, legitimately the caller's |
| `.user` / `.guild` / `.data` / `.client` / `.channel` | pass through |
| `.message.edit(...)` / `.edit_original_response(...)` / `.delete_original_response()` | `AdoptionError` — a second writer |

`edit_message(view=self)` is the single most common line in a discord.py callback, and
interpreting it as "I am done mutating; flush" is what makes adoption worth building. The
refusals are loud for the same reason: a silently swallowed second write is the failure mode 90
rejected adoption to avoid, and a raised error at the migration boundary is the opposite of that.

Swallowing the edit leaves the *real* interaction response unconsumed, which is deliberate: the
mount then answers it with `response.edit_message` itself, so the click costs exactly one HTTP
call. `notice`, `defer` and `send_modal` do consume it, and `Mount.flush` falls back to editing
through the followup — the path `native()`'s docstring already describes.

**`native()`.** That docstring (`discord/actions.py:170-173`) says *"Do not drive `.response`
yourself: the mount owns this interaction's response lifecycle."* The proxy is the sanctioned
exception, and this plan amends the docstring to name it rather than leaving two documents in
contradiction.

**The modal round-trip.** A callback that opens a modal does its view write from the *modal
submit*, which is a second interaction the proxy never wrapped —
`squid/bot/voting/poll_wizard.py:226-232,303` is exactly this shape and it is common in the
wild. Left alone, that submit calls `edit_message(view=self)` on an interaction Squid does not
own: a live second writer, the precise thing 90 rejected. So `send_modal` wraps
`modal.on_submit` in the same proxy, over a fresh `ActionResponder` for the submit interaction,
and after it returns calls `self.mutated(view)` and `await mount.refresh()` — the mount owns the
message, so it re-renders through its own `EditHandle` and never needs the modal's interaction
for anything but an acknowledgement.

A modal submit therefore runs *outside* the mount's dispatch funnel: no author lock, no
generation check, no transaction. That is the same bargain [14](14-routed-actions.md):152 struck
for routed handlers ("routed handlers own their concurrency"), it is stated in the docstring,
and it is the price of not having a second writer.

### 6. View-level API

| member | behaviour |
|---|---|
| `view.stop()` | detected after each callback via the public `is_finished()`, and mapped to `ActionResponder.finish()` |
| `view.interaction_check` | awaited before the legacy callback when overridden; a `False` refuses the press. Composes with the mount's `access` — both must pass |
| `view.on_error` | called when overridden, with the proxy and the offending item, exactly as discord.py would; otherwise the error propagates to the mount's `on_error` |
| `view.timeout` | ignored — the mount owns the timeout, with its own expiry policy and disable-on-finish behaviour |
| `view.on_timeout` | **refused** at `adopt()` when overridden, unless `discard_timeout=True` |

`on_timeout` is refused rather than ignored because an override routinely does real cleanup —
releasing a lock, writing a row, cancelling a job — and dropping that silently is data loss
wearing a migration's clothes. `discard_timeout=True` is how a caller says the cleanup is
genuinely disposable. The timeout *number* is still never inferred: a mount's timeout interacts
with expiry policy, session lifetime and disable-on-finish, and silently adopting a view's value
would produce surprises in all three.

## Docs

- `../../squid-layouts-redesign/90-deferred.md`: the entry is already amended with the live/unsent distinction; the
  2026-08-23 paragraph is corrected to cite `is_dispatching()` rather than `view.message`.
- `packages/squid-layouts/README.md:57-58`: "adopting an arbitrary existing `discord.py` view is
  intentionally unsupported" becomes "adopting a *live* view"; adoption joins the list of
  adoption routes at 60-67 as a fourth entry.
- `../../../../packages/squid-layouts/docs/migrating.md`: a section between "Keep persistent custom IDs with
  a Router" (`:97`) and "Hand one whole message to a Mount" (`:128`) — adoption is literally the
  rung between those two. It carries the three warnings a reader needs: the timeout is the
  mount's, in-place mutations do not roll back, and a rebuilding view wants `keys=`.
- `discord/actions.py`: `native()`'s docstring names the proxy as the exception to its rule.

## Considered, not done

- **Adopting a live view.** The standing rejection. Unchanged.
- **`view.message is not None` as the sent-check.** It is a convention, not a framework fact;
  `is_dispatching()` is the real one. Kept only as a secondary signal.
- **Adopting a `LayoutView` whose children are all `ActionRow`s of buttons.** Translatable in
  principle, but it is the case `contribute()` already serves without any of this machinery, and
  supporting it would make "adoption is for classic views" stop being true.
- **A `BridgedView` base class** that lets a legacy view keep ownership while hosting Squid
  regions. That is `contribute()` with a class around it; [35](35-discord-v2-fragments.md)
  already ships the capability and the README already documents the stateless-region rule.
- **Best-effort tolerance of unsupported calls.** Every refusal in §5 could instead be a warning
  and a pass-through. That reintroduces two writers on one message for the sake of convenience
  during a migration that is supposed to end.
- **Auto-translating layout items.** See §2; the semantic layer is the answer and a lossy
  automatic mapping would hide that.
- **Routing the modal submit through the mount's dispatch funnel.** It would need a `FormSpec`
  the adopted modal does not have. Refreshing after it is the honest alternative, and §5 says
  what is given up.

## Verification

Adversarial, because the proxy is the part that rots:

- every refused call raises `AdoptionError` and names the offending expression;
- a dispatching view refuses at `adopt()`, a finished view refuses, and a view carrying the
  `message` convention refuses;
- a `LayoutView` refuses and the message names `contribute()`;
- an overridden `on_timeout` refuses, and passes under `discard_timeout=True`;
- the canonical paginator — `self.page += 1`; `self.next.disabled = …`;
  `await interaction.response.edit_message(view=self)` — renders, re-renders and disables
  correctly with **zero** HTTP calls issued by the legacy object;
- a callback that raises reaches the mount's `on_error`, and an overridden `view.on_error`
  intercepts it first;
- `view.stop()` inside a callback finishes the mount;
- rows: explicit `item.row`, auto-packed buttons, and a select forced onto its own row each land
  where discord.py's `_ViewWeights` would have put them;
- a modal opened from a callback re-renders the mount on submit, and the modal's own
  `edit_message(view=self)` issues no HTTP;
- `conform(strict=True)` passes on the adopted scene;
- keys stay stable across re-render for author-set `custom_id`s, and duplicates raise;
- a view holding a `ChannelSelect` adopts, with `channel_types` and `default_values`
  round-tripping — the [52](52-entity-selects.md) dependency, asserted rather than assumed.

Then `tests/test_adoption.py`, `tests/test_mount.py`, `tests/test_conform.py`,
`just typecheck`, `git diff --check`.

Live gate: a real legacy paginator through `adopt()` against a test guild — the message edits
once per click, and the legacy object issues no HTTP of its own. That the translation is
faithful is testable offline; that the result *feels* like the original view is not. This gate
carries more weight than usual, because the repo has no adoptable view to exercise the path.

## Status

Designed 2026-08-23, revised the same day after auditing every claim against the tree: the
sent-check was wrong, the row claim was unimplementable, the modal round-trip was unanswered,
the rollback claim was false, and the motivation cited views this repo has already migrated
away from. Depends on [52](52-entity-selects.md) for `EntitySelect`. Last of the three, and the
largest.
