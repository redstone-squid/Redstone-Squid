"""Built-in semantic HTML target and adapter profile."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, overload

from squid_ui import scene
from squid_ui.capabilities import Capability
from squid_ui.planning.adapter import AdapterCapability, AdapterProfile
from squid_ui.planning.resources import Axis
from squid_ui.planning.target import Target
from squid_ui.target_types import HtmlAdapter, HtmlTarget

if TYPE_CHECKING:
    from squid_ui.planning.html_planner import HtmlPlanner


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
    """Native semantic HTML scene shape and its complete planner backend."""

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
    def planner(self) -> HtmlPlanner:
        from squid_ui.planning.html_planner import HTML_PLANNER

        return HTML_PLANNER


HTML_DIALECT = HtmlDialect()
HTML_ADAPTER = AdapterProfile(
    HtmlAdapter,
    "squid-ui.html",
    ">=0.1.0a1,<0.2",
    capabilities=frozenset({AdapterCapability.RENDER_HTML}),
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
