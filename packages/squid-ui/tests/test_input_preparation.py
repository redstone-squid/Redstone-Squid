"""Target-native traversal participates in shared authoring policies."""

from dataclasses import dataclass
from typing import Any, override

from squid_ui.authoring import semantic as sem
from squid_ui.content.text import NEUTRAL, plain
from squid_ui.ext.discord.primitives import nodes as primitive
from squid_ui.extensions import ExtensionKind
from squid_ui.planning.composition_input import InputPreparation
from squid_ui.planning.dialect import InputPolicy, NativeInput
from squid_ui.target_types import Renderable


@dataclass(frozen=True)
class NativeLabel(Renderable):
    text: str


class LabelInput(NativeInput):
    def __init__(self, context: InputPreparation) -> None:
        self.context = context

    @override
    def visit(self, node: Renderable[Any]) -> Renderable[Any] | None:
        if isinstance(node, NativeLabel):
            return self.context.visit(sem.Paragraph(plain(node.text)))
        return None


def test_native_lowering_inherits_portable_shortening_and_work_estimates() -> None:
    preparation = InputPreparation(InputPolicy(text_ladder=100, native=LabelInput), NEUTRAL, frozenset(), {})
    result = preparation.visit(sem.Truncated(NativeLabel("example"), keep="head"))
    assert isinstance(result, sem.Truncated)
    assert isinstance(result.node, sem.Paragraph)
    assert preparation.combinations == 7
    assert preparation.shortening_depth == 0


def test_target_without_native_visitor_checks_the_extension_fallback() -> None:
    fallback = primitive.Variants.of(sem.Paragraph(plain("Portable")), sem.Paragraph(plain("Short")))
    node = primitive.Extension(ExtensionKind[str, object]("test.label"), 1, "Native", fallback)
    preparation = InputPreparation(InputPolicy(), NEUTRAL, frozenset(), {})
    assert preparation.visit(node) is node
    assert preparation.variant_index == 1
