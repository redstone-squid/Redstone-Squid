"""Static target constraints through nested containers and component boundaries.

A V2-only Panel must still require a V2 target when buried inside several portable
containers. These examples check that wrappers do not erase that restriction.
Suppressions mark intentional invalid combinations; an unused suppression means its
negative example needs review.
"""

from typing import assert_type

import squid_ui as sl
from squid_ui.planning import ClassicTarget, ComponentsV2Target, plan
from squid_ui.primitives import Boundary, Card, Panel, Text
from squid_ui.semantic import Stack
from squid_ui_discord.target import classic, v2

# --- a container takes the meet of its children's modes ----------------------------------

portable = sl.stack(sl.heading("title"), sl.paragraph("body"))
assert_type(portable, Stack[sl.RenderTarget])

v2_only = sl.stack(sl.heading("title"), Panel((Text("inner"),)))
assert_type(v2_only, Stack[ComponentsV2Target])

classic_only = sl.stack(sl.heading("title"), Card(children=(Text("inner"),)))
assert_type(classic_only, Stack[ClassicTarget])

# --- and the meet survives nesting, wrappers, and plain strings ---------------------------

assert_type(sl.stack(sl.stack(sl.stack(Panel(())), "text"), None), Stack[ComponentsV2Target])
assert_type(sl.truncate(sl.stack(Panel(()))), sl.semantic.Truncated[ComponentsV2Target])
assert_type(sl.stack(Panel(())).truncate(), sl.semantic.Truncated[ComponentsV2Target])
assert_type(sl.stack(Panel(())).truncate().budget(min=1, prefer=2), sl.semantic.Budgeted[ComponentsV2Target])
assert_type(sl.section(sl.heading("h"), sl.aside(Panel(()))), sl.semantic.Section[ComponentsV2Target])

# --- which makes the mismatch a type error where the author wrote it ----------------------

plan(portable, target=classic())
plan(portable, target=v2())
plan(v2_only, target=v2())
plan(classic_only, target=classic())

plan(v2_only, target=classic())  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[no-matching-overload, bad-argument-type]
plan(classic_only, target=v2())  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[no-matching-overload, bad-argument-type]

# Keep target restrictions through several container layers.
buried = sl.stack(sl.section(sl.heading("h"), sl.aside(sl.stack(Panel(())))))
plan(buried, target=classic())  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[no-matching-overload, bad-argument-type]
plan(buried, target=v2())
# The method form of a wrapper keeps the restriction too.
plan(sl.stack(Panel(())).optional(), target=classic())  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[no-matching-overload, bad-argument-type]


def boundaries(
    portable_child: sl.Component,
    v2_child: sl.Component[ComponentsV2Target],
    classic_parent: sl.Component[ClassicTarget],
) -> None:
    direct = Boundary(v2_child, key="direct")
    assert_type(direct, Boundary[ComponentsV2Target])
    assert_type(direct.component, sl.Component[ComponentsV2Target])
    wrapped = v2_child.boundary(v2_child, key="child")
    assert_type(wrapped, Boundary[ComponentsV2Target])
    assert_type(v2_child.boundary(portable_child, key="portable"), Boundary[ComponentsV2Target])
    nested = sl.stack(sl.section(sl.heading("child"), wrapped))
    assert_type(nested, Stack[ComponentsV2Target])
    plan(nested, target=v2())
    plan(nested, target=classic())  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[bad-argument-type]
    classic_parent.boundary(v2_child, key="wrong")  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[bad-argument-type]
    portable_child.boundary(v2_child, key="wrong")  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[bad-argument-type]


def direct_children(v2_child: sl.Component[ComponentsV2Target], portable_child: sl.Component) -> None:
    assert_type(v2_child.keyed("child"), Boundary[ComponentsV2Target])
    assert_type(v2_child.keyed(42), Boundary[ComponentsV2Target])
    assert_type(sl.stack(v2_child), Stack[ComponentsV2Target])
    assert_type(sl.stack(v2_child.keyed("child")), Stack[ComponentsV2Target])
    portable_node: sl.LayoutNode[ComponentsV2Target] = portable_child
    classic_node: sl.LayoutNode[ClassicTarget] = v2_child  # type: ignore  # Deliberately incompatible target or context.
    classic_keyed: sl.LayoutNode[ClassicTarget] = v2_child.keyed("child")  # type: ignore  # Deliberately incompatible target or context.
