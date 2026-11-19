"""Discord Components V2 composition dialect."""

from squid_ui.authoring.document import DocumentLike
from squid_ui.capabilities import Capability
from squid_ui.ext.discord import scene
from squid_ui.ext.discord.capabilities import DiscordCapability
from squid_ui.ext.discord.limits import LIMITS, V2Limits
from squid_ui.ext.discord.planning.composition_v2 import V2CompositionRules
from squid_ui.ext.discord.planning.input import V2Input
from squid_ui.ext.discord.target_types import ComponentsV2Target
from squid_ui.planning.composition_planner import RuleComposition
from squid_ui.planning.dialect import InputPolicy, TargetComposition
from squid_ui.planning.pagination.navigation import PlannedNav


class V2Dialect:
    """Discord Components V2 shape. Everything else about planning is shared."""

    id = "discord.components-v2"
    version = 1
    capabilities = frozenset(
        {
            Capability.ACTIONS_BUTTONS,
            DiscordCapability.ACTIONS_PREMIUM,
            Capability.ACTIONS_SELECT,
            Capability.ACTIONS_ENTITY,
            DiscordCapability.FORMS_ENTITY,
            DiscordCapability.FORMS_FILE,
            DiscordCapability.FORMS_CHECKBOX_GROUP,
            Capability.FORMS_MODAL,
            Capability.LAYOUT_CONTAINER,
            Capability.LAYOUT_GALLERY,
            Capability.LAYOUT_SECTION,
        }
    )
    render_target = ComponentsV2Target
    body_type = scene.ComponentsV2
    default_limits = LIMITS
    realizes_extensions = True

    @property
    def navigation(self) -> PlannedNav | None:
        return None

    def input_policy(self, limits: V2Limits) -> InputPolicy:
        return InputPolicy(limits.total_text, character_axis=True, native=V2Input)

    def composer[R](self, document: DocumentLike[R], limits: V2Limits) -> TargetComposition[R, scene.ComponentsV2]:
        return RuleComposition(V2CompositionRules[R, object](limits))


V2_DIALECT = V2Dialect()
