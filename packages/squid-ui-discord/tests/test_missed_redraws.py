"""A message without live updates reports when it shows state changed outside its interactions."""

from typing import override

import pytest

import squid_ui as sl
import squid_ui_discord as sd


class Shared(sl.Reactive):
    count: int = sl.state(0)


class Counter(sd.Screen[None]):
    def __init__(self, shared: Shared) -> None:
        self.shared = shared

    @override
    def render(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        return sl.stack(
            sl.paragraph(f"Count {self.shared.count}"),
            sl.action_controls(sl.action_control("Bump", self.bump, key="bump"), key="actions"),
        )

    async def bump(self, event: sd.PressEvent) -> None:
        self.shared.count += 1


def missed(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if getattr(record, "squid_unscheduled_change", False)]


async def test_a_press_on_a_message_left_behind_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    shared = Shared()
    async with sd.testing.stage(Counter(shared), owner=object()) as ui:
        shared.count = 5
        await ui.press("bump")

    (message,) = missed(caplog)
    assert message.startswith("Counter was pressed while still showing state from before a change")
    assert "(Shared.count)" in message


async def test_closing_a_message_left_behind_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    shared = Shared()
    async with sd.testing.stage(Counter(shared), owner=object()):
        shared.count = 5

    (message,) = missed(caplog)
    assert message.startswith("Counter was closed while still showing state")


async def test_changes_from_the_message_itself_are_not_reported(caplog: pytest.LogCaptureFixture) -> None:
    async with sd.testing.stage(Counter(Shared()), owner=object()) as ui:
        await ui.press("bump")
        await ui.press("bump")

    assert missed(caplog) == []


async def test_an_explicit_refresh_catches_the_message_up(caplog: pytest.LogCaptureFixture) -> None:
    shared = Shared()
    async with sd.testing.stage(Counter(shared), owner=object()) as ui:
        shared.count = 5
        await ui.root.refresh()
        assert any("Count 5" in text for text in ui.texts())
        await ui.press("bump")

    assert missed(caplog) == []


async def test_live_updates_redraw_instead_of_reporting(caplog: pytest.LogCaptureFixture) -> None:
    shared = Shared()
    async with sd.testing.stage(Counter(shared), owner=object(), live_updates=True) as ui:
        shared.count = 5
        assert isinstance(ui.root.scheduler, sd.MessageRootScheduler)
        await ui.root.scheduler.wait_idle()
        assert any("Count 5" in text for text in ui.texts())

    assert missed(caplog) == []
