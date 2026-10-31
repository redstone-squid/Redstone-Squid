"""Design A against the real union shape, which is where it could still fail.

`design_a_inference.py` solved `ModeT` through a bare `Renderable[ModeT]` parameter. The
real factories take `ChildLike[ModeT]` -- a union of unparameterized semantic leaves,
parameterized containers, `Renderable[ModeT]`, and plain text. Solving a contravariant
variable through a union member is a strictly harder job, so it is tested separately.
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


# Mode-neutral semantic leaves: they adapt to any dialect, so they carry no parameter.
class Heading: ...


class Paragraph: ...


class Table: ...


class Stack[ModeT = DiscordTarget](Renderable[ModeT]):
    children: tuple[LayoutNode[ModeT], ...]

    def __init__(self, children: tuple[LayoutNode[ModeT], ...]) -> None:
        self.children = children


class Truncated[ModeT = DiscordTarget](Renderable[ModeT]):
    node: LayoutNode[ModeT]

    def __init__(self, node: LayoutNode[ModeT]) -> None:
        self.node = node


type SemanticNode[ModeT = DiscordTarget] = Stack[ModeT] | Heading | Paragraph | Table
type Adaptation[ModeT = DiscordTarget] = Truncated[ModeT]
type LayoutNode[ModeT = DiscordTarget] = SemanticNode[ModeT] | Adaptation[ModeT] | Renderable[ModeT]
type ChildLike[ModeT = DiscordTarget] = LayoutNode[ModeT] | str | None


def stack[ModeT](*children: ChildLike[ModeT]) -> Stack[ModeT]:
    return Stack(tuple(child for child in children if not isinstance(child, str | None)))


def truncate[ModeT](node: LayoutNode[ModeT]) -> Truncated[ModeT]:
    return Truncated(node)


def plan[ModeT](node: LayoutNode[ModeT], *, target: type[ModeT]) -> None:
    del node, target


class Bogus: ...


# --- the meet, through a union parameter -------------------------------------------------

assert_type(stack(Text(), Text()), Bogus)
assert_type(stack(Text(), Panel()), Bogus)
assert_type(stack(Text(), Card()), Bogus)
assert_type(stack(Heading(), Paragraph()), Bogus)
assert_type(stack(Heading(), Panel(), "bare text", None), Bogus)
assert_type(stack(stack(Heading(), Panel()), Text()), Bogus)
assert_type(truncate(stack(Heading(), Panel())), Bogus)
assert_type(stack(Panel(), Card()), Bogus)

# --- the goal ----------------------------------------------------------------------------

plan(stack(Heading(), Text()), target=ClassicTarget)
plan(stack(Heading(), Panel()), target=ClassicTarget)
plan(stack(Heading(), Panel()), target=ComponentsV2Target)
plan(stack(stack(Heading(), Panel()), Text()), target=ClassicTarget)
plan(truncate(stack(Heading(), Panel())), target=ClassicTarget)
