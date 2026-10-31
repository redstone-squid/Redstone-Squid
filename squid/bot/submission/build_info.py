"""Semantic build details and navigation."""

from typing import TYPE_CHECKING

import squid_layouts as sl
from squid.bot.i18n import t
from squid.bot.routes import build_edit
from squid.bot.ui import create_mount
from squid.core.i18n import _

if TYPE_CHECKING:
    from squid.builds.domain import Build


class BuildInfoComponent(sl.Component):
    """Show a rendered build card with an edit action at a native-form boundary."""

    def __init__(
        self,
        build: Build,
        node: sl.LayoutNode,
        *,
        locale: str | None = None,
        ephemeral: bool = False,
        timeout: float = 300,
        lock_to: int | None = None,
    ) -> None:
        self.build = build
        self._node = node
        self.locale = locale
        self._ephemeral = ephemeral
        self._timeout = timeout
        self._lock_to = lock_to

    def render(self) -> tuple[sl.LayoutNode, ...]:
        if self.build.id is None:
            # Nothing stored to point a route at yet, so the control lives in this session.
            edit = sl.primitives.Row((sl.primitives.Button(t(self.locale, _("Edit")), self._edit, "edit"),))
        else:
            edit = sl.primitives.Section(
                (sl.primitives.Text(t(self.locale, _("Edit this build.")), priority=-10),),
                sl.primitives.RoutedButton(t(self.locale, _("Edit")), build_edit.id(build_id=self.build.id)),
            )
        return (self._node, edit)

    async def _edit(self, event: sl.PressEvent) -> None:
        interaction = sl.discord.native(event)
        from squid.bot.submission.ui.views import BuildEditComponent

        await BuildEditComponent(
            self.build,
            interaction.client.services.builds,
            locale=self.locale,
        ).send(interaction, ephemeral=self._ephemeral, parent=sl.discord.responder(event).mount)

    def mount(self) -> sl.discord.Mount:
        return create_mount(
            self,
            locale=self.locale,
            timeout=self._timeout,
            lock_to=self._lock_to,
        )
