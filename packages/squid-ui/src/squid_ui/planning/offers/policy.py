"""Resolve inherited text constraints shared by all realization handlers."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import EllipsisType
from typing import Literal

from squid_i18n import tree
from squid_ui.authoring import semantic as sem
from squid_ui.authoring.presentations import (
    Budget,
    Shortening,
)
from squid_ui.planning.composition import (
    DiscoveryCompletion,
    OfferDiscovery,
    PlanningContext,
    RealizationOffer,
    SemanticOccurrence,
)
from squid_ui.planning.realizations import (
    Realization,
    TableForm,
    TextRealization,
)
from squid_ui.planning.resources import Axis
from squid_ui.planning.text_presentations import (
    _unsupported,
    text_ends,
)
from squid_ui.primitives import nodes as primitive

type Offers[RenderTargetT, ContextT] = (
    tuple[RealizationOffer[RenderTargetT, ContextT, Realization], ...]
    | OfferDiscovery[RenderTargetT, ContextT, Realization]
)


@dataclass(frozen=True, slots=True)
class OfferPolicy:
    """What a target can draw, which decides the realizations `semantic_offers` proposes.

    Attributes:
        table_forms: Table forms the target can draw, in order of preference.
        panels: Whether grouped content may become a panel (a Discord container or HTML section).
        nested_panels: Whether a panel may contain another panel.
        disabled_controls: Whether the target can show a disabled control instead of omitting it.
        character_budgets: Whether `Budgeted`, `Paged`, and primitive `Budget` regions are honored.
            When False, those regions produce no offers.
        text_limit: Characters one text display may hold, unless the planning context sets a
            `DISPLAY_TEXT` capacity.
        native_controls: Whether controls may use the target's native selects and entity pickers.
        panel_text_limit: Replaces `text_limit` for text inside a panel.
        heading_limit: Further caps the text of a shortened heading.
        native_v2: Whether extensions and raw items may use their native Components V2 composition.
        grid_columns: Controls one row may hold.
        embed_limits: Character limit for each classic embed slot (`CardSlot.kind`). When None,
            card slots produce no offers.
    """

    table_forms: tuple[TableForm, ...]
    panels: bool
    nested_panels: bool = True
    disabled_controls: bool = True
    character_budgets: bool = False
    text_limit: int | None = None
    native_controls: bool = False
    panel_text_limit: int | None = None
    heading_limit: int | None = None
    native_v2: bool = False
    grid_columns: int | None = None
    embed_limits: Mapping[str, int] | None = None


@dataclass(frozen=True, slots=True)
class _OfferScope[RenderTargetT, ContextT]:
    """One occurrence's offer inputs, after its ancestors have set the text rules.

    Attributes:
        text_capacity: Characters the occurrence's text may use, or None when unconstrained.
        keep: Which end of the text a `Truncated` or `BestEffort` ancestor lets shortening keep.
            None means the text may not be shortened.
        spill: Whether a `Spilled` or `BestEffort` ancestor lets text continue onto later pages.
        inside_panel: Whether a block, section, article, aside, panel, or card encloses the occurrence.
    """

    occurrence: SemanticOccurrence[RenderTargetT, ContextT]
    context: PlanningContext
    policy: OfferPolicy
    text_capacity: int | None
    keep: Literal["head", "tail"] | None
    spill: bool
    inside_panel: bool

    @classmethod
    def for_occurrence(
        cls,
        occurrence: SemanticOccurrence[RenderTargetT, ContextT],
        context: PlanningContext,
        policy: OfferPolicy,
    ) -> _OfferScope[RenderTargetT, ContextT]:
        """Resolve ancestor constraints before proposing this occurrence's realizations."""
        text_capacity = context.capacities.get(Axis.DISPLAY_TEXT, policy.text_limit)
        keep: Literal["head", "tail"] | None = None
        spill = False
        inside_panel = False
        for ancestor in occurrence.ancestors():
            inside_panel |= isinstance(
                ancestor, sem.Block | sem.Section | sem.Article | sem.Aside | primitive.Panel | primitive.Card
            )
            if isinstance(ancestor, sem.Truncated):
                keep = ancestor.keep
                spill = False
            elif isinstance(ancestor, sem.Spilled):
                keep, spill = None, True
            elif isinstance(ancestor, sem.BestEffort):
                keep, spill = "head", True
        if inside_panel and policy.panel_text_limit is not None:
            text_capacity = policy.panel_text_limit
        for ancestor in occurrence.ancestors():
            cap = (
                ancestor.chars
                if isinstance(ancestor, sem.Paged)
                else ancestor.preferred + ancestor.stretch
                if isinstance(ancestor, sem.Budgeted | primitive.Budget)
                else ancestor.limits.get(Axis.DISPLAY_TEXT)
                if isinstance(ancestor, Budget)
                else None
            )
            if cap is not None:
                text_capacity = min(text_capacity, cap) if text_capacity is not None else cap
        return cls(occurrence, context, policy, text_capacity, keep, spill, inside_panel)

    def text_offers(
        self,
        full: RealizationOffer[RenderTargetT, ContextT, Realization],
        payload: TextRealization,
        permission: Literal["head", "tail"] | EllipsisType | None = ...,
        priority: int = 0,
    ) -> Offers[RenderTargetT, ContextT]:
        """Offer `full` plus each shorter cut of its text that fits the capacity.

        `permission` defaults to the enclosing `keep`; None offers only the full text.
        """
        occurrence, context = self.occurrence, self.context
        if permission is ...:
            permission = self.keep
        text_capacity = self.text_capacity
        heading_limit = self.policy.heading_limit
        if permission is None or text_capacity is None:
            return (full,)
        if feature := _unsupported(payload.nodes):
            return OfferDiscovery((full,), DiscoveryCompletion.UNSUPPORTED, f"shortening inline {feature}")
        length = sum(len(tree.plain(node)) for node in payload.nodes)
        if not length:
            return (full,)
        cap = (
            min(text_capacity, heading_limit)
            if payload.role == "heading" and heading_limit is not None
            else text_capacity
        )
        upper = min(length - 1, max(0, cap - 1))
        context.work.consume(length + max(0, upper + 1))
        cuts = (0, *text_ends(payload.nodes))
        retained_lengths = sorted(
            {retained for end in cuts if (retained := end if permission == "head" else length - end) <= upper},
            reverse=True,
        )
        return (
            replace(full, preference_priority=priority),
            *(
                replace(
                    full,
                    key=f"shorten-{retained}",
                    payload=replace(payload, shortening=operation),
                    preference=length - retained,
                    effects=(*full.effects, *occurrence.scope_effects((operation,))),
                    fragments=None,
                    preference_priority=priority,
                )
                for retained in retained_lengths
                for operation in (Shortening(occurrence.reference, retained, permission),)
            ),
            replace(
                full,
                key="shorten-empty",
                payload=replace(payload, shortening=Shortening(occurrence.reference, 0, permission), ellipsis=False),
                preference=length + 1,
                effects=(*full.effects, *occurrence.scope_effects((Shortening(occurrence.reference, 0, permission),))),
                fragments=None,
                preference_priority=priority,
            ),
        )
