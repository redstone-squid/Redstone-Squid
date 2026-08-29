"""Immutable, serializable output of target planning."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from squid_layouts.emoji import Emoji
from squid_layouts.entity import ChannelType, EntityRef, EntityType
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.forms import FormBinding
from squid_layouts.interactions import ActionBinding, ActionPolicy
from squid_layouts.primitives.styles import ActionStyle, Color
from squid_layouts.runtime.presentation import SessionUpdate
from squid_layouts.text import TextDialect


@dataclass(frozen=True, slots=True)
class SceneText:
    content: str
    dialect: TextDialect = TextDialect.DISCORD_MARKDOWN


@dataclass(frozen=True, slots=True)
class SceneTime:
    instant: str
    style: str
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class SceneZonedTime:
    instant: str
    timezone: str
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class SceneFile:
    asset_key: str
    name: str
    media_type: str
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class SceneSeparator:
    large: bool = False
    visible: bool = True


@dataclass(frozen=True, slots=True)
class SceneLink:
    label: str | None
    url: str
    emoji: Emoji | None = None
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class ScenePremiumButton:
    sku_id: int


@dataclass(frozen=True, slots=True)
class SceneButton:
    label: str | None
    action: str
    style: ActionStyle = ActionStyle.SECONDARY
    emoji: Emoji | None = None
    disabled: bool = False
    policy: ActionPolicy = ActionPolicy.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class SceneRoutedButton:
    """A button carrying its own route id, with no binding for a frontend to wire.

    That absence is the point: a renderer can draw one without a live session, which is
    what lets a sessionless document hold a control, and a codec can round-trip one,
    which a process-local handler could never be.
    """

    label: str | None
    route_id: str
    style: ActionStyle = ActionStyle.SECONDARY
    emoji: Emoji | None = None
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class SceneOption:
    label: str
    value: str
    description: str | None = None
    default: bool = False
    emoji: Emoji | None = None


@dataclass(frozen=True, slots=True)
class SceneSelect:
    options: tuple[SceneOption, ...]
    action: str
    placeholder: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    policy: ActionPolicy = ActionPolicy.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class SceneRoutedSelect:
    options: tuple[SceneOption, ...]
    route_id: str
    placeholder: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class SceneEntitySelect:
    entity_type: EntityType
    action: str
    placeholder: str | None = None
    default_values: tuple[EntityRef, ...] = ()
    channel_types: tuple[ChannelType, ...] = ()
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    policy: ActionPolicy = ActionPolicy.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class SceneRow:
    items: tuple[SceneLink | ScenePremiumButton | SceneButton | SceneRoutedButton | SceneExtension, ...]


@dataclass(frozen=True, slots=True)
class SceneThumbnail:
    url: str
    description: str | None = None
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class SceneGalleryItem:
    url: str
    description: str | None = None
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class SceneGallery:
    items: tuple[SceneGalleryItem, ...]


@dataclass(frozen=True, slots=True)
class SceneSection:
    texts: tuple[SceneText, ...]
    accessory: SceneThumbnail | SceneLink | ScenePremiumButton | SceneButton | SceneRoutedButton | SceneExtension


@dataclass(frozen=True, slots=True)
class ScenePanel:
    children: tuple[SceneNode, ...]
    accent: Color | None = None
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class SceneExtension:
    """Versioned target payload prepared by a registered extension adapter."""

    kind: str
    version: int
    payload: Mapping[str, object]


type SceneNode = (
    SceneText
    | SceneTime
    | SceneZonedTime
    | SceneFile
    | SceneSeparator
    | SceneRow
    | SceneSelect
    | SceneRoutedSelect
    | SceneEntitySelect
    | SceneRoutedButton
    | ScenePremiumButton
    | SceneThumbnail
    | SceneGallery
    | SceneSection
    | ScenePanel
    | SceneExtension
)


# --- Message bodies -------------------------------------------------------------------------
#
# A scene resolves to *one* Discord message, and Discord has two kinds. A Components V2
# message is a component tree and has no content or embeds at all; a classic message is
# content, embeds, and action rows and cannot hold a component tree. Modelling both as one
# flat child list would force every consumer to rediscover which kind it was holding, so the
# body says so once.


@dataclass(frozen=True, slots=True)
class SceneComponentsV2:
    """A Components V2 message: the component tree is the whole message."""

    children: tuple[SceneNode, ...] = ()


@dataclass(frozen=True, slots=True)
class SceneEmbedField:
    name: str
    value: str
    inline: bool = False


@dataclass(frozen=True, slots=True)
class SceneEmbedAuthor:
    name: str
    url: str | None = None
    icon_url: str | None = None


@dataclass(frozen=True, slots=True)
class SceneEmbedFooter:
    text: str
    icon_url: str | None = None


@dataclass(frozen=True, slots=True)
class SceneEmbedMedia:
    """One embed image or thumbnail. The description is kept even where Discord drops it."""

    url: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class SceneEmbed:
    """One resolved embed. Every grouping and overflow decision is already made.

    Server-generated properties — provider, video, and the fields Discord fills from a URL
    it unfurls — are deliberately absent. A scene describes what Squid asked for, and
    round-tripping something the API invents would make the fingerprint lie.
    """

    title: str | None = None
    url: str | None = None
    description: str | None = None
    fields: tuple[SceneEmbedField, ...] = ()
    footer: SceneEmbedFooter | None = None
    author: SceneEmbedAuthor | None = None
    colour: Color | None = None
    image: SceneEmbedMedia | None = None
    thumbnail: SceneEmbedMedia | None = None
    timestamp: str | None = None
    """An ISO-8601 instant, or None. Stored as text so the scene stays plain data."""


type SceneControl = (
    SceneLink
    | ScenePremiumButton
    | SceneButton
    | SceneRoutedButton
    | SceneSelect
    | SceneRoutedSelect
    | SceneEntitySelect
    | SceneExtension
)


@dataclass(frozen=True, slots=True)
class SceneClassicRow:
    """One classic action row. Row assignment is a planning decision, not a drawing one."""

    controls: tuple[SceneControl, ...] = ()


@dataclass(frozen=True, slots=True)
class SceneClassicMessage:
    """A pre-Components-V2 message: content, embeds, and up to five action rows."""

    content: str | None = None
    embeds: tuple[SceneEmbed, ...] = ()
    rows: tuple[SceneClassicRow, ...] = ()


type SceneBody = SceneComponentsV2 | SceneClassicMessage


@dataclass(frozen=True, slots=True)
class SceneAsset:
    key: str
    name: str
    media_type: str


@dataclass(frozen=True, slots=True)
class ScenePager:
    key: str
    page: int
    pages: int
    content_fingerprint: str


@dataclass(frozen=True, slots=True)
class SceneDocument[BodyT = SceneBody]:
    """A target-resolved scene with no callbacks or native frontend objects."""

    protocol: int
    target: str
    target_version: int
    body: BodyT
    assets: tuple[SceneAsset, ...] = ()
    pagers: tuple[ScenePager, ...] = ()

    @property
    def components_v2(self) -> SceneComponentsV2:
        """The Components V2 body, for a caller that only speaks V2.

        Raises:
            LayoutInvariantError: This scene resolved to some other kind of message.
        """
        if not isinstance(self.body, SceneComponentsV2):
            message = f"scene for target {self.target!r} has a {type(self.body).__name__} body, not Components V2"
            raise LayoutInvariantError(message)
        return self.body

    def expect_body[ExpectedT](self, body_type: type[ExpectedT]) -> ExpectedT:
        """Narrow a broadly decoded scene at an explicit frontend boundary."""
        if not isinstance(self.body, body_type):
            message = (
                f"scene for target {self.target!r} has a {type(self.body).__name__} body, not {body_type.__name__}"
            )
            raise LayoutInvariantError(message)
        return self.body


class PlanSeverity(StrEnum):
    ADAPTATION = "adaptation"
    DEGRADATION = "degradation"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class PlanEvent:
    code: str
    path: str
    message: str
    severity: PlanSeverity = PlanSeverity.ADAPTATION
    before: Mapping[str, int] = field(default_factory=dict)
    after: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlanReport:
    events: tuple[PlanEvent, ...] = ()
    logical_fingerprint: str = ""
    scene_fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class PlanMetrics:
    """Planner instrumentation kept outside deterministic reports and scene payloads."""

    states_explored: int = 0
    """`measure()` calls the search spent, across strategies, fallbacks, and ladder rungs."""
    cache_hit: bool = False
    search_fallback: bool = False


@dataclass(frozen=True, slots=True)
class PlanResult[BodyT = SceneBody]:
    scene: SceneDocument[BodyT]
    bindings: Mapping[str, ActionBinding]
    report: PlanReport
    form_bindings: Mapping[str, FormBinding] = field(default_factory=dict)
    """What each declared form key presents right now, for resolving a late submission."""
    resources: Mapping[str, object] = field(default_factory=dict)
    metrics: PlanMetrics = field(default_factory=PlanMetrics)
    session_updates: tuple[SessionUpdate, ...] = ()
    """Presentation writes this plan earned but did not make.

    Planning only reads the session. A frontend applies these once the render has
    actually reached the reader, so a failed delivery leaves them where the message
    still shows them."""
