# Spike 74 — authored versus lowered text

Plan 74 phase 4. One set of node classes serves two stages: an authored node's `label`,
`content`, `placeholder` and `description` are `TextLike`, and the same fields are factually
`str` once `lower_semantics` has resolved them against the localization. The readers downstream
— `realization.py`, `classic.py`, `v2.py`, `dialect.py` — call `len()`, `.strip()` and
`trim_keep` on them, which is 48 errors and no way to tell the two stages apart.

## The invariant, measured first

Before choosing a representation, the premise itself needed checking: *does* lowering resolve
every text field, or do some pass through untouched? A document holding a `Message` (the
deferred, unresolved `TextLike`) in every text-bearing position was run through
`lower_semantics` and the output searched for surviving `Message` instances.

    UNRESOLVED: []
    NODE TYPES: ['Text', 'Heading', 'Footer', 'Code', 'Lines', 'RoutedButton',
                 'RoutedSelect', 'Thumbnail']

Nothing survives. `Node[str]` is a true statement about the lowering output, not an aspiration.
That probe is kept as `tests/test_lowering_resolves_text.py`, because it is the invariant every
`str` annotation downstream now rests on.

## Candidate A — parameterize the text-bearing leaves

`class Option[TextT: TextLike = TextLike]`, and the same for the other fifteen classes, with
`type Node[TextT: TextLike = TextLike]` over them.

Measured, against a 524-error baseline:

| step | errors |
|---|---:|
| baseline | 524 |
| parameterize one class (`Option`) | 524 |
| parameterize all sixteen leaves | 525 |
| parameterize the `Node` union | 525 |
| declare the dialect boundary `Node[str]` | 528 |
| declare `SemanticLowering.nodes` as `Node[str]` | 527 |

The parameter itself is free, which is the important result: because `TextT` defaults to
`TextLike`, every existing bare annotation keeps meaning exactly what it meant, and the frozen
dataclasses are covariant in it, so `Option[str]` flows into anything expecting `Option`. No
churn at author-facing sites.

The cost is not annotations, it is diagnostics. `Node[TextLike]` and `Node[str]` both render as
`Node` in pyrefly's output, so a mismatch reads as ``Argument `tuple[Node, ...]` is not
assignable to parameter `nodes` with type `tuple[Node, ...]` ``. Threading the parameter through
the lowering internals is mechanical, but every error along the way is spelled that way.

### What refuted it

Parameterizing the sixteen leaves was error-neutral across every package source file — the src
error set was byte-identical, 146 both ways. Exactly one new error appeared, in `squid/`:

    squid/bot/diagnostics_view.py:88
    Argument `Message` is not assignable to parameter `placeholder` with type `str | None`
    in function `squid_ui.primitives.nodes.SelectMenu.__init__`

The call is legitimate, and idiomatic:

    sl.primitives.SelectMenu(
        options=tuple(
            sl.primitives.Option(label=report.reference, value=str(index), ...)
            for index, report in enumerate(self._reports)
        ),
        placeholder=L(t"Choose an error to open"),
    )

The option labels are data — an error reference, nothing to translate — while the placeholder is
prose that must be. One `TextT` per class forces every text field on that class to the same kind,
so it forbids the mix. Giving each field its own parameter (`SelectMenu[PlaceholderT,
OptionLabelT, ...]`) is not a serious proposal.

This is a property of the authored stage specifically: an author mixes settled and deferred text
freely, and only after lowering is a node uniformly one kind. A single per-class parameter cannot
say that, because it constrains both stages at once.

## Candidate B — lowered counterparts

Distinct classes for the resolved stage, with `lower_semantics` as the single conversion boundary.

The counterexample above is what decides between them. A separate lowered class has *all* its
text fields `str`, which is exactly what is true after lowering, while the authored class keeps
all of them `TextLike` and so keeps allowing any mix the author wants. The two stages get two
statements instead of one parameter trying to serve both.

The cost is real and was not paid here: sixteen counterpart classes and a conversion for each.
`dataclasses.replace` cannot help — it returns whatever type it was handed, so the conversion has
to construct, arm by arm. `_primitive` in `lowering.py` is where it goes; it already exists, is
already the one place authored text is resolved, and its docstring already says so.

## Decision

Candidate B, on the evidence above. Candidate A was prototyped to completion at the leaf level,
measured as free, and then refuted by a call site the codebase actually contains.

Not implemented in this pass. What is landed is the measurement, this record, and
`test_lowering_resolves_text`, which pins the invariant — lowering leaves no deferred text behind
— that any `str`-typed lowered representation depends on. The 43 remaining `TextLike` errors in
`v2.py`, `classic.py`, `realization.py` and `dialect.py` are the work Candidate B would retire.
