"""Semantic build details and navigation."""

from typing import TYPE_CHECKING

import squid_layouts as sl
from squid.bot.i18n import t
from squid.bot.submission.ui.components import DynamicBuildEditButton, EphemeralBuildEditButton
from squid.bot.ui import create_mount
from squid.core.i18n import _

if TYPE_CHECKING:
    from squid.builds.domain import Build


class BuildInfoComponent(sl.Component):
    """Show a rendered build card with an edit action at a native-form boundary."""

    def __init__(
        self,
        build: Build,
        node: sl.primitives.Node,
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
        edit_fallback = sl.primitives.Button(
            t(self.locale, _("Edit")),
            self._edit,
            "edit",
        )
        native_edit = sl.primitives.Section(
            (sl.primitives.Text(t(self.locale, _("Edit this build.")), priority=-10),),
            sl.primitives.RawItem(
                lambda: (
                    EphemeralBuildEditButton(self.build)
                    if self.build.id is None
                    else DynamicBuildEditButton(self.build)
                ),
                kind="discord.item",
                version=1,
            ),
        )
        edit = sl.primitives.Choice(
            (
                sl.primitives.Variant(native_edit, frozenset({"extension.discord.item"})),
                sl.primitives.Variant(
                    sl.primitives.Row((edit_fallback,)),
                ),
            )
        )
        return (self._node, edit)

    async def _edit(self, event: sl.PressEvent) -> None:
        interaction = getattr(event.responder, "interaction", None)
        if interaction is None:
            return
        from squid.bot.submission.ui.views import BuildEditComponent

        await BuildEditComponent(
            self.build,
            interaction.client.services.builds,
            locale=self.locale,
        ).send(interaction, ephemeral=self._ephemeral)

    def mount(self) -> sl.discord.Mount:
        return create_mount(
            self,
            locale=self.locale,
            timeout=self._timeout,
            lock_to=self._lock_to,
        )
