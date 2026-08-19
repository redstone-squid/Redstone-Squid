"""Immutable, serializable output of target planning."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from squid_layouts.actions import ActionBinding, ActionPolicy
from squid_layouts.styles import ActionStyle, Color
from squid_layouts.text import TextDialect


@dataclass(frozen=True, slots=True)
class SceneText:
    content: str
    dialect: TextDialect = TextDialect.DISCORD_MARKDOWN


@dataclass(frozen=True, slots=True)
class SceneSeparator:
    large: bool = False
    visible: bool = True


@dataclass(frozen=True, slots=True)
class SceneLink:
    label: str
    url: str


@dataclass(frozen=True, slots=True)
class SceneButton:
    label: str
    action: str
    style: ActionStyle = ActionStyle.SECONDARY
    emoji: str | None = None
    disabled: bool = False
    policy: ActionPolicy = ActionPolicy.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class SceneOption:
    label: str
    value: str
    description: str | None = None
    default: bool = False


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
class SceneRow:
    items: tuple[SceneLink | SceneButton | SceneExtension, ...]


@dataclass(frozen=True, slots=True)
class SceneThumbnail:
    url: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class SceneGalleryItem:
    url: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class SceneGallery:
    items: tuple[SceneGalleryItem, ...]


@dataclass(frozen=True, slots=True)
class SceneSection:
    texts: tuple[SceneText, ...]
    accessory: SceneThumbnail | SceneLink | SceneButton | SceneExtension


@dataclass(frozen=True, slots=True)
class ScenePanel:
    children: tuple[SceneNode, ...]
    accent: Color | None = None


@dataclass(frozen=True, slots=True)
class SceneExtension:
    """Versioned target payload prepared by a registered extension adapter."""

    kind: str
    version: int
    payload: Mapping[str, object]


type SceneNode = (
    SceneText
    | SceneSeparator
    | SceneRow
    | SceneSelect
    | SceneThumbnail
    | SceneGallery
    | SceneSection
    | ScenePanel
    | SceneExtension
)


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
class SceneDocument:
    """A target-resolved scene with no callbacks or native frontend objects."""

    protocol: int
    target: str
    target_version: int
    children: tuple[SceneNode, ...]
    assets: tuple[SceneAsset, ...] = ()
    pagers: tuple[ScenePager, ...] = ()


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
    cache_hit: bool = False


@dataclass(frozen=True, slots=True)
class PlanResult:
    scene: SceneDocument
    bindings: Mapping[str, ActionBinding]
    report: PlanReport
    resources: Mapping[str, object] = field(default_factory=dict)
    metrics: PlanMetrics = field(default_factory=PlanMetrics)
