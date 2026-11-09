"""Component authoring and tree entry points retained at their original import path.

Definitions live in ``definition``, tree values in ``tree``, and expansion in
``expansion``. Runtime implementation modules import those owners directly.
"""

from squid_ui.runtime.definition import (
    AnyComponent,
    Component,
    ContextT,
    RenderTargetT,
    RuntimeOwner,
)
from squid_ui.runtime.expansion import render_component_tree
from squid_ui.runtime.tree import ComponentTree, IncrementalRender

__all__ = [
    "AnyComponent",
    "Component",
    "ComponentTree",
    "ContextT",
    "IncrementalRender",
    "RenderTargetT",
    "RuntimeOwner",
    "render_component_tree",
]
