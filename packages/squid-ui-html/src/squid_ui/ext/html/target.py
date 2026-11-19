"""Built-in semantic HTML target and adapter profile."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self, overload

from squid_ui.authoring.document import DocumentLike
from squid_ui.capabilities import Capability
from squid_ui.ext.html import scene
from squid_ui.ext.html.capabilities import HtmlAdapterCapability
from squid_ui.ext.html.composition import HtmlCompositionRules
from squid_ui.ext.html.target_types import HtmlAdapter, HtmlTarget
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.composition_planner import RuleComposition
from squid_ui.planning.dialect import InputPolicy, TargetComposition
from squid_ui.planning.pagination.navigation import PlannedNav, page_select_nav
from squid_ui.planning.resources import Axis
from squid_ui.planning.target import Target


@dataclass(frozen=True, slots=True)
class HtmlLimits:
    """Unbounded target limits: semantic HTML has no reservable global axes."""

    @property
    def capacities(self) -> Mapping[Axis, int]:
        return {}

    def with_capacities(self, reductions: Mapping[Axis, int]) -> Self:
        if reductions:
            message = "HTML has no reservable global resource axes"
            raise ValueError(message)
        return self

    def digest(self) -> tuple[tuple[str, object], ...]:
        return ()


HTML_LIMITS = HtmlLimits()


class HtmlDialect:
    """Native semantic HTML scene shape, capabilities, and limits."""

    id = "html.semantic"
    version = 1
    capabilities = frozenset(
        {
            Capability.ACTIONS_BUTTONS,
            Capability.ACTIONS_SELECT,
            Capability.FORMS_INLINE,
            Capability.LAYOUT_CONTAINER,
            Capability.LAYOUT_GALLERY,
            Capability.LAYOUT_SECTION,
            Capability.LAYOUT_SEMANTIC,
        }
    )
    render_target = HtmlTarget
    body_type = scene.HtmlBody
    default_limits = HTML_LIMITS
    realizes_extensions = False

    @property
    def navigation(self) -> PlannedNav | None:
        return page_select_nav

    def input_policy(self, limits: HtmlLimits) -> InputPolicy:
        return InputPolicy(character_axis=True, budget_ladder=True)

    def composer[R](self, document: DocumentLike[R], limits: HtmlLimits) -> TargetComposition[R, scene.HtmlBody]:
        return RuleComposition(HtmlCompositionRules[R, object]())


HTML_DIALECT = HtmlDialect()
HTML_ADAPTER = AdapterProfile(
    HtmlAdapter,
    "squid-ui.html",
    ">=0.1.0a1,<0.2",
    capabilities=frozenset({HtmlAdapterCapability.RENDER_HTML}),
    frontend="html",
)


@overload
def target() -> Target[HtmlLimits, scene.HtmlBody, HtmlTarget, HtmlAdapter]: ...


@overload
def target[AdapterT: HtmlAdapter](
    *, adapter: AdapterProfile[AdapterT]
) -> Target[HtmlLimits, scene.HtmlBody, HtmlTarget, AdapterT]: ...


def target(
    *, adapter: AdapterProfile[HtmlAdapter] = HTML_ADAPTER
) -> Target[HtmlLimits, scene.HtmlBody, HtmlTarget, HtmlAdapter]:
    """Return a semantic HTML target realized by the selected HTML adapter."""
    return Target(HTML_DIALECT, adapter, HTML_LIMITS)


__all__ = ["HTML_ADAPTER", "HTML_DIALECT", "HTML_LIMITS", "HtmlLimits", "target"]
