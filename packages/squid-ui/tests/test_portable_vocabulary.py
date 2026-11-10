"""The portable union stays closed, derived, and distinct from the primitive escape."""

import squid_ui as sl
from squid_i18n import tr
from squid_ui.factories import is_builtin_layout_node, is_layout_node, is_portable_node
from squid_ui.primitives import Text
from squid_ui.target_types import Renderable, RenderTarget


def test_portable_nodes_are_portable():
    assert is_portable_node(sl.paragraph("leaf"))
    # Generic containers require resolving their runtime origin for membership checks.
    assert is_portable_node(sl.stack(sl.paragraph("child")))
    assert is_portable_node(sl.truncate(sl.paragraph("adaptation")))
    assert is_portable_node(sl.fallback(sl.paragraph("primary"), sl.paragraph("alternate")))


def test_primitives_are_layout_nodes_but_not_portable():
    primitive = Text("raw")
    assert is_layout_node(primitive)
    assert is_builtin_layout_node(primitive)
    assert not is_portable_node(primitive)


def test_a_caller_s_own_renderable_is_a_layout_node_but_not_a_builtin_one():
    """Authoring accepts custom Renderable subclasses without making them built-in vocabulary.

    A target must still explicitly support the node before it can plan it.
    """

    class Custom(Renderable[RenderTarget]):
        __slots__ = ()

    node = Custom()
    assert is_layout_node(node)
    assert not is_builtin_layout_node(node)
    assert not is_portable_node(node)


def test_a_message_child_is_promoted_rather_than_rejected():
    """`ChildLike` admits every `TextLike`, and `Message` is one."""
    stack = sl.stack(tr(t"test-hello = hello"))
    assert stack.children == (sl.paragraph(tr(t"test-hello = hello")),)


def test_non_nodes_are_neither():
    assert not is_portable_node("text")
    assert not is_portable_node(None)
    assert not is_layout_node("text")
    assert not is_layout_node(None)
