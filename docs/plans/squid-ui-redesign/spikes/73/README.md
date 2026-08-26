# Spike 73 — how a container gets its mode

## Question

A container holding a portable child and a V2-only child must come out V2-only: the **meet**
of its children's modes over the `DiscordTarget` lattice. Python has no intersection type, so
the meet can only arrive as a solver result — one contravariant type variable handed two
upper bounds. Whether a checker computes it is unspecified by the typing spec, so it was
measured rather than argued.

Two designs were on the table:

- **A** — one generic signature, `def stack[ModeT](*children: ChildLike[ModeT]) -> Stack[ModeT]`,
  and let the solver do it.
- **B** — an ordered three-overload ladder per factory (`DiscordTarget` first, then the two
  leaves), which is deterministic but costs ~54 extra overloads across the 19 container and
  adaptation factories.

## Verdict: A, with a `ModeT = DiscordTarget` default on the function's type parameter

Design A computes the meet in both checkers, so B was never written. The default matters and
is not decoration: without it an all-neutral container infers `Stack[Unknown]`, and with it
the same call infers `Stack[DiscordTarget]` *without* defeating the meet elsewhere.

Measured with `pyrefly 1.2.0` and `basedpyright` (CI's checker) on
`design_a_realistic.py`, which uses the real shape — `ChildLike[ModeT]` as a union of
unparameterized semantic leaves, parameterized containers, `Renderable[ModeT]` and plain text.
Solving a contravariant variable *through a union member* is the hard case, and it is the one
that matters; `design_a_inference.py` is the simpler warm-up.

| Call | pyrefly | basedpyright |
|---|---|---|
| `stack(Text(), Text())` | `Stack[DiscordTarget]` | same |
| `stack(Text(), Panel())` | `Stack[ComponentsV2Target]` | same |
| `stack(Text(), Card())` | `Stack[ClassicTarget]` | same |
| `stack(Heading(), Paragraph())` — all neutral leaves | `Stack[DiscordTarget]` | same |
| `stack(Heading(), Panel(), "text", None)` | `Stack[ComponentsV2Target]` | same |
| `stack(stack(Heading(), Panel()), Text())` — nested | `Stack[ComponentsV2Target]` | same |
| `truncate(stack(Heading(), Panel()))` — through an adaptation | `Truncated[ComponentsV2Target]` | same |
| `stack(Panel(), Card())` — mixed dialects | `Stack[ClassicTarget \| ComponentsV2Target]` | **error at the call** |

And the goal itself, in both checkers:

```python
plan(stack(Heading(), Text()), target=ClassicTarget)              # no error, correctly
plan(stack(Heading(), Panel()), target=ClassicTarget)             # error
plan(stack(stack(Heading(), Panel()), Text()), target=ClassicTarget)  # error, arbitrary depth
plan(truncate(stack(Heading(), Panel())), target=ClassicTarget)   # error, through a wrapper
```

## The one gap, and why it is acceptable

A container mixing a V2-only and a classic-only child works in *neither* dialect, but
contravariance makes the union the solver's natural fallback, and `Stack[Classic | ComponentsV2]`
reads as "accepts either marker" — so **pyrefly accepts that container against both targets
instead of neither**. BasedPyright rejects it at the call with a precise message, so CI catches
it; only the local pre-push check misses it.

Left as a known gap rather than designed around:

- It is the rarer mistake by far. "I used a `Panel` in a portable component" is the everyday
  error and is caught; "I put a `Panel` and a `Card` in one container" is not something an
  author does by accident.
- The planner still raises `LayoutInvariantError` at runtime, so nothing reaches Discord.
- Closing it would need "meet or fail", which no overload ladder expresses either — Design B
  has the same hole, since its ladder would simply match no overload and report a worse message.
