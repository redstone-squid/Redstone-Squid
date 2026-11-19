"""Peer presentations of shared semantic content.

Fidelity is an explicit author declaration. Structural validation can check that
referenced units and interactions survive, but cannot prove prose equivalent.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import ClassVar, Literal

from squid_ui.authoring.semantic import Wrapper
from squid_ui.content.text import Text, TextLike
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.resources import Axis
from squid_ui.target_types import Renderable, RenderTarget, Structure


@dataclass(frozen=True, slots=True)
class SemanticRef:
    """A path through semantic keys, relative to an alternatives group's content."""

    keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.keys or any(not key for key in self.keys):
            msg = "semantic references need nonempty keys"
            raise LayoutInvariantError(msg)


def ref(*keys: str) -> SemanticRef:
    """Reference semantic keys relative to the shared content."""
    return SemanticRef(keys)


def _ref(value: SemanticRef | str) -> SemanticRef:
    return value if isinstance(value, SemanticRef) else ref(value)


@dataclass(frozen=True, slots=True)
class Omission:
    source: SemanticRef


@dataclass(frozen=True, slots=True)
class Replacement:
    """A whole authored replacement whose equivalence has not been declared."""

    source: SemanticRef


@dataclass(frozen=True, slots=True)
class Shortening:
    source: SemanticRef
    max_chars: int
    keep: Literal["head", "tail"] = "head"

    def __post_init__(self) -> None:
        if self.keep not in ("head", "tail"):
            msg = "shortening must retain the head or tail"
            raise LayoutInvariantError(msg)
        if self.max_chars < 0:
            msg = "shortening cannot have a negative character limit"
            raise LayoutInvariantError(msg)


@dataclass(frozen=True, slots=True)
class InteractionChange:
    source: SemanticRef
    attribute: str

    def __post_init__(self) -> None:
        if not self.attribute:
            msg = "an interaction change must name the changed attribute"
            raise LayoutInvariantError(msg)


type FidelityEffect = Omission | Replacement | Shortening | InteractionChange


@dataclass(frozen=True, slots=True)
class Fidelity:
    """No effects declares equivalence; effects authorize only the named changes."""

    effects: tuple[FidelityEffect, ...] = ()

    @property
    def lossless(self) -> bool:
        return not self.effects


def equivalent() -> Fidelity:
    """Declare that a presentation preserves the shared content."""
    return Fidelity()


def omit(source: SemanticRef | str) -> Omission:
    """Authorize omission of one source unit."""
    return Omission(_ref(source))


def shorten(source: SemanticRef | str, *, max_chars: int, keep: Literal["head", "tail"] = "head") -> Shortening:
    """Authorize retaining the head or tail of a source text."""
    return Shortening(_ref(source), max_chars, keep)


def change_interaction(source: SemanticRef | str, *, attribute: str) -> InteractionChange:
    """Authorize a change to one interaction attribute."""
    return InteractionChange(_ref(source), attribute)


def losses(*effects: FidelityEffect) -> Fidelity:
    """Declare the specific changes a presentation may make."""
    if not effects:
        msg = "losses() needs specific effects; use equivalent() for preservation"
        raise LayoutInvariantError(msg)
    return Fidelity(effects)


@dataclass(frozen=True, slots=True)
class Correspondence:
    """Map one presentation unit to source units; repeat a source to describe a split."""

    presentation: SemanticRef
    content: tuple[SemanticRef, ...]

    def __post_init__(self) -> None:
        if not self.content:
            msg = "a correspondence needs at least one source unit"
            raise LayoutInvariantError(msg)


def corresponds(presentation: SemanticRef | str, *content: SemanticRef | str) -> Correspondence:
    """Map a presentation unit to its source units."""
    if not content:
        msg = "a correspondence needs at least one source unit"
        raise LayoutInvariantError(msg)
    return Correspondence(_ref(presentation), tuple(_ref(item) for item in content))


@dataclass(frozen=True, slots=True)
class Alternative[RenderTargetT = RenderTarget, ContextT = object]:
    STRUCTURE: ClassVar[Structure] = Structure(layout=("node",))

    node: Renderable[RenderTargetT, ContextT]
    key: str
    fidelity: Fidelity
    preference: int = 0
    correspondence: tuple[Correspondence, ...] = ()
    requires: frozenset[str] = field(default=frozenset(), kw_only=True)

    def __post_init__(self) -> None:
        if not self.key:
            msg = "an alternative needs a stable key"
            raise LayoutInvariantError(msg)


@dataclass(frozen=True, slots=True)
class Alternatives[RenderTargetT = RenderTarget, ContextT = object](Renderable[RenderTargetT, ContextT]):
    """Presentations of a semantic reference that is not itself additional output."""

    # Selectors see the group as one leaf: every choice restates `content`.
    STRUCTURE: ClassVar[Structure] = Structure(layout=("content",), nested=(("choices", "node"),), query=())

    content: Renderable[RenderTargetT, ContextT]
    choices: tuple[Alternative[RenderTargetT, ContextT], ...]
    key: str
    priority: int = field(default=0, kw_only=True)

    def __post_init__(self) -> None:
        if not self.key or not self.choices:
            msg = "alternatives need a group key and at least one candidate"
            raise LayoutInvariantError(msg)
        keys = [choice.key for choice in self.choices]
        if len(set(keys)) != len(keys):
            msg = "alternative keys must be unique within their group"
            raise LayoutInvariantError(msg)


def alternative[RenderTargetT, ContextT](
    node: Renderable[RenderTargetT, ContextT],
    *,
    key: str,
    fidelity: Fidelity,
    preference: int = 0,
    correspondence: tuple[Correspondence, ...] = (),
) -> Alternative[RenderTargetT, ContextT]:
    """Offer one presentation with declared fidelity and preference."""
    return Alternative(node, key, fidelity, preference, correspondence)


def alternatives[RenderTargetT, ContextT](
    *choices: Alternative[RenderTargetT, ContextT],
    content: Renderable[RenderTargetT, ContextT],
    key: str,
) -> Alternatives[RenderTargetT, ContextT]:
    """Group presentations of the same semantic content."""
    return Alternatives(content, choices, key)


@dataclass(frozen=True, slots=True)
class TextPresentation(Renderable[RenderTarget]):
    """Text assembled from source references, with literal connective text.

    A Shortening fragment applies that exact operation to its source unit after
    localization. Its effect must also be declared by the containing alternative.
    References retain their complete structured text, including links and styling.
    """

    parts: tuple[Text | SemanticRef | Shortening, ...]
    key: str

    def __post_init__(self) -> None:
        if not self.key:
            message = "a text presentation needs a stable key"
            raise LayoutInvariantError(message)


def present_text(*parts: TextLike | SemanticRef | Shortening, key: str) -> TextPresentation:
    """Assemble text from source references and literal fragments."""
    return TextPresentation(
        tuple(part if isinstance(part, SemanticRef | Shortening) else Text(part) for part in parts), key
    )


@dataclass(frozen=True, slots=True)
class Budget[RenderTargetT = RenderTarget, ContextT = object](Wrapper[RenderTargetT, ContextT]):
    """Additional resource limits for the selected presentation of this subtree.

    Each axis uses the target's protocol counting rules. Limits never relax platform
    capacities or authorize content loss. Unsupported axes reject the candidate.
    """

    node: Renderable[RenderTargetT, ContextT]
    limits: Mapping[Axis, int]

    def __post_init__(self) -> None:
        if any(value < 0 for value in self.limits.values()):
            message = "resource limits cannot be negative"
            raise LayoutInvariantError(message)
        object.__setattr__(self, "limits", MappingProxyType(dict(self.limits)))


def budget[RenderTargetT, ContextT](
    node: Renderable[RenderTargetT, ContextT], *, limits: Mapping[Axis, int]
) -> Budget[RenderTargetT, ContextT]:
    return Budget(node, limits)


@dataclass(frozen=True, slots=True)
class AtomicNode[RenderTargetT = RenderTarget, ContextT = object](Renderable[RenderTargetT, ContextT]):
    """An opaque semantic leaf realized by explicitly supplied target rules.

    Subclasses retain their own typed payload. Their offers must declare an atomic
    resource and placement contract. The core never infers internal break points or
    text transformations from that payload.
    """

    key: str
    effects: tuple[FidelityEffect, ...] = field(default=(), kw_only=True)
    """Author-authorized effects for this atom; preservation is the default."""
