# 53 — Adopting an unsent discord.py view

## Problem

The migration ladder in `docs/migrating.md` has a missing rung. A host can contribute a
measured region to a view it still owns ([35](35-discord-v2-fragments.md)), keep persistent
custom ids with a `Router` ([14](14-routed-actions.md)), or hand a whole message to a `Mount`
— but the step from the second to the third is a rewrite. Every control, every piece of view
state, and every callback moves at once, and until it does, the screen gets none of the
budget measurement the package exists for.

The consequence is visible in this repo: `squid/bot/voting/poll_wizard.py:235-281` holds
three hand-rolled `discord.ui.Select` subclasses, `squid/bot/claims_view.py:408` a fourth,
and `squid/bot/submission/ui/components.py:30,60` two more. Each is a small state machine
whose *layout* is what Squid could measure and whose *logic* is fine as it stands.

## Relation to the standing rejection

[90](90-deferred.md) rejects `compose(into=view)` / adopting existing discord.py views, and
the 2026-08-22 revisit refined it:

> *Adoption* — Squid and a live view both claiming lifecycle or edit ownership of one
> message — stays rejected. *Fragment composition* — the host stays the sole owner while
> Squid measures it and contributes a sessionless, fully planned region to what is left — is
> [35](35-discord-v2-fragments.md), and is the supported incremental boundary.

That rejection is about a **live** view: one that has been sent, owns a message, and will
edit it. This plan does not touch that case and does not reopen it.

An **unsent** view owns no message and claims no lifecycle. It is a bag of items and
callbacks that has not yet met Discord. Squid can read those items, build its *own* exact
primitives from them, become the sole writer, and leave the legacy object as a model plus a
set of handlers. Renderer ownership — the property 90 says "is what keeps budget measurement
sound" — is fully preserved, because Squid constructs every item it draws.

| | owns the message | Squid measures it | status |
|---|---|---|---|
| `contribute(document, to=view)` | the host view | yes | shipped ([35](35-discord-v2-fragments.md)) |
| adopting a **live** view | contested | unsound | **stays rejected** |
| adopting an **unsent** view | Squid, solely | yes | this plan |

`adopt()` raises when `view.message is not None`. That check is the plan's load-bearing line:
it is what makes this a narrowing of the recorded rejection rather than a reversal of it, and
90 is amended to say so rather than having the entry deleted.

## Design

### 1. Shape

```python
def adopt(
    view: discord.ui.View,
    *,
    keys: Callable[[discord.ui.Item], str] | None = None,
) -> Component
```

New `discord/adoption.py`. It returns a `Component`, not a `Mount`, so an adopted view
composes with `MountDefaults`, [51](51-screens.md)'s `Screen`, `Navigator`, and
`self.boundary(child, key=…)` exactly like anything else — including being embedded as one
region of a larger Squid screen, which is how a migration finishes.

### 2. Translation

`view.children` become exact primitives, with `item.row` preserved as `primitives.Row` so the
legacy layout is reproduced verbatim. Exact primitives are non-degradable by contract, which
is the right semantics here: the author already chose this layout, and the engine's job is to
measure it, not to reinterpret it.

| discord.py item | primitive |
|---|---|
| `Button` with a `callback` | `Button` |
| `Button` with a `url` | `LinkButton` |
| `Select` (string) | `SelectMenu` + `Option` |
| `UserSelect` / `RoleSelect` / `ChannelSelect` / `MentionableSelect` | `EntitySelect` ([52](52-entity-selects.md)) |
| `TextDisplay` / `Container` / `Section` / any layout item | refused |

Layout items are refused on purpose: they are *content*, and content is what the semantic
layer is for. A `LayoutView` full of `TextDisplay` has nothing to gain from adoption and
everything to gain from `sl.section`. The error says that.

Refusals raise `AdoptionError` naming the item and its attribute.

### 3. Keys

`item.custom_id` when the author set one — it is stable and intentional — otherwise
`f"adopted-{index}"`. discord.py generates a random `custom_id` per instance when none is
given, so it cannot be trusted as identity without checking whether the author chose it.
`keys=` overrides both.

### 4. Re-render, and the mutation seam

The view is held as `sl.state(view, opaque=True)` — a collaborator the component holds and
never persists, which is exactly what `opaque=True` is for ([41](41-reactivity-cells.md)).
`render()` re-reads `view.children` every time.

Legacy callbacks mutate the view in place (`self.next.disabled = True`), and no tracked read
can see that. `Component.mutated(collaborator)` (`runtime/component.py:305`) already exists
for precisely this situation, so the adapter calls `self.mutated(self._view)` after every
callback returns. No new reactivity machinery.

### 5. The interaction proxy

This is the engineering, and where the design can rot if it is not pinned. Callbacks receive
a proxy over `sl.discord.native(event)`:

| call | behaviour |
|---|---|
| `response.edit_message(view=<the adopted view>, **fields)` | records intent, performs no HTTP; the mount flushes the re-render |
| `response.edit_message(view=<anything else>)` | `AdoptionError` — that is a different screen |
| `response.defer(...)` | `ActionResponder.acknowledge()` |
| `response.send_message(..., ephemeral=True)` | `ActionResponder.notice(...)` |
| `response.send_modal(modal)` | `ActionResponder.send_modal(modal)` — already accepts a `discord.ui.Modal` (`discord/actions.py:33`) |
| `followup.send(...)` | passes through: a different message, legitimately the caller's |
| `.user` / `.guild` / `.data` / `.client` / `.channel` | pass through |
| `.message.edit(...)` / `.edit_original_response(...)` | `AdoptionError` — a second writer |

`edit_message(view=self)` is the single most common line in a discord.py callback, and
interpreting it as "I am done mutating; flush" is what makes adoption worth building. The
refusals are loud for the same reason: a silently swallowed second write is the failure mode
90 rejected adoption to avoid, and a raised error at the migration boundary is the opposite
of that.

**Timeouts.** `view.timeout` and `view.on_timeout` are ignored — the mount owns the timeout,
with its own expiry policy and disable-on-finish behaviour. Stated in the docstring and the
migration guide, because it is the one behavioural difference an author will notice.

## Docs

- `90-deferred.md`: amend the adoption entry with the live/unsent distinction; the entry
  stays, and states that the live half is still rejected.
- `packages/squid-layouts/README.md:57-58`: "adopting an arbitrary existing `discord.py` view
  is intentionally unsupported" becomes "adopting a *live* view"; adoption joins the list of
  adoption routes at 60-67 as a fourth entry.
- `docs/migrating.md`: a section between "Keep persistent custom IDs with a Router" (`:97`)
  and "Hand one whole message to a Mount" (`:128`) — adoption is literally the rung between
  those two.

## Considered, not done

- **Adopting a live view.** The standing rejection. Unchanged.
- **A `BridgedView` base class** that lets a legacy view keep ownership while hosting Squid
  regions. That is `contribute()` with a class around it; [35](35-discord-v2-fragments.md)
  already ships the capability and the README already documents the stateless-region rule.
- **Best-effort tolerance of unsupported calls.** Every refusal in §5 could instead be a
  warning and a pass-through. That reintroduces two writers on one message for the sake of
  convenience during a migration that is supposed to end.
- **Auto-translating layout items.** See §2; the semantic layer is the answer and a lossy
  automatic mapping would hide that.
- **Inferring `view.timeout`.** A mount's timeout interacts with expiry policy, session
  lifetime and disable-on-finish; silently adopting a view's number would produce surprises
  in all three.

## Verification

Adversarial, because the proxy is the part that rots:

- every refused call raises `AdoptionError` and names the offending expression;
- a live view (`view.message is not None`) refuses at `adopt()`;
- the canonical paginator — `self.page += 1`; `self.next.disabled = …`;
  `await interaction.response.edit_message(view=self)` — renders, re-renders and disables
  correctly with **zero** HTTP calls issued by the legacy object;
- a callback that raises rolls back and reaches `on_error` like any mounted handler;
- `item.row` survives into `Row` grouping;
- `conform(strict=True)` passes on the adopted scene;
- keys stay stable across re-render for author-set `custom_id`s and change for generated ones
  only when the item's position changes;
- a view holding a `ChannelSelect` adopts — the [52](52-entity-selects.md) dependency,
  asserted rather than assumed.

Then `tests/test_adoption.py`, `tests/test_mount.py`, `tests/test_conform.py`,
`just typecheck`, `git diff --check`.

Live gate: a real legacy paginator through `adopt()` against a test guild — the message edits
once per click, and the legacy object issues no HTTP of its own. That the translation is
faithful is testable offline; that the result *feels* like the original view is not.

## Status

Designed 2026-08-23. Depends on [52](52-entity-selects.md) for `EntitySelect`. Last of the
three, and the largest.
