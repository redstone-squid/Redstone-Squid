"""Evaluate contextual declarations against captured inputs without effects."""

from squid_reactivity import capture_reads
from squid_reactivity.transactions import read_only
from squid_ui._contextual import EVALUATING
from squid_ui.authoring.document import Document, as_document
from squid_ui.authoring.semantic import Contextual
from squid_ui.errors import LayoutError
from squid_ui.layout import LayoutContext


def evaluate_contextual[R, C](block: Contextual[R, C], context: LayoutContext) -> Document[R, C]:
    token = EVALUATING.set(True)
    try:
        with read_only(), capture_reads() as reads:
            output = as_document(block.callback(context))
        if reads.sources:
            message = f"context {block.key}: capture reactive inputs in render before defining the callback"
            raise LayoutError(message)
        return output
    finally:
        EVALUATING.reset(token)
