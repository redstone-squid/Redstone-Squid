"""Small on-demand snapshots of the same state and plan the showcase actually uses."""

from datetime import UTC, datetime

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.ui import tr


class InspectablePanel(sd.Screen):
    policy = sd.ResponseSpec(audience="public", live_updates=True)
    inspection: str = sl.state("", persist=False)

    def inspection_state(self) -> str:
        """Describe this panel's meaningful application state without mutating it."""
        return ""

    def inspector(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        controls = sl.action_controls(
            sl.action_control(
                tr(t"layout-inspector-refresh-inspector = Refresh inspector")
                if self.inspection
                else tr(t"layout-inspector-inspect = Inspect"),
                self._inspect,
                key="inspect",
            ),
            *(
                (
                    sl.action_control(
                        tr(t"layout-inspector-hide-inspector = Hide inspector"),
                        self._hide_inspector,
                        key="hide-inspector",
                    ),
                )
                if self.inspection
                else ()
            ),
            key="inspection-actions",
        )
        if not self.inspection:
            return controls
        return sl.section(
            sl.heading(tr(t"layout-inspector-developer-snapshot = Developer snapshot")),
            sl.paragraph(self.inspection),
            controls,
        )

    async def _inspect(self, event: sl.PressEvent[sd.Request]) -> None:
        root = event.context.root
        report = None if root is None or root.plan is None else root.plan.report
        events = () if report is None else report.events
        decisions = "\n".join(f"• {item.code}: {item.message}" for item in events) or "No layout adaptations recorded."
        source = type(self).__module__.replace(".", "/") + ".py"
        stamp = datetime.now(UTC).strftime("%H:%M:%S UTC")
        self.inspection = (
            f"Captured at {stamp}. Refresh after an interaction to compare.\n"
            f"{self.inspection_state()}\n\n"
            f"Latest committed layout before this press:\n{decisions[:900]}\n\n"
            f"Source: `{source}` · `{type(self).__name__}`"
        )

    async def _hide_inspector(self, event: sl.PressEvent[sd.Request]) -> None:
        self.inspection = ""
