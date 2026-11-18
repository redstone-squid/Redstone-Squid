"""Review catalogue components in Discord with disposable sample data."""

from typing import override

import squid_ui as sl
import squid_ui.ext.discord as sd
import squid_ui_widgets as sp
from squid.bot.catalogue.record_display import record_standings
from squid.bot.submission.build_card import build_card
from squid.bot.ui import tr
from squid.builds.domain import Status
from squid.builds.models import DoorBuild
from squid.records.domain.models import BaseTitle, BuildKind, VersionScope
from squid_ui.ext.discord.target_types import ComponentsV2Target


class CataloguePreview(sd.Screen):
    policy = sd.ResponseSpec(audience="personal", session=sd.SessionSpec("layout-catalogue"))

    def __init__(self) -> None:
        title = "Smallest 2x2 Seamless Door"
        subtitle = "Observerless"
        holders = tuple(range(42, 72))
        published = record_standings(
            title,
            subtitle,
            BaseTitle.SMALLEST,
            VersionScope.ALL_TIME,
            holders,
            self._open_holder,
            build_kind=BuildKind.DOOR,
        )
        search = record_standings(
            sl.md(t"Smallest **2x2** Seamless Door"),
            subtitle,
            BaseTitle.SMALLEST,
            VersionScope.ALL_TIME,
            holders,
            self._open_holder,
            current_holder=holders[0],
        )
        build = DoorBuild(
            id=holders[0],
            submission_status=Status.CONFIRMED,
            width=3,
            height=4,
            depth=2,
            door_width=2,
            door_height=2,
            door_depth=1,
            normal_opening_time=8,
            component_restrictions=["Observerless"],
            version_spec="1.20+",
        )
        self.tabs = sp.Tabs[ComponentsV2Target, sd.Request](
            (
                sp.Tab("published", tr(t"layout-catalogue-published = Published record"), published),
                sp.Tab("search", tr(t"layout-catalogue-search = Search result"), search),
                sp.Tab("build", tr(t"layout-catalogue-build = Build card"), build_card(build)),
            ),
            key="catalogue-preview",
        )

    @override
    def render(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request], ...]:
        return (self.tabs,)

    async def _open_holder(self, build_id: int, event: sl.PressEvent) -> None:
        await event.notice(tr(t"layout-catalogue-sample-holder = Sample holder #{build_id!s}"))
