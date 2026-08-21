# 18 — Forms: `FormSpec`, funnel submission, and the retry loop

## Problem

Modal handling is the last UI surface the framework does not own:

- Seven `ErrorHandledModal` subclasses across four host modules, fourteen `send_modal`
  sites. Each hand-rolls field construction, prefill, parsing, and error handling.
- `SubmitEvent` is exported and never dispatched; `ModalSpec` has zero production
  consumers (plan 02's findings, unchanged since).
- The write-back path leaves the funnel. `PollConfirmationComponent.set_duration` takes
  a raw `discord.Interaction` and calls `mount.flush(interaction)` — no generation
  check, no `ActionPolicy`, none of what plan 01 bought.
- Validation retry is hand-rolled per modal and loses the reader's input:
  `CustomDurationModal` parses, sends an ephemeral error, and the attempted value is
  gone.
- `modal.py` wraps text inputs only, while modals now accept string/user/role/channel
  selects, file uploads, radio and checkbox groups (docs.discord.com components
  reference, checked 2026-08-21). The field vocabulary below maps nearly 1:1.

## Design

> A form's schema is portable; its entry point is content; its presentation is the
> frontend's decision.

1. **`FormSpec` value core**: a frozen schema — title, fields, prefill — executing plan
   02's reserved move ("promote `ModalSpec` to a frontend-neutral `FormSpec` … would
   need `SubmitEvent` to actually be dispatched first"). Both halves land here.
   `ModalSpec`/`conform_modal` remain the clamp gate under the Discord adapter.

2. **Two entry points, one machinery.** As a node, `sl.form(spec, key=…)` materializes
   on Discord as a button that presents the modal — the same shape as `Details`: a
   control plus deferred content; HTML renders it inline when that renderer grows up.
   Imperatively, `responder.present_form(spec)` from any handler — today's fourteen
   sites' shape.

3. **Submission goes through the funnel.** A modal submit dispatches `SubmitEvent`
   through the mount's dispatch path — generation checks, `ActionPolicy`, handle
   renewal — closing the `set_duration` hole.

4. **Validation**: typed fields parse themselves; a `validate(self)` hook runs only
   when every field parsed (no Django-style partial cleaned-data — cross-field logic
   over half-parsed values is a foot-gun); errors are a flat
   `FieldError(key, message) | FormError(message)` list.

5. **The retry loop is framework-owned.** Verified against the interaction docs: the
   `MODAL` callback is "Not available for `MODAL_SUBMIT` and `PING` interactions", so
   the only possible round trip is submit → ephemeral notice rendering the errors +
   a Try again button → re-present the modal prefilled with the attempted values. That
   loop is pure mechanics, which is why it lives here. Per-form policy: `retry`
   (default) or `accept_and_mark` (submission lands; the panel renders the errors).

6. **`sl.Form` sugar**: descriptor fields compile to a `FormSpec`; submitted values
   bind to typed attributes; `on_submit` defaults to a method on the class. Both
   layers are public because dynamic forms — a field per configured role — cannot be
   class attributes; because plan 02 reserved the value shape; and because it mirrors
   the package's own idiom, `FormSpec : sl.Form :: LayoutNode : Component`.

7. **Field inventory.** Portable core: `Text`, `TextArea`, `Int`, `Float`, `Duration`,
   `Date`, `Choice`, `Bool`. In `sl.discord`: `Entity` (user/role/channel), `File`.
   Extension fields follow the `Extension` primitive's pattern: an optional portable
   fallback field (an `Entity` may fall back to a `Text` taking an id); without one,
   presenting the form on another frontend is a planning error, never a silent skip.

8. **Modal budget**: 1–5 components per modal. A form larger than its target's form
   budget is a planning error; chunking into steps is `Wizard`'s explicit job
   (plan 19), never automatic.

## Rejected

- **Method-only submit** (forced Django style): dynamic forms need the value layer
  public.
- **Callback-only submit**: typed binding gets bolted on and the retry loop has no
  obvious owner.
- **Auto-chunking a >5-field form into modal steps**: magic; `Wizard(Step(…), …)` is
  the honest spelling.
- **Form as response-intent only**: keeps Form out of the semantic vocabulary and
  leaves non-Discord frontends nothing; `present_form` stays, but as one of two doors.

## Migration targets

`CustomDurationModal` first — smallest, and it exercises the typed-parse + retry loop
path end to end. Then `PollModal` (create/edit prefill), `SubmissionModal`,
`SubmissionDetailsModal`, `ProfileEditModal`, `RoleWeightModal`, `VoteEmojiModal`.
`set_duration`'s raw-interaction path is deleted with its caller.

## Verification

- Package tests: descriptor compile; parse failure → `FieldError`; `validate` gating;
  retry re-presents attempted values; `SubmitEvent` dispatch bumps generations and
  respects EXCLUSIVE; extension field without fallback errors on the HTML renderer.
- Host: `CustomDurationModal` migration lands with the framework change; the poll
  wizard's unit module covers the funnel path.
- `just typecheck`.

## Implemented API

The value layer lives in `squid_layouts.forms` and is re-exported from the package root:

```python
spec = sl.FormSpec(
    "Edit build",
    (
        sl.TextField(key="name", label="Name"),
        sl.IntField(key="ticks", label="Ticks", minimum=1),
    ),
    prefill={"name": "3x3 door"},
)

await event.present_form(spec, key="edit-build", on_submit=save)
```

`sl.form(spec, key=..., on_submit=...)` is the content entry point. It lowers to a normal
target action, while `ActionEvent.present_form` is the imperative entry point; both reach the
same adapter and mount submission path. A `FormSpec` deliberately does not own its submit
callback, so the frozen value can be reused with different destinations.

Descriptor sugar uses field-suffixed root names to avoid collisions with semantic `Choice`
and the existing lowercase factories. Short inventory names remain available under
`sl.forms`:

```python
class EditBuild(sl.Form):
    title = "Edit build"
    name = sl.TextField()
    category = sl.ChoiceField(options=(...))

    async def on_submit(self, event: sl.SubmitEvent) -> None:
        ...
```

Discord-only `EntityField` and `FileField` live under `sl.discord`. Their capabilities are
resolved while planning a form node and while presenting an imperative form. An unsupported
extension field must provide a portable `fallback`; modal targets enforce their 1–5-field
budget without automatic chunking.

Modal validation runs after the mount's ownership, generation, and action-policy gates.
Successful values are typed in `SubmitEvent.values`; `attempted` retains adapter values and
`errors` carries `FieldError | FormError`. Retry policy answers an invalid modal submit with
an ephemeral error panel and a reader-locked Try again button whose modal is prefilled from
`attempted`. `accept_and_mark` dispatches the same event with its errors intact.

## Status

Implemented 2026-08-21 in `426f8a01` (framework) and `92d21876` (first host migration).

`PollConfirmationComponent` now uses a portable `DurationField`; its raw-interaction
`set_duration`/manual `mount.flush` path is deleted. The legacy `PollConfirmation` still owns
`CustomDurationModal`, and the remaining migration targets listed above are follow-up host
cleanup rather than blockers for the framework contract.
