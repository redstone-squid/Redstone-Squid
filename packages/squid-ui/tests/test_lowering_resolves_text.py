"""The invariant every `Node[str]` annotation downstream rests on.

`lower_semantics` resolves each text field against the localization, which is why the dialects
and the measurement pipeline may call `len()` and `.strip()` on what reaches them. If a node
type is ever added that carries text through lowering untouched, an unresolved `Message` would
reach those readers and raise there instead of here.
"""

from squid_ui.chrome import DEFAULT_CHROME
from squid_ui.planning.limits import LIMITS
from squid_ui.planning.semantic_adaptation.lowering import lower_semantics
from squid_ui.primitives.nodes import (
    Code,
    Footer,
    Heading,
    Lines,
    Option,
    RoutedButton,
    RoutedSelect,
    Text,
    Thumbnail,
)
from squid_ui.runtime.presentation_state import PresentationState
from squid_ui.text import NEUTRAL, Message

_TEXT_FIELDS = ("content", "label", "description", "placeholder")


def _deferred_text_in(node: object) -> list[str]:
    name = type(node).__name__
    found = [f"{name}.{field}" for field in _TEXT_FIELDS if isinstance(getattr(node, field, None), Message)]
    found += [
        f"{name}.options.label" for option in getattr(node, "options", ()) or () if isinstance(option.label, Message)
    ]
    found += [f"{name}.lines[]" for line in getattr(node, "lines", ()) or () if isinstance(line, Message)]
    return found


def test_lowering_leaves_no_deferred_text_behind() -> None:
    deferred = Message(template="hello")
    authored = [
        Text(content=deferred),
        Heading(content=deferred),
        Footer(content=deferred),
        Code(content=deferred),
        Lines(lines=(deferred,)),
        RoutedButton(label=deferred, route_id="r"),
        RoutedSelect(options=(Option(label=deferred, value="v"),), route_id="r2"),
        Thumbnail(url="http://example.invalid/y.png", description=deferred),
    ]

    lowered = lower_semantics(
        authored,
        limits=LIMITS,
        chrome=DEFAULT_CHROME,
        localization=NEUTRAL,
        session=PresentationState(),
    )

    assert [type(node).__name__ for node in lowered.nodes] == [
        "Text",
        "Heading",
        "Footer",
        "Code",
        "Lines",
        "RoutedButton",
        "RoutedSelect",
        "Thumbnail",
    ]
    assert [name for node in lowered.nodes for name in _deferred_text_in(node)] == []
