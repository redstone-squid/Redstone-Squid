"""Portable scene envelopes, assets, and pager coordinates."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import Field

from squid_ui._query_index import QueryIndexOwner
from squid_ui.errors import LayoutInvariantError
from squid_ui.scene.base import Body as Body
from squid_ui.scene.base import SceneNode as SceneNode
from squid_ui.scene.values import SceneValue

type JsonValue = str | int | float | bool | Sequence[JsonValue] | Mapping[str, JsonValue] | None


class Asset(SceneValue):
    """An asset declaration keyed to a separately supplied delivery resource."""

    key: str
    name: str
    media_type: str


class Pager(SceneValue):
    """A paginator's zero-based selected page, total page count, and content identity."""

    key: str
    page: int = Field(ge=0, strict=True)
    pages: int | None = Field(ge=1, strict=True)
    content_fingerprint: str

    def browser_state(self) -> dict[str, Any]:
        """Return the page coordinates exposed to preview and HTML hosts."""
        # Content identity stays server-side; browser controls only need coordinates.
        return self.model_dump(mode="json", include={"key", "page", "pages"})


@dataclass(frozen=True, slots=True)
class Scene[BodyT = Body](QueryIndexOwner, SceneNode):
    """One planned display, including its target, content, assets, and selected pages.

    The body holds the target's content structure: for example, classic Discord
    messages have content, embeds, and action rows. Callbacks and native frontend
    objects belong to PlanResult and do not cross the scene codec.

    Frozen dataclasses prevent attribute reassignment but do not freeze mappings
    nested in extension payloads. Preserve those values after planning so a scene's
    fingerprint continues to identify the same content.
    """

    DISPLAY_FIELDS: ClassVar[tuple[str, ...]] = ("body",)

    protocol: int
    target: str
    target_version: int
    body: BodyT
    assets: tuple[Asset, ...] = ()
    pagers: tuple[Pager, ...] = ()

    def expect_body[ExpectedT](self, body_type: type[ExpectedT]) -> ExpectedT:
        """Return the body as the requested type, or raise `LayoutInvariantError`."""
        if not isinstance(self.body, body_type):
            message = (
                f"scene for target {self.target!r} has a {type(self.body).__name__} body, not {body_type.__name__}"
            )
            raise LayoutInvariantError(message)
        return self.body
