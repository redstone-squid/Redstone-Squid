"""Discord presentation limits as data, for both message modes.

Exceeding any of these makes Discord reject the payload with HTTP 400, error code 50035
("Invalid Form Body"). Each number comes from:

- https://docs.discord.com/developers/resources/message (classic content, embeds)
- https://docs.discord.com/developers/components/reference (rows, buttons, selects, modals)
- https://docs.discord.com/developers/components/overview (the V2 transition)

discord.py 2.7 checks only `len(embeds) > 10` and the 25-child `discord.ui.View` cap locally;
everything else is server-only, which is why the renderer audits the payload before sending.

`ComponentLimits` is what every component obeys in either mode, `EmbedLimits` what one embed
may hold, and a `MessageLimits` subclass the message-wide budgets of one mode. A shared
planning layer takes a `MessageLimits` and reads only what that base declares.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from typing import Self

from squid_ui.planning.resources import TEXT_AXES as TEXT_AXES
from squid_ui.planning.resources import Axis as Axis

ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"


@dataclass(frozen=True, slots=True)
class ComponentLimits:
    """Caps every Discord component obeys, whichever message mode holds it; both modes share one instance.

    A function that reads only these takes `ComponentLimits`, which is what makes it callable from
    either mode's path.
    """

    # ActionRow.
    row_buttons: int = 5
    """Buttons per action row; a select occupies the entire row."""
    button_label: int = 80
    link_url: int = 512

    # Select menus.
    select_options: int = 25
    select_placeholder: int = 150
    option_label: int = 100
    option_value: int = 100
    option_description: int = 100

    # Modals.
    modal_title: int = 45
    modal_components: int = 5
    modal_text: int = 4000
    label_text: int = 45
    label_description: int = 100
    text_input_placeholder: int = 100
    text_input_value: int = 4000
    """Cap on a TextInput's value, default, and max_length."""

    custom_id: int = 100


COMPONENT_LIMITS = ComponentLimits()
"""The component caps currently enforced by Discord, in either message mode."""


@dataclass(frozen=True, slots=True)
class EmbedLimits:
    """Per-embed caps, validated like `ComponentLimits.button_label`.

    The message-wide `Axis.EMBED_TEXT` pool several embeds share is a budget and lives on `ClassicLimits`.
    """

    title: int = 256
    description: int = 4096
    fields: int = 25
    field_name: int = 256
    field_value: int = 1024
    footer: int = 2048
    author: int = 256


EMBED_LIMITS = EmbedLimits()
"""The per-embed caps currently enforced by Discord."""


def _cap_values(value: object, prefix: str = "") -> Iterator[tuple[str, object]]:
    """Every leaf cap under `value`, by dotted name."""
    if not is_dataclass(value) or isinstance(value, type):
        return
    for cap in fields(value):
        held = getattr(value, cap.name)
        name = f"{prefix}{cap.name}"
        if is_dataclass(held) and not isinstance(held, type):
            yield from _cap_values(held, f"{name}.")
        else:
            yield name, held


@dataclass(frozen=True, slots=True)
class MessageLimits(ABC):
    """What both Discord message modes share; the message-wide totals live on the subclasses.

    A shared planning layer reads only what this class declares. Anything mode-specific goes
    through an abstract member here or moves onto the dialect.
    """

    components: ComponentLimits = COMPONENT_LIMITS
    attachments: int = 10
    """Files per message. Discord's own cap, not discord.py's; the message docs omit it."""
    embeds: EmbedLimits | None = None
    """What one embed may hold, or `None` in a mode with no embeds; a Components V2 message cannot carry one."""

    @property
    @abstractmethod
    def capacities(self) -> Mapping[Axis, int]:
        """Every message-wide budget this mode declares, with the room it has left.

        Owned by the limits rather than the target, so the caps and the axes naming them
        cannot fall out of step.
        """

    @abstractmethod
    def with_capacities(self, reductions: Mapping[Axis, int]) -> Self:
        """These limits with each named amount withheld from that axis, clamped at zero.

        Axes this mode does not budget are ignored; `Target.reserve` rejects those by name
        before it gets here, so reaching this method with one is already a caller's bug.
        """

    @property
    @abstractmethod
    def text_axes(self) -> Mapping[Axis, int]:
        """Every independent text pool this mode budgets, by axis.

        Two pools do not lend to each other, so the allocator runs once per pool over the
        units tagged to it rather than once over a single total.
        """

    @abstractmethod
    def fits_controls(self, controls: int, rows: int) -> bool:
        """Whether a message of this mode could hold that many controls in that many rows.

        The semantic adapters ask this to decide whether laying actions out individually is
        offerable at all. Mode-specific because a V2 message spends one component budget on
        rows and buttons alike, while a classic message caps view children and rows apart.
        """

    @property
    @abstractmethod
    def component_budget(self) -> int:
        """The most components one page of this mode may spend."""

    def digest(self) -> tuple[tuple[str, object], ...]:
        """Every cap these limits hold, by dotted name in name order.

        Part of `Target.fingerprint`, so two targets sharing an id but differing in any cap
        are told apart.
        """
        return tuple(sorted(_cap_values(self)))


@dataclass(frozen=True, slots=True)
class V2Limits(MessageLimits):
    """Hard limits for a Components V2 message and its children."""

    # Message-wide budgets.
    total_text: int = 4000
    """Combined length of all TextDisplay content in one message."""
    total_components: int = 40
    """Components per message, counting nested children."""

    # Section and media.
    section_texts: int = 3
    """TextDisplay children per Section, excluding the accessory."""
    gallery_items: int = 10
    gallery_item_description: int = 1024

    @property
    def capacities(self) -> Mapping[Axis, int]:
        return {
            Axis.DISPLAY_TEXT: self.total_text,
            Axis.COMPONENTS: self.total_components,
            Axis.ATTACHMENTS: self.attachments,
        }

    def with_capacities(self, reductions: Mapping[Axis, int]) -> Self:
        return replace(
            self,
            total_text=max(0, self.total_text - reductions.get(Axis.DISPLAY_TEXT, 0)),
            total_components=max(0, self.total_components - reductions.get(Axis.COMPONENTS, 0)),
            attachments=max(0, self.attachments - reductions.get(Axis.ATTACHMENTS, 0)),
        )

    @property
    def text_axes(self) -> Mapping[Axis, int]:
        # Exactly one pool, which is why this looks like ceremony here and stops looking
        # like it the moment a target has two.
        return {Axis.DISPLAY_TEXT: self.total_text}

    def fits_controls(self, controls: int, rows: int) -> bool:
        # An ActionRow and each of its buttons are all components against one total.
        return controls + rows <= self.total_components

    @property
    def component_budget(self) -> int:
        return self.total_components


@dataclass(frozen=True, slots=True)
class ClassicLimits(MessageLimits):
    """Hard limits for a pre-Components-V2 message: content, embeds, and action rows."""

    embeds: EmbedLimits = EMBED_LIMITS
    """Narrowed from the base's optional: a classic message always has embeds, so classic code reads it unguarded."""

    # Message-wide budgets.
    content: int = 2000
    """Length of the `content` field. Its own pool: it never borrows from embed text."""
    embed_text: int = 6000
    """Titles, descriptions, field names and values, footers, and author names, added up
    across every embed on the message. Server-enforced only."""
    embed_count: int = 10
    """Embeds per message, which discord.py checks locally."""
    rows: int = 5
    controls: int = 25
    """Interactive components per message: 5 action rows of 5 buttons.

    Discord's cap, not discord.py's, though `discord.ui.View` is the one layer that checks it
    locally by refusing a 26th child.
    """

    @property
    def capacities(self) -> Mapping[Axis, int]:
        return {
            Axis.CONTENT_TEXT: self.content,
            Axis.EMBED_TEXT: self.embed_text,
            Axis.EMBEDS: self.embed_count,
            Axis.ROWS: self.rows,
            Axis.CONTROLS: self.controls,
            Axis.ATTACHMENTS: self.attachments,
        }

    def with_capacities(self, reductions: Mapping[Axis, int]) -> Self:
        return replace(
            self,
            content=max(0, self.content - reductions.get(Axis.CONTENT_TEXT, 0)),
            embed_text=max(0, self.embed_text - reductions.get(Axis.EMBED_TEXT, 0)),
            embed_count=max(0, self.embed_count - reductions.get(Axis.EMBEDS, 0)),
            rows=max(0, self.rows - reductions.get(Axis.ROWS, 0)),
            controls=max(0, self.controls - reductions.get(Axis.CONTROLS, 0)),
            attachments=max(0, self.attachments - reductions.get(Axis.ATTACHMENTS, 0)),
        )

    @property
    def text_axes(self) -> Mapping[Axis, int]:
        return {Axis.CONTENT_TEXT: self.content, Axis.EMBED_TEXT: self.embed_text}

    def fits_controls(self, controls: int, rows: int) -> bool:
        return controls <= self.controls and rows <= self.rows

    @property
    def component_budget(self) -> int:
        return self.controls


LIMITS = V2Limits()
"""The Components V2 limits currently enforced by Discord."""

CLASSIC_LIMITS = ClassicLimits()
"""The classic-message limits currently enforced by Discord."""
