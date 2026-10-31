"""Design A: can plain inference compute a container's mode from its children?

Self-contained -- no `squid_ui` import -- so the answer is about the type system rather
than about this codebase. Nothing here runs.

The lattice is `DiscordTarget` with two leaves. `Renderable[ModeT]` puts `ModeT` in a
parameter position, so it is contravariant: `Renderable[X]` satisfies `Renderable[M]`
exactly when `M <: X`. A container holding a portable child and a V2-only child must
therefore come out `ComponentsV2Target` -- the *meet* of its children's modes, which
Python has no syntax for. The question is whether a solver handed two upper bounds on one
contravariant variable computes it anyway.
"""

from typing import assert_type


class DiscordTarget: ...


class ComponentsV2Target(DiscordTarget): ...


class ClassicTarget(DiscordTarget): ...


class Renderable[ModeT = DiscordTarget]:
    def _accepts_target(self, target: ModeT, /) -> None:
        del target


class Text(Renderable[DiscordTarget]): ...


class Panel(Renderable[ComponentsV2Target]): ...


class Card(Renderable[ClassicTarget]): ...


class Stack[ModeT = DiscordTarget](Renderable[ModeT]):
    def __init__(self, *children: Renderable[ModeT]) -> None:
        self.children = children


def stack[ModeT](*children: Renderable[ModeT]) -> Stack[ModeT]:
    return Stack(*children)


def plan[ModeT](node: Renderable[ModeT], *, target: type[ModeT]) -> None:
    del node, target


# --- what the checker infers -------------------------------------------------------------

assert_type(stack(Text(), Text()), Stack[DiscordTarget])
assert_type(stack(Text(), Panel()), Stack[ComponentsV2Target])
assert_type(stack(Text(), Card()), Stack[ClassicTarget])

# A document that genuinely mixes dialects has no mode; this call should not type-check.
stack(Panel(), Card())

# --- what the author actually writes -----------------------------------------------------

plan(stack(Text(), Text()), target=ClassicTarget)  # legal: portable content
plan(stack(Text(), Panel()), target=ClassicTarget)  # THE GOAL: must be an error
plan(stack(Text(), Panel()), target=ComponentsV2Target)  # legal
