"""Preparation is concurrent, instance-owned, and diagnostic rather than timed out."""

from typing import override

import anyio
import pytest

import squid_ui as sl
from squid_ui.runtime.preparation import ComponentPreparation


class Prepared(sl.Component):
    @override
    def render(self) -> sl.LayoutNode:
        return sl.paragraph("ready")


async def test_shared_components_load_once_and_report_every_placement() -> None:
    loaded: list[str] = []
    entered = anyio.Event()

    class First(Prepared):
        @override
        async def on_load(self) -> None:
            await entered.wait()
            loaded.append("first")

    class Second(Prepared):
        @override
        async def on_load(self) -> None:
            entered.set()
            loaded.append("second")

    first, second = First(), Second()
    preparation = ComponentPreparation(slow_threshold=0)
    batch = await preparation.prepare({"first": first, "preview": first, "second": second})
    assert sorted(loaded) == ["first", "second"]
    assert len(batch.components) == 2
    assert batch.components[0].paths == ("first", "preview")
    assert all(report.outcome == "ready" and not report.reused for report in batch.components)
    assert batch.elapsed >= max(report.elapsed for report in batch.components)
    reused = await preparation.prepare({"first": first})
    assert reused.components[0].reused
    assert sorted(loaded) == ["first", "second"]


async def test_failed_and_cancelled_preparation_remains_retryable() -> None:
    waiting = anyio.Event()

    class Waiting(Prepared):
        @override
        async def on_load(self) -> None:
            waiting.set()
            await anyio.sleep_forever()

    class Broken(Prepared):
        @override
        async def on_load(self) -> None:
            await waiting.wait()
            raise ValueError("cannot prepare")

    first, second = Waiting(), Broken()
    preparation = ComponentPreparation()
    with pytest.raises(ExceptionGroup):
        await preparation.prepare({"waiting": first, "broken": second})
    assert not first._loaded and not second._loaded
    assert [report.outcome for report in preparation.latest.components] == ["cancelled", "failed"]
    assert isinstance(preparation.latest.components[1].error, ValueError)
