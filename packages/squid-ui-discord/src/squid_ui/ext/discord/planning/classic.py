"""Discord classic-message composition dialect."""

from squid_ui.authoring.document import DocumentLike
from squid_ui.capabilities import Capability
from squid_ui.ext.discord import scene
from squid_ui.ext.discord.capabilities import DiscordCapability
from squid_ui.ext.discord.limits import CLASSIC_LIMITS, ClassicLimits
from squid_ui.ext.discord.planning.composition_classic import ClassicCompositionRules
from squid_ui.ext.discord.planning.input import ClassicInput
from squid_ui.ext.discord.target_types import ClassicTarget
from squid_ui.planning.composition_planner import RuleComposition
from squid_ui.planning.composition_request import TargetComposition
from squid_ui.planning.dialect import InputPolicy
from squid_ui.planning.pagination.navigation import PlannedNav


class ClassicDialect:
    """Pre-Components-V2 message shape. Everything else about planning is shared."""

    id = "discord.components-v1"
    version = 1
    capabilities = frozenset(
        {
            Capability.ACTIONS_BUTTONS,
            DiscordCapability.ACTIONS_PREMIUM,
            Capability.ACTIONS_SELECT,
            Capability.ACTIONS_ENTITY,
            Capability.FORMS_MODAL,
            DiscordCapability.FORMS_CHECKBOX_GROUP,
            DiscordCapability.LAYOUT_EMBED,
            DiscordCapability.LAYOUT_EMBED_FIELDS,
            DiscordCapability.MESSAGE_CONTENT,
        }
    )
    render_target = ClassicTarget
    body_type = scene.ClassicMessage
    default_limits = CLASSIC_LIMITS
    # A classic message has no component that can hold a native discord.py item.
    realizes_extensions = False

    @property
    def navigation(self) -> PlannedNav | None:
        return None

    def input_policy(self, limits: ClassicLimits) -> InputPolicy:
        return InputPolicy(max(limits.content, limits.embeds.description), native=ClassicInput)

    def composer[R](
        self, document: DocumentLike[R], limits: ClassicLimits
    ) -> TargetComposition[R, scene.ClassicMessage]:
        return RuleComposition(ClassicCompositionRules[R, object](limits))


CLASSIC_DIALECT = ClassicDialect()
