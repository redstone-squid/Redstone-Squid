"""Test whether a checker infers a container's target from contravariant child types.

The experiment is self-contained and has no squid_ui imports. A portable child and a
Components V2 child should produce a ComponentsV2Target container. Mixed classic and V2
children should be rejected. The assert_type calls and final plan calls test those
expectations; this is a static-checker probe, not a runtime behavior test.

Renderable consumes its target type, making that parameter contravariant: content
that accepts any Discord target can also be used where only V2 content is accepted.
The container must infer a target accepted by every child. This tests whether the
checker can find that common restriction without an explicit intersection type.
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
