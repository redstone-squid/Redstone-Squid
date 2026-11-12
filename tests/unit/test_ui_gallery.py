"""The UI gallery plugin still sees renders through the message-root hook it relies on."""

import json
from pathlib import Path

import squid_ui as sl
import squid_ui_discord as sd
from tests.support.ui_gallery import GalleryRecorder


class _Sample(sd.Screen):
    def render(self) -> sl.LayoutNode:
        async def noop(event: sl.PressEvent) -> None:
            del event

        return sl.section(
            sl.heading("Sample"),
            sl.action_controls(
                sl.action_control("Save", noop, key="save"),
                sl.action_control("Cancel", noop, key="cancel"),
                key="actions",
            ),
            sl.toggle("Alerts", key="alerts"),
        )


async def test_recorder_captures_each_distinct_render_once(tmp_path: Path) -> None:
    recorder = GalleryRecorder(module_prefix=__name__)
    uninstall = recorder.install()
    try:
        async with sd.testing.stage(_Sample(), owner=object()) as ui:
            await ui.press("alerts")
    finally:
        uninstall()

    # Pressing the toggle changes its label, so the press produces a second distinct scene.
    first, pressed = recorder.renders.values()
    assert (first.screen, pressed.screen) == ("_Sample", "_Sample")
    assert "Alerts: Off" in first.preview
    assert "Alerts: On" in pressed.preview
    assert first.error is None

    recorder.write(tmp_path)

    assert len(json.loads((tmp_path / "index.json").read_text())) == 2
    assert "Alerts: Off" in (tmp_path / "index.html").read_text()
    assert len(list((tmp_path / "renders").glob("*.html"))) == 2


async def test_recorder_ignores_components_outside_the_module_prefix() -> None:
    recorder = GalleryRecorder(module_prefix="squid.")
    uninstall = recorder.install()
    try:
        async with sd.testing.stage(_Sample(), owner=object()):
            pass
    finally:
        uninstall()

    assert recorder.renders == {}
