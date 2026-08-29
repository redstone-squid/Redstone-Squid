"""The portable union stays closed, derived, and distinct from the primitive escape."""

import squid_ui as sl
from squid_ui.factories import is_builtin_layout_node, is_layout_node, is_portable_node
from squid_ui.primitives import Text
from squid_ui.target_types import Renderable, RenderTarget
from squid_ui.text import Message


def test_portable_nodes_are_portable():
    assert is_portable_node(sl.paragraph("leaf"))
    # A container: guards the get_origin branch that once dropped every generic node.
    assert is_portable_node(sl.stack(sl.paragraph("child")))
    assert is_portable_node(sl.truncate(sl.paragraph("adaptation")))
    assert is_portable_node(sl.fallback(sl.paragraph("primary"), sl.paragraph("alternate")))


def test_primitives_are_layout_nodes_but_not_portable():
    primitive = Text("raw")
    assert is_layout_node(primitive)
    assert is_builtin_layout_node(primitive)
    assert not is_portable_node(primitive)


def test_a_caller_s_own_renderable_is_a_layout_node_but_not_a_builtin_one():
    """The open arm of `LayoutNode`, which is the whole point of the escape hatch.

    A frontend may ship a node this package has never heard of. The authoring surface has
    to accept it; the Discord lowering has to be able to say it cannot draw it.
    """

    class Custom(Renderable[RenderTarget]):
        __slots__ = ()

    node = Custom()
    assert is_layout_node(node)
    assert not is_builtin_layout_node(node)
    assert not is_portable_node(node)


def test_a_message_child_is_promoted_rather_than_rejected():
    """`ChildLike` admits every `TextLike`, and `Message` is one."""
    stack = sl.stack(Message("hello"))
    assert stack.children == (sl.paragraph(Message("hello")),)


def test_non_nodes_are_neither():
    assert not is_portable_node("text")
    assert not is_portable_node(None)
    assert not is_layout_node("text")
    assert not is_layout_node(None)


def test_nodes_are_really_slotted():
    """`Renderable` carries an empty `__slots__`, so `slots=True` on a node means something.

    Nothing else notices this regressing: a base without `__slots__` hands every subclass a
    `__dict__` back, and the dataclasses keep working exactly as before while paying for it.
    """
    assert not hasattr(sl.stack(sl.paragraph("child")), "__dict__")
    assert not hasattr(sl.paragraph("leaf"), "__dict__")
    assert not hasattr(Text("raw"), "__dict__")
