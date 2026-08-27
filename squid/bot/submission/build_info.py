"""Semantic build details and navigation."""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.i18n import t
from squid.bot.submission.ui.components import build_edit
from squid.core.i18n import _
from squid.topics import resource_topic

if TYPE_CHECKING:
    from squid.builds.domain import Build

type Projection = tuple[Build, sl.LayoutNode[sl.ComponentsV2Target]]
type Refresh = Callable[[int], Awaitable[Projection | None]]


class BuildInfoComponent(sl.Component[sl.ComponentsV2Target]):
    """Show a rendered build card with an edit action at a native-form boundary.

    Given a `refresh`, the card stays current: the resource below watches the build's topic,
    so anything that publishes it -- another command, the render worker -- redraws every panel
    showing that build without either side knowing about the other.
    """

    def __init__(
        self,
        build: Build,
        node: sl.LayoutNode[sl.ComponentsV2Target],
        *,
        refresh: Refresh | None = None,
        locale: str | None = None,
        ephemeral: bool = False,
        timeout: float = 300,
        access: sd.AccessPolicy | None = None,
    ) -> None:
        self._seed: Projection | None = (build, node)
        self._refresh = refresh
        self._build_id = build.id
        self.locale = locale
        self._ephemeral = ephemeral
        self._timeout = timeout
        self._access = access if access is not None else sd.Everyone()

    @sl.resource(pending=sl.resources.PendingMode.ATOMIC)
    async def projection(self) -> Projection:
        """The build and its rendered card, reloaded whenever the build's topic is published.

        The caller had already fetched both, so the first settle consumes that rather than
        querying again -- but it still runs, because the watch has to be a tracked read of
        this loader for the mount to follow the topic at all.
        """
        if self._build_id is not None:
            sl.runtime.watch(resource_topic("build", str(self._build_id)))
        seed, self._seed = self._seed, None
        if seed is not None:
            return seed
        if self._refresh is None or self._build_id is None:
            message = "build info has no way to reload itself"
            raise sl.resources.ResourceNotReadyError(message)
        latest = await self._refresh(self._build_id)
        if latest is None:
            message = f"build {self._build_id} no longer exists"
            raise LookupError(message)
        return latest

    @property
    def build(self) -> Build:
        """The build currently on screen: the last one that loaded, stale or not."""
        return self._current()[0]

    def _current(self) -> Projection:
        if self._seed is not None:
            return self._seed
        state = self.projection.status
        if isinstance(state, sl.resources.Ready):
            return state.value
        if state.previous is not None:
            # A failed or in-flight reload keeps showing what is on screen. The topic will be
            # published again the next time the build changes.
            return state.previous.value
        message = "build info has not loaded yet"
        raise sl.resources.ResourceNotReadyError(message)

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        if not isinstance(self.projection.status, sl.resources.Ready) and self.projection.status.previous is None:
            return (sl.status(t(self.locale, _("Loading build."))),)
        build, node = self._current()
        if build.id is None:
            # Nothing stored to point a route at yet, so the control lives in this session.
            edit = sl.action_controls(
                sl.action_control(t(self.locale, _("Edit")), self._edit, key="edit"), key="build-actions"
            )
        else:
            edit = sl.primitives.Section(
                (sl.primitives.Text(t(self.locale, _("Edit this build.")), priority=-10),),
                sl.primitives.RoutedButton(t(self.locale, _("Edit")), build_edit.id(build_id=build.id)),
            )
        return (node, edit)

    async def _edit(self, event: sl.PressEvent) -> None:
        interaction = sd.native(event)
        from squid.bot.submission.ui.views import BuildEditComponent

        await BuildEditComponent(
            self.build,
            interaction.client.services.builds,
        ).send(interaction, ephemeral=self._ephemeral, parent=sd.responder(event).message_root)
