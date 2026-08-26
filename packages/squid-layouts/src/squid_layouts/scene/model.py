"""Immutable, serializable output of target planning."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar

from squid_layouts.emoji import Emoji
from squid_layouts.entity import ChannelType, EntityRef, EntityType
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.forms import FormBinding
from squid_layouts.interactions import ActionBinding, ActionPolicy
from squid_layouts.primitives.styles import ActionStyle, Color
from squid_layouts.runtime.presentation import SessionUpdate
from squid_layouts.text import Markup


@dataclass(frozen=True, slots=True)
class SceneText:
    KIND: ClassVar[str] = "text"

    content: str
    markup: Markup = Markup.DISCORD_MARKDOWN


@dataclass(frozen=True, slots=True)
class SceneTime:
    KIND: ClassVar[str] = "time"

    instant: str
    style: str
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class SceneZonedTime:
    KIND: ClassVar[str] = "zoned_time"

    instant: str
    timezone: str
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class SceneFile:
    KIND: ClassVar[str] = "file"

    asset_key: str
    name: str
    media_type: str
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class SceneSeparator:
    KIND: ClassVar[str] = "separator"

    large: bool = False
    visible: bool = True


@dataclass(frozen=True, slots=True)
class SceneLink:
    KIND: ClassVar[str] = "link"

    label: str | None
    url: str
    emoji: Emoji | None = None
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class ScenePremiumButton:
    KIND: ClassVar[str] = "premium_button"

    sku_id: int


@dataclass(frozen=True, slots=True)
class SceneButton:
    KIND: ClassVar[str] = "button"

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

    KIND: ClassVar[str] = "routed_button"

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
    KIND: ClassVar[str] = "select"

    options: tuple[SceneOption, ...]
    action: str
    placeholder: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    policy: ActionPolicy = ActionPolicy.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class SceneRoutedSelect:
    KIND: ClassVar[str] = "routed_select"

    options: tuple[SceneOption, ...]
    route_id: str
    placeholder: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class SceneEntitySelect:
    KIND: ClassVar[str] = "entity_select"

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
    KIND: ClassVar[str] = "row"

    items: tuple[SceneLink | ScenePremiumButton | SceneButton | SceneRoutedButton | SceneExtension, ...]


@dataclass(frozen=True, slots=True)
class SceneThumbnail:
    KIND: ClassVar[str] = "thumbnail"

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
    KIND: ClassVar[str] = "gallery"

    items: tuple[SceneGalleryItem, ...]


@dataclass(frozen=True, slots=True)
class SceneSection:
    KIND: ClassVar[str] = "section"

    texts: tuple[SceneText, ...]
    accessory: SceneThumbnail | SceneLink | ScenePremiumButton | SceneButton | SceneRoutedButton | SceneExtension


@dataclass(frozen=True, slots=True)
class ScenePanel:
    KIND: ClassVar[str] = "panel"

    children: tuple[SceneNode, ...]
    accent: Color | None = None
    spoiler: bool = False


type JsonValue = str | int | float | bool | None | Sequence[JsonValue] | Mapping[str, JsonValue]
"""What may cross the scene codec. Stated by the type rather than only by prose."""


@dataclass(frozen=True, slots=True)
class SceneExtension:
    """Versioned target payload prepared by a registered extension adapter."""

    KIND: ClassVar[str] = "extension"

    kind: str
    version: int
    payload: Mapping[str, JsonValue]


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

    KIND: ClassVar[str] = "components_v2"

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

    KIND: ClassVar[str] = "classic_message"

    content: str | None = None
    embeds: tuple[SceneEmbed, ...] = ()
    rows: tuple[SceneClassicRow, ...] = ()


type SceneBody = SceneComponentsV2 | SceneClassicMessage


_KIND_OWNERS: dict[str, type] = {}
for _kind_cls in (
    SceneText,
    SceneTime,
    SceneZonedTime,
    SceneFile,
    SceneSeparator,
    SceneLink,
    ScenePremiumButton,
    SceneButton,
    SceneRoutedButton,
    SceneSelect,
    SceneRoutedSelect,
    SceneEntitySelect,
    SceneRow,
    SceneThumbnail,
    SceneGallery,
    SceneSection,
    ScenePanel,
    SceneExtension,
    SceneComponentsV2,
    SceneClassicMessage,
):
    if _kind_cls.KIND in _KIND_OWNERS:
        # A reused tag would let the codec's `match kind:` misroute an unrelated node type.
        message = f"scene kind tag {_kind_cls.KIND!r} is used by both {_KIND_OWNERS[_kind_cls.KIND].__name__} and {_kind_cls.__name__}"
        raise AssertionError(message)
    _KIND_OWNERS[_kind_cls.KIND] = _kind_cls
del _kind_cls


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
