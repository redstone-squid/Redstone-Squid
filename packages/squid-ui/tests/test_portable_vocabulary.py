"""The portable union stays closed, derived, and distinct from the primitive escape."""

import squid_ui as sl
from squid_ui.factories import is_layout_node, is_portable_node
from squid_ui.primitives import Text


def test_portable_nodes_are_portable():
    assert is_portable_node(sl.paragraph("leaf"))
    # A container: guards the get_origin branch that once dropped every generic node.
    assert is_portable_node(sl.stack(sl.paragraph("child")))
    assert is_portable_node(sl.truncate(sl.paragraph("adaptation")))
    assert is_portable_node(sl.fallback(sl.paragraph("primary"), sl.paragraph("alternate")))


def test_primitives_are_layout_nodes_but_not_portable():
    primitive = Text("raw")
    assert is_layout_node(primitive)
    assert not is_portable_node(primitive)


def test_non_nodes_are_neither():
    assert not is_portable_node("text")
    assert not is_portable_node(None)


def test_nodes_are_really_slotted():
    """`Renderable` carries an empty `__slots__`, so `slots=True` on a node means something.

    Nothing else notices this regressing: a base without `__slots__` hands every subclass a
    `__dict__` back, and the dataclasses keep working exactly as before while paying for it.
    """
    assert not hasattr(sl.stack(sl.paragraph("child")), "__dict__")
    assert not hasattr(sl.paragraph("leaf"), "__dict__")
    assert not hasattr(Text("raw"), "__dict__")
